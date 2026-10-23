"""v6.2.0: CVM 模块构建/校验/运行测试。"""

import os
import zipfile

from codecin import cvm, mkn


def _write_hello(path):
    with open(path, 'w', encoding='utf-8') as f:
        f.write('function main() -> int {\n'
                '    println("hello cvm")\n'
                '    return 0\n'
                '}\n')


def test_cvm_build_inspect_verify_run(workdir, monkeypatch):
    monkeypatch.setenv('CVM_HOME', os.path.join(workdir, 'cvm-home'))
    src = os.path.join(workdir, 'hello.cin')
    _write_hello(src)
    out = os.path.join(workdir, 'hello.cvm')
    assert cvm.main(['build', src, '-o', out, '--name', 'hello',
                     '--module-version', '1.2.0']) == 0
    assert os.path.isfile(out)

    module = cvm.load_module(out)
    assert module.name == 'hello' and module.version == '1.2.0'
    assert module.manifest['cvm']['format'] == 1
    assert module.manifest['cvm']['engine'] == 'codecin-native'

    assert cvm.main(['inspect', out]) == 0
    assert cvm.main(['verify', out]) == 0
    assert cvm.main(['run', out]) == 0


def test_cvm_detects_tampered_code(workdir, monkeypatch):
    monkeypatch.setenv('CVM_HOME', os.path.join(workdir, 'cvm-home'))
    src = os.path.join(workdir, 'hello.cin')
    _write_hello(src)
    out = os.path.join(workdir, 'hello.cvm')
    assert cvm.main(['build', src, '-o', out, '--name', 'hello',
                     '--module-version', '1.0.0']) == 0

    tampered = os.path.join(workdir, 'bad.cvm')
    with zipfile.ZipFile(out) as src_zip, zipfile.ZipFile(tampered, 'w') as dst:
        for info in src_zip.infolist():
            data = src_zip.read(info.filename)
            if info.filename == cvm.CODE_MEMBER:
                data = data[:-1] + bytes([data[-1] ^ 0xFF])
            dst.writestr(info, data)
    assert cvm.main(['verify', tampered]) == 1


def test_cvm_build_from_mkn_project(workdir, monkeypatch):
    monkeypatch.setenv('CVM_HOME', os.path.join(workdir, 'cvm-home'))
    monkeypatch.setenv('MKN_HOME', os.path.join(workdir, 'mkn-home'))
    assert mkn.main(['new', 'app', '--dir', os.path.join(workdir, 'app')]) == 0
    project = os.path.join(workdir, 'app')
    out = os.path.join(workdir, 'app.cvm')
    assert cvm.main(['build', project, '-o', out]) == 0
    module = cvm.load_module(out)
    assert module.name == 'app' and module.version == '0.1.0'
    assert cvm.main(['run', out]) == 0


def test_cvm_install_list_and_resolve(workdir, monkeypatch):
    monkeypatch.setenv('CVM_HOME', os.path.join(workdir, 'cvm-home'))
    src = os.path.join(workdir, 'hello.cin')
    _write_hello(src)
    out = os.path.join(workdir, 'hello.cvm')
    assert cvm.main(['build', src, '-o', out, '--name', 'hello',
                     '--module-version', '1.0.0']) == 0
    assert cvm.main(['install', out]) == 0
    assert cvm.main(['list']) == 0
    assert cvm.main(['which', 'hello']) == 0
    runtime = cvm.CVM()
    module = runtime.load('hello')
    assert module.name == 'hello'
    assert runtime.modules()
