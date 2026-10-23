"""``cvm`` —— Code CIN 虚拟机 (CVM) 与 ``.cvm`` 模块运行时。

定位 (与 JVM 对应, 但不再是 CPU/硬件模拟器):

* ``.cvm`` 是**应用模块** (zip): 清单 + UCBC 字节码 + 可选元数据;
* ``cvm`` 是**运行时**: 负责模块路径、校验、装载与执行;
* ``CPU`` 保留为底层执行核 (寄存器/内存/指令), 不再是对外概念;
* ``cvm run app.cvm`` 可脱离源码运行; ``cvm build`` 把 CIN/BIN/mkn 项目
  打包为可分发模块。

模块布局::

    CVM-INF/MANIFEST.TOML
    code/program.bin
    META-INF/SHA256SUMS

CVM 模块只依赖 Code CIN 原生引擎的 ABI, 与宿主 Python 版本无关;
同一 ``.cvm`` 可在 Windows/Linux/macOS 上由对应平台的 ``cvm`` 执行。
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import __version__
from .config import Config
from .packaging import archive
from .packaging.model import (
    ManifestError,
    validate_name,
    validate_version,
)
from .packaging.toml import dumps as toml_dumps
from .packaging.toml import loads as toml_loads

__all__ = ['CVM', 'CVMModule', 'CVMError', 'build_module', 'load_module',
           'main', 'module_paths', 'run_module', 'verify_module']

CVM_FORMAT = 1
CVM_ENGINE = 'codecin-native'
CVM_ABI = 2
MANIFEST_MEMBER = 'CVM-INF/MANIFEST.TOML'
CODE_MEMBER = 'code/program.bin'
CHECKSUMS_MEMBER = 'META-INF/SHA256SUMS'


class CVMError(RuntimeError):
    """CVM 模块损坏/不兼容或构建失败。"""


@dataclass
class CVMModule:
    path: str
    name: str
    version: str
    main: str
    manifest: Dict[str, Any]
    code: bytes

    @property
    def size(self) -> int:
        return len(self.code)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'path': self.path,
            'name': self.name,
            'version': self.version,
            'main': self.main,
            'size': self.size,
            'format': self.manifest.get('cvm', {}).get('format'),
            'engine': self.manifest.get('cvm', {}).get('engine'),
            'abi': self.manifest.get('cvm', {}).get('abi'),
            'build': self.manifest.get('build', {}),
        }


def _home() -> str:
    return os.path.abspath(os.environ.get('CVM_HOME')
                           or os.path.join(os.path.expanduser('~'), '.cvm'))


def module_paths() -> List[str]:
    """模块搜索路径: ``CVM_MODULE_PATH`` + ``$CVM_HOME/modules``。"""
    raw = os.environ.get('CVM_MODULE_PATH', '')
    dirs = [os.path.abspath(p) for p in raw.split(os.pathsep) if p.strip()]
    default = os.path.join(_home(), 'modules')
    if default not in dirs:
        dirs.append(default)
    return dirs


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _bin_header(data: bytes) -> Dict[str, Any]:
    """读取 BIN v2/v3 头部 (只读校验, 不装载)。"""
    from .isa import Constants
    if len(data) < 6 or data[:5] != Constants.MAGIC_NUMBER:
        raise CVMError('不是有效的 UCBC 字节码 (BIN magic 不匹配)')
    import struct
    version = data[5]
    info: Dict[str, Any] = {'version': version, 'bytes': len(data)}
    if version == Constants.BIN_VERSION and len(data) >= 42:
        info['mem_size'] = struct.unpack_from('<Q', data, 6)[0]
        info['entry'] = struct.unpack_from('<I', data, 14)[0]
        info['sp'] = struct.unpack_from('<Q', data, 18)[0]
        info['heap_base'] = struct.unpack_from('<Q', data, 26)[0]
        info['bytecode_len'] = struct.unpack_from('<I', data, 34)[0]
    elif version == Constants.BIN_VERSION_V2 and len(data) >= 26:
        info['mem_size'] = struct.unpack_from('<I', data, 6)[0]
        info['entry'] = struct.unpack_from('<I', data, 10)[0]
    else:
        raise CVMError(f'不支持的 BIN 版本: {version}')
    return info


def _compile_source(source: str, *, optimize: int = 0,
                    mem_size: Optional[int] = None,
                    bounds_check: bool = False) -> Tuple[bytes, Dict[str, Any]]:
    from . import crom
    from .cpu import CPU
    config = Config(log_level='ERROR', optimize=optimize)
    if mem_size:
        config.mem_size = int(mem_size)
    config.bounds_check = bounds_check
    config.validate()
    cpu = CPU(config, source)
    fd, tmp = tempfile.mkstemp(suffix='.bin', prefix='.cvm-build-')
    os.close(fd)
    try:
        crom.save_bin(cpu, tmp)
        with open(tmp, 'rb') as f:
            data = f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    info = _bin_header(data)
    info['mem_size'] = len(cpu.memory)
    info['optimize'] = config.optimize
    return data, info


def _compile_input(source: str, *, optimize: int, mem_size: Optional[int],
                   bounds_check: bool, release: bool) -> Tuple[bytes, Dict[str, Any], str, str, str]:
    """返回 (bytecode, header, name, version, source_label)。"""
    source = os.path.abspath(source)
    if not os.path.exists(source):
        raise CVMError(f'输入不存在: {source}')
    if os.path.isdir(source):
        manifest_path = os.path.join(source, 'mkn.toml')
        if not os.path.isfile(manifest_path):
            raise CVMError(f'目录不是 mkn 项目 (缺少 mkn.toml): {source}')
        from .mkn import build_project, load_project
        project = load_project(source)
        level = max(optimize, 2) if release else optimize
        out = build_project(project, optimize=level, mem_size=mem_size,
                            bounds_check=bounds_check)
        if out.lower().endswith('.co'):
            raise CVMError(
                f'{source}: 库项目没有 main 函数, 不能构建 .cvm 应用模块 '
                f'(产物为对象文件 {out})')
        with open(out, 'rb') as f:
            data = f.read()
        info = _bin_header(data)
        return data, info, project.name, project.version, project.entry_path

    ext = os.path.splitext(source)[1].lower()
    if ext == '.bin':
        with open(source, 'rb') as f:
            data = f.read()
        info = _bin_header(data)
        stem = os.path.splitext(os.path.basename(source))[0]
        name = validate_name(stem if stem and stem[0].isalpha() else 'app')
        return data, info, name, '0.0.0', source
    if ext in ('.cin', '.asm', '.pl'):
        level = max(optimize, 2) if release else optimize
        data, info = _compile_source(source, optimize=level,
                                     mem_size=mem_size,
                                     bounds_check=bounds_check)
        stem = os.path.splitext(os.path.basename(source))[0]
        name = stem if stem and stem[0].isalpha() else 'app'
        return data, info, name, '0.0.0', source
    raise CVMError(f'不支持的输入类型: {source} (需要 .cin/.asm/.pl/.bin/目录)')


def build_module(source: str, output: Optional[str] = None, *,
                 name: Optional[str] = None, version: Optional[str] = None,
                 main: Optional[str] = None, optimize: int = 0,
                 mem_size: Optional[int] = None, bounds_check: bool = False,
                 release: bool = False) -> str:
    """把 CIN/BIN/mkn 项目编译为 ``.cvm`` 模块。"""
    code, info, detected_name, detected_version, label = _compile_input(
        source, optimize=optimize, mem_size=mem_size,
        bounds_check=bounds_check, release=release)
    module_name = validate_name(name or detected_name)
    module_version = validate_version(version or detected_version)
    before = {}
    if info.get('version') is not None:
        before['bin_version'] = info['version']
    manifest = {
        'cvm': {
            'format': CVM_FORMAT,
            'engine': CVM_ENGINE,
            'abi': CVM_ABI,
            'generator': f'cvm {__version__}',
        },
        'module': {
            'name': module_name,
            'version': module_version,
            'main': main or module_name,
            'created': _now(),
        },
        'build': {
            'source': label,
            'optimize': int(info.get('optimize', optimize)),
            'mem_size': int(info.get('mem_size') or mem_size or
                            Config().mem_size),
            'bin_version': int(info.get('version', 3)),
            'bytecode_len': int(info.get('bytecode_len', 0) or 0),
        },
    }
    out = os.path.abspath(output or f'{module_name}-{module_version}.cvm')
    entries = {
        MANIFEST_MEMBER: toml_dumps(manifest).encode('utf-8'),
        CODE_MEMBER: code,
    }
    archive.write_zip(out, archive.build_archive_entries(entries))
    return out


def load_module(path: str) -> CVMModule:
    """读取并校验 ``.cvm`` 模块。"""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise CVMError(f'模块不存在: {path}')
    text = archive.read_manifest_member(path, (MANIFEST_MEMBER,))
    try:
        manifest = toml_loads(text)
    except Exception as e:  # noqa: BLE001 - 统一转换为 CVMError
        raise CVMError(f'{path}: 模块清单无法解析: {e}') from e
    cvm = manifest.get('cvm') or {}
    module = manifest.get('module') or {}
    if not isinstance(cvm, dict) or not isinstance(module, dict):
        raise CVMError(f'{path}: 清单缺少 [cvm] / [module] 表')
    try:
        fmt = int(cvm.get('format', 0))
    except (TypeError, ValueError):
        fmt = 0
    if fmt != CVM_FORMAT:
        raise CVMError(
            f'{path}: 不支持的 CVM 模块格式 {fmt} (当前支持 {CVM_FORMAT})')
    engine = str(cvm.get('engine') or '')
    if engine and engine != CVM_ENGINE:
        raise CVMError(
            f'{path}: 模块要求引擎 {engine!r}, 当前为 {CVM_ENGINE!r}')
    try:
        name = validate_name(module.get('name'))
        version = validate_version(module.get('version'))
    except ManifestError as e:
        raise CVMError(f'{path}: {e}') from e

    checked, failures = archive.verify_checksums(path, CHECKSUMS_MEMBER)
    if failures:
        raise CVMError(
            f'{path}: 模块校验失败 ({len(failures)} 项):\n  '
            + '\n  '.join(failures))
    if checked == 0:
        raise CVMError(f'{path}: 模块缺少 META-INF/SHA256SUMS, 无法校验')

    try:
        with archive.zipfile.ZipFile(path) as zf:
            if CODE_MEMBER not in zf.namelist():
                raise CVMError(f'{path}: 缺少字节码成员 {CODE_MEMBER}')
            code = zf.read(CODE_MEMBER)
    except archive.zipfile.BadZipFile as e:
        raise CVMError(f'{path}: 不是有效的 .cvm (zip): {e}') from e
    _bin_header(code)  # 校验 magic/版本
    return CVMModule(path=path, name=name, version=version,
                     main=str(module.get('main') or name),
                     manifest=manifest, code=code)


def verify_module(path: str) -> CVMModule:
    return load_module(path)


def run_module(module: CVMModule, args: Optional[Sequence[str]] = None, *,
               mem_size: Optional[int] = None,
               max_instructions: Optional[int] = None,
               sandbox: bool = False,
               log_level: str = 'ERROR') -> Any:
    """执行模块, 返回执行完的 CPU (底层执行核)。"""
    from .cpu import CPU
    config = Config(log_level=log_level, sandbox_mode=sandbox)
    build = module.manifest.get('build') or {}
    if mem_size:
        config.mem_size = int(mem_size)
    elif build.get('mem_size'):
        config.mem_size = int(build['mem_size'])
    if max_instructions:
        config.max_instructions = int(max_instructions)
    config.program_args = list(args or [])
    config.validate()

    fd, tmp = tempfile.mkstemp(suffix='.bin', prefix='.cvm-run-')
    os.close(fd)
    try:
        with open(tmp, 'wb') as f:
            f.write(module.code)
        cpu = CPU(config, tmp)
        cpu.run()
        return cpu
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


class CVM:
    """CVM 运行时 API (供 Python 调用; CLI 是其薄封装)。"""

    def __init__(self, module_path: Optional[Sequence[str]] = None):
        self.dirs = [os.path.abspath(p) for p in (
            module_path if module_path is not None else module_paths())]

    def resolve(self, target: str) -> str:
        return _resolve_target(target, self.dirs)

    def load(self, target: str) -> CVMModule:
        return load_module(self.resolve(target))

    def run(self, target: str, args: Optional[Sequence[str]] = None,
            **overrides: Any) -> Any:
        return run_module(self.load(target), args, **overrides)

    def modules(self) -> List[CVMModule]:
        modules: List[CVMModule] = []
        seen = set()
        for directory in self.dirs:
            if not os.path.isdir(directory):
                continue
            for entry in sorted(os.listdir(directory)):
                path = os.path.join(directory, entry)
                candidates = []
                if os.path.isfile(path) and entry.endswith('.cvm'):
                    candidates = [path]
                elif os.path.isdir(path):
                    candidates = sorted(
                        os.path.join(path, name)
                        for name in os.listdir(path)
                        if name.endswith('.cvm'))
                for candidate in candidates:
                    if candidate in seen:
                        continue
                    seen.add(candidate)
                    try:
                        modules.append(load_module(candidate))
                    except CVMError:
                        continue
        return modules


def _resolve_target(target: str, dirs: Sequence[str]) -> str:
    if os.path.isfile(target):
        return os.path.abspath(target)
    name = target[:-4] if target.endswith('.cvm') else target
    for directory in dirs:
        candidates = [
            os.path.join(directory, f'{name}.cvm'),
            os.path.join(directory, name, f'{name}.cvm'),
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
        if os.path.isdir(os.path.join(directory, name)):
            for entry in sorted(os.listdir(os.path.join(directory, name)),
                                reverse=True):
                if entry.endswith('.cvm'):
                    return os.path.join(directory, name, entry)
    raise CVMError(
        f'找不到模块 {target!r}; 已搜索: '
        + ', '.join(dirs)
        + '\n  可执行 `cvm build` 生成, 或 `cvm install <file.cvm>` 安装到模块路径')


# ==================== 命令实现 ====================


def cmd_build(args) -> int:
    out = build_module(args.source, output=args.output, name=args.name,
                       version=args.version, main=args.main,
                       optimize=args.optimize or 0, mem_size=args.mem_size,
                       bounds_check=args.bounds_check, release=args.release)
    module = load_module(out)
    print(f'Built CVM module {module.name} {module.version}: {out}')
    print(f'  engine: {module.manifest["cvm"].get("engine")} '
          f'abi={module.manifest["cvm"].get("abi")} '
          f'bytecode={module.size} bytes')
    return 0


def cmd_run(args) -> int:
    runtime = CVM()
    path = runtime.resolve(args.module)
    module = load_module(path)
    program_args = list(args.args or [])
    if program_args and program_args[0] == '--':
        program_args = program_args[1:]
    cpu = run_module(module, program_args, mem_size=args.mem_size,
                     max_instructions=args.max_instructions,
                     sandbox=args.sandbox, log_level=args.log_level)
    return 1 if getattr(cpu, 'execution_failed', False) else 0


def cmd_inspect(args) -> int:
    module = CVM().load(args.module)
    data = module.to_dict()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f'Module  : {module.name} {module.version}')
    print(f'Path    : {module.path}')
    print(f'Main    : {module.main}')
    print(f'Size    : {module.size} bytes')
    print(f'Format  : {data["format"]}')
    print(f'Engine  : {data["engine"]} (abi {data["abi"]})')
    for key, value in (data.get('build') or {}).items():
        print(f'Build.{key}: {value}')
    return 0


def cmd_verify(args) -> int:
    module = verify_module(CVM().resolve(args.module))
    print(f'OK {module.name} {module.version} '
          f'({module.size} bytes, checksums verified)')
    return 0


def cmd_list(args) -> int:
    modules = CVM().modules()
    if args.json:
        print(json.dumps([m.to_dict() for m in modules], ensure_ascii=False,
                         indent=2))
        return 0
    if not modules:
        print('No CVM modules found in module path.')
        return 0
    print('NAME                 VERSION     SIZE        PATH')
    for module in modules:
        print(f'{module.name:<20} {module.version:<11} '
              f'{module.size:<11} {module.path}')
    return 0


def cmd_env(args) -> int:
    from .version import build_info
    info = build_info()
    data = {
        'cvm': __version__,
        'format': CVM_FORMAT,
        'engine': CVM_ENGINE,
        'abi': CVM_ABI,
        'module_path': module_paths(),
        'native': info.get('native'),
        'native_version': info.get('native_version'),
        'platform': info.get('platform'),
    }
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f'cvm {__version__} (CVM format {CVM_FORMAT}, abi {CVM_ABI})')
    print(f'engine      : {CVM_ENGINE}')
    print(f'native      : {info.get("native")} '
          f'({info.get("native_version") or "unknown"})')
    print(f'platform    : {info.get("platform")}')
    print('module path :')
    for path in module_paths():
        print(f'  {path}')
    return 0


def cmd_install(args) -> int:
    module = load_module(args.module)
    dest_dir = os.path.join(_home(), 'modules', module.name)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f'{module.version}.cvm')
    shutil.copy2(module.path, dest)
    print(f'Installed {module.name} {module.version} -> {dest}')
    return 0


def cmd_which(args) -> int:
    print(CVM().resolve(args.module))
    return 0


# ==================== argparse ====================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='cvm',
        description='Code CIN 虚拟机: 构建/校验/运行 .cvm 应用模块 '
                    '(CPU 仅为内部执行核)')
    parser.add_argument('--version', action='version',
                        version=f'cvm {__version__}')
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('build', help='构建 .cvm 模块')
    p.add_argument('source', help='.cin/.asm/.pl/.bin 或含 mkn.toml 的项目目录')
    p.add_argument('-o', '--output')
    p.add_argument('--name')
    p.add_argument('--module-version', dest='module_version', metavar='VERSION',
                   help='模块版本 (默认取自 mkn.toml 或 0.0.0)')
    p.add_argument('--main')
    p.add_argument('--release', action='store_true')
    p.add_argument('--optimize', type=int, choices=range(0, 4))
    p.add_argument('--mem-size', type=int, dest='mem_size')
    p.add_argument('--bounds-check', action='store_true', dest='bounds_check')
    p.set_defaults(func=cmd_build)

    p = sub.add_parser('run', help='运行模块')
    p.add_argument('module', help='.cvm 文件或模块名')
    p.add_argument('--mem-size', type=int, dest='mem_size')
    p.add_argument('--max-instructions', type=int, dest='max_instructions')
    p.add_argument('--sandbox', action='store_true')
    p.add_argument('--log-level', default='ERROR', dest='log_level',
                   choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'])
    p.add_argument('args', nargs=argparse.REMAINDER,
                   help='传给程序的参数 (写在 -- 之后)')
    p.set_defaults(func=cmd_run)

    p = sub.add_parser('inspect', help='查看模块清单')
    p.add_argument('module')
    p.add_argument('--json', action='store_true')
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser('verify', help='校验模块完整性')
    p.add_argument('module')
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser('list', help='列出模块路径中的模块')
    p.add_argument('--json', action='store_true')
    p.set_defaults(func=cmd_list)

    p = sub.add_parser('env', help='显示 CVM 环境')
    p.add_argument('--json', action='store_true')
    p.set_defaults(func=cmd_env)

    p = sub.add_parser('install', help='把 .cvm 安装到模块路径')
    p.add_argument('module')
    p.set_defaults(func=cmd_install)

    p = sub.add_parser('which', help='解析模块路径')
    p.add_argument('module')
    p.set_defaults(func=cmd_which)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .toolkit import configure_stdio
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    # build 的模块版本统一到 module_version
    if args.command == 'build':
        args.version = getattr(args, 'module_version', None)
    try:
        return int(args.func(args) or 0)
    except (CVMError, ManifestError, archive.ArchiveError, OSError) as e:
        sys.stderr.write(f'cvm: error: {e}\n')
        return 1


if __name__ == '__main__':
    sys.exit(main())
