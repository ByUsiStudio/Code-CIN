"""v6.2.0: mkn 项目/依赖/生命周期测试。"""

import os
import zipfile

import pytest

from codecin import mkn


@pytest.fixture(autouse=True)
def _pic_offline(monkeypatch):
    monkeypatch.setenv('PIC_OFFLINE', '1')


def test_mkn_new_build_package_install(workdir, monkeypatch):
    monkeypatch.setenv('MKN_HOME', os.path.join(workdir, 'mkn-home'))
    monkeypatch.chdir(workdir)
    assert mkn.main(['new', 'demo']) == 0
    project = os.path.join(workdir, 'demo')
    monkeypatch.chdir(project)

    assert mkn.main(['build']) == 0
    assert os.path.isfile(os.path.join(project, 'target', 'demo-0.1.0.bin'))

    assert mkn.main(['package']) == 0
    pkg = os.path.join(project, 'target', 'demo-0.1.0.mkn')
    assert os.path.isfile(pkg)
    with zipfile.ZipFile(pkg) as zf:
        names = set(zf.namelist())
    assert 'mkn.toml' in names
    assert 'pic.toml' in names
    assert 'src/main.cin' in names
    assert 'META-INF/SHA256SUMS' in names

    assert mkn.main(['install']) == 0
    installed = os.path.join(workdir, 'mkn-home', 'repository', 'demo', '0.1.0')
    assert os.path.isfile(os.path.join(installed, 'pic.toml'))

    assert mkn.main(['verify']) == 0
    assert mkn.main(['tree']) == 0
    assert mkn.main(['clean', '--deps']) == 0
    assert not os.path.isdir(os.path.join(project, 'target'))


def test_mkn_local_path_dependency_and_import(workdir, monkeypatch):
    monkeypatch.setenv('MKN_HOME', os.path.join(workdir, 'mkn-home'))
    monkeypatch.setenv('PIC_HOME', os.path.join(workdir, 'pic-home'))
    lib = os.path.join(workdir, 'libx')
    assert mkn.main(['init', 'libx', '--path', lib]) == 0
    with open(os.path.join(lib, 'src', 'main.cin'), 'w',
              encoding='utf-8') as f:
        f.write('function lib_value() -> int { return 40 }\n')

    app = os.path.join(workdir, 'app')
    assert mkn.main(['new', 'app', '--dir', app]) == 0
    monkeypatch.chdir(app)
    assert mkn.main(['add', 'libx', '--local-path', lib, '--no-resolve']) == 0
    with open(os.path.join(app, 'src', 'main.cin'), 'w',
              encoding='utf-8') as f:
        f.write('import "libx"\n'
                'function main() -> int { println(lib_value() + 2); return 0 }\n')
    assert mkn.main(['build']) == 0
    assert os.path.isdir(os.path.join(app, 'pic_modules', 'libx'))
    assert mkn.main(['run']) == 0


def test_mkn_dependency_from_local_repository(workdir, monkeypatch):
    mkn_home = os.path.join(workdir, 'mkn-home')
    monkeypatch.setenv('MKN_HOME', mkn_home)
    monkeypatch.setenv('PIC_HOME', os.path.join(workdir, 'pic-home'))
    lib = os.path.join(workdir, 'liby')
    assert mkn.main(['init', 'liby', '--path', lib]) == 0
    with open(os.path.join(lib, 'src', 'main.cin'), 'w',
              encoding='utf-8') as f:
        f.write('function lib_y() -> int { return 7 }\n')
    monkeypatch.chdir(lib)
    assert mkn.main(['install']) == 0

    app = os.path.join(workdir, 'appy')
    assert mkn.main(['new', 'appy', '--dir', app]) == 0
    with open(os.path.join(app, 'mkn.toml'), 'a', encoding='utf-8') as f:
        f.write('\n[dependencies]\nliby = "0.1.0"\n')
    with open(os.path.join(app, 'src', 'main.cin'), 'w',
              encoding='utf-8') as f:
        f.write('import "liby"\n'
                'function main() -> int { return lib_y() }\n')
    monkeypatch.chdir(app)
    assert mkn.main(['build']) == 0
    assert os.path.isfile(os.path.join(app, 'pic.lock'))
    with open(os.path.join(app, 'pic.lock'), encoding='utf-8') as f:
        assert 'liby' in f.read()


def test_mkn_test_runner(workdir, monkeypatch):
    monkeypatch.setenv('MKN_HOME', os.path.join(workdir, 'mkn-home'))
    monkeypatch.chdir(workdir)
    assert mkn.main(['new', 'tdemo']) == 0
    project = os.path.join(workdir, 'tdemo')
    monkeypatch.chdir(project)
    assert mkn.main(['test']) == 0
    assert mkn.main(['doctor']) == 0


def test_mkn_test_runner_detects_failed_assertion(workdir, monkeypatch):
    monkeypatch.setenv('MKN_HOME', os.path.join(workdir, 'mkn-home'))
    monkeypatch.chdir(workdir)
    assert mkn.main(['new', 'fdemo']) == 0
    project = os.path.join(workdir, 'fdemo')
    monkeypatch.chdir(project)
    with open(os.path.join(project, 'src', 'test', 'main_test.cin'), 'w',
              encoding='utf-8') as f:
        f.write('import "test.cin"\n\n'
                'function main() -> int {\n'
                '    t_eq_int(1, 2, "intentional failure")\n'
                '    return t_report()\n'
                '}\n')
    assert mkn.main(['test']) == 1
