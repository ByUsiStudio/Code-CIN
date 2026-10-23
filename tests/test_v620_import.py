"""v6.2.0: 安装后的包可以直接 import。"""

import os

import pytest

from codecin.cin import CINCompiler
from codecin.errors import CompilerError
from tests.helpers import run_cin_file


def _make_package(root, name='greet', version='1.0.0'):
    pkg = os.path.join(root, name)
    os.makedirs(os.path.join(pkg, 'src'), exist_ok=True)
    with open(os.path.join(pkg, 'pic.toml'), 'w', encoding='utf-8') as f:
        f.write(f'[package]\nname = "{name}"\nversion = "{version}"\n'
                f'entry = "src/{name}.cin"\n')
    with open(os.path.join(pkg, 'src', f'{name}.cin'), 'w',
              encoding='utf-8') as f:
        f.write('function greet() -> int { return 41 }\n')
    with open(os.path.join(pkg, 'src', 'extra.cin'), 'w', encoding='utf-8') as f:
        f.write('function extra() -> int { return 1 }\n')
    return pkg


def test_cin_path_package_is_importable(workdir, monkeypatch):
    pkgs = os.path.join(workdir, 'packages')
    _make_package(pkgs)
    monkeypatch.setenv('CIN_PATH', pkgs)
    monkeypatch.delenv('PIC_HOME', raising=False)
    main = os.path.join(workdir, 'main.cin')
    with open(main, 'w', encoding='utf-8') as f:
        f.write('import "greet"\nimport "greet/extra.cin"\n'
                'function main() -> int { return greet() + extra() }\n')
    cpu = run_cin_file(main)
    assert cpu.regs.read(0) == 42


def test_project_pic_modules_is_importable(workdir, monkeypatch):
    project = os.path.join(workdir, 'project')
    os.makedirs(os.path.join(project, 'src'), exist_ok=True)
    with open(os.path.join(project, 'mkn.toml'), 'w', encoding='utf-8') as f:
        f.write('[project]\nname = "app"\nversion = "0.1.0"\n'
                'entry = "src/main.cin"\n')
    _make_package(os.path.join(project, 'pic_modules'))
    main = os.path.join(project, 'src', 'main.cin')
    with open(main, 'w', encoding='utf-8') as f:
        f.write('import "greet"\n'
                'function main() -> int { return greet() }\n')
    monkeypatch.delenv('CIN_PATH', raising=False)
    monkeypatch.delenv('PIC_HOME', raising=False)
    assert run_cin_file(main).regs.read(0) == 41


def test_globally_installed_package_is_importable(workdir, monkeypatch):
    home = os.path.join(workdir, 'home')
    monkeypatch.setenv('PIC_HOME', home)
    monkeypatch.delenv('CIN_PATH', raising=False)
    pkg = _make_package(os.path.join(workdir, 'store-src'))
    from codecin import pic
    from codecin.packaging.store import PackageStore
    pic.install_path(pkg, PackageStore())
    main = os.path.join(workdir, 'main.cin')
    with open(main, 'w', encoding='utf-8') as f:
        f.write('import "greet"\n'
                'function main() -> int { return greet() }\n')
    assert run_cin_file(main).regs.read(0) == 41


def test_package_import_missing_has_actionable_hint(workdir, monkeypatch):
    monkeypatch.delenv('CIN_PATH', raising=False)
    monkeypatch.delenv('PIC_HOME', raising=False)
    main = os.path.join(workdir, 'missing.cin')
    with open(main, 'w', encoding='utf-8') as f:
        f.write('import "not_installed"\nfunction main() -> int { return 0 }\n')
    with pytest.raises(CompilerError) as exc:
        CINCompiler().compile(main)
    message = str(exc.value)
    assert '内置标准库' in message
    assert 'pic install' in message
