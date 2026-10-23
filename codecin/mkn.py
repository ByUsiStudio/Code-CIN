"""``mkn`` —— Code CIN 的项目构建与依赖工具 (Maven 风格)。

Maven 的核心概念在 CIN 中的对应关系:

====================  ==================================================
Maven                 mkn
====================  ==================================================
``pom.xml``           ``mkn.toml`` (``[project]`` / ``[build]`` / ``[dependencies]``)
标准目录布局          ``src/main.cin``、``src/test/``、``target/``
生命周期              ``compile -> test -> package -> install -> deploy``
依赖坐标              ``name@version`` / Git 仓库 / 本地路径
本地仓库              ``$MKN_HOME/repository/<name>/<version>``
传递依赖              安装到项目 ``pic_modules/``, CIN 可直接 import
====================  ==================================================

``mkn`` 复用现有编译器与 ``pic`` 的解析/存储能力, 不重复实现包管理。
"""

import argparse
import contextlib
import glob
import json
import os
import re
import shutil
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import __version__
from .config import Config
from .packaging import archive, vcs
from .packaging.model import (
    Dependency,
    ManifestError,
    MknManifest,
    find_project_root,
    load_mkn_manifest,
    satisfies,
    validate_name,
    validate_version,
)
from .packaging.store import copy_package_tree, default_store
from .packaging.toml import dumps as toml_dumps
from .packaging.toml import load as toml_load

__all__ = ['main', 'build_project', 'resolve_dependencies', 'load_project']

_IGNORE = shutil.ignore_patterns(
    '.git', '.hg', '.svn', '__pycache__', '*.pyc', '.pytest_cache',
    '.ruff_cache', 'pic_modules', '.pic', '.mkn', 'node_modules', '.venv',
    'venv', '*.mkn', '*.cinpkg', '*.cvm')


class MknError(RuntimeError):
    """mkn 的可预期失败。"""


def _home() -> str:
    return os.path.abspath(os.environ.get('MKN_HOME')
                           or os.path.join(os.path.expanduser('~'), '.mkn'))


def _repository_dir() -> str:
    return os.path.join(_home(), 'repository')


# ==================== 项目模型 ====================


def load_project(start: Optional[str] = None,
                 manifest: Optional[str] = None) -> MknManifest:
    if manifest:
        path = os.path.abspath(manifest)
        if os.path.isdir(path):
            path = os.path.join(path, 'mkn.toml')
    else:
        root = find_project_root(start or os.getcwd(), ('mkn.toml',))
        if root is None:
            raise MknError(
                '找不到 mkn.toml; 请在项目根运行, 或先执行 `mkn init`')
        path = os.path.join(root, 'mkn.toml')
    return load_mkn_manifest(path)


def _build_config(project: MknManifest, *, release: bool,
                  optimize: Optional[int], mem_size: Optional[int]
                  ) -> Config:
    build = project.build or {}
    level = optimize
    if level is None:
        level = int(build.get('optimize', 0) or 0)
    if release:
        level = max(level, 2)
    size = mem_size or int(build.get('mem_size', 0) or 0) or None
    config = Config(log_level='ERROR', optimize=level)
    if size:
        config.mem_size = int(size)
    config.validate()
    return config


def _has_main(entry: str) -> bool:
    """入口及其 import 闭包中是否存在 ``function main(...)``。"""
    files: List[str]
    try:
        from .cin import collect_imported_files
        files = collect_imported_files(entry)
    except Exception:  # noqa: BLE001 - 编译错误留给真正的编译步骤报告
        files = [entry]
    pattern = re.compile(r'\bfunction\s+main\s*\(')
    for path in files:
        if not path.endswith('.cin'):
            continue
        try:
            with open(path, encoding='utf-8-sig') as f:
                if pattern.search(f.read()):
                    return True
        except OSError:
            continue
    return False


def build_project(project: MknManifest, *,
                  output: Optional[str] = None, release: bool = False,
                  optimize: Optional[int] = None,
                  mem_size: Optional[int] = None,
                  bounds_check: bool = False,
                  resolve: bool = True) -> str:
    """编译项目入口; 可执行项目产出 ``.bin``, 库项目产出 ``.co`` 对象。"""
    if resolve:
        resolve_dependencies(project)
    entry = project.entry_path
    config = _build_config(project, release=release, optimize=optimize,
                           mem_size=mem_size)
    config.bounds_check = bounds_check or bool(
        project.build.get('bounds_check', False))

    if not _has_main(entry):
        from .object import ObjectError, compile_asm_object, compile_cin_object, write_object
        out = output or os.path.join(
            project.target_dir, f'{project.name}-{project.version}.co')
        out = os.path.abspath(out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        try:
            if entry.lower().endswith('.cin'):
                obj = compile_cin_object(entry, mem_size=config.mem_size)
            else:
                obj = compile_asm_object(entry, mem_size=config.mem_size)
            write_object(obj, out)
        except ObjectError:
            raise
        return out

    from . import crom as crom_mod
    from .cpu import CPU
    cpu = CPU(config, entry)
    out = output or os.path.join(
        project.target_dir, f'{project.name}-{project.version}.bin')
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    crom_mod.save_bin(cpu, out)
    return out


# ==================== 依赖解析 ====================


def _repo_versions(name: str) -> List[str]:
    path = os.path.join(_repository_dir(), name)
    if not os.path.isdir(path):
        return []
    return sorted(v for v in os.listdir(path)
                  if os.path.isdir(os.path.join(path, v)))


def _install_from_repository(name: str, constraint: str,
                             target_root: str) -> Optional[str]:
    versions = [v for v in _repo_versions(name) if satisfies(v, constraint)]
    if not versions:
        return None
    version = versions[-1]
    src = os.path.join(_repository_dir(), name, version)
    dest = os.path.join(target_root, name)
    copy_package_tree(src, dest)
    meta = {'repo': f'mkn-repository:{name}', 'rev': '', 'editable': False,
            'source': src, 'installed_by': f'mkn {__version__}'}
    with open(os.path.join(dest, '.pic-meta.json'), 'w',
              encoding='utf-8', newline='\n') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return dest


def _project_dep_satisfied(project: MknManifest, dep: Dependency) -> bool:
    path = os.path.join(project.pic_modules, dep.name)
    if not os.path.isdir(path):
        return False
    from .packaging.store import resolve_link
    real = resolve_link(path)
    try:
        meta = load_mkn_manifest(real) if os.path.isfile(
            os.path.join(real, 'mkn.toml')) else None
    except ManifestError:
        meta = None
    if meta is None:
        try:
            from .packaging.model import load_package_manifest
            pkg = load_package_manifest(real, require_entry=False)
        except ManifestError:
            return False
        version = pkg.version
    else:
        version = meta.version
    return satisfies(version, dep.constraint) if dep.constraint else True


def resolve_dependencies(project: MknManifest, *, force: bool = False
                         ) -> List[str]:
    """把 ``[dependencies]`` 解析到 ``pic_modules/``; 返回新增包名。"""
    target = project.pic_modules
    os.makedirs(target, exist_ok=True)
    installed: List[str] = []
    seen: set = set()

    def _install(dep: Dependency) -> None:
        if dep.name in seen:
            return
        seen.add(dep.name)
        if not force and _project_dep_satisfied(project, dep):
            return
        if dep.path:
            from . import pic
            pic.install_path(dep.path, default_store(), target_root=target,
                             force=force, expected_name=dep.name)
        elif dep.git:
            from . import pic
            pic.install_git(dep.git, default_store(),
                            ref=dep.tag or dep.rev or None,
                            target_root=target, force=force,
                            expected_name=dep.name)
        else:
            if _install_from_repository(dep.name, dep.constraint or '*',
                                        target) is not None:
                installed.append(dep.name)
                return
            from . import pic
            pic.install_named(dep.name, dep.constraint, default_store(),
                              target_root=target, force=force)
        installed.append(dep.name)

    for dep in project.dependencies:
        _install(dep)
    if installed:
        from . import pic
        pic.write_project_lock(project.root, target)
    return installed


# ==================== 打包 ====================


def _project_files(project: MknManifest) -> Iterable[Tuple[str, str]]:
    root = project.root
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in ('.git', '.hg', '.svn', '__pycache__', 'target',
                         'pic_modules', '.pic', '.mkn', 'node_modules',
                         '.venv', 'venv', '.pytest_cache'))
        for filename in sorted(filenames):
            if filename.endswith(('.pyc', '.mkn', '.cinpkg', '.cvm')):
                continue
            path = os.path.join(dirpath, filename)
            yield os.path.relpath(path, root).replace(os.sep, '/'), path


def _generated_pic_manifest(project: MknManifest) -> str:
    data = {
        'package': {
            'name': project.name,
            'version': project.version,
            'description': project.description,
            'license': project.license,
            'entry': project.entry.replace('\\', '/'),
            'authors': project.authors,
        },
    }
    if project.repo:
        data['package']['repo'] = project.repo
    return toml_dumps(data)


def package_project(project: MknManifest, bin_path: Optional[str] = None,
                    out: Optional[str] = None) -> Tuple[str, Dict[str, str]]:
    """构建 ``.mkn`` 分发包 (同时含 mkn.toml / pic.toml / 源码 / 字节码)。"""
    bin_path = bin_path or build_project(project)
    out = out or os.path.join(
        project.target_dir, f'{project.name}-{project.version}.mkn')
    out = os.path.abspath(out)
    files: Dict[str, str] = {}
    for rel, path in _project_files(project):
        files[rel] = path
    files['mkn.toml'] = project.manifest_path
    files['pic.toml'] = ':generated:'
    files[f'target/{os.path.basename(bin_path)}'] = bin_path

    entries: Dict[str, bytes] = {}
    for rel, path in files.items():
        if path == ':generated:':
            entries[rel] = _generated_pic_manifest(project).encode('utf-8')
        else:
            with open(path, 'rb') as f:
                entries[rel] = f.read()
    archive.write_zip(out, archive.build_archive_entries(entries))
    return out, {rel: archive.sha256_bytes(data)
                 for rel, data in entries.items()}


def install_project(project: MknManifest, bin_path: Optional[str] = None
                    ) -> str:
    """安装到本地 mkn 仓库 ``$MKN_HOME/repository/<name>/<version>``。"""
    bin_path = bin_path or build_project(project)
    dest = os.path.join(_repository_dir(), project.name, project.version)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest, exist_ok=True)
    for rel, path in _project_files(project):
        target = os.path.join(dest, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(path, target)
    with open(os.path.join(dest, 'pic.toml'), 'w', encoding='utf-8',
              newline='\n') as f:
        f.write(_generated_pic_manifest(project))
    target_bin = os.path.join(dest, 'target', os.path.basename(bin_path))
    os.makedirs(os.path.dirname(target_bin), exist_ok=True)
    shutil.copy2(bin_path, target_bin)
    meta = {
        'name': project.name,
        'version': project.version,
        'installed_at': __import__('datetime').datetime.now().astimezone()
        .replace(microsecond=0).isoformat(),
        'entry': project.entry,
    }
    with open(os.path.join(dest, '.mkn-meta.json'), 'w', encoding='utf-8',
              newline='\n') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return dest


# ==================== 命令实现 ====================


def _project_from_args(args) -> MknManifest:
    return load_project(getattr(args, 'path', None) or os.getcwd(),
                        getattr(args, 'manifest', None))


def _create_project(root: str, name: str, version: str, description: str = '',
                    force: bool = False, require_empty: bool = True) -> int:
    validate_name(name)
    validate_version(version)
    root = os.path.abspath(root)
    manifest_path = os.path.join(root, 'mkn.toml')
    if os.path.isfile(manifest_path) and not force:
        raise MknError(f'{manifest_path} 已存在 (使用 --force 覆盖)')
    if require_empty and os.path.isdir(root) and os.listdir(root) \
            and not force:
        raise MknError(f'目录非空: {root} (使用 --force 覆盖)')
    os.makedirs(os.path.join(root, 'src', 'test'), exist_ok=True)
    manifest = {
        'project': {
            'name': name,
            'version': version,
            'description': description or f'{name} project',
            'license': 'MIT',
            'entry': 'src/main.cin',
            'authors': [],
        },
        'build': {
            'optimize': 0,
            'backend': 'vm',
        },
    }
    with open(os.path.join(root, 'mkn.toml'), 'w', encoding='utf-8',
              newline='\n') as f:
        f.write(toml_dumps(manifest))
    main = os.path.join(root, 'src', 'main.cin')
    if not os.path.isfile(main):
        with open(main, 'w', encoding='utf-8', newline='\n') as f:
            f.write(
                'function main() -> int {\n'
                f'    println("Hello from {name}!")\n'
                '    return 0\n'
                '}\n')
    test = os.path.join(root, 'src', 'test', 'main_test.cin')
    if not os.path.isfile(test):
        with open(test, 'w', encoding='utf-8', newline='\n') as f:
            f.write(
                'import "test.cin"\n\n'
                'function main() -> int {\n'
                '    t_eq_int(1 + 1, 2, "arithmetic")\n'
                '    return t_report()\n'
                '}\n')
    readme = os.path.join(root, 'README.md')
    if not os.path.isfile(readme):
        with open(readme, 'w', encoding='utf-8', newline='\n') as f:
            f.write(f'# {name}\n\n```bash\nmkn build\nmkn run\nmkn test\n```\n')
    gitignore = os.path.join(root, '.gitignore')
    if not os.path.isfile(gitignore):
        with open(gitignore, 'w', encoding='utf-8', newline='\n') as f:
            f.write('target/\npic_modules/\n*.bin\n*.mkn\n*.cvm\n'
                    '__pycache__/\n')
    print(f'Created mkn project {name} {version} at {root}')
    print(f'  cd {root}')
    print('  mkn build')
    return 0


def cmd_new(args) -> int:
    root = os.path.abspath(args.dir or args.name)
    return _create_project(root, args.name, args.version,
                           args.description, args.force)


def cmd_init(args) -> int:
    root = os.path.abspath(args.path or os.getcwd())
    name = args.name or os.path.basename(root.rstrip('\\/')) or 'my-project'
    name = ''.join(c if c.isalnum() or c in '_-' else '-' for c in name)
    if not name or not name[0].isalpha():
        name = 'project-' + name
    return _create_project(root, name, args.version, args.description,
                           args.force, require_empty=False)


def cmd_build(args) -> int:
    project = _project_from_args(args)
    if args.clean:
        shutil.rmtree(project.target_dir, ignore_errors=True)
    out = build_project(project, output=args.output, release=args.release,
                        optimize=args.optimize, mem_size=args.mem_size,
                        bounds_check=args.bounds_check,
                        resolve=not args.no_deps)
    print(f'Built {project.name} {project.version}: {out}')
    if args.package:
        pkg, _ = package_project(project, bin_path=out)
        print(f'Packaged: {pkg}')
    return 0


def cmd_run(args) -> int:
    project = _project_from_args(args)
    out = build_project(project, release=args.release,
                        optimize=args.optimize, mem_size=args.mem_size,
                        resolve=not args.no_deps)
    if out.lower().endswith('.co'):
        raise MknError('项目没有 main 函数 (库项目); 使用 mkn package/install')
    from .cpu import CPU
    config = _build_config(project, release=args.release,
                           optimize=args.optimize, mem_size=args.mem_size)
    config.program_args = list(args.args or [])
    if config.program_args and config.program_args[0] == '--':
        config.program_args = config.program_args[1:]
    cpu = CPU(config, out)
    cpu.run()
    return 1 if getattr(cpu, 'execution_failed', False) else 0


def cmd_test(args) -> int:
    project = _project_from_args(args)
    resolve_dependencies(project)
    patterns = [
        os.path.join(project.root, 'src', 'test', '**', '*.cin'),
        os.path.join(project.root, 'tests', '**', '*.cin'),
    ]
    files: List[str] = []
    for pattern in patterns:
        files.extend(glob.glob(pattern, recursive=True))
    if args.filter:
        files = [p for p in files if args.filter in p]
    files = sorted(set(files))
    if not files:
        raise MknError('没有找到测试文件 (src/test/**/*.cin 或 tests/**/*.cin)')
    from .cpu import CPU
    failures = 0
    for path in files:
        rel = os.path.relpath(path, project.root)
        config = _build_config(project, release=False, optimize=args.optimize,
                               mem_size=args.mem_size)
        try:
            cpu = CPU(config, path)
            cpu.run()
        except Exception as e:  # noqa: BLE001 - 单个测试失败不终止其余测试
            failures += 1
            print(f'FAIL {rel}: {e}')
            continue
        if getattr(cpu, 'execution_failed', False):
            failures += 1
            print(f'FAIL {rel}')
        else:
            print(f'PASS {rel}')
    print(f'\n{len(files) - failures} passed, {failures} failed')
    return 1 if failures else 0


def cmd_package(args) -> int:
    project = _project_from_args(args)
    out = build_project(project, release=args.release,
                        optimize=args.optimize, mem_size=args.mem_size,
                        resolve=not args.no_deps)
    pkg, checksums = package_project(project, bin_path=out, out=args.output)
    print(f'Packaged {project.name} {project.version}: {pkg}')
    print(f'  files: {len(checksums)}')
    return 0


def cmd_install(args) -> int:
    project = _project_from_args(args)
    out = build_project(project, release=args.release,
                        optimize=args.optimize, resolve=not args.no_deps)
    dest = install_project(project, bin_path=out)
    print(f'Installed {project.name} {project.version} -> {dest}')
    return 0


def cmd_deploy(args) -> int:
    project = _project_from_args(args)
    code = cmd_install(args)
    if code:
        return code
    if args.repo:
        from . import pic
        argv = ['publish', '--manifest', project.manifest_path,
                '--repo', args.repo]
        if args.push:
            argv.append('--push')
        return pic.main(argv)
    print('提示: 未提供 --repo, 仅安装到本地 mkn 仓库')
    return 0


def cmd_clean(args) -> int:
    project = _project_from_args(args)
    shutil.rmtree(project.target_dir, ignore_errors=True)
    print(f'Cleaned {project.target_dir}')
    if args.deps:
        shutil.rmtree(project.pic_modules, ignore_errors=True)
        lock = os.path.join(project.root, 'pic.lock')
        if os.path.isfile(lock):
            os.remove(lock)
        print(f'Cleaned {project.pic_modules}')
    return 0


def cmd_add(args) -> int:
    project = _project_from_args(args)
    validate_name(args.name, '依赖名')
    data = toml_load(project.manifest_path)
    deps = data.setdefault('dependencies', {})
    if args.local_path:
        value: Any = {'path': args.local_path}
    elif args.git:
        value = {'git': args.git}
        if args.tag:
            value['tag'] = args.tag
        if args.rev:
            value['rev'] = args.rev
        if args.version:
            value['version'] = args.version
    else:
        value = args.version or '*'
    deps[args.name] = value
    with open(project.manifest_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(toml_dumps(data))
    print(f'Added dependency {args.name}')
    if not args.no_resolve:
        reloaded = load_mkn_manifest(project.manifest_path)
        resolve_dependencies(reloaded)
    return 0


def cmd_tree(args) -> int:
    project = _project_from_args(args)
    lines: List[str] = [f'{project.name}@{project.version}']

    def visit(deps: Sequence[Dependency], prefix: str, depth: int,
              seen: set) -> None:
        for index, dep in enumerate(deps):
            last = index == len(deps) - 1
            branch = '└── ' if last else '├── '
            version = ''
            path = os.path.join(project.pic_modules, dep.name)
            if os.path.isdir(path):
                try:
                    from .packaging.model import load_package_manifest
                    version = load_package_manifest(
                        path, require_entry=False).version
                except ManifestError:
                    version = '?'
            label = f'{dep.name}@{version}' if version else dep.name
            if dep.constraint:
                label += f' ({dep.constraint})'
            lines.append(prefix + branch + label)
            if depth >= args.depth or dep.name in seen:
                continue
            try:
                from .packaging.model import load_package_manifest
                meta = load_package_manifest(path, require_entry=False)
                child_prefix = prefix + ('    ' if last else '│   ')
                visit(meta.dependencies, child_prefix, depth + 1,
                      seen | {dep.name})
            except (ManifestError, OSError):
                pass

    visit(project.dependencies, '', 1, set())
    sys.stdout.write('\n'.join(lines) + '\n')
    return 0


def cmd_verify(args) -> int:
    project = _project_from_args(args)
    problems: List[str] = []
    try:
        entry = project.entry_path
        if not os.path.isfile(entry):
            problems.append(f'入口不存在: {entry}')
    except ManifestError as e:
        problems.append(str(e))
    for dep in project.dependencies:
        if not _project_dep_satisfied(project, dep):
            problems.append(f'依赖未解析: {dep.spec}')
    if problems:
        for problem in problems:
            print(f'ERROR {problem}')
        return 1
    print(f'OK {project.name} {project.version} '
          f'({len(project.dependencies)} dependencies)')
    return 0


def cmd_doctor(args) -> int:
    project = None
    with contextlib.suppress(MknError):
        project = _project_from_args(args)
    print(f'mkn {__version__}')
    print(f'home       : {_home()}')
    print(f'git        : {shutil.which("git") or "NOT FOUND"}')
    print(f'go         : {shutil.which("go") or "NOT FOUND"}')
    if project:
        print(f'project    : {project.name} {project.version}')
        print(f'entry      : {project.entry_path}')
        print(f'target     : {project.target_dir}')
        print(f'deps       : {len(project.dependencies)}')
    return 0


# ==================== argparse ====================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='mkn',
        description='Code CIN 项目构建与依赖工具 (Maven 风格生命周期)')
    parser.add_argument('--version', action='version',
                        version=f'mkn {__version__}')
    sub = parser.add_subparsers(dest='command', required=True)

    def project_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument('--path', help='项目目录 (默认当前目录)')
        p.add_argument('--manifest', help='mkn.toml 路径')

    p = sub.add_parser('new', help='创建标准项目骨架')
    p.add_argument('name')
    p.add_argument('--dir', help='目标目录 (默认 ./<name>)')
    p.add_argument('--version', default='0.1.0')
    p.add_argument('--description', default='')
    p.add_argument('--force', action='store_true')
    p.set_defaults(func=cmd_new)

    p = sub.add_parser('init', help='在当前目录初始化项目')
    p.add_argument('name', nargs='?')
    p.add_argument('--path')
    p.add_argument('--version', default='0.1.0')
    p.add_argument('--description', default='')
    p.add_argument('--force', action='store_true')
    p.set_defaults(func=cmd_init)

    p = sub.add_parser('build', aliases=['compile'], help='编译入口为 .bin')
    project_opts(p)
    p.add_argument('-o', '--output')
    p.add_argument('--release', action='store_true', help='优化级别至少为 2')
    p.add_argument('--optimize', type=int, choices=range(0, 4))
    p.add_argument('--mem-size', type=int, dest='mem_size')
    p.add_argument('--bounds-check', action='store_true', dest='bounds_check')
    p.add_argument('--no-deps', action='store_true', dest='no_deps')
    p.add_argument('--clean', action='store_true')
    p.add_argument('--package', action='store_true', help='编译后同时打包')
    p.set_defaults(func=cmd_build)

    p = sub.add_parser('run', help='编译并运行')
    project_opts(p)
    p.add_argument('--release', action='store_true')
    p.add_argument('--optimize', type=int, choices=range(0, 4))
    p.add_argument('--mem-size', type=int, dest='mem_size')
    p.add_argument('--no-deps', action='store_true', dest='no_deps')
    p.add_argument('args', nargs=argparse.REMAINDER,
                   help='传给 CIN 程序的参数 (写在 -- 之后)')
    p.set_defaults(func=cmd_run)

    p = sub.add_parser('test', help='运行 src/test 下的测试程序')
    project_opts(p)
    p.add_argument('--filter', help='只运行路径包含该子串的测试')
    p.add_argument('--optimize', type=int, choices=range(0, 4))
    p.add_argument('--mem-size', type=int, dest='mem_size')
    p.set_defaults(func=cmd_test)

    p = sub.add_parser('package', help='生成 .mkn 分发包')
    project_opts(p)
    p.add_argument('-o', '--output')
    p.add_argument('--release', action='store_true')
    p.add_argument('--optimize', type=int, choices=range(0, 4))
    p.add_argument('--mem-size', type=int, dest='mem_size')
    p.add_argument('--no-deps', action='store_true', dest='no_deps')
    p.set_defaults(func=cmd_package)

    p = sub.add_parser('install', help='安装到本地 mkn 仓库')
    project_opts(p)
    p.add_argument('--release', action='store_true')
    p.add_argument('--optimize', type=int, choices=range(0, 4))
    p.add_argument('--no-deps', action='store_true', dest='no_deps')
    p.set_defaults(func=cmd_install)

    p = sub.add_parser('deploy', help='安装并可发布到 Git')
    project_opts(p)
    p.add_argument('--repo', help='Git 仓库地址')
    p.add_argument('--push', action='store_true')
    p.add_argument('--release', action='store_true')
    p.add_argument('--optimize', type=int, choices=range(0, 4))
    p.add_argument('--no-deps', action='store_true', dest='no_deps')
    p.add_argument('--output')
    p.set_defaults(func=cmd_deploy)

    p = sub.add_parser('clean', help='删除 target/')
    project_opts(p)
    p.add_argument('--deps', action='store_true', help='同时删除 pic_modules/')
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser('add', help='添加依赖')
    project_opts(p)
    p.add_argument('name')
    p.add_argument('--version')
    p.add_argument('--git')
    p.add_argument('--tag')
    p.add_argument('--rev')
    p.add_argument('--local-path', dest='local_path',
                   help='本地路径依赖 (独立于项目的 --path)')
    p.add_argument('--no-resolve', action='store_true', dest='no_resolve')
    p.set_defaults(func=cmd_add)

    p = sub.add_parser('tree', help='打印依赖树')
    project_opts(p)
    p.add_argument('--depth', type=int, default=8)
    p.set_defaults(func=cmd_tree)

    p = sub.add_parser('verify', help='校验清单/入口/依赖')
    project_opts(p)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser('doctor', help='检查工具链环境')
    project_opts(p)
    p.set_defaults(func=cmd_doctor)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .toolkit import configure_stdio
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        return int(args.func(args) or 0)
    except (MknError, ManifestError, archive.ArchiveError,
            vcs.VcsError, OSError) as e:
        sys.stderr.write(f'mkn: error: {e}\n')
        return 1


if __name__ == '__main__':
    sys.exit(main())
