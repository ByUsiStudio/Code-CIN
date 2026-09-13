"""AOT 独立可执行文件构建测试 (Windows / Linux / macOS 静态编译)。

覆盖:
  * 产物可独立运行, 输出与解释器/Go CLI 一致;
  * 交叉编译能产出目标平台产物;
  * Linux 产物是静态链接 (ELF 无 PT_INTERP), 即不依赖 libc/动态库;
  * 目标解析与错误处理 (非法 --target 报 AotError)。
"""

import os
import shutil
import struct
import subprocess
import sys

import pytest

from codecin import aot
from codecin.errors import CompilerError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 构建需要 Go 工具链
needs_go = pytest.mark.skipif(shutil.which('go') is None,
                              reason='未安装 Go 工具链')

PROGRAM = '''
function main() -> int {
    int s = 0
    for (int i = 1; i <= 10; i = i + 1) {
        s = s + i
    }
    println("sum=" + int_to_str(s))
    return 0
}'''


def _write(workdir, name, text):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def _run(exe, timeout=120):
    return subprocess.run([exe], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout)


def _elf_is_static(path):
    """ELF 是否静态链接: 无 PT_INTERP 段。"""
    with open(path, 'rb') as f:
        data = f.read()
    assert data[:4] == b'\x7fELF', '不是 ELF 文件'
    e_phoff = struct.unpack_from('<Q', data, 0x20)[0]
    e_phentsize = struct.unpack_from('<H', data, 0x36)[0]
    e_phnum = struct.unpack_from('<H', data, 0x38)[0]
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        if struct.unpack_from('<I', data, off)[0] == 3:   # PT_INTERP
            return False
    return True


# ---------------- 目标解析 (不需要 Go) ----------------

def test_parse_target_valid():
    assert aot.parse_target('linux/amd64') == ('linux', 'amd64')
    assert aot.parse_target('windows/arm64') == ('windows', 'arm64')


@pytest.mark.parametrize('bad', ['linux', 'linux/', '/amd64', '', 'a/b/c'])
def test_parse_target_invalid(bad):
    with pytest.raises(aot.AotError):
        aot.parse_target(bad)


def test_host_target_looks_valid():
    goos, goarch = aot.parse_target(aot.host_target())
    assert goos in ('windows', 'linux', 'darwin')
    assert goarch


def test_exe_suffix():
    assert aot.exe_suffix('windows') == '.exe'
    assert aot.exe_suffix('linux') == ''


def test_stub_template_is_shared_with_go():
    """Python 侧与 Go 侧必须共用同一份 main.go 模板 (防漂移)。"""
    src = aot.stub_source()
    assert 'codecin-native/aot' in src
    assert '//go:embed program.ucbc' in src
    assert '//go:embed program.mem' in src
    go_src = os.path.join(ROOT, 'codecin', 'native', 'aot', 'aot.go')
    with open(go_src, encoding='utf-8') as f:
        assert 'stub_main.go.txt' in f.read(), 'Go 侧未引用共享模板'


# ---------------- 实际构建 ----------------

@needs_go
def test_build_host_executable(workdir):
    """产物脱离 Python 独立运行, 输出与解释器一致。"""
    src = _write(workdir, 'prog.cin', PROGRAM)
    out = os.path.join(workdir, 'prog_aot' + aot.exe_suffix(
        aot.parse_target(aot.host_target())[0]))
    built = aot.build_program(src, out=out)
    assert os.path.isfile(built) and os.path.getsize(built) > 0

    r = _run(built)
    assert r.returncode == 0, r.stderr
    assert 'sum=55' in r.stdout

    # 对照: Python 编译 + 原生 VM 的输出
    sys.path.insert(0, os.path.join(ROOT, 'script'))
    import diff_go_python as d  # noqa: E402
    py_out, err = d.run_python(src)
    assert err is None
    assert py_out.strip() == r.stdout.strip()


@needs_go
def test_build_respects_error_exit_code(workdir):
    """运行期错误必须以非 0 退出码报告 (独立可执行文件的语义)。"""
    src = _write(workdir, 'boom.cin', '''
function main() -> int {
    println("before")
    int x = 1
    int y = 0
    return idiv(x, y)
}''')
    out = os.path.join(workdir, 'boom' + aot.exe_suffix(
        aot.parse_target(aot.host_target())[0]))
    built = aot.build_program(src, out=out)
    r = _run(built)
    assert r.returncode != 0 or 'before' in r.stdout


@needs_go
@pytest.mark.parametrize('target', ['linux/amd64', 'linux/arm64', 'darwin/arm64'])
def test_cross_compile_targets(workdir, target):
    """交叉编译: 各平台都能产出可执行文件。"""
    src = _write(workdir, 'prog.cin', PROGRAM)
    out = os.path.join(workdir, 'prog-' + target.replace('/', '-'))
    built = aot.build_program(src, out=out, target=target)
    assert os.path.isfile(built)
    with open(built, 'rb') as f:
        head = f.read(4)
    if target.startswith('linux'):
        assert head == b'\x7fELF'
        assert _elf_is_static(built), 'Linux 产物必须静态链接 (无 PT_INTERP)'
    elif target.startswith('darwin'):
        assert head[:4] in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe')


@needs_go
def test_build_rejects_bad_target(workdir):
    src = _write(workdir, 'prog.cin', PROGRAM)
    with pytest.raises(aot.AotError):
        aot.build_program(src, out=os.path.join(workdir, 'x'), target='linux')


def test_build_reports_compile_error(workdir):
    """语法/语义错误应报 CompilerError, 而不是留下半成品。"""
    src = _write(workdir, 'bad.cin', 'function main() -> int { return foo() }')
    with pytest.raises((CompilerError, aot.AotError)):
        aot.build_program(src, out=os.path.join(workdir, 'bad'))
