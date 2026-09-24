"""三执行路径一致性 (解释器 / JIT / Go 原生) 的**源码级**端到端检查。

背景: `tests/test_three_paths.py` 用的是 9 条指令的手写程序, 覆盖不到源码级
语义 (SYS、字符串、数组、浮点)。这里直接跑 `examples/*.cin` 的三条路径并
逐字节比对 stdout —— 正是 docs/SUGGESTIONS_NEXT.md §6 指出的空白。
"""

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKER = os.path.join(ROOT, 'script', 'check_paths.py')

#: 三条路径都应给出相同输出的示例 (system_interaction.cin 按设计只能用原生路径)
FILES = [
    'control_flow.cin',
    'literals_types.cin',
    'modules_demo.cin',
    'bitwise_builtins.cin',
]


@pytest.mark.parametrize('name', FILES)
def test_three_paths_same_output(name):
    r = subprocess.run(
        [sys.executable, CHECKER, os.path.join(ROOT, 'examples', name)],
        capture_output=True, cwd=ROOT, timeout=300)
    detail = (r.stdout or b'').decode('utf-8', 'replace') + \
             (r.stderr or b'').decode('utf-8', 'replace')
    assert r.returncode == 0, f'{name}: 三条路径输出不一致\n{detail}'
