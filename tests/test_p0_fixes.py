r"""P0/P1 修复回归测试 (SUGGESTIONS_NEXT.md 第一批落地)。

覆盖点:
  1. enum 最后一个成员无尾逗号 (此前 expect RBRACE 前未跳过换行)
  2. input() 真实现 (此前恒编译成 0): 三条路径经 input_buffer / 标准输入读入
  3. substr / indexof 统一字节语义 (与 strlen / s[i] 一致, 非 ASCII 不再算错)
  4. sqrt(-1) 等数学域错误: 解释器与 Go 一致返回 NaN (不再抛 CPython 异常)
  5. --sandbox 真实现: 拦截宿主能力 SYS, 并强制回退解释器路径
  6. 条件断点白名单求值: 拒绝 Call/Attribute/Subscript (远程端口不可执行任意代码)
  7. 汇编器非法数字字面量报错 (不再静默变 0)

断言约定与 tests/test_cin_syntax_ext.py 一致。
"""

import pytest

from codecin import CPU, Config, native
from codecin.assembler import _eval_expr
from codecin.cin import CINCompiler
from codecin.debugger import _eval_breakpoint_condition
from codecin.errors import CompilerError

needs_native = pytest.mark.skipif(
    native.get_engine() is None, reason="native Go library not built")

PATHS = (False, True)
PATH_IDS = ('interp', 'native')


def build_cpu(src: str, use_native: bool, **cfg_kwargs) -> CPU:
    res = CINCompiler().compile_source(src)
    cfg = Config(interactive_mode=False, log_level='ERROR',
                 use_native=use_native, **cfg_kwargs)
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


def run_cin(src: str, use_native: bool, **cfg_kwargs):
    """编译并运行, 返回 (x0, stdout, native_used)。"""
    cpu = build_cpu(src, use_native, **cfg_kwargs)
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


@pytest.mark.parametrize('use_native', PATHS, ids=PATH_IDS)
def test_enum_last_member_without_trailing_comma(use_native):
    # GREEN=5, MASK=(1<<3)|1=9 -> 509
    x0, _, _ = run_cin(ENUM_NO_TRAILING_COMMA, use_native)
    assert x0 == 509


# ====================================================================
# 2. input() 真实现
# ====================================================================

INPUT_SRC = r'''
function main() -> int {
    int a = input()
    return a
}'''


@pytest.mark.parametrize('use_native', PATHS, ids=PATH_IDS)
def test_input_reads_stdin_line(use_native):
    res = CINCompiler().compile_source(INPUT_SRC)
    op_names = {ins[0] for ins in res.instructions}
    assert 'IN' in op_names, "input() 必须编译成 IN 指令 (此前恒为 MOV x0, 0)"
    cpu = build_cpu(INPUT_SRC, use_native)
    cpu.input_buffer = "42\n"
    cpu.run()
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 42


@pytest.mark.parametrize('use_native', PATHS, ids=PATH_IDS)
def test_input_invalid_line_returns_zero(use_native):
    cpu = build_cpu(INPUT_SRC, use_native)
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


@pytest.mark.parametrize('use_native', PATHS, ids=PATH_IDS)
def test_substr_indexof_byte_semantics(tmp_path, use_native):
    # "héllo" 的 UTF-8 字节: h(0) é(1..2) l(3) l(4) o(5)
    # indexof("lo") = 4 (字节索引); substr(4, 2) = "lo" -> 400 + 1 = 401
    path = tmp_path / 'str_bytes.cin'
    path.write_text(STR_BYTES_SRC, encoding='utf-8')
    cpu = run_cin_file(str(path), use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 401


def test_substr_byte_semantics_interp_direct(tmp_path):
    """非 ASCII 前缀下 substr 按字节取, 与 strlen 一致。"""
    src = '''
import "str.cin"
function main() -> string {
    string s = "héllo"
    return substr(s, strlen(s) - 2, 2)
}'''
    path = tmp_path / 'str_sub.cin'
    path.write_text(src, encoding='utf-8')
    cpu = run_cin_file(str(path), use_native=False)
    assert not cpu.execution_failed
    # 返回的字符串指针 -> 读内存
    out = cpu.memory.read_string(cpu.regs.read(0))
    assert out == "lo"


# ====================================================================
# 4. sqrt / pow 数学域: NaN 对齐
# ====================================================================

NAN_BITS = 0x7FF8000000000000

SQRT_SRC = r'''
function main() -> float {
    return sqrt(-1.0)
}'''


@pytest.mark.parametrize('use_native', PATHS, ids=PATH_IDS)
def test_sqrt_negative_returns_nan(use_native):
    x0, _, _ = run_cin(SQRT_SRC, use_native)
    assert x0 == NAN_BITS, "sqrt(-1) 必须与 Go 路径一致返回 NaN"


POW_SRC = r'''
function main() -> float {
    return pow(-2.0, 0.5)
}'''


@pytest.mark.parametrize('use_native', PATHS, ids=PATH_IDS)
def test_pow_negative_base_fractional_exp_nan(use_native):
    x0, _, _ = run_cin(POW_SRC, use_native)
    assert x0 == NAN_BITS, "pow(负底数, 非整数指数) 必须与 Go 一致返回 NaN"


# ====================================================================
# 5. --sandbox
# ====================================================================

SANDBOX_SRC = r'''
function main() -> int {
    return exec("echo SANDBOX_ESCAPED")
}'''


def test_sandbox_blocks_host_syscall():
    cpu = build_cpu(SANDBOX_SRC, False, sandbox_mode=True)
    cpu.run()
    assert cpu.execution_failed


@needs_native
def test_sandbox_forces_interpreter_path():
    """沙箱模式下必须放弃原生路径 (宿主调用拦截只在解释器侧生效)。"""
    cpu = build_cpu(SANDBOX_SRC, True, sandbox_mode=True)
    assert cpu._try_native_run() is None


def test_sandbox_allows_pure_builtins():
    """沙箱只拦宿主能力 (>= AUDIOPLAY), 数学/字符串/输出类不受影响。"""
    src = r'''
function main() -> int {
    string s = "ok"
    println(strlen(s))
    return 7
}'''
    x0, out, _ = run_cin(src, False, sandbox_mode=True)
    assert x0 == 7
    assert "2" in out


# ====================================================================
# 6. 条件断点白名单求值
# ====================================================================

def test_breakpoint_condition_allows_register_expressions():
    ns = {'x0': 5, 'x1': 0, 'x32': 0x7FFF_FFF0, 'sp': 0x7FFF_FFF0,
          'pc': 16, 'N': False, 'Z': True, 'C': False, 'V': False}
    assert _eval_breakpoint_condition('x0 == 5', ns) is True
    assert _eval_breakpoint_condition('x0 > 3 and not N', ns) is True
    assert _eval_breakpoint_condition('sp == x32', ns) is True
    assert _eval_breakpoint_condition('(x0 + 1) * 2 == 12', ns) is True
    assert _eval_breakpoint_condition('x0 == 6', ns) is False


@pytest.mark.parametrize('bad', [
    "__import__('os').system('id')",
    "x0.__class__",
    "(lambda: 1)()",
    "open('/etc/passwd')",
    "x0.bit_length()",
    "[x for x in range(3)]",
    "",
])
def test_breakpoint_condition_rejects_executable_syntax(bad):
    ns = {'x0': 5, 'x1': 0, 'sp': 0, 'pc': 0,
          'N': False, 'Z': False, 'C': False, 'V': False}
    with pytest.raises(ValueError):
        _eval_breakpoint_condition(bad, ns)


# ====================================================================
# 7. 汇编器: 非法数字字面量不再静默变 0
# ====================================================================

def test_assembler_invalid_numeric_literal_fails():
    assert _eval_expr('0x_', {}) is None      # 此前静默求值为 0
    assert _eval_expr('0b__', {}) is None
    assert _eval_expr('0x1F + 0b1010', {}) == 31 + 10
    assert _eval_expr('(2 + 3) * 4 - 10 % 3', {}) == 19
