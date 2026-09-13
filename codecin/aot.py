"""AOT 构建: 把 CIN 程序编译成**独立静态可执行文件** (Windows / Linux / macOS)。

产物特点:
  * 内嵌 UCBC 字节码与初始内存镜像, 由内置 Go VM 执行;
  * `CGO_ENABLED=0` 静态链接 —— 不依赖 Python、Go 工具链、libc 或任何动态库;
  * 可用 `--target` 交叉编译 (windows/amd64、linux/arm64、darwin/arm64 ...)。

实现说明: 本模块只做"编排", 生成的 main.go 模板与 Go 侧
`codecin/native/aot` 共用同一份文件 (``stub_main.go.txt``), 避免两处漂移。
临时包建在 `codecin-native` 模块内的 ``.aotbuild-*`` 目录: Go 工具链会忽略
以 '.' 开头的目录, 因此既不影响 `go build ./...`, 也不需要 replace 指令。
"""

import os
import platform
import secrets
import shutil
import subprocess
from typing import List, Optional

#: 仓库内 codecin-native 模块目录 (含 go.mod)。
_NATIVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'native')

#: 生成的 main.go 模板 (与 Go 侧共用)。
STUB_PATH = os.path.join(_NATIVE_DIR, 'aot', 'stub_main.go.txt')

#: 默认内存大小, 与解释器 / Go CLI 一致。
DEFAULT_MEM_SIZE = 65536

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
    with open(STUB_PATH, 'r', encoding='utf-8') as f:
        return f.read()


def build(bytecode: bytes,
          mem_image: bytes,
          out: str,
          target: Optional[str] = None,
          keep_temp: bool = False,
          logger=None) -> str:
    """构建独立静态可执行文件, 返回产物的绝对路径。

    :param bytecode: UCBC 字节码 (codecin.native.encode_program 的产物)
    :param mem_image: 初始内存镜像 (数据段已写入)
    :param out: 输出路径
    :param target: ``os/arch``, 默认当前平台
    :param keep_temp: 保留临时构建目录 (排查失败用)
    :param logger: 可选 logger (记录 go build 输出)
    """
    if not bytecode:
        raise AotError('字节码为空')
    goos, goarch = parse_target(target) if target else parse_target(host_target())
    mod = module_dir()

    # 注意: 这里用 os.makedirs 而不是 tempfile.mkdtemp —— mkdtemp 会创建 0700
    # 目录, 在受限环境 (沙箱 / 部分 CI 安全策略) 下随后向其中写文件会被拒绝。
    tmp = os.path.join(mod, '.aotbuild-' + secrets.token_hex(6))
    os.makedirs(tmp, exist_ok=False)
    try:
        with open(os.path.join(tmp, 'main.go'), 'w', encoding='utf-8',
                  newline='\n') as f:
            f.write(stub_source())
        with open(os.path.join(tmp, 'program.ucbc'), 'wb') as f:
            f.write(bytecode)
        with open(os.path.join(tmp, 'program.mem'), 'wb') as f:
            f.write(mem_image)

        out_abs = os.path.abspath(out)
        os.makedirs(os.path.dirname(out_abs) or '.', exist_ok=True)

        env = dict(os.environ)
        env['CGO_ENABLED'] = '0'                  # 关键: 静态链接
        env['GOOS'] = goos
        env['GOARCH'] = goarch
        # -trimpath 去掉本机路径; -s -w 去掉符号表
        cmd = ['go', 'build', '-trimpath',
               '-tags', 'netgo,osusergo',
               '-ldflags', '-s -w',
               '-o', out_abs,
               '.' + os.sep + os.path.basename(tmp)]
        if logger:
            logger.debug(f'AOT build: {cmd} (cwd={mod}, GOOS={goos}, '
                         f'GOARCH={goarch}, CGO_ENABLED=0)')
        try:
            proc = subprocess.run(cmd, cwd=mod, env=env,
                                  capture_output=True, text=True,
                                  encoding='utf-8', errors='replace',
                                  timeout=900)
        except FileNotFoundError as e:
            raise AotError('未找到 go 命令; AOT 构建需要 Go 工具链 '
                           '(https://go.dev/dl/)') from e
        except subprocess.TimeoutExpired as e:
            raise AotError('go build 超时') from e
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or '').strip()
            raise AotError(f'go build 失败 (目标 {goos}/{goarch}): {detail}')
        if not os.path.isfile(out_abs):
            raise AotError(f'go build 未产出文件: {out_abs}')
        return out_abs
    finally:
        if keep_temp:
            if logger:
                logger.info(f'AOT 临时目录保留: {tmp}')
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def build_program(program_file: str,
                  out: Optional[str] = None,
                  target: Optional[str] = None,
                  keep_temp: bool = False,
                  logger=None) -> str:
    """便捷入口: 直接由 .cin 源文件构建可执行文件。"""
    from .cin import CINCompiler
    from .native import encode_program

    goos, _goarch = parse_target(target) if target else \
        parse_target(host_target())
    res = CINCompiler().compile(program_file)
    labels = dict(res.labels)
    labels.update(res.data_labels)
    bytecode = encode_program(res.instructions,
                              getattr(res, 'entry_pc', 0) or 0, labels)
    mem = bytearray(DEFAULT_MEM_SIZE)
    for addr, data in res.data_writes:
        end = addr + len(data)
        if 0 <= addr and end <= len(mem):
            mem[addr:end] = data
    if not out:
        out = os.path.splitext(program_file)[0] + exe_suffix(goos)
    return build(bytes(bytecode), bytes(mem), out, target=target,
                 keep_temp=keep_temp, logger=logger)


def supported_targets() -> List[str]:
    """文档/帮助用: 常用目标列表。"""
    return list(SUPPORTED_TARGETS)
