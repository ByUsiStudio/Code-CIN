"""v6.2.0 工具链基础设施: TOML / 清单 / 版本约束 / 归档 / 存储。"""

import os
import zipfile

import pytest

from codecin.packaging import archive
from codecin.packaging.model import (
    ManifestError,
    load_package_manifest,
    satisfies,
    validate_name,
)
from codecin.packaging.store import PackageStore
from codecin.packaging.toml import TomlError, dumps, loads


def test_toml_roundtrip_tables_arrays_and_inline():
    text = '''
# comment
title = "CIN"
count = 1_000
ratio = 1.5
enabled = true
items = [1, 2, 3,]
meta = { a = 1, b = "x" }

[package]
name = "hello"
version = "1.0.0"
authors = ["A <a@b.c>", "B"]

[dependencies.mathx]
git = "https://example.com/x.git"
tag = "v1.0.0"

[[targets]]
name = "a"
kind = "bin"
[[targets]]
name = "b"
'''
    data = loads(text)
    assert data['count'] == 1000
    assert data['meta'] == {'a': 1, 'b': 'x'}
    assert data['dependencies']['mathx']['tag'] == 'v1.0.0'
    assert data['targets'][1]['name'] == 'b'
    assert loads(dumps(data)) == data


def test_toml_rejects_invalid_syntax():
    with pytest.raises(TomlError):
        loads('a = "unterminated')
    with pytest.raises(TomlError):
        loads('a = 1\na = 2\n')
    with pytest.raises(TomlError):
        loads('a = bare-word\n')


def test_version_constraints():
    assert satisfies('1.2.5', '^1.2.0')
    assert satisfies('1.9.9', '^1.2.0')
    assert not satisfies('2.0.0', '^1.2.0')
    assert satisfies('1.2.9', '~1.2.0')
    assert not satisfies('1.3.0', '~1.2.0')
    assert satisfies('1.5.0', '>=1.0,<2.0')
    assert satisfies('1.2.9', '1.2.x')
    assert satisfies('1.9.0', '1.x')
    assert satisfies('0.2.5', '^0.2.0')
    assert not satisfies('0.3.0', '^0.2.0')
    assert satisfies('1.0.0', '*')


def test_validate_name_rules():
    assert validate_name('hello-world_2') == 'hello-world_2'
    for bad in ('', '2bad', 'bad name', 'x' * 65):
        with pytest.raises(ManifestError):
            validate_name(bad)


def test_package_manifest_and_entry(workdir):
    pkg = os.path.join(workdir, 'pkg')
    os.makedirs(os.path.join(pkg, 'src'))
    with open(os.path.join(pkg, 'pic.toml'), 'w', encoding='utf-8') as f:
        f.write('[package]\nname = "greet"\nversion = "1.2.3"\n'
                'entry = "src/greet.cin"\n\n[dependencies]\n'
                'other = ">=1.0"\n')
    with open(os.path.join(pkg, 'src', 'greet.cin'), 'w', encoding='utf-8') as f:
        f.write('function greet() -> string { return "hi" }\n')
    meta = load_package_manifest(pkg)
    assert meta.name == 'greet'
    assert meta.entry_path == os.path.join(pkg, 'src', 'greet.cin')
    assert meta.entry_rel == 'src/greet.cin'
    assert meta.dependencies[0].name == 'other'


def test_archive_is_deterministic_and_checksummed(workdir):
    a = os.path.join(workdir, 'a.zip')
    b = os.path.join(workdir, 'b.zip')
    entries = archive.build_archive_entries({'x.txt': b'hello'})
    archive.write_zip(a, entries)
    archive.write_zip(b, entries)
    with open(a, 'rb') as fa, open(b, 'rb') as fb:
        assert fa.read() == fb.read()
    checked, failures = archive.verify_checksums(a)
    assert checked == 1 and not failures
    with zipfile.ZipFile(a) as zf:
        assert zf.read('x.txt') == b'hello'


def test_archive_rejects_unsafe_members(workdir):
    path = os.path.join(workdir, 'bad.zip')
    with pytest.raises(archive.ArchiveError):
        archive.write_zip(path, {'../escape.txt': b'x'})
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('../escape.txt', 'x')
    with pytest.raises(archive.ArchiveError):
        archive.safe_extract(path, os.path.join(workdir, 'out'))


def test_store_index_and_remove(workdir, monkeypatch):
    monkeypatch.setenv('PIC_HOME', os.path.join(workdir, 'home'))
    src = os.path.join(workdir, 'hello')
    os.makedirs(os.path.join(src, 'src'))
    with open(os.path.join(src, 'pic.toml'), 'w', encoding='utf-8') as f:
        f.write('[package]\nname = "hello"\nversion = "1.0.0"\n'
                'entry = "src/hello.cin"\n')
    with open(os.path.join(src, 'src', 'hello.cin'), 'w', encoding='utf-8') as f:
        f.write('function hello() -> int { return 1 }\n')

    store = PackageStore()
    pkg = store.add(src, 'hello', '1.0.0')
    assert os.path.isdir(pkg.path)
    assert store.get('hello').version == '1.0.0'
    assert store.package_entry('hello') == os.path.join(
        pkg.path, 'src', 'hello.cin')

    # 新实例从 index.json 读取
    assert PackageStore().get('hello') is not None
    assert store.remove('hello') is True
    assert store.get('hello') is None
