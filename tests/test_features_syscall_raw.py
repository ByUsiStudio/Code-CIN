"""8.1.x 回归: 通用系统调用 ``syscall()`` / ``syscall_str()``。

8.1.0 把 SYS 从「只能写死在语言内建里」放开成运行期分派, 但当时没有任何
回归用例: 参数布置 (功能号进 x7、实参 x0-x4、缺省补 0)、字符串返回、6 实参
上限, 以及未知功能号的报错文案都没有被锁住。本文件把这些约束固定下来。

功能号常量取自 ``codecin/native/engine/isa_gen.go`` 的 ``sys*`` 定义, 与
``isa.py`` 的 ``Syscall`` 一致。
"""

import pytest

from codecin.errors import CompilerError
from tests.helpers import run_cin_source

SYS_ABS = 2
SYS_STRLEN = 16
SYS_STRCMP = 17
SYS_ITOA = 22
SYS_TOUPPER = 30


def _run(src: str, **cfg):
    cpu = run_cin_source(src, **cfg)
    assert not cpu.execution_failed, 'syscall 用例不应执行失败'
    return cpu


def test_syscall_dispatches_on_runtime_id():
    """syscall(nr, ...) 的返回值来自运行期功能号, 与同名内建一致。"""
    src = """
function main() -> int {
    int a = syscall(2, 0 - 5)      // abs(-5)
    int b = syscall(2, 7)          // abs(7)
    int c = abs(0 - 5)             // 内建路径
    return a * 100 + b * 10 + c
}"""
    # 5*100 + 7*10 + 5 = 575
    assert _run(src).regs.read(0) == 575


def test_syscall_args_land_in_x0_x1():
    """实参依次进 x0/x1, 功能号另放在 x7, 不与实参抢寄存器。"""
    src = """
function main() -> int {
    int n = syscall(16, "hello")          // strlen -> 5
    int eq = syscall(17, "abc", "abc")    // strcmp -> 0
    return n * 10 + eq
}"""
    assert _run(src).regs.read(0) == 50


def test_syscall_missing_args_are_zero_filled():
    """未给出的实参补 0 (abs(0) == 0), 不留上一次调用的残值。"""
    src = """
function main() -> int {
    int dirty = syscall(2, 0 - 9)   // 先把 x0 弄脏
    int zero = syscall(2)           // 不带实参 -> abs(0)
    return dirty * 10 + zero
}"""
    assert _run(src).regs.read(0) == 90


def test_syscall_str_uses_x0_as_new_heap_string():
    """``syscall_str`` 把返回的 x0 当字符串 (新堆指针) 使用。"""
    src = """
function main() -> int {
    string it = syscall_str(22, 42)      // itoa(42) -> "42"
    string up = syscall_str(30, "abc")   // upper   -> "ABC"
    if (strcmp(it, "42") != 0) { return -1 }
    if (strcmp(up, "ABC") != 0) { return -2 }
    return strlen(it) * 10 + strlen(up)
}"""
    # "42" 两字节, "ABC" 三字节 -> 20 + 3
    assert _run(src).regs.read(0) == 2 * 10 + 3


def test_syscall_rejects_more_than_six_args():
    """上限是功能号 + 5 个实参; 第 7 个参数在编译期报错。"""
    src = 'function main() -> int { return syscall(1, 1, 2, 3, 4, 5, 6) }'
    with pytest.raises(CompilerError) as info:
        run_cin_source(src)
    assert 'syscall() accepts at most 6 arguments' in str(info.value)


def test_syscall_str_rejects_more_than_six_args():
    src = 'function main() -> int { return strlen(syscall_str(1, 1, 2, 3, 4, 5, 6)) }'
    with pytest.raises(CompilerError) as info:
        run_cin_source(src)
    assert 'at most 6 arguments' in str(info.value)


def test_unknown_syscall_id_reports_runtime_id(capsys):
    """越界功能号报错必须报**运行期功能号**, 不能报寄存器编号。

    8.1.0 的实现把 SYS 操作数的 value 直接拼进提示, 而寄存器形式下那是寄存器
    编号 (x7 -> 7), 于是 ``syscall(999)`` 报成 ``unsupported SYS call id: 7``。
    """
    cpu = run_cin_source('function main() -> int { return syscall(999) }')
    assert cpu.execution_failed is True
    assert 'unsupported SYS call id: 999' in capsys.readouterr().out


def test_unknown_syscall_str_id_reports_runtime_id(capsys):
    src = 'function main() -> int { return strlen(syscall_str(999)) }'
    cpu = run_cin_source(src)
    assert cpu.execution_failed is True
    assert 'unsupported SYS call id: 999' in capsys.readouterr().out


def test_supported_id_above_previous_immediate_range():
    """SYS 159 仍在白名单内 (旧实现只认立即数时靠 imm 编码, 这里确认运行期分派
    没有把高位功能号截断)。"""
    src = 'function main() -> int { return syscall(159) }'
    cpu = run_cin_source(src)
    assert not cpu.execution_failed
