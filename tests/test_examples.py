"""examples/ 示例程序冒烟: 每个示例解释路径完整运行且结果确定。"""

import os

import pytest

from tests.helpers import asm_program, run_cin_file

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'examples')

# 每个示例期望的返回值 (X0 或指定寄存器)
CIN_EXPECTED = {
    'control_flow.cin': 24,     # sum=20(偶数至20 break) + d=4
    'literals_types.cin': 77,   # 31+13+15+6+6+4+2
    'modules_demo.cin': 12,     # import lib/math.cin + lib/str.cin
    'bitwise_builtins.cin': 256,  # 位运算/整除/下标/新内建各项之和
    'stdlib_demo.cin': 0,       # 官方标准库断言全部通过 (t_report 返回失败数)
}
ASM_EXPECTED = {
    'asm_constants.asm': ('x0', 63),
}


@pytest.mark.parametrize('name,expected', sorted(CIN_EXPECTED.items()))
def test_example_cin(name, expected):
    path = os.path.join(EXAMPLES, name)
    cpu = run_cin_file(path)   # 文件级运行: 支持 import 展开
    assert cpu.regs.read(0) == expected, f'{name}: X0={cpu.regs.read(0)}'


@pytest.mark.parametrize('name,reg_expected', sorted(ASM_EXPECTED.items()))
def test_example_asm(name, reg_expected, workdir):
    path = os.path.join(EXAMPLES, name)
    with open(path, encoding='utf-8') as f:
        source = f.read()
    cpu = asm_program(source, workdir)
    cpu.run()
    regname, expected = reg_expected
    idx = 0 if regname == 'x0' else int(regname[1:])
    assert cpu.regs.read(idx) == expected, \
        f'{name}: {regname}={cpu.regs.read(idx)}'


def test_example_files_exist():
    """examples/ 下文件完整 (asm 不受 .gitignore 影响, 需显式入库)。"""
    for name in list(CIN_EXPECTED) + list(ASM_EXPECTED):
        path = os.path.join(EXAMPLES, name)
        assert os.path.exists(path)
