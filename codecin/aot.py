"""AOT 构建: 把 CIN 程序编译成**独立静态可执行文件** (Windows / Linux / macOS)。

两种后端 (默认 ``auto`` 按指令数选择):
  * ``native``: 把指令流直接生成 Go 源码, 核心指令不再经过
    UCBC 解码/opcode dispatch, 由 Go 编译器产出 x86-64/ARM64 机器码;
  * ``vm``: 内嵌 UCBC 字节码与初始内存镜像, 由内置 Go VM 执行。

产物均 `CGO_ENABLED=0` 静态链接, 不依赖 Python、Go 工具链、libc 或任何
动态库; 可用 ``--build-target`` 交叉编译。

实现说明: VM 后端的 main.go 模板与 Go 侧 `codecin/native/aot` 共用同一份
文件 (``stub_main.go.txt``); native 后端的源码由 ``codecin/native_codegen.py``
生成。临时包建在 ``codecin-native`` 模块内的 ``.aotbuild-*`` 目录, 受限
环境下改用可写的 Go module + replace。

临时目录的生命周期 (加固, 见 docs/SUGGESTIONS_NEXT.md §3.3):
  * 每次 :func:`build` 之前先调用 :func:`sweep_stale_build_dirs` 清理**陈旧**残留,
    避免受限环境 (沙箱 / 只读 ACL) 下删不掉的目录无限累积;
  * ``finally`` 里的 ``rmtree`` 失败**不再静默**: 有 logger 就 ``warning``,
    没有 logger 时退回模块级 stdlib logger (``codecin.aot``), 绝不吞掉;
  * 只清理 ``codecin/native/`` **本目录下**、名字带 ``.aotbuild-`` /
    ``.aotprobe-`` 前缀、且**不是符号链接**的目录, 绝不递归删除别处。
"""

import functools
import hashlib
import logging
import os
import platform
import secrets
import shutil
import struct
import subprocess
import sys
import time
from typing import Dict, List, NamedTuple, Optional, Tuple

#: 仓库内 codecin-native 模块目录 (含 go.mod)。
_NATIVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'native')

#: 模块级 logger: 调用方没有传 logger 时的兜底 (绝不能因为"没人接"就静默)。
#: 它是 ``codecin`` logger 的子 logger, 因此 CLI 构造的 Logger (RichHandler) 会
#: 照常渲染它; 完全没配置 logging 时 stdlib 的 lastResort 也会打到 stderr。
_LOGGER = logging.getLogger(__name__)

#: AOT 临时目录名前缀 (``.aotprobe-`` 是历史遗留, 一并纳入守卫)。
TEMP_DIR_PREFIXES = ('.aotbuild-', '.aotprobe-')

#: 陈旧残留的默认容忍时长 (6 小时): 远大于任何一次 go build, 因此不会误删
#: 并发构建中的临时目录。
DEFAULT_STALE_AGE_SECONDS = 6 * 3600

#: 生成的 main.go 模板 (与 Go 侧共用)。
STUB_PATH = os.path.join(_NATIVE_DIR, 'aot', 'stub_main.go.txt')

#: 默认内存大小 (1 GiB): 与解释器 / Go CLI 一致; 由 VM 按需分配 (OS 懒提交)。
DEFAULT_MEM_SIZE = 1 << 30

#: 栈槽位 (与 isa.Constants.STACK_SLOT 一致, 避免循环导入)。
_STACK_SLOT = 8

#: auto 后端切换到 native 的指令数上限。native 后端把每条指令展开成
#: Go 语句, 数万条指令的程序 (如 basic.cin) 会让 go build 极慢; 超过阈值
#: 自动回退到 VM 后端。显式 ``--build-backend native`` 仍可强制 native。
NATIVE_INSTRUCTION_LIMIT = 20000


def resolve_backend(backend: str, instruction_count: int) -> str:
    """把 ``auto|native|vm`` 解析成实际后端。"""
    if backend == 'auto':
        return ('native' if int(instruction_count) <= NATIVE_INSTRUCTION_LIMIT
                else 'vm')
    if backend not in ('native', 'vm'):
        raise AotError(f'未知 AOT 后端: {backend} (可选 auto / native / vm)')
    return backend


# ==================== 增量构建缓存 ====================

def _cache_root() -> str:
    """AOT 缓存目录 (可用 CODECIN_AOT_CACHE 覆盖)。"""
    explicit = os.environ.get('CODECIN_AOT_CACHE')
    if explicit:
        return os.path.abspath(explicit)
    system = platform.system()
    if system == 'Windows':
        base = os.environ.get('LOCALAPPDATA') or os.path.join(
            os.path.expanduser('~'), 'AppData', 'Local')
    elif system == 'Darwin':
        base = os.path.join(os.path.expanduser('~'), 'Library', 'Caches')
    else:
        base = os.environ.get('XDG_CACHE_HOME') or os.path.join(
            os.path.expanduser('~'), '.cache')
    return os.path.join(base, 'codecin', 'aot')


def _toolchain_fingerprint() -> str:
    """AOT 产物相关工具链/代码生成器的指纹。"""
    return _toolchain_fingerprint_for(_NATIVE_DIR)


@functools.lru_cache(maxsize=4)
def _toolchain_fingerprint_for(native_dir: str) -> str:
    h = hashlib.sha256()
    py_dir = os.path.dirname(os.path.abspath(__file__))
    # 前端/运行时 Python 源码也参与指纹: CIN 编译器、汇编器、内存等变更
    # 即使源码相同也可能改变产物。
    for dirpath, dirnames, filenames in os.walk(py_dir):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in ('__pycache__', 'native', 'lib'))
        for name in sorted(filenames):
            if not name.endswith('.py'):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, 'rb') as f:
                    h.update(os.path.relpath(path, py_dir).encode()
                             + b'\0' + f.read())
            except OSError:
                pass
    for dirpath, dirnames, filenames in os.walk(native_dir):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in ('__pycache__', '.git')
                             and not d.startswith('.aotbuild-'))
        for name in sorted(filenames):
            if not (name.endswith('.go') or name in ('go.mod', 'go.sum')):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, 'rb') as f:
                    h.update(os.path.relpath(path, native_dir).encode()
                             + b'\0' + f.read())
            except OSError:
                pass
    try:
        proc = subprocess.run(['go', 'version'], capture_output=True,
                              text=True, encoding='utf-8', errors='replace',
                              timeout=10)
        h.update(b'go\0' + (proc.stdout or proc.stderr).encode())
    except Exception:                       # noqa: BLE001
        pass
    h.update(f'{platform.system()}\0{platform.machine()}\0'
             f'{sys.version}'.encode())
    return h.hexdigest()


def build_cache_key(program_file: str,
                    target: Optional[str],
                    backend: str,
                    optimize: int,
                    mem_size: Optional[int],
                    max_instructions: int,
                    debug: bool = False) -> Optional[str]:
    """计算 AOT 增量缓存键; 源/依赖不可读时返回 None (不缓存)。"""
    try:
        deps = program_dependencies(program_file)
    except Exception:                       # noqa: BLE001
        return None
    h = hashlib.sha256()
    for path in deps:
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError:
            return None
        h.update(os.path.basename(path).encode() + b'\0' + data)
    goos, goarch = parse_target(target) if target else parse_target(
        host_target())
    total = int(mem_size) if mem_size else DEFAULT_MEM_SIZE
    total = max(256, total)
    opts = (f'{goos}/{goarch}\0{backend}\0{int(optimize or 0)}\0'
            f'{total}\0{int(max_instructions)}\0{int(bool(debug))}\0'
            f'{_toolchain_fingerprint()}')
    h.update(opts.encode())
    return h.hexdigest()


def link_cache_key(paths,
                   target: Optional[str],
                   backend: str,
                   optimize: int,
                   mem_size: int,
                   max_instructions: int,
                   debug: bool = False,
                   gc_sections: bool = False) -> Optional[str]:
    """对象/归档链接的增量缓存键; 输入不可读时返回 None。"""
    h = hashlib.sha256()
    for path in paths:
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError:
            return None
        h.update(os.path.basename(path).encode() + b'\0' + data)
    goos, goarch = parse_target(target) if target else parse_target(
        host_target())
    opts = (f'{goos}/{goarch}\0{backend}\0{int(optimize or 0)}\0'
            f'{int(mem_size)}\0{int(max_instructions)}\0{int(bool(debug))}\0'
            f'{int(bool(gc_sections))}\0{_toolchain_fingerprint()}')
    h.update(opts.encode())
    return h.hexdigest()


def _cache_path(key: str, goos: str) -> str:
    suffix = '.exe' if goos == 'windows' else ''
    return os.path.join(_cache_root(), key + suffix)


def _cache_lookup(key: str, goos: str, out: str, logger=None) -> bool:
    path = _cache_path(key, goos)
    try:
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            return False
        os.makedirs(os.path.dirname(os.path.abspath(out)) or '.',
                    exist_ok=True)
        shutil.copyfile(path, out)
        if goos != 'windows':
            os.chmod(out, 0o755)
    except OSError as e:
        if logger:
            logger.debug(f'AOT cache: 命中但复制失败 ({e})')
        return False
    if logger:
        logger.info(f'AOT cache hit: {key[:12]} -> {out}')
    return True


def _cache_store(key: str, goos: str, built: str, logger=None) -> None:
    path = _cache_path(key, goos)
    tmp = f'{path}.tmp-{os.getpid()}'
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copyfile(built, tmp)
        os.replace(tmp, path)
    except OSError as e:
        if logger:
            logger.debug(f'AOT cache: 写入失败 ({e})')
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    else:
        if logger:
            logger.debug(f'AOT cache store: {key[:12]}')


def pack_seg_file(mem_size: int, sp: int, heap_base: int,
                  segs: List[Tuple[int, bytes]]) -> bytes:
    """打包段文件 (program.segs), 由 aot.Main 解析:
    seg_count u32 | mem_size u64 | sp u64 | heap_base u64 |
    每段: addr u64 + len u32 + data
    """
    out = bytearray()
    out += struct.pack('<I', len(segs))
    out += struct.pack('<QQQ', mem_size, sp, heap_base)
    for addr, data in segs:
        out += struct.pack('<QI', addr, len(data))
        out += data
    return bytes(out)

#: 常用交叉编译目标 (go 本身支持更多组合)。
SUPPORTED_TARGETS = [
    'windows/amd64', 'windows/arm64',
    'linux/amd64', 'linux/arm64',
    'darwin/amd64', 'darwin/arm64',
]

#: go 的 GOOS 取值与 Python platform.system() 的对应
_GOOS_BY_SYSTEM = {'Windows': 'windows', 'Linux': 'linux', 'Darwin': 'darwin'}
_GOARCH_BY_MACHINE = {
    'x86_64': 'amd64', 'AMD64': 'amd64', 'amd64': 'amd64',
    'aarch64': 'arm64', 'arm64': 'arm64', 'ARM64': 'arm64',
    'i386': '386', 'i686': '386',
    'armv7l': 'arm', 'armv6l': 'arm',
}


class AotError(Exception):
    """AOT 构建失败。"""


def host_target() -> str:
    """返回当前平台的 ``os/arch``。"""
    goos = _GOOS_BY_SYSTEM.get(platform.system(), platform.system().lower())
    machine = platform.machine()
    goarch = _GOARCH_BY_MACHINE.get(machine, machine.lower())
    return f'{goos}/{goarch}'


def parse_target(target: str) -> tuple:
    """把 ``os/arch`` 解析为 (goos, goarch); 非法时抛 AotError。"""
    parts = target.split('/')
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise AotError(
            f"--target 格式应为 os/arch (例如 linux/amd64); 支持: "
            f"{', '.join(SUPPORTED_TARGETS)}")
    return parts[0], parts[1]


def exe_suffix(goos: str) -> str:
    """目标平台的可执行文件后缀。"""
    return '.exe' if goos == 'windows' else ''


def module_dir() -> str:
    """返回 codecin-native 模块目录 (含 go.mod)。"""
    if os.path.isfile(os.path.join(_NATIVE_DIR, 'go.mod')):
        return _NATIVE_DIR
    raise AotError(
        f'找不到 Go 模块目录 {_NATIVE_DIR} (AOT 构建需要仓库中的 Go 源码)')


def stub_source() -> str:
    """读取生成的 main.go 模板。"""
    try:
        with open(STUB_PATH, encoding='utf-8') as f:
            return f.read()
    except OSError as e:
        raise AotError(
            f'找不到 AOT 模板 {STUB_PATH}; --build-exe 需要包含 '
            f'codecin/native/ 的源码树') from e


def _cache_fallback_dir(mod_dir: str) -> str:
    """仓库内的构建缓存目录 (install 脚本同款; 已被 .gitignore 忽略)。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(mod_dir)))
    return os.path.join(root, '.gocache')


def _looks_like_cache_denied(text: str) -> bool:
    low = text.lower()
    if 'go-build' not in low and 'gocache' not in low and 'cache' not in low:
        return False
    return ('access is denied' in low or 'permission denied' in low
            or 'operation not permitted' in low)


def _run_go_build(cmd, mod, env, logger):
    """执行 go build; 默认构建缓存不可写时自动回退到仓库内缓存重试一次。

    受限环境 (沙箱 / 只读 HOME / 部分 CI) 下 Go 默认的
    ``$LOCALAPPDATA/go-build`` 或 ``~/.cache/go-build`` 可能不可写,
    此时直接用用户环境会得到一个难以理解的 "Access is denied"。
    """
    proc = subprocess.run(cmd, cwd=mod, env=env, capture_output=True,
                          text=True, encoding='utf-8', errors='replace',
                          timeout=1800)
    detail = (proc.stderr or proc.stdout or '').strip()
    if proc.returncode != 0 and _looks_like_cache_denied(detail) \
            and 'GOCACHE' not in os.environ:
        cache = _cache_fallback_dir(mod)
        os.makedirs(cache, exist_ok=True)
        if logger:
            logger.info(f'AOT: 默认 Go 构建缓存不可写, 回退到 {cache} 重试')
        retry_env = dict(env)
        retry_env['GOCACHE'] = cache
        proc = subprocess.run(cmd, cwd=mod, env=retry_env, capture_output=True,
                              text=True, encoding='utf-8', errors='replace',
                              timeout=1800)
        detail = (proc.stderr or proc.stdout or '').strip()
    return proc, detail


class SweepResult(NamedTuple):
    """:func:`sweep_stale_build_dirs` 的结果 (两个绝对路径列表)。"""

    #: 成功删除的目录
    removed: List[str]
    #: 判定为残留但删除/读取失败的目录 (调用方应提示用户手动处理)
    failed: List[str]


def _warn(logger, message: str) -> None:
    """记一条 warning; 没有 logger 时退回模块级 stdlib logger (绝不静默)。

    日志本身出问题不能掩盖构建结果 (本函数常在 ``finally`` 里被调用),
    因此这里再包一层兜底。
    """
    try:
        if logger is not None:
            logger.warning(message)
        else:
            _LOGGER.warning(message)
    except Exception:                     # noqa: BLE001 - 日志失败不得影响构建
        pass


def _debug(logger, message: str) -> None:
    """记一条 debug (同样带兜底)。"""
    try:
        if logger is not None:
            logger.debug(message)
        else:
            _LOGGER.debug(message)
    except Exception:                     # noqa: BLE001
        pass


def _manual_removal_hint(path: str) -> str:
    """给用户的「怎么手动删掉它」提示 (Windows + POSIX 各一条)。"""
    return (f'请手动删除该目录 —— Windows PowerShell: '
            f'Remove-Item -LiteralPath "{path}" -Recurse -Force ; '
            f'Linux/macOS: rm -rf "{path}"')


def _is_temp_dir_name(name: str) -> bool:
    """名字是否是本模块自己产生的临时目录名 (只认前缀, 不做任何路径解析)。

    显式拒绝 ``.``/``..``、含路径分隔符的名字以及绝对路径 —— 这些都不可能是
    ``secrets.token_hex`` 的产物, 一旦出现就说明有人往我们手里塞了别的东西。
    """
    if not name or name in ('.', '..'):
        return False
    if name != os.path.basename(name):          # 含 / 或 \ -> 不是直接子项
        return False
    if os.sep in name or (os.altsep and os.altsep in name):
        return False
    if os.path.isabs(name):
        return False
    return name.startswith(TEMP_DIR_PREFIXES)


def sweep_stale_build_dirs(max_age_seconds: float = DEFAULT_STALE_AGE_SECONDS,
                           logger=None) -> SweepResult:
    """删除 ``codecin/native/`` 下**陈旧**的 AOT 临时构建目录。

    只处理 ``codecin/native/`` 的**直接子目录** (不递归), 名字必须带
    ``.aotbuild-`` / ``.aotprobe-`` 前缀, 且 mtime 早于 ``max_age_seconds``;
    符号链接与普通文件一律跳过。任何失败都不会抛异常, 只记录日志并把路径
    放进 :attr:`SweepResult.failed`。

    :param max_age_seconds: 容忍时长 (默认 6 小时)。``0`` 表示删除所有匹配目录
        —— 并发构建时不要这么做。
    :param logger: 可选 logger; 缺省时用模块级 stdlib logger。
    :return: 被删除的路径与清理失败的路径
    """
    removed: List[str] = []
    failed: List[str] = []

    root = os.path.abspath(_NATIVE_DIR)
    try:
        names = os.listdir(root)
    except OSError as e:                  # 目录不存在 / 不可读: 尽力而为
        _warn(logger, f'AOT: 无法扫描临时目录所在位置 {root} ({e}); 跳过清理')
        return SweepResult(removed, failed)

    try:
        max_age = max(0.0, float(max_age_seconds))
    except (TypeError, ValueError):
        max_age = float(DEFAULT_STALE_AGE_SECONDS)

    now = time.time()
    for name in names:
        if not _is_temp_dir_name(name):
            continue
        path = os.path.join(root, name)
        # 双保险: 解析后必须仍然是 root 的直接子项 (防 '..' / 绝对路径混入)
        if os.path.dirname(os.path.abspath(path)) != root:
            _warn(logger, f'AOT: 跳过可疑的临时目录路径: {path}')
            continue
        if os.path.islink(path) or not os.path.isdir(path):
            _debug(logger, f'AOT: 跳过非普通目录的临时项: {path}')
            continue

        try:
            age = now - os.path.getmtime(path)
        except OSError as e:
            failed.append(path)
            _warn(logger, f'AOT: 无法读取残留临时目录的时间戳: {path} ({e}); '
                          f'{_manual_removal_hint(path)}')
            continue

        if age < max_age:
            _debug(logger, f'AOT: 临时目录仍然新鲜, 保留: {path} '
                           f'({age / 3600:.1f} 小时前)')
            continue

        try:
            shutil.rmtree(path)
        except OSError as e:
            failed.append(path)
            _warn(logger, f'AOT: 残留临时目录清理失败: {path} '
                          f'({age / 3600:.1f} 小时前, {e}); '
                          f'{_manual_removal_hint(path)}')
            continue

        removed.append(path)
        _debug(logger, f'AOT: 已清理陈旧临时目录: {path} '
                       f'({age / 3600:.1f} 小时前)')

    if removed:
        _debug(logger, f'AOT: 陈旧临时目录清理完成, 共 {len(removed)} 个')
    return SweepResult(removed, failed)


def _remove_temp_dir(tmp: str, logger) -> None:
    """删除本次构建的临时目录; 失败时告警而不是静默吞掉 (§3.3)。

    ``shutil.rmtree(ignore_errors=True)`` 会让「删不掉」完全静默, 于是残留
    在受限环境 (沙箱 / 异常 ACL) 里不断累积, 最终让任何遍历仓库的工具报
    Access is denied。这里改为: 失败 -> warning (含绝对路径与手动删除提示)。
    """
    try:
        shutil.rmtree(tmp)
    except FileNotFoundError:
        pass                              # 目录已不存在: 目的已达成
    except Exception as e:                # noqa: BLE001 - rmtree 正常只抛 OSError,
        # 但这里处于 finally, 任何异常都不能掩盖构建结果, 一律降级为 warning。
        _warn(logger, f'AOT: 临时构建目录清理失败, 残留目录: '
                      f'{os.path.abspath(tmp)} (原因: {e}); '
                      f'{_manual_removal_hint(tmp)}')


def build(bytecode: bytes,
          seg_file: bytes,
          out: str,
          target: Optional[str] = None,
          keep_temp: bool = False,
          debug: bool = False,
          logger=None) -> str:
    """构建独立静态可执行文件, 返回产物的绝对路径。

    :param bytecode: UCBC 字节码 (codecin.native.encode_program 的产物)
    :param seg_file: 段文件 (pack_seg_file 的产物; 初始内存段 + mem_size/sp/heap)
    :param out: 输出路径
    :param target: ``os/arch``, 默认当前平台
    :param keep_temp: 保留临时构建目录 (排查失败用)
    :param logger: 可选 logger (记录 go build 输出)
    """
    if not bytecode:
        raise AotError('字节码为空')
    goos, goarch = parse_target(target) if target else parse_target(host_target())
    mod = module_dir()

    # 先清掉上一次留下的陈旧残留 (默认 > 6 小时), 再建新目录: 受限环境下
    # rmtree 可能失败, 没有这一步残留就会无限累积。
    # 清理是尽力而为 —— 任何异常都必须吞掉, 绝不能因此让构建失败。
    try:
        sweep_stale_build_dirs(logger=logger)
    except Exception as e:                # noqa: BLE001
        _debug(logger, f'AOT: 陈旧临时目录清理跳过 ({e})')

    # 注意: 这里用 os.makedirs 而不是 tempfile.mkdtemp —— mkdtemp 会创建 0700
    # 目录, 在受限环境 (沙箱 / 部分 CI 安全策略) 下随后向其中写文件会被拒绝。
    # 模块目录不可写时 (只读安装 / 受限 ACL) 回退到可写临时 module + replace。
    temp_module = False
    tmp = os.path.join(mod, '.aotbuild-' + secrets.token_hex(6))
    try:
        os.makedirs(tmp, exist_ok=False)
    except OSError:
        temp_module = True
        tmp = os.path.join(_native_build_root(),
                           '.aotbuild-' + secrets.token_hex(6))
        os.makedirs(tmp, exist_ok=False)
    try:
        with open(os.path.join(tmp, 'main.go'), 'w', encoding='utf-8',
                  newline='\n') as f:
            f.write(stub_source())
        with open(os.path.join(tmp, 'program.ucbc'), 'wb') as f:
            f.write(bytecode)
        with open(os.path.join(tmp, 'program.segs'), 'wb') as f:
            f.write(seg_file)
        if temp_module:
            native_dir = _NATIVE_DIR.replace(os.sep, '/')
            with open(os.path.join(tmp, 'go.mod'), 'w', encoding='utf-8',
                      newline='\n') as f:
                f.write('module codecin-aot-vm\n\n')
                f.write(f'go {_module_go_version()}\n\n')
                f.write('require codecin-native v0.0.0\n\n')
                f.write(f'replace codecin-native => "{native_dir}"\n')

        out_abs = os.path.abspath(out)
        os.makedirs(os.path.dirname(out_abs) or '.', exist_ok=True)

        env = dict(os.environ)
        env['CGO_ENABLED'] = '0'                  # 关键: 静态链接
        env['GOOS'] = goos
        env['GOARCH'] = goarch
        build_cwd = mod
        pkg_arg = '.' + os.sep + os.path.basename(tmp)
        if temp_module:
            build_cwd = tmp
            pkg_arg = '.'
            go_tmp = os.path.join(tmp, '.gotmp')
            os.makedirs(go_tmp, exist_ok=True)
            env['GOTMPDIR'] = go_tmp
            env['TMP'] = go_tmp
            env['TEMP'] = go_tmp
            if 'GOCACHE' not in env:
                cache = os.path.join(os.path.dirname(tmp), '.gocache')
                try:
                    os.makedirs(cache, exist_ok=True)
                    env['GOCACHE'] = cache
                except OSError:
                    pass
        # -trimpath 去掉本机路径; 默认 -s -w 去掉符号表, --build-debug 保留。
        ldflags = [] if debug else ['-ldflags', '-s -w']
        cmd = ['go', 'build', '-trimpath',
               '-tags', 'netgo,osusergo',
               *ldflags,
               '-o', out_abs,
               pkg_arg]
        if logger:
            logger.debug(f'AOT build: {cmd} (cwd={build_cwd}, GOOS={goos}, '
                         f'GOARCH={goarch}, CGO_ENABLED=0)')
        try:
            proc, detail = _run_go_build(cmd, build_cwd, env, logger)
        except FileNotFoundError as e:
            from .environment import missing_tool_message
            raise AotError(missing_tool_message(
                'go', 'AOT 构建独立可执行文件')) from e
        except subprocess.TimeoutExpired as e:
            raise AotError('go build 超时') from e
        if proc.returncode != 0:
            if _looks_like_cache_denied(detail) and 'GOCACHE' not in os.environ:
                detail += ('\n提示: 默认 Go 构建缓存不可写且回退失败; '
                           '可显式设置 GOCACHE 指向可写目录后重试')
            raise AotError(f'go build 失败 (目标 {goos}/{goarch}): {detail}')
        if not os.path.isfile(out_abs):
            raise AotError(f'go build 未产出文件: {out_abs}')
        return out_abs
    finally:
        if keep_temp:
            if logger:
                logger.info(f'AOT 临时目录保留: {tmp}')
        else:
            _remove_temp_dir(tmp, logger)


def _native_build_root() -> str:
    """返回 native 后端可写的临时构建根目录。

    优先使用源码树根 (workspace), 受限安装环境回退到模块目录/当前目录。
    这里刻意使用 ``os.makedirs`` 而不是 ``tempfile.mkdtemp``: 后者在 Windows
    受限 ACL 下会创建 0700 目录, 随后写文件被拒绝。
    """
    candidates = [
        os.path.dirname(os.path.dirname(_NATIVE_DIR)),
        os.path.dirname(_NATIVE_DIR),
        _NATIVE_DIR,
        os.getcwd(),
    ]
    for root in candidates:
        try:
            os.makedirs(root, exist_ok=True)
            probe = os.path.join(root, '.aotwrite-' + secrets.token_hex(4))
            os.makedirs(probe, exist_ok=False)
            os.rmdir(probe)
            return root
        except OSError:
            continue
    return os.getcwd()


def _module_go_version() -> str:
    """读取 codecin-native/go.mod 的 go 指令 (拿不到时用 1.26)。"""
    try:
        with open(os.path.join(_NATIVE_DIR, 'go.mod'), encoding='utf-8') as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2 and parts[0] == 'go':
                    return parts[1]
    except OSError:
        pass
    return '1.26'


def build_native(go_source: str,
                 out: str,
                 target: Optional[str] = None,
                 keep_temp: bool = False,
                 debug: bool = False,
                 logger=None) -> str:
    """用 native 代码生成产物构建独立静态可执行文件。

    与 :func:`build` 的区别: 临时模块包含生成好的 main.go (无 UCBC/段文件),
    通过 ``replace codecin-native => <源码树>`` 复用 engine 运行时。
    """
    if not go_source.strip():
        raise AotError('native 代码生成结果为空')
    goos, goarch = parse_target(target) if target else parse_target(host_target())
    root = _native_build_root()
    tmp = os.path.join(root, '.aotbuild-' + secrets.token_hex(6))
    os.makedirs(tmp, exist_ok=False)
    try:
        with open(os.path.join(tmp, 'main.go'), 'w', encoding='utf-8',
                  newline='\n') as f:
            f.write(go_source)
        native_dir = _NATIVE_DIR.replace(os.sep, '/')
        with open(os.path.join(tmp, 'go.mod'), 'w', encoding='utf-8',
                  newline='\n') as f:
            f.write('module codecin-aot\n\n')
            f.write(f'go {_module_go_version()}\n\n')
            f.write('require codecin-native v0.0.0\n\n')
            f.write(f'replace codecin-native => "{native_dir}"\n')

        out_abs = os.path.abspath(out)
        os.makedirs(os.path.dirname(out_abs) or '.', exist_ok=True)

        env = dict(os.environ)
        env['CGO_ENABLED'] = '0'
        env['GOOS'] = goos
        env['GOARCH'] = goarch
        # 编译子进程的临时目录: 受限 Windows ACL 下系统 TMP 可能不可写。
        go_tmp = os.path.join(tmp, '.gotmp')
        os.makedirs(go_tmp, exist_ok=True)
        env['GOTMPDIR'] = go_tmp
        env['TMP'] = go_tmp
        env['TEMP'] = go_tmp
        if 'GOCACHE' not in env:
            cache = os.path.join(root, '.gocache')
            try:
                os.makedirs(cache, exist_ok=True)
                env['GOCACHE'] = cache
            except OSError:
                pass
        ldflags = [] if debug else ['-ldflags', '-s -w']
        cmd = ['go', 'build', '-trimpath',
               '-tags', 'netgo,osusergo',
               *ldflags,
               '-o', out_abs, '.']
        if logger:
            logger.debug(f'AOT native build: {cmd} (cwd={tmp}, '
                         f'GOOS={goos}, GOARCH={goarch}, CGO_ENABLED=0)')
        try:
            proc, detail = _run_go_build(cmd, tmp, env, logger)
        except FileNotFoundError as e:
            from .environment import missing_tool_message
            raise AotError(missing_tool_message(
                'go', 'AOT native 构建')) from e
        except subprocess.TimeoutExpired as e:
            raise AotError('go build 超时') from e
        if proc.returncode != 0:
            if _looks_like_cache_denied(detail) and 'GOCACHE' not in os.environ:
                detail += ('\n提示: 默认 Go 构建缓存不可写且回退失败; '
                           '可显式设置 GOCACHE 指向可写目录后重试')
            raise AotError(f'go build 失败 (native, 目标 {goos}/{goarch}): '
                           f'{detail}')
        if not os.path.isfile(out_abs):
            raise AotError(f'go build 未产出文件: {out_abs}')
        return out_abs
    finally:
        if keep_temp:
            if logger:
                logger.info(f'AOT native 临时目录保留: {tmp}')
        else:
            _remove_temp_dir(tmp, logger)


def program_dependencies(program_file: str) -> List[str]:
    """返回程序依赖的全部 .cin 文件 (含自身, 去重保序)。

    ASM/PL/BIN 没有 import 闭包, 返回自身; CIN 与 ``build_program`` 使用同一
    套解析规则。
    """
    if os.path.splitext(program_file)[1].lower() != '.cin':
        return [program_file]
    from .cin import collect_imported_files
    return collect_imported_files(program_file)


def _compile_program(program_file: str,
                     total: int,
                     optimize: int,
                     logger=None) -> dict:
    """把 CIN/ASM/PL/BIN 统一装载成 AOT 需要的指令与内存段。"""
    ext = os.path.splitext(program_file)[1].lower()
    from .errors import CompilerError

    if ext == '.cin':
        from .cin import CINCompiler, collect_imported_files
        try:
            deps = collect_imported_files(program_file)
        except CompilerError as e:
            raise AotError(f'依赖检查失败: {e}') from e
        missing = [p for p in deps if not os.path.isfile(p)]
        if missing:
            raise AotError('依赖库缺失: ' + ', '.join(missing))
        try:
            res = CINCompiler().compile(program_file)
        except CompilerError as e:
            raise AotError(f'编译失败: {e}') from e
        labels = dict(res.labels)
        entry_pc = getattr(res, 'entry_pc', 0) or 0
        instructions = list(res.instructions)
        if int(optimize or 0) > 0:
            from .optimizer import optimize as optimize_ir
            instructions, labels, entry_pc = optimize_ir(
                instructions, labels, int(optimize), int(entry_pc))
        oob = []
        for addr, data in res.data_writes:
            end = addr + len(data)
            if addr < 0 or end > total:
                oob.append((addr, len(data)))
        if oob:
            raise AotError(
                f'数据段超出内存大小 {total} 字节: '
                + ', '.join(f'addr=0x{a:x} size={n}' for a, n in oob[:4])
                + '; 用 --mem-size 增大后重试')
        segs: List[Tuple[int, bytes]] = []
        if res.data_writes:
            end_max = max(addr + len(data) for addr, data in res.data_writes)
            buf = bytearray(end_max)
            for addr, data in res.data_writes:
                buf[addr:addr + len(data)] = data
            segs.append((0, bytes(buf)))
        return {
            'instructions': instructions,
            'labels': labels,
            'data_labels': dict(res.data_labels),
            'entry_pc': entry_pc,
            'mem_size': total,
            'sp': total - _STACK_SLOT,
            'heap_base': total // 2,
            'segments': segs,
            'deps': deps,
        }

    if ext == '.co':
        from .object import ObjectError, read_object
        try:
            obj = read_object(program_file)
        except ObjectError as e:
            raise AotError(str(e)) from e
        return {
            'instructions': list(obj['instructions']),
            'labels': dict(obj.get('labels', {})),
            'data_labels': dict(obj.get('data_labels', {})),
            'entry_pc': int(obj.get('entry_pc', 0)),
            'mem_size': int(obj.get('mem_size', total)),
            'sp': int(obj.get('sp', total - _STACK_SLOT)),
            'heap_base': int(obj.get('heap_base', total // 2)),
            'segments': list(obj.get('segments', [])),
            'deps': [program_file],
        }

    if ext not in ('.asm', '.pl', '.bin'):
        raise AotError(f'不支持 AOT 输入格式: {ext or "(无扩展名)"}')

    # ASM/PL 由 Assembler 装载; BIN 由 crom.load_bin 解码。
    from . import Config
    from .cpu import CPU
    from .errors import CPUSimulatorError
    try:
        cpu = CPU(Config(log_level='ERROR', mem_size=total,
                         optimize=optimize), program_file)
    except (CPUSimulatorError, CompilerError, OSError) as e:
        raise AotError(f'编译失败: {e}') from e
    return {
        'instructions': list(cpu.instructions),
        'labels': dict(cpu.labels),
        'data_labels': dict(cpu.data_labels),
        'entry_pc': cpu.entry_pc,
        'mem_size': len(cpu.memory),
        'sp': cpu.sp,
        'heap_base': cpu.heap_ptr,
        'segments': list(cpu.memory.snapshot_segments()),
        'deps': [program_file],
    }


def build_from_ir(instructions,
                  labels: Dict[str, int],
                  data_labels: Dict[str, int],
                  segments: List[Tuple[int, bytes]],
                  entry_pc: int,
                  mem_size: int,
                  sp: int,
                  heap_base: int,
                  out: str,
                  target: Optional[str] = None,
                  backend: str = 'auto',
                  max_instructions: int = 100_000_000,
                  optimize: int = 0,
                  program_name: str = 'linked',
                  keep_temp: bool = False,
                  debug: bool = False,
                  use_cache: bool = False,
                  cache_key: Optional[str] = None,
                  logger=None) -> str:
    """从已解析的指令流/内存段构建 AOT 产物 (供 linker 复用)。"""
    from .native import encode_program

    if int(optimize or 0) > 0:
        from .optimizer import optimize as optimize_ir
        instructions, labels, entry_pc = optimize_ir(
            list(instructions), dict(labels), int(optimize), int(entry_pc))

    all_labels = dict(labels)
    all_labels.update(data_labels)
    bytecode = encode_program(instructions, entry_pc, all_labels)
    seg_file = pack_seg_file(mem_size, sp, heap_base, segments)

    goos, _goarch = parse_target(target) if target else \
        parse_target(host_target())
    if not out:
        out = program_name + exe_suffix(goos)
    if goos == 'windows' and not out.lower().endswith('.exe'):
        out += '.exe'

    if use_cache and cache_key and _cache_lookup(cache_key, goos, out, logger):
        return os.path.abspath(out)

    effective = resolve_backend(backend, len(instructions))
    if effective != backend and logger:
        logger.info(
            f'AOT: 指令数 {len(instructions)} 超过 native 自动阈值 '
            f'{NATIVE_INSTRUCTION_LIMIT}, 使用 VM 后端')
    elif backend == 'native' and len(instructions) > NATIVE_INSTRUCTION_LIMIT \
            and logger:
        logger.warning(
            f'AOT: 指令数 {len(instructions)} 超过 native 自动阈值 '
            f'{NATIVE_INSTRUCTION_LIMIT}, 强制 native 可能编译较慢')

    if effective == 'vm':
        built = build(bytes(bytecode), seg_file, out, target=target,
                      keep_temp=keep_temp, debug=debug, logger=logger)
    else:
        if effective != 'native':
            raise AotError(f'未知 AOT 后端: {backend} (可选 auto / native / vm)')

        from .native_codegen import NativeCodegenError, generate_go_source
        try:
            source = generate_go_source(
                instructions,
                labels,
                segments,
                entry_pc=entry_pc,
                mem_size=mem_size,
                sp=sp,
                heap_base=heap_base,
                max_steps=max_instructions,
                program_name=program_name,
            )
        except NativeCodegenError as e:
            raise AotError(f'native 代码生成失败: {e}') from e
        built = build_native(source, out, target=target, keep_temp=keep_temp,
                             debug=debug, logger=logger)
    if use_cache and cache_key:
        _cache_store(cache_key, goos, built, logger)
    return built


def build_program(program_file: str,
                  out: Optional[str] = None,
                  target: Optional[str] = None,
                  keep_temp: bool = False,
                  mem_size: Optional[int] = None,
                  backend: str = 'auto',
                  max_instructions: int = 100_000_000,
                  optimize: int = 0,
                  use_cache: bool = True,
                  debug: bool = False,
                  logger=None) -> str:
    """便捷入口: 由 .cin/.asm/.pl/.bin 构建可执行文件。

    CIN 构建前会解析 import 闭包做**依赖完整性检查** (缺失/循环 ->
    :class:`AotError`), 再把全部依赖模块在编译期展开并**嵌入**产物。
    ASM/PL 由汇编器装载, BIN 由 crom.load_bin 解码。

    ``backend='auto'`` (默认) 在指令数不超过 :data:`NATIVE_INSTRUCTION_LIMIT`
    时使用 native 后端, 否则回退 VM; ``'native'``/``'vm'`` 可强制指定。
    """
    if not os.path.isfile(program_file):
        raise AotError(f'找不到程序文件: {program_file}')

    goos, _goarch = parse_target(target) if target else \
        parse_target(host_target())

    total = int(mem_size) if mem_size else DEFAULT_MEM_SIZE
    if total < 256:
        total = 256

    if not out:
        out = os.path.splitext(program_file)[0] + exe_suffix(goos)
    if goos == 'windows' and not out.lower().endswith('.exe'):
        out += '.exe'          # README: Windows 目标自动补 .exe

    cache_key = None
    if use_cache:
        cache_key = build_cache_key(program_file, target, backend, optimize,
                                    mem_size, max_instructions, debug=debug)
        if cache_key and _cache_lookup(cache_key, goos, out, logger):
            return os.path.abspath(out)

    compiled = _compile_program(program_file, total, optimize, logger)
    instructions = compiled['instructions']
    labels = compiled['labels']
    total = compiled['mem_size']
    deps = compiled['deps']

    if logger:
        extra = [os.path.basename(p) for p in deps[1:]]
        if extra:
            logger.info(f'AOT: 已嵌入 {len(extra)} 个依赖库: '
                        f'{", ".join(extra)}')
        else:
            logger.info('AOT: 无外部依赖 (单文件程序)')

    built = build_from_ir(
        instructions,
        labels,
        compiled['data_labels'],
        compiled['segments'],
        entry_pc=compiled['entry_pc'],
        mem_size=total,
        sp=compiled['sp'],
        heap_base=compiled['heap_base'],
        out=out,
        target=target,
        backend=backend,
        max_instructions=max_instructions,
        program_name=os.path.basename(program_file),
        keep_temp=keep_temp,
        debug=debug,
        logger=logger,
    )
    if cache_key:
        _cache_store(cache_key, goos, built, logger)
    return built


def supported_targets() -> List[str]:
    """文档/帮助用: 常用目标列表。"""
    return list(SUPPORTED_TARGETS)
