"""新功能 A1-A3: CIN assert/越界检查、--seed 确定性、--disasm 反汇编。"""

import os

import pytest

from codecin import crom
from codecin.disasm import _extract_from_cpusa, disassemble_bytes, disassemble_file
from codecin.errors import CPUSimulatorError
from codecin.native import decode_program
from tests.helpers import asm_program, run_cin_source

# ---------------- A1: assert ----------------

def test_assert_passes_and_continues():
    src = """
function main() -> int {
    int x = 2
    assert(x * 2 == 4)
    assert(x > 1, "x 应大于 1")
    return 7
}"""
    assert run_cin_source(src).regs.read(0) == 7


def test_assert_failure_aborts_with_message(capsys):
    src = "function main() -> int { assert(1 == 2, \"boom\")\nreturn 5 }\n"
    run_cin_source(src)
    out = capsys.readouterr().out
    assert 'Runtime abort' in out and 'boom' in out


def test_assert_without_message_aborts(capsys):
    src = "function main() -> int { assert(false)\nreturn 1 }\n"
    run_cin_source(src)
    out = capsys.readouterr().out
    assert 'assertion failed' in out


# ---------------- A1: --bounds-check ----------------

BOUNDS_OK = """
function main() -> int {
    int a[4]
    a[3] = 7
    return a[3]
}
"""


def test_bounds_check_ok():
    assert run_cin_source(BOUNDS_OK, bounds_check=True).regs.read(0) == 7


def test_bounds_check_negative_index_aborts(capsys):
    src = "function main() -> int { int a[4]\na[-1] = 1\nreturn 0 }\n"
    run_cin_source(src, bounds_check=True)
    assert 'negative array index' in capsys.readouterr().out


def test_bounds_check_too_high_index_aborts(capsys):
    src = "function main() -> int { int a[4]\na[4] = 1\nreturn 0 }\n"
    run_cin_source(src, bounds_check=True)
    assert 'length' in capsys.readouterr().out


def test_bounds_check_two_dimensions():
    src = """
function main() -> int {
    int m[2][3]
    m[1][2] = 9
    return m[1][2]
}"""
    assert run_cin_source(src, bounds_check=True).regs.read(0) == 9


# ---------------- A2: --seed ----------------

RAND_SRC = "function main() -> int { int a = rand()\nreturn a }\n"


def test_seed_is_deterministic():
    def val(seed):
        return run_cin_source(RAND_SRC, seed=seed).regs.read(0)
    assert val(42) == val(42)
    assert val(42) != val(7)


# ---------------- A3: --disasm ----------------

ASM_SRC = """
.text
main:
    mov x0, 5
    addi x0, x0, 3
    halt
"""


def _bin_path(workdir, name='p.asm'):
    cpu = asm_program(ASM_SRC, workdir, name=name)
    out = os.path.join(workdir, 'p.bin')
    crom.save_bin(cpu, out)
    return cpu, out


def test_disasm_file_contains_mnemonics(workdir):
    _cpu, path = _bin_path(workdir)
    lines = disassemble_file(path)
    text = "\n".join(lines)
    assert 'MOV' in text and 'ADDI' in text and 'HALT' in text
    assert 'CPUSA binary' in lines[0]


def test_disasm_roundtrip_matches_instructions(workdir):
    cpu, path = _bin_path(workdir)
    with open(path, 'rb') as f:
        data = f.read()
    bc, _mem, _e, _sp = _extract_from_cpusa(data)
    instructions, _ = decode_program(bc)
    assert [tuple(i) for i in instructions] == \
        [tuple(i) for i in cpu.instructions]


def test_disasm_rejects_wrong_magic():
    with pytest.raises(CPUSimulatorError):
        disassemble_bytes(b'NOPE' + b'\x00' * 32)
