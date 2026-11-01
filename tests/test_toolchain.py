"""AOT 工具链发现 (GCC/Clang) 与自带工具链安装。"""

import hashlib
import io
import os
import tarfile

import pytest

from codecin import toolchain


def test_target_aliases():
    assert toolchain.get_target('linux-x86_64').name == 'x86_64-linux'
    assert toolchain.get_target('aarch64').name == 'aarch64-linux'
    assert toolchain.get_target('windows').name == 'x86_64-windows'
    with pytest.raises(toolchain.ToolchainError):
        toolchain.get_target('mips-unknown')


def test_matches_tolerates_distro_suffixes():
    win = toolchain.get_target('x86_64-windows')
    assert toolchain.matches(win, 'x86_64-w64-mingw32')
    assert toolchain.matches(win, 'x86_64-w64-windows-gnu')
    assert not toolchain.matches(win, 'x86_64-linux-gnu')

    arm = toolchain.get_target('aarch64-linux')
    assert toolchain.matches(arm, 'aarch64-linux-gnu')
    assert toolchain.matches(arm, 'aarch64-unknown-linux-gnu')
    assert not toolchain.matches(arm, 'x86_64-linux-gnu')

    x64 = toolchain.get_target('x86_64-linux')
    assert toolchain.matches(x64, 'x86_64-pc-linux-gnu')
    assert not toolchain.matches(x64, 'aarch64-linux-gnu')


def test_probe_reports_failure_without_raising():
    target = toolchain.get_target('x86_64-linux')
    info = toolchain.probe(os.path.join('no', 'such', 'compiler'), target)
    assert info.ok is False and not info
    assert '不可用' in info.describe()


def test_find_compiler_prefers_matching_target(monkeypatch, tmp_path):
    """CODECIN_CC_<TARGET> 指向一个"假编译器"时会被采信。"""
    if os.name == 'nt':
        pytest.skip('用 shell 脚本模拟编译器, Windows 上跳过')

    fake = tmp_path / 'fake-gcc'
    fake.write_text('#!/bin/sh\n'
                    'case "$1" in\n'
                    '  -dumpmachine) echo aarch64-linux-gnu ;;\n'
                    '  --version) echo "gcc (fake) 13.0" ;;\n'
                    'esac\n')
    fake.chmod(0o755)
    monkeypatch.setenv('CODECIN_CC_AARCH64_LINUX', str(fake))
    info = toolchain.find_compiler(toolchain.get_target('aarch64-linux'))
    assert info is not None
    assert info.machine == 'aarch64-linux-gnu'
    assert 'gcc (fake)' in info.version


def test_download_from_file_uri(tmp_path):
    src = tmp_path / 'payload.bin'
    payload = b'codecin-toolchain'
    src.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    dest = tmp_path / 'copy.bin'
    toolchain.download(src.as_uri(), str(dest), sha256=digest)
    assert dest.read_bytes() == payload
    with pytest.raises(toolchain.ToolchainError):
        toolchain.download(src.as_uri(), str(dest), sha256='0' * 64)


def test_install_archive_extracts_and_rejects_traversal(tmp_path):
    target = toolchain.get_target('aarch64-linux')
    archive = tmp_path / 'tc.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        data = b'#!/bin/sh\necho hi\n'
        info = tarfile.TarInfo('bin/aarch64-linux-gnu-gcc')
        info.size = len(data)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(data))
    root = tmp_path / 'gcc-home'
    dest = toolchain.install_archive(str(archive), target, root=str(root))
    gcc = os.path.join(dest, 'bin', 'aarch64-linux-gnu-gcc')
    assert os.path.isfile(gcc)
    if os.name != 'nt':
        assert os.access(gcc, os.X_OK)

    evil = tmp_path / 'evil.tar.gz'
    with tarfile.open(evil, 'w:gz') as tar:
        info = tarfile.TarInfo('../escape.sh')
        info.size = 1
        tar.addfile(info, io.BytesIO(b'x'))
    with pytest.raises(toolchain.ToolchainError):
        toolchain.install_archive(str(evil), target, root=str(root))
    assert not os.path.exists(os.path.join(root, 'escape.sh'))


def test_hint_mentions_env_and_mirror():
    text = toolchain.hint(toolchain.get_target('x86_64-windows'))
    assert 'CODECIN_CC_X86_64_WINDOWS' in text
    assert toolchain.MIRROR.rstrip('/') in text
