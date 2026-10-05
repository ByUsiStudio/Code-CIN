r"""P0/P1 修复回归测试 (SUGGESTIONS_NEXT.md 第一批落地)。

覆盖点:
  1. enum 最后一个成员无尾逗号 (此前 expect RBRACE 前未跳过换行)
  2. input() 真实现 (此前恒编译成 0): 经 input_buffer / 标准输入读入
  3. substr / indexof 统一字节语义 (与 strlen / s[i] 一致, 非 ASCII 不再算错)
  4. sqrt(-1) 等数学域错误: 原生引擎返回 NaN (不再抛 CPython 异常)
  5. --sandbox 新语义: Go 引擎侧拦截宿主能力 SYS, 引擎报错 ->
     CPU.run 置 execution_failed=True
  6. 汇编器非法数字字面量报错 (不再静默变 0)

断言约定与 tests/test_cin_syntax_ext.py 一致。
"""

import os

import pytest

from codecin import CPU, Config, native
from codecin.assembler import _eval_expr
from codecin.cin import CINCompiler
from tests.helpers import run_cin_file

needs_native = pytest.mark.skipif(
    native.get_engine() is None, reason="native Go library not built")


def build_cpu(src: str, **cfg_kwargs) -> CPU:
    res = CINCompiler().compile_source(src)
    cfg = Config(log_level='ERROR', **cfg_kwargs)
    cpu = CPU(cfg)
    cpu.instructions = res.instructions
    cpu.labels = res.labels
    cpu.data_labels = res.data_labels
    for addr, data in res.data_writes:
        cpu.memory.write_block(addr, data)
    cpu.entry_pc = 0
    cpu.pc = 0
    cpu._capture_output = True
    return cpu


def run_cin(src: str, **cfg_kwargs):
    """编译并运行, 返回 (x0, stdout, native_used)。"""
    cpu = build_cpu(src, **cfg_kwargs)
    cpu.run()
    assert not cpu.execution_failed, '程序执行失败 (ExecutionError 被 CPU.run 吞掉)'
    return cpu.regs.read(0), ''.join(cpu.output_buffer), cpu.native_used


# ====================================================================
# 1. enum: 最后一个成员无尾逗号
# ====================================================================

ENUM_NO_TRAILING_COMMA = r'''
enum Color {
    RED,
    GREEN = 5,
    MASK = (1 << 3) | 1
}
function main() -> int {
    return GREEN * 100 + MASK
}'''


def test_enum_last_member_without_trailing_comma():
    # GREEN=5, MASK=(1<<3)|1=9 -> 509
    x0, _, _ = run_cin(ENUM_NO_TRAILING_COMMA)
    assert x0 == 509


# ====================================================================
# 2. input() 真实现
# ====================================================================

INPUT_SRC = r'''
function main() -> int {
    int a = input()
    return a
}'''


def test_input_reads_stdin_line():
    res = CINCompiler().compile_source(INPUT_SRC)
    op_names = {ins[0] for ins in res.instructions}
    assert 'IN' in op_names, "input() 必须编译成 IN 指令 (此前恒为 MOV x0, 0)"
    cpu = build_cpu(INPUT_SRC)
    cpu.input_buffer = "42\n"
    cpu.run()
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 42


def test_input_invalid_line_returns_zero():
    cpu = build_cpu(INPUT_SRC)
    cpu.input_buffer = "not-a-number\n"
    cpu.run()
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


# ====================================================================
# 3. substr / indexof 字节语义
# ====================================================================

STR_BYTES_SRC = '''
import "str.cin"
function main() -> int {
    string s = "héllo"
    return indexof(s, "lo") * 100 + (strcmp(substr(s, 4, 2), "lo") == 0 ? 1 : 0)
}'''


def test_substr_indexof_byte_semantics(workdir):
    # "héllo" 的 UTF-8 字节: h(0) é(1..2) l(3) l(4) o(5)
    # indexof("lo") = 4 (字节索引); substr(4, 2) = "lo" -> 400 + 1 = 401
    # 注意: 用 workspace 内的 workdir 夹具而不是 tmp_path —— 受限沙箱下
    # pytest 的 tmp_path 会在 setup 阶段就 PermissionError。
    path = os.path.join(workdir, 'str_bytes.cin')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(STR_BYTES_SRC)
    cpu = run_cin_file(path)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 401


def test_substr_byte_semantics_direct(workdir):
    """非 ASCII 前缀下 substr 按字节取, 与 strlen 一致。"""
    src = '''
import "str.cin"
function main() -> string {
    string s = "héllo"
    return substr(s, strlen(s) - 2, 2)
}'''
    path = os.path.join(workdir, 'str_sub.cin')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    cpu = run_cin_file(path)
    assert not cpu.execution_failed
    # 返回的字符串指针 -> 读内存
    out = cpu.memory.read_string(cpu.regs.read(0))
    assert out == "lo"


# ====================================================================
# 4. sqrt / pow 数学域: NaN 对齐
# ====================================================================

def _is_nan_bits(v: int) -> bool:
    """IEEE-754 NaN 判定: 指数位全 1 且尾数非 0。

    不断言精确位模式 —— Python 的 float('nan') 为 0x7FF8000000000000,
    而 x86 硬件 SQRTSD 返回带符号位的 0xFFF8000000000000, IEEE-754
    允许同一 NaN 值有多个位模式。
    """
    return ((v & 0x7FF0000000000000) == 0x7FF0000000000000
            and (v & 0x000FFFFFFFFFFFFF) != 0)


SQRT_SRC = r'''
function main() -> float {
    return sqrt(-1.0)
}'''


def test_sqrt_negative_returns_nan():
    x0, _, _ = run_cin(SQRT_SRC)
    assert _is_nan_bits(x0), "sqrt(-1) 必须与原生引擎一致返回 NaN"


POW_SRC = r'''
function main() -> float {
    return pow(-2.0, 0.5)
}'''


def test_pow_negative_base_fractional_exp_nan():
    x0, _, _ = run_cin(POW_SRC)
    assert _is_nan_bits(x0), "pow(负底数, 非整数指数) 必须与原生引擎一致返回 NaN"


# ====================================================================
# 5. --sandbox (v5.9.0: Go 引擎侧拦截宿主能力 SYS)
# ====================================================================

SANDBOX_SRC = r'''
function main() -> int {
    return exec("echo SANDBOX_ESCAPED")
}'''


def test_sandbox_blocks_host_syscall():
    """沙箱下宿主能力 SYS 被引擎拒绝 -> 引擎报错 -> execution_failed=True。"""
    cpu = build_cpu(SANDBOX_SRC, sandbox_mode=True)
    cpu.run()
    assert cpu.execution_failed


def test_sandbox_allows_pure_builtins():
    """沙箱只拦宿主能力, 数学/字符串/输出类内建不受影响。"""
    src = r'''
function main() -> int {
    string s = "ok"
    println(strlen(s))
    return 7
}'''
    x0, out, _ = run_cin(src, sandbox_mode=True)
    assert x0 == 7
    assert "2" in out


# ====================================================================
# 6. 汇编器: 非法数字字面量不再静默变 0
# ====================================================================

def test_assembler_invalid_numeric_literal_fails():
    assert _eval_expr('0x_', {}) is None      # 此前静默求值为 0
    assert _eval_expr('0b__', {}) is None
    assert _eval_expr('0x1F + 0b1010', {}) == 31 + 10
    assert _eval_expr('(2 + 3) * 4 - 10 % 3', {}) == 19
