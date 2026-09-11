"""CIN 系统原生交互 / Termux API 内建测试 (依赖 Go 原生库, 无则跳过)。"""

import os

import pytest

from codecin import native
from tests.helpers import run_cin_source

needs_native = pytest.mark.skipif(
    native.get_engine() is None, reason="native Go library not built")


@needs_native
def test_file_io_roundtrip(workdir):
    p = os.path.join(workdir, 'io_test.txt').replace('\\', '/')
    src = f'''
function main() -> int {{
    if (file_write("{p}", "abc") != 0) {{ return 1 }}
    if (file_exists("{p}") != 1) {{ return 2 }}
    if (file_size("{p}") != 3) {{ return 3 }}
    if (strcmp(file_read("{p}"), "abc") != 0) {{ return 4 }}
    if (file_append("{p}", "de") != 0) {{ return 5 }}
    if (file_size("{p}") != 5) {{ return 6 }}
    if (file_delete("{p}") != 0) {{ return 7 }}
    if (file_exists("{p}") != 0) {{ return 8 }}
    return 0
}}'''
    assert run_cin_source(src, use_native=True).regs.read(0) == 0


@needs_native
def test_mkdir_and_dir_list(workdir):
    d = os.path.join(workdir, 'subdir').replace('\\', '/')
    src = f'''
function main() -> int {{
    if (mkdir("{d}") != 0) {{ return 1 }}
    if (file_exists("{d}") != 1) {{ return 2 }}
    return 0
}}'''
    assert run_cin_source(src, use_native=True).regs.read(0) == 0
    assert os.path.isdir(os.path.join(workdir, 'subdir'))


@needs_native
def test_system_info_and_env():
    src = '''
function main() -> int {
    if (strlen(os_name()) == 0) { return 1 }
    if (strlen(cwd()) == 0) { return 2 }
    if (strlen(hostname()) == 0) { return 3 }
    if (strlen(home_dir()) == 0) { return 4 }
    if (setenv("CC_ENV_TEST", "hello") != 0) { return 5 }
    if (strcmp(getenv("CC_ENV_TEST"), "hello") != 0) { return 6 }
    return 0
}'''
    assert run_cin_source(src, use_native=True).regs.read(0) == 0


@needs_native
def test_exec_and_output():
    src = '''
function main() -> int {
    int rc = exec("echo hi")
    if (rc != 0) { return 1 }
    string out = exec_output("echo codecin_ok")
    if (indexof(out, "codecin_ok") < 0) { return 2 }
    return 0
}'''
    assert run_cin_source(src, use_native=True).regs.read(0) == 0


@needs_native
def test_termux_available_returns_flag():
    src = '''
function main() -> int {
    int a = termux_available()
    if (a == 0) { return 10 }
    if (a == 1) { return 20 }
    return 30
}'''
    assert run_cin_source(src, use_native=True).regs.read(0) in (10, 20)


def test_system_builtins_compile_without_native():
    """含系统内建的源码应能编译 (解释执行会报错, 仅验证编译)。"""
    from codecin.cin import CINCompiler
    src = '''
function main() -> int {
    file_write("x", "y")
    string s = file_read("x")
    int a = exec("true")
    return strlen(s) + a
}'''
    res = CINCompiler().compile_source(src)
    assert res is not None
    assert any(i[0] == 'SYS' for i in res.instructions)
