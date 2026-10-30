"""v6.4.0: pic 包的分块 import 规格 ``pkg:module`` / ``pkg::version:*``。"""

import os

import pytest

from codecin.cin import CINCompiler
from codecin.errors import CompilerError
from codecin.packaging.spec import SpecError, parse_package_spec
from tests.helpers import run_cin_file


def _make_package(root: str, version: str = '1.0.0') -> str:
    pkg = os.path.join(root, 'rich')
    os.makedirs(os.path.join(pkg, 'src', 'test'), exist_ok=True)
    with open(os.path.join(pkg, 'pic.toml'), 'w', encoding='utf-8') as f:
        f.write('[package]\nname = "rich"\nversion = "' + version + '"\n'
                'entry = "src/rich.cin"\n')
    files = {
        'src/rich.cin': 'function rich_entry() -> int { return 1 }\n',
        'src/extra.cin': 'function extra() -> int { return 2 }\n',
        'src/table.cin': 'function rich_table() -> int { return 3 }\n',
        'src/main.cin': 'function main() -> int { return 99 }\n',
        'src/test/rich_test.cin': 'function broken() -> int { return 9 }\n',
    }
    for rel, text in files.items():
        path = os.path.join(pkg, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
    return pkg


def _program(workdir: str, source: str) -> str:
    path = os.path.join(workdir, 'main.cin')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return path


def test_package_spec_parser():
    spec = parse_package_spec('rich::1.1.1:*')
    assert spec.name == 'rich' and spec.version == '1.1.1'
    assert spec.is_all and spec.display == 'rich::1.1.1:*'
    assert parse_package_spec('rich:table,markdown').modules == (
        'table', 'markdown')
    assert parse_package_spec('rich@1.2.0:table').version == '1.2.0'
    with pytest.raises(SpecError):
        parse_package_spec('rich::1.1:*')
    with pytest.raises(SpecError):
        parse_package_spec('../x.cin')


def test_wildcard_import_pulls_public_chunks(workdir, monkeypatch):
    packages = os.path.join(workdir, 'packages')
    _make_package(packages)
    monkeypatch.setenv('CIN_PATH', packages)
    monkeypatch.delenv('PIC_HOME', raising=False)
    main = _program(workdir,
                    'import "rich:*"\n'
                    'function main() -> int {\n'
                    '    return rich_entry() + extra() + rich_table()\n'
                    '}\n')
    assert run_cin_file(main).regs.read(0) == 6


def test_single_module_import(workdir, monkeypatch):
    packages = os.path.join(workdir, 'packages')
    _make_package(packages)
    monkeypatch.setenv('CIN_PATH', packages)
    monkeypatch.delenv('PIC_HOME', raising=False)
    main = _program(workdir,
                    'import "rich:extra"\n'
                    'function main() -> int { return extra() }\n')
    assert run_cin_file(main).regs.read(0) == 2
    main = _program(workdir,
                    'import "rich:extra,table"\n'
                    'function main() -> int { return extra() + rich_table() }\n')
    assert run_cin_file(main).regs.read(0) == 5


def test_versioned_module_import(workdir, monkeypatch):
    packages = os.path.join(workdir, 'packages')
    _make_package(packages)
    monkeypatch.setenv('CIN_PATH', packages)
    monkeypatch.delenv('PIC_HOME', raising=False)
    main = _program(workdir,
                    'import "rich::1.0.0:extra"\n'
                    'function main() -> int { return extra() }\n')
    assert run_cin_file(main).regs.read(0) == 2


def test_missing_module_and_version_are_actionable(workdir, monkeypatch):
    packages = os.path.join(workdir, 'packages')
    _make_package(packages)
    monkeypatch.setenv('CIN_PATH', packages)
    monkeypatch.delenv('PIC_HOME', raising=False)
    main = _program(workdir,
                    'import "rich:missing"\n'
                    'function main() -> int { return 0 }\n')
    with pytest.raises(CompilerError) as exc:
        CINCompiler().compile(main)
    assert 'pic modules rich' in str(exc.value)

    main = _program(workdir,
                    'import "rich::2.0.0:extra"\n'
                    'function main() -> int { return 0 }\n')
    with pytest.raises(CompilerError) as exc:
        CINCompiler().compile(main)
    assert 'pic install rich@2.0.0' in str(exc.value)


def test_global_store_picks_latest_or_explicit_version(workdir, monkeypatch):
    home = os.path.join(workdir, 'home')
    monkeypatch.setenv('PIC_HOME', home)
    monkeypatch.delenv('CIN_PATH', raising=False)
    for version, value in (('1.0.0', 11), ('2.0.0', 22)):
        pkg = os.path.join(home, 'packages', 'rich', version)
        os.makedirs(os.path.join(pkg, 'src'), exist_ok=True)
        with open(os.path.join(pkg, 'pic.toml'), 'w', encoding='utf-8') as f:
            f.write('[package]\nname = "rich"\nversion = "' + version + '"\n'
                    'entry = "src/rich.cin"\n')
        with open(os.path.join(pkg, 'src', 'rich.cin'), 'w',
                  encoding='utf-8') as f:
            f.write('function rich_version_value() -> int { return '
                    + str(value) + ' }\n')
    import json
    # index.json 的 current 故意指向旧版本: import 仍应选择最新版本
    with open(os.path.join(home, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump({'format': 1, 'packages': {
            'rich': {'current': '1.0.0', 'versions': {
                '1.0.0': {'path': os.path.join(
                    home, 'packages', 'rich', '1.0.0')},
                '2.0.0': {'path': os.path.join(
                    home, 'packages', 'rich', '2.0.0')},
            }},
        }}, f)

    main = _program(workdir,
                    'import "rich:*"\n'
                    'function main() -> int { return rich_version_value() }\n')
    assert run_cin_file(main).regs.read(0) == 22
    main = _program(workdir,
                    'import "rich::2.0.0:*"\n'
                    'function main() -> int { return rich_version_value() }\n')
    assert run_cin_file(main).regs.read(0) == 22
    main = _program(workdir,
                    'import "rich::1.0.0:*"\n'
                    'function main() -> int { return rich_version_value() }\n')
    assert run_cin_file(main).regs.read(0) == 11
