"""``.sdgcc`` 容器: 格式、BIN v3 兼容、单文件/多文件模式。"""

import os
import zipfile

import pytest

from codecin import cli, crom, cvm, disasm, mkn, sdgcc
from codecin.config import Config
from codecin.cpu import CPU

HELLO = ('function main() -> int {\n'
         '    println("hello sdgcc")\n'
         '    return 0\n'
         '}\n')


def _hello(workdir, name='hello.cin'):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(HELLO)
    return path


def _cpu(path):
    return CPU(Config(log_level='ERROR'), path)


# ==================== container ====================


def test_roundtrip_preserves_bin_bytes(workdir):
    cpu = _cpu(_hello(workdir))
    bin_bytes = crom.dump_bin(cpu)
    data = sdgcc.build(bin_bytes, multi_file=True, target='linux-x86_64',
                       symbols=[(0, 'main'), (7, 'main_body')],
                       sources=['a.cin', 'b.cin'])
    container = sdgcc.parse(data)
    assert container.code == bin_bytes          # CODE 段逐字节等于旧 .bin
    assert container.multi_file is True
    assert container.target == 'linux-x86_64'
    assert container.symbols == [(0, 'main'), (7, 'main_body')]
    assert container.sources == ['a.cin', 'b.cin']
    assert container.meta['sdgcc']['format'] == sdgcc.SDGCC_VERSION


def test_compression_roundtrip(workdir):
    cpu = _cpu(_hello(workdir))
    code = crom.dump_bin(cpu)
    symbols = [(i, f'sym_{i}_{"x" * 40}') for i in range(40)]
    data = sdgcc.build(code, multi_file=True, symbols=symbols,
                       sources=['a.cin'], compress=True)
    container = sdgcc.parse(data)
    assert container.version == sdgcc.SDGCC_VERSION_COMPRESSED
    assert container.code == code                     # CODE 段永不压缩
    assert container.symbols == symbols
    assert container.entries['CODE'][3] is False
    assert container.entries['SYMS'][3] is True
    assert container.flags & sdgcc.FLAG_COMPRESSED


def test_describe_and_dump(workdir):
    cpu = _cpu(_hello(workdir))
    data = sdgcc.build(crom.dump_bin(cpu), sources=['a.cin'], compress=True)
    container = sdgcc.parse(data)
    info = sdgcc.describe(container)
    names = [sec['name'] for sec in info['sections']]
    assert names[:2] == ['CODE', 'META']
    assert info['version'] == sdgcc.SDGCC_VERSION_COMPRESSED
    out = os.path.join(workdir, 'parts')
    paths = sdgcc.dump(container, out)
    assert os.path.isfile(os.path.join(out, 'program.bin'))
    assert os.path.isfile(os.path.join(out, 'meta.toml'))
    with open(os.path.join(out, 'program.bin'), 'rb') as f:
        assert f.read() == container.code
    assert all(os.path.isabs(p) for p in paths)


def test_single_file_flag(workdir):
    cpu = _cpu(_hello(workdir))
    data = sdgcc.build(crom.dump_bin(cpu), multi_file=False)
    container = sdgcc.parse(data)
    assert container.multi_file is False
    assert container.meta['sdgcc']['mode'] == 'single'
    assert container.symbols == [] and container.sources == []


def test_legacy_bin_is_valid_input(workdir):
    bin_path = os.path.join(workdir, 'legacy.bin')
    crom.save_bin(_cpu(_hello(workdir)), bin_path)
    with open(bin_path, 'rb') as f:
        raw = f.read()
    assert not sdgcc.is_sdgcc(raw)
    code, container = sdgcc.unwrap(raw)
    assert code == raw and container is None
    assert sdgcc.read_code(bin_path) == raw


def test_rejects_corruption(workdir):
    cpu = _cpu(_hello(workdir))
    data = sdgcc.build(crom.dump_bin(cpu))
    with pytest.raises(sdgcc.SdgccError):
        sdgcc.parse(data[:-8])                  # 截断
    bad = bytearray(data)
    bad[0:5] = b'XXXXX'
    with pytest.raises(sdgcc.SdgccError):
        sdgcc.parse(bytes(bad))
    with pytest.raises(sdgcc.SdgccError):
        sdgcc.build(b'not-a-bin')


def test_cpu_runs_sdgcc(workdir, capsys):
    path = _hello(workdir)
    out = os.path.join(workdir, 'hello.sdgcc')
    sdgcc.save_program(_cpu(path), out, multi_file=False, sources=[path])
    assert cli.main([out, '--log-level', 'ERROR']) == 0
    assert 'hello sdgcc' in capsys.readouterr().out
    lines = disasm.disassemble_file(out)
    assert any('CPUSA binary' in line for line in lines)


def test_compile_only_defaults_to_sdgcc(workdir):
    src = _hello(workdir)
    assert cli.main([src, '--compile-only']) == 0
    produced = os.path.join(workdir, 'hello.sdgcc')
    assert os.path.isfile(produced)
    container = sdgcc.load(produced)
    assert container.meta['sdgcc']['mode'] == 'single'
    assert CPU(Config(log_level='ERROR'), produced)


# ==================== CVM ====================


def test_cvm_module_carries_sdgcc(workdir, monkeypatch):
    monkeypatch.setenv('CVM_HOME', os.path.join(workdir, 'cvm-home'))
    src = _hello(workdir)
    out = os.path.join(workdir, 'hello.cvm')
    assert cvm.main(['build', src, '-o', out, '--name', 'hello',
                     '--module-version', '1.0.0']) == 0
    with zipfile.ZipFile(out) as zf:
        assert cvm.CODE_MEMBER in zf.namelist()
        payload = zf.read(cvm.CODE_MEMBER)
    assert sdgcc.is_sdgcc(payload)
    assert cvm.main(['run', out]) == 0


def test_cvm_accepts_legacy_bin_input(workdir, monkeypatch):
    monkeypatch.setenv('CVM_HOME', os.path.join(workdir, 'cvm-home'))
    bin_path = os.path.join(workdir, 'legacy.bin')
    crom.save_bin(_cpu(_hello(workdir)), bin_path)
    out = os.path.join(workdir, 'legacy.cvm')
    assert cvm.main(['build', bin_path, '-o', out, '--name', 'legacy',
                     '--module-version', '1.0.0']) == 0
    with zipfile.ZipFile(out) as zf:
        payload = zf.read(cvm.CODE_MEMBER)
    assert sdgcc.is_sdgcc(payload)              # 输入旧 .bin, 输出被封装
    assert cvm.main(['run', out]) == 0


def _project(workdir):
    root = os.path.join(workdir, 'proj')
    os.makedirs(os.path.join(root, 'src'), exist_ok=True)
    with open(os.path.join(root, 'mkn.toml'), 'w', encoding='utf-8') as f:
        f.write('[project]\nname = "proj"\nversion = "0.1.0"\n'
                'entry = "src/main.cin"\n')
    with open(os.path.join(root, 'src', 'main.cin'), 'w',
              encoding='utf-8') as f:
        f.write('import "./util.cin"\n\nfunction main() -> int {\n'
                '    println(util_text())\n    return 0\n}\n')
    with open(os.path.join(root, 'src', 'util.cin'), 'w',
              encoding='utf-8') as f:
        f.write('function util_text() -> string {\n'
                '    return "multi-file"\n}\n')
    return root


def test_cvm_project_defaults_to_multi_file(workdir, monkeypatch):
    monkeypatch.setenv('CVM_HOME', os.path.join(workdir, 'cvm-home'))
    monkeypatch.setenv('MKN_HOME', os.path.join(workdir, 'mkn-home'))
    project = _project(workdir)
    out = os.path.join(workdir, 'proj.cvm')
    assert cvm.main(['build', project, '-o', out]) == 0
    module = cvm.load_module(out)
    assert module.manifest['build']['mode'] == 'multi'
    container = sdgcc.parse(module.code)
    assert container.multi_file
    assert len(container.sources) >= 2         # 入口 + import 闭包
    assert cvm.main(['run', out]) == 0


def test_cvm_project_single_file_mode(workdir, monkeypatch):
    monkeypatch.setenv('CVM_HOME', os.path.join(workdir, 'cvm-home'))
    monkeypatch.setenv('MKN_HOME', os.path.join(workdir, 'mkn-home'))
    project = _project(workdir)
    out = os.path.join(workdir, 'proj-single.cvm')
    assert cvm.main(['build', project, '-o', out, '--single']) == 0
    module = cvm.load_module(out)
    assert module.manifest['build']['mode'] == 'single'
    container = sdgcc.parse(module.code)
    assert container.multi_file is False
    assert [os.path.basename(p) for p in container.sources] == ['main.cin']
    assert cvm.main(['run', out]) == 0


def test_mkn_build_emits_sdgcc(workdir, monkeypatch):
    monkeypatch.setenv('MKN_HOME', os.path.join(workdir, 'mkn-home'))
    project = _project(workdir)
    out = mkn.build_project(mkn.load_project(project))
    assert out.endswith('.sdgcc')
    container = sdgcc.load(out)
    assert container.multi_file
    assert len(container.sources) >= 2
    assert container.meta['build']['entry'] == 'src/main.cin'
    # 显式 -o *.bin 仍产出旧格式
    legacy = os.path.join(workdir, 'legacy-out.bin')
    out2 = mkn.build_project(mkn.load_project(project), output=legacy)
    with open(out2, 'rb') as f:
        assert not sdgcc.is_sdgcc(f.read())
