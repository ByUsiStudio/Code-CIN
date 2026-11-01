"""AOT 工具链发现与获取 (GCC 家族, 支持交叉编译)。

AOT 后端不再调用 Go: 生成的 C 源码交给 GCC/Clang 编译成本机或交叉目标的
可执行文件。本模块只回答三个问题——**用哪个编译器、它是哪个目标、缺了怎么装**,
不负责生成代码 (见 :mod:`codecin.cbackend`)。

每个目标独立按下列顺序查找:

1. ``CODECIN_CC_<TARGET>`` (例如 ``CODECIN_CC_AARCH64_LINUX``);
2. ``CODECIN_CC``;
3. 自带工具链 ``$CODECIN_GCC_HOME`` / ``~/.codecin/gcc/<target>/bin/<prefix>gcc``;
4. PATH 中的目标专用驱动 (``aarch64-linux-gnu-gcc`` / ``x86_64-w64-mingw32-gcc``);
5. PATH 中的本机 ``gcc`` / ``clang``, 仅当 ``-dumpmachine`` 与目标匹配。

``python -m codecin.toolchain install <target> --url <压缩包>`` 可把交叉工具链
解包到 ``~/.codecin/gcc/<target>``; 未指定 ``--url`` 时使用 :data:`MIRROR` 的
约定路径。
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    'MIRROR',
    'TARGETS',
    'CompilerInfo',
    'Target',
    'ToolchainError',
    'download',
    'find_compiler',
    'get_target',
    'hint',
    'install_archive',
    'mirror_url',
    'probe',
    'require',
]

#: 预编译 GCC 工具链镜像 (用户可自带; 也可用 ``--url`` 指定任意地址)。
MIRROR = 'https://mirror.marwan.ma/gcc/'


class ToolchainError(RuntimeError):
    """找不到可用的 C 编译器 / 工具链安装失败。"""


@dataclass(frozen=True)
class Target:
    """一个 AOT 目标: 名字、GNU triple、可执行文件后缀、驱动前缀。"""

    name: str
    triple: str
    exe: str
    prefix: str
    fmt: str
    aliases: Tuple[str, ...] = ()

    @property
    def is_host(self) -> bool:
        return self.triple == _host_triple()


TARGETS: Dict[str, Target] = {
    t.name: t for t in (
        Target('x86_64-windows', 'x86_64-w64-mingw32', '.exe',
               'x86_64-w64-mingw32-', 'pe',
               ('windows-x86_64', 'win64', 'windows')),
        Target('x86_64-linux', 'x86_64-linux-gnu', '', 'x86_64-linux-gnu-',
               'elf', ('linux-x86_64', 'linux', 'linux64')),
        Target('aarch64-linux', 'aarch64-linux-gnu', '', 'aarch64-linux-gnu-',
               'elf', ('linux-aarch64', 'arm64-linux', 'aarch64')),
    )
}

_ALIASES = {alias: t.name for t in TARGETS.values() for alias in t.aliases}
_ALIASES.update({name: name for name in TARGETS})


def _host_triple() -> str:
    """本机 triple 的近似值 (只用于 ``Target.is_host`` 判断)。"""
    system = sys.platform
    machine = (os.environ.get('PROCESSOR_ARCHITECTURE') or '').lower()
    try:
        import platform
        machine = platform.machine().lower()
    except Exception:  # noqa: BLE001 - 探测失败不影响交叉编译
        pass
    arch = 'aarch64' if machine in ('arm64', 'aarch64') else 'x86_64'
    if system == 'win32':
        return 'x86_64-w64-mingw32'
    if system == 'darwin':
        return f'{arch}-apple-darwin'
    return f'{arch}-linux-gnu'


def get_target(name: str) -> Target:
    """按名字或别名解析目标 (``linux-x86_64`` -> ``x86_64-linux``)。"""
    key = (name or '').strip().lower()
    resolved = _ALIASES.get(key)
    if resolved is None:
        raise ToolchainError(
            f'未知 AOT 目标: {name!r} (可选: {", ".join(sorted(TARGETS))})')
    return TARGETS[resolved]


def mirror_url(target: Target) -> str:
    """镜像上的约定路径 (压缩包内含 ``<target>/bin/...``)。"""
    return urllib.parse.urljoin(MIRROR, f'{target.name}.tar.gz')


def _toolchain_root() -> str:
    explicit = os.environ.get('CODECIN_GCC_HOME')
    if explicit:
        return os.path.abspath(explicit)
    return os.path.join(os.path.expanduser('~'), '.codecin', 'gcc')


def compiler_candidates(target: Target) -> List[str]:
    """按优先级返回候选编译器路径 (不保证存在)。"""
    env_key = 'CODECIN_CC_' + re.sub(r'[^A-Z0-9]+', '_', target.name.upper())
    paths: List[str] = []
    for key in (env_key, 'CODECIN_CC'):
        value = os.environ.get(key)
        if value:
            paths.append(value)
    root = _toolchain_root()
    for prefix in (os.path.join(root, target.name), root):
        for name in (f'{target.prefix}gcc', f'{target.prefix}clang',
                     'gcc', 'cc', 'clang'):
            paths.append(os.path.join(prefix, 'bin', name + '.exe'
                                      if sys.platform == 'win32'
                                      and not name.endswith('.exe') else name))
    for name in (f'{target.prefix}gcc', f'{target.prefix}clang',
                 'gcc', 'cc', 'clang'):
        found = shutil.which(name)
        if found:
            paths.append(found)
    seen = set()
    unique = []
    for path in paths:
        path = os.path.abspath(path) if os.path.isabs(path) else path
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


@dataclass
class CompilerInfo:
    path: str
    target: Target
    machine: str = ''
    version: str = ''
    ok: bool = False

    def __bool__(self) -> bool:
        return self.ok

    def describe(self) -> str:
        if not self.ok:
            return f'{self.path} (不可用)'
        return f'{self.path} [{self.machine}] {self.version}'.strip()


def probe(path: str, target: Target, *, timeout: float = 10.0) -> CompilerInfo:
    """运行 ``-dumpmachine`` / ``--version`` 探测编译器; 失败不抛异常。"""
    info = CompilerInfo(path=path, target=target)
    if not path or not (os.path.isfile(path) or shutil.which(path)):
        return info
    try:
        machine = subprocess.run(
            [path, '-dumpmachine'], capture_output=True, text=True,
            timeout=timeout, check=False)
        version = subprocess.run(
            [path, '--version'], capture_output=True, text=True,
            timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return info
    info.machine = (machine.stdout or '').strip()
    first = (version.stdout or '').strip().splitlines()
    info.version = first[0] if first else ''
    info.ok = bool(info.machine) and machine.returncode == 0
    return info


def matches(target: Target, machine: str) -> bool:
    """``-dumpmachine`` 结果是否可用于该目标 (容忍发行版后缀差异)。"""
    if not machine:
        return False
    machine = machine.lower()
    if target.name.endswith('windows'):
        return any(token in machine for token in ('mingw', 'windows', 'win32'))
    if target.name.startswith('aarch64'):
        if not any(token in machine for token in ('aarch64', 'arm64')):
            return False
    elif not any(token in machine for token in ('x86_64', 'amd64')):
        return False
    if target.name.endswith('linux'):
        return 'linux' in machine
    return True


def find_compiler(target: Target) -> Optional[CompilerInfo]:
    """找到第一个**目标匹配**的编译器; 找不到返回 None。"""
    for path in compiler_candidates(target):
        info = probe(path, target)
        if info.ok and matches(target, info.machine):
            return info
    return None


def hint(target: Target) -> str:
    """缺失工具链时的安装指引 (可复制执行)。"""
    env_key = 'CODECIN_CC_' + re.sub(r'[^A-Z0-9]+', '_', target.name.upper())
    lines = [f'缺少可用于 {target.name} ({target.triple}) 的 C 编译器。',
             '任选其一:',
             f'  1. 安装系统交叉工具链并把驱动加入 PATH'
             f' (例如 {target.prefix}gcc);',
             f'  2. 指定现成编译器: 设置 {env_key}=<路径>,'
             f' 或 CODECIN_CC=<路径>;',
             f'  3. 使用自带工具链: python -m codecin.toolchain install'
             f' {target.name} --url <压缩包>',
             f'     默认镜像: {mirror_url(target)}',
             f'     解包目录: {os.path.join(_toolchain_root(), target.name)}']
    return '\n'.join(lines)


def require(target: Target) -> CompilerInfo:
    info = find_compiler(target)
    if info is None:
        raise ToolchainError(hint(target))
    return info


# ==================== 下载 / 安装 ====================


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: str, *, sha256: Optional[str] = None,
             timeout: float = 60.0) -> str:
    """下载 ``url`` 到 ``dest`` (支持 ``file://``), 可选校验 sha256。"""
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or '.', exist_ok=True)
    scheme = urllib.parse.urlparse(url).scheme
    if scheme in ('', 'file'):
        src = urllib.request.url2pathname(urllib.parse.urlparse(url).path) \
            if scheme == 'file' else url
        if not os.path.isfile(src):
            raise ToolchainError(f'文件不存在: {src}')
        shutil.copyfile(src, dest)
    else:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response, \
                    open(dest, 'wb') as out:
                shutil.copyfileobj(response, out)
        except Exception as e:  # noqa: BLE001 - 统一转换为 ToolchainError
            raise ToolchainError(f'下载失败: {url}: {e}') from e
    if sha256:
        actual = _sha256_file(dest)
        if actual.lower() != sha256.lower():
            raise ToolchainError(
                f'sha256 不匹配: 期望 {sha256}, 实际 {actual}')
    return os.path.abspath(dest)


def _safe_members(names: Sequence[str]) -> None:
    for name in names:
        normalized = name.replace('\\', '/')
        if normalized.startswith('/') or '..' in normalized.split('/'):
            raise ToolchainError(f'压缩包包含越界路径: {name!r}')


def install_archive(archive: str, target: Target, *,
                    root: Optional[str] = None) -> str:
    """把工具链压缩包解到 ``root/<target>``, 返回该目录。"""
    dest = os.path.join(root or _toolchain_root(), target.name)
    os.makedirs(dest, exist_ok=True)
    lower = archive.lower()
    if lower.endswith(('.tar.gz', '.tgz', '.tar.xz', '.tar.bz2')):
        with tarfile.open(archive, 'r:*') as tar:
            members = tar.getmembers()
            _safe_members([m.name for m in members])
            tar.extractall(dest)
    elif lower.endswith('.zip'):
        with zipfile.ZipFile(archive) as zf:
            _safe_members(zf.namelist())
            zf.extractall(dest)
    else:
        raise ToolchainError(f'不支持的压缩格式: {archive}')
    for dirpath, _dirnames, filenames in os.walk(dest):
        for name in filenames:
            if name.endswith(('gcc', 'g++', 'cc', 'ld', '.exe')) or \
                    name in ('clang',):
                path = os.path.join(dirpath, name)
                if sys.platform != 'win32':
                    os.chmod(path, 0o755)
    return dest


def install(target_name: str, url: Optional[str] = None,
            sha256: Optional[str] = None, *,
            root: Optional[str] = None) -> str:
    """下载并安装自带工具链 (返回解包目录)。"""
    target = get_target(target_name)
    source = url or mirror_url(target)
    with tempfile.TemporaryDirectory(prefix='.gccdl-') as tmp:
        archive = download(source, os.path.join(tmp, 'toolchain.archive'),
                           sha256=sha256)
        return install_archive(archive, target, root=root)


# ==================== CLI ====================


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog='python -m codecin.toolchain',
        description='AOT 工具链 (GCC/Clang) 发现与安装')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('list', help='列出支持的 AOT 目标')

    p = sub.add_parser('which', help='显示某目标实际会使用的编译器')
    p.add_argument('target')

    p = sub.add_parser('install', help='下载并解包自带工具链')
    p.add_argument('target')
    p.add_argument('--url')
    p.add_argument('--sha256')
    p.add_argument('--root')

    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        if args.command == 'list':
            for name in sorted(TARGETS):
                target = TARGETS[name]
                found = find_compiler(target)
                state = found.describe() if found else '未找到'
                print(f'{name:<16} {target.triple:<24} {state}')
            return 0
        if args.command == 'which':
            target = get_target(args.target)
            found = find_compiler(target)
            print(found.path if found else hint(target))
            return 0 if found else 1
        dest = install(args.target, args.url, args.sha256, root=args.root)
        print(f'工具链已安装到: {dest}')
        return 0
    except ToolchainError as e:
        sys.stderr.write(f'toolchain: error: {e}\n')
        return 1


if __name__ == '__main__':
    sys.exit(main())
