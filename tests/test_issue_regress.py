"""2026-10-04 用户压测程序问题反馈的回归测试。

覆盖:
1. 浮点下标 (5.5.3 修复) 的最小用例回归 —— 含 "float 表达式先存入 int
   变量再作下标" 的间接形态, 三条路径一致;
2. 局部大数组撑爆栈: 以前报晦涩的 "address ffff... out of bounds" (SP 被
   prologue 直接减成负地址), 现在报带数值与建议的 Stack overflow;
3. 数组长度非常量的编译期报错附修复提示 (hint);
4. to_int / to_float 显式类型转换内建 (int(x)/float(x) 报错附提示);
5. Heap exhausted 报错附 need/free 与 --mem-size 建议;
6. CLI 程序名之后的裸参数透传给 arg_count()/arg(i);
7. time_us / time_ns 高精度计时内建。
"""

import pytest

from codecin import native as native_mod
from tests.helpers import run_cin_source

needs_native = pytest.mark.skipif(native_mod.get_engine() is None,
                                  reason="native library not built")

# 问题 1 的最小用例 (原样: float 表达式先存 int 变量再作下标)
FLOAT_INDEX_SRC = """
function main() -> int {
    int a[10]
    for (int i = 0; i < 10; i = i + 1) { a[i] = i }

    int n = 10
    int p = 50
    int idx = (n * p) / 100
    return a[idx]
}
"""

@needs_native
def test_float_index_via_int_variable_native():
    assert run_cin_source(FLOAT_INDEX_SRC).regs.read(0) == 5

# 问题 2: 数组长度必须是编译期常量 —— 报错要告诉用户怎么修
def test_nonconstant_array_length_error_has_hint():
    from codecin.cin import CINCompiler
    from codecin.errors import CompilerError
    src = ("int MEM_SIZE = 5000\n"
           "function main() -> int {\n"
           "    int buf[MEM_SIZE]\n"
           "    return 0\n"
           "}\n")
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(src)
    msg = str(ei.value)
    assert "non-constant name 'MEM_SIZE'" in msg
    assert "const int MEM_SIZE" in msg          # 修复方式
    assert "compile-time constants" in msg      # 规则说明

# 问题 5: to_int / to_float 显式转换; int()/float() 报错附提示
CONVERT_SRC = """
function main() -> int {
    int r = to_int(3.9) * 100          // 向零截断 -> 3
    int r2 = to_int(-3.9) + 10         // -> -3 + 10 = 7
    float f = to_float(7) / 2.0        // -> 3.5
    float frac = to_float(3) / to_float(4)
    if (frac < 0.74 && frac > 0.76) { return 999 }
    return r + r2 + to_int(f)          // 300 + 7 + 3 = 310
}
"""

def test_to_int_to_float():
    assert run_cin_source(CONVERT_SRC).regs.read(0) == 310

def test_float_call_error_hints_to_int_builtin():
    from codecin.cin import CINCompiler
    from codecin.errors import CompilerError
    with pytest.raises(CompilerError) as ei:
        CINCompiler().compile_source(
            'function main() -> int { return to_int(float(3) / float(4)) }')
    assert "Unknown function: float" in str(ei.value)
    assert "to_float(x)" in str(ei.value)

# 问题 1 的真因: 局部大数组撑爆栈 (10000 元素 = 80000 字节, 需小内存预算复现)
BIG_LOCAL_SRC = """
function percentile() -> int {
    int latencies[10000]
    for (int i = 0; i < 10000; i++) { latencies[i] = i }
    int n = 10000
    int p = 50
    int idx = (n * p) / 100
    return latencies[idx]
}

function main() -> int {
    return percentile()
}
"""

def test_oversized_local_array_reports_stack_overflow(capsys):
    """以前: 晦涩的 address 0xff...c6f8 out of bounds; 现在: 数值 + 建议。

    v5.9.0 起 CPU.run() 捕获所有执行期异常并置 execution_failed,
    通过控制台输出断言错误信息。默认 mem_size 1GiB 时栈预算足够,
    需显式传小内存才能复现溢出。
    """
    cpu = run_cin_source(BIG_LOCAL_SRC, mem_size=64 * 1024)
    assert cpu.execution_failed is True
    out = capsys.readouterr().out
    assert "Stack overflow" in out
    assert "--mem-size" in out
    assert "frame needs" in out
    assert "0x" in out  # SP / guard 数值
    del cpu

def test_oversized_local_array_ok_with_bigger_mem(capsys):
    """同程序在 --mem-size 256KB 下应正常跑完 (根因是内存预算, 不是下标)。"""
    cpu = run_cin_source(BIG_LOCAL_SRC, mem_size=256 * 1024)
    assert cpu.regs.read(0) == 5000
    assert cpu.execution_failed is False
    _ = capsys

# 问题 4: Heap exhausted 附 need/free 与建议
def test_heap_exhausted_message_has_numbers(capsys):
    src = """
function main() -> int {
    string s = "x"
    for (int i = 0; i < 30; i++) {
        s = s + s
    }
    return 0
}
"""
    cpu = run_cin_source(src)
    assert cpu.execution_failed is True
    out = capsys.readouterr().out
    assert "Heap exhausted" in out
    assert "need " in out
    assert "free " in out
    assert "--mem-size" in out

# 问题 3: CLI 程序名之后的裸参数透传 (arg_count / arg)
ARGS_PROG = """
function main() -> int {
    println(int_to_str(arg_count()))
    println(arg(0))
    println(arg(1))
    return 0
}
"""

@needs_native
def test_cli_direct_args_passthrough(tmp_path, capsys):
    path = tmp_path / "args_cli.cin"
    path.write_text(ARGS_PROG, encoding='utf-8')
    from codecin import cli
    code = cli.main([str(path), 'https://example.com', '24',
                     '--log-level', 'ERROR'])
    assert code == 0
    out = capsys.readouterr().out
    assert "2" in out
    assert "https://example.com" in out
    assert "24" in out

@needs_native
def test_cli_double_dash_still_works(tmp_path, capsys):
    path = tmp_path / "args_dash.cin"
    path.write_text(ARGS_PROG, encoding='utf-8')
    from codecin import cli
    code = cli.main([str(path), '--', 'a b', '-x'])
    assert code == 0
    out = capsys.readouterr().out
    assert "2" in out
    assert "a b" in out
    assert "-x" in out

def test_cli_options_after_file_still_recognized(tmp_path, capsys):
    """向后兼容: 选项写在程序文件之后仍归 CLI (5.8.0 前的习惯)。"""
    prog = 'function main() -> int { println("ok"); return 0 }\n'
    path = tmp_path / "opts_after.cin"
    path.write_text(prog, encoding='utf-8')
    from codecin import cli
    code = cli.main([str(path), '--max-instructions', '1000000',
                     '--log-level', 'ERROR'])
    assert code == 0
    assert 'ok' in capsys.readouterr().out

# 新功能: time_us / time_ns (Unix 纪元, 与 time_ms 同基)
def test_time_us_ns_core_syscalls():
    src = """
function main() -> int {
    int us = time_us()
    int ns = time_ns()
    if (us < 1600000000000000) { return 1 }        // 2020-09 之后的微秒
    if (ns < us * 1000) { return 2 }               // 纳秒 >= 微秒*1000
    if (ns > us * 1000 + 1000000000) { return 3 }  // 时差 < 1 秒
    return 42
}
"""
    assert run_cin_source(src).regs.read(0) == 42

def test_time_us_ns_time_advances():
    src = """
function main() -> int {
    int t0 = time_ns()
    int acc = 0
    for (int i = 0; i < 100000; i++) { acc = acc + i }
    int t1 = time_ns()
    if (t1 < t0) { return 1 }          // 时间前进
    if (t1 == t0) { return 2 }         // 纳秒级不该完全相等
    return 42
}
"""
    assert run_cin_source(src).regs.read(0) == 42
