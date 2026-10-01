r"""键盘输入监听 (SYS 116..118 / key.cin) 回归测试。

覆盖点:
  1. HOST_BUILTINS 注册: key_hit / get_key / key_flush 编译为 SYS 116/117/118
  2. 解释器路径 (--no-native): 宿主 SYS 报 "require the native Go runtime"
  3. sandbox 模式: 键盘 SYS 同样被拦截
  4. 原生路径非终端 (pytest 无 tty): key_hit=0, get_key=-1 (优雅失败, 不阻塞)
  5. lib/key.cin: 键码常量与辅助函数

断言约定与 tests/test_p0_fixes.py 一致。
"""

import os

import pytest

from codecin import CPU, Config, native
from codecin.cin import CINCompiler
from tests.helpers import run_cin_file

needs_native = pytest.mark.skipif(
    native.get_engine() is None, reason="native Go library not built")


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


# ====================================================================
# 1. HOST_BUILTINS 注册 -> SYS 功能号
# ====================================================================

def test_keyboard_builtins_compile_to_sys_ids():
    for name, sys_id in (('key_hit', 116), ('get_key', 117),
                         ('key_flush', 118)):
        src = f'function main() -> int {{ return {name}() }}'
        res = CINCompiler().compile_source(src)
        ops = [(ins[0], ins[1][0][1]) for ins in res.instructions
               if ins[0] == 'SYS']
        assert ('SYS', sys_id) in ops, f"{name}() 应编译为 SYS {sys_id}"


# ====================================================================
# 2. 解释器路径: 宿主 SYS 明确报错 (核心实现只在 Go)
# ====================================================================

KEY_SRC = '''
function main() -> int {
    int n = key_hit()
    key_flush()
    int k = get_key()
    return n * 100 + (k == -1 ? 1 : 0)
}'''


def test_interpreter_path_rejects_host_sys():
    cpu = build_cpu(KEY_SRC, use_native=False)
    cpu.run()
    # CPU.run 会把执行期异常记日志后吞掉, 用 execution_failed 判定
    assert cpu.execution_failed


# ====================================================================
# 3. sandbox: 键盘 SYS 属宿主能力, 被拦截
# ====================================================================

def test_sandbox_blocks_keyboard_sys():
    cpu = build_cpu(KEY_SRC, use_native=False, sandbox_mode=True)
    cpu.run()
    assert cpu.execution_failed


# ====================================================================
# 4. 原生路径非终端: 优雅失败 (不阻塞)
# ====================================================================

@needs_native
def test_native_no_tty_graceful():
    """pytest 无真实终端: key_hit=0, get_key=-1 -> x0 = 0*100+1 = 1。"""
    cpu = build_cpu(KEY_SRC, use_native=True)
    cpu.run()
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 1


@needs_native
def test_native_flush_returns_zero():
    cpu = build_cpu(
        'function main() -> int { return key_flush() }', use_native=True)
    cpu.run()
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


# ====================================================================
# 5. lib/key.cin
# ====================================================================

KEY_LIB_SRC = '''
import "key.cin"
function main() -> int {
    if (k_ctrl(67) != 3) { return 1 }          // Ctrl+C
    if (k_is_special(K_UP) != 1) { return 2 }
    if (k_is_special(K_F10) != 1) { return 3 }
    if (k_is_special(65) != 0) { return 4 }    // 普通字符
    if (K_ESC != 27 || K_ENTER != 13) { return 5 }
    return 0
}'''


@pytest.mark.parametrize('use_native', (False, True),
                         ids=('interp', 'native'))
def test_key_lib_constants_and_helpers(workdir, use_native):
    # 纯常量与计算, 无宿主调用, 两条路径均可执行
    # 注意: workdir (workspace 内) 而不是 tmp_path —— 受限沙箱下 tmp_path 不可用。
    path = os.path.join(workdir, 'key_lib.cin')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(KEY_LIB_SRC)
    cpu = run_cin_file(path, use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0
