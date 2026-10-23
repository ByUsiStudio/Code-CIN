"""v6.2.0: pic 包管理器端到端测试 (不依赖网络)。"""

import os
import zipfile

import pytest

from codecin import pic
from codecin.packaging.store import PackageStore


@pytest.fixture(autouse=True)
def _pic_offline(monkeypatch):
    """这些用例只覆盖本地包流程; 在线注册表见 test_v620_pic_registry.py。"""
    monkeypatch.setenv('PIC_OFFLINE', '1')


def _init(path, name, version='1.0.0', repo=None):
    argv = ['init', name, '--path', path, '--version', version]
    if repo:
        argv += ['--repo', repo]
    assert pic.main(argv) == 0


def test_pic_init_build_publish_and_install(workdir, monkeypatch, capsys):
    home = os.path.join(workdir, 'home')
    monkeypatch.setenv('PIC_HOME', home)
    pkg = os.path.join(workdir, 'greet')
    _init(pkg, 'greet', '1.2.3')

    assert pic.main(['build', '--manifest', pkg]) == 0
    archive_path = os.path.join(pkg, 'dist', 'greet-1.2.3.cinpkg')
    assert os.path.isfile(archive_path)
    with zipfile.ZipFile(archive_path) as zf:
        names = set(zf.namelist())
    assert 'pic.toml' in names
    assert 'src/greet.cin' in names
    assert 'META-INF/SHA256SUMS' in names

    repo = 'https://example.com/cin-greet.git'
    assert pic.main(['publish', '--manifest', pkg, '--repo', repo,
                     '--no-tag']) == 0
    store = PackageStore()
    entry = store.lookup('greet')
    assert entry['repo'] == repo
    assert '1.2.3' in entry['versions']

    assert pic.main(['install', archive_path]) == 0
    store = PackageStore()
    installed = store.get('greet')
    assert installed is not None and installed.version == '1.2.3'
    assert os.path.isfile(os.path.join(installed.path, 'src', 'greet.cin'))
    assert pic.main(['show', 'greet']) == 0
    assert pic.main(['list']) == 0


def test_pic_project_install_and_lock(workdir, monkeypatch):
    monkeypatch.setenv('PIC_HOME', os.path.join(workdir, 'home'))
    pkg = os.path.join(workdir, 'greet')
    _init(pkg, 'greet', '1.0.0')
    project = os.path.join(workdir, 'app')
    os.makedirs(project)
    monkeypatch.chdir(project)

    assert pic.main(['install', pkg, '--project']) == 0
    assert os.path.isdir(os.path.join(project, 'pic_modules', 'greet'))
    assert os.path.isfile(os.path.join(project, 'pic.lock'))
    with open(os.path.join(project, 'pic.lock'), encoding='utf-8') as f:
        lock = f.read()
    assert 'name = "greet"' in lock
    assert pic.main(['list', '--project', '--json']) == 0
    assert pic.main(['uninstall', 'greet', '--project']) == 0
    assert not os.path.isdir(os.path.join(project, 'pic_modules', 'greet'))


def test_pic_transitive_path_dependency(workdir, monkeypatch):
    monkeypatch.setenv('PIC_HOME', os.path.join(workdir, 'home'))
    b = os.path.join(workdir, 'lib-b')
    _init(b, 'libb', '0.2.0')
    a = os.path.join(workdir, 'lib-a')
    _init(a, 'liba', '0.1.0')
    with open(os.path.join(a, 'pic.toml'), 'a', encoding='utf-8') as f:
        f.write('\n[dependencies.libb]\npath = "../lib-b"\n')
    # 覆盖 init 生成的清单时依赖段需要位于文件末尾 (TOML 表)
    project = os.path.join(workdir, 'app')
    os.makedirs(project)
    monkeypatch.chdir(project)
    assert pic.main(['install', a, '--project']) == 0
    assert os.path.isdir(os.path.join(project, 'pic_modules', 'liba'))
    assert os.path.isdir(os.path.join(project, 'pic_modules', 'libb'))


def test_pic_editable_install_uses_link(workdir, monkeypatch):
    monkeypatch.setenv('PIC_HOME', os.path.join(workdir, 'home'))
    pkg = os.path.join(workdir, 'greet')
    _init(pkg, 'greet', '1.0.0')
    project = os.path.join(workdir, 'app')
    os.makedirs(project)
    monkeypatch.chdir(project)
    assert pic.main(['install', pkg, '--project', '--editable']) == 0
    link = os.path.join(project, 'pic_modules', 'greet', '.pic-link')
    assert os.path.isfile(link)
    with open(link, encoding='utf-8') as f:
        assert os.path.samefile(f.read().strip(), pkg)


def test_pic_registry_add_search_remove(workdir, monkeypatch):
    monkeypatch.setenv('PIC_HOME', os.path.join(workdir, 'home'))
    assert pic.main(['registry', 'add', 'greet',
                     'https://example.com/greet.git']) == 0
    assert pic.main(['search', 'greet']) == 0
    assert pic.main(['registry', 'list']) == 0
    assert pic.main(['registry', 'remove', 'greet']) == 0
