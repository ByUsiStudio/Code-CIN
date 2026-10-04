r"""GUI 窗口 (SYS 119..125, 128) 与音频扩展 (SYS 126..127) 回归测试。

覆盖点:
  1. HOST_BUILTINS 注册: gui_* / mouse_* / audio_pos / beep 编译为对应 SYS
  2. 解释器路径 (--no-native): 宿主 SYS 报 "require the native Go runtime"
  3. sandbox 模式: 同样被拦截
  4. 原生路径无显示服务 / 无窗口: gui_* 优雅失败, mouse_* = -1
  5. 音频参数校验 (三平台一致, 不触碰设备) 与 lib/gui.cin 封装

注意: 测试绝不调用 gui_new (Windows 开发机上会真的弹出窗口),
也不调用参数合法的 beep (会真的发声); 只测"无窗口优雅失败"与
"触碰设备之前完成的参数校验"。
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

def test_gui_audio_builtins_compile_to_sys_ids():
    mapping = [
        ('gui_new', 119), ('gui_update', 120), ('gui_close', 121),
        ('gui_closed', 122), ('mouse_x', 123), ('mouse_y', 124),
        ('mouse_button', 125), ('audio_pos', 126), ('beep', 127),
        ('gui_active', 128),
    ]
    for name, sys_id in mapping:
        src = f'function main() -> int {{ return {name}'
        src += '("t", 1, 2)' if name == 'gui_new' else \
               '(440, 10)' if name == 'beep' else '()'
        src += ' }'
        res = CINCompiler().compile_source(src)
        ops = [(ins[0], ins[1][0][1]) for ins in res.instructions
               if ins[0] == 'SYS']
        assert ('SYS', sys_id) in ops, f"{name}() 应编译为 SYS {sys_id}"


# ====================================================================
# 2/3. 解释器路径与 sandbox: 宿主 SYS 明确报错
# ====================================================================

GUI_SRC = '''
function main() -> int {
    if (gui_active() != 0) { return 1 }
    int mx = mouse_x()
    int p = audio_pos()
    gui_close()
    return mx + p * 0
}'''


@pytest.mark.parametrize('use_native, sandbox', ((False, False), (False, True)),
                         ids=('interp', 'sandbox'))
def test_non_native_paths_reject_gui_sys(use_native, sandbox):
    cpu = build_cpu(GUI_SRC, use_native=use_native, sandbox_mode=sandbox)
    cpu.run()
    # CPU.run 会把执行期异常记日志后吞掉, 用 execution_failed 判定
    assert cpu.execution_failed


# ====================================================================
# 4. 原生路径无窗口: 优雅失败 (不阻塞、不弹窗)
# ====================================================================

@needs_native
def test_native_no_window_graceful():
    """无窗口: gui_active=0 / gui_update=-1 / mouse_*=-1 / audio_pos=-1。"""
    cpu = build_cpu('''
function main() -> int {
    if (gui_active() != 0) { return 1 }
    if (gui_closed() != 0) { return 2 }
    if (gui_update() != -1) { return 3 }
    if (gui_close() != 0) { return 4 }
    if (mouse_x() != -1) { return 5 }
    if (mouse_y() != -1) { return 6 }
    if (mouse_button() != -1) { return 7 }
    if (audio_pos() != -1) { return 8 }
    return 0
}''', use_native=True)
    cpu.run()
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


@needs_native
def test_native_beep_argument_validation():
    """参数校验在触碰设备之前完成, 三平台一致 (不发声)。"""
    cpu = build_cpu('''
function main() -> int {
    if (beep(19, 100) != -1) { return 1 }      // 频率过低
    if (beep(20001, 100) != -1) { return 2 }   // 频率过高
    if (beep(440, 0) != -1) { return 3 }       // 时长为 0
    return 0
}''', use_native=True)
    cpu.run()
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


# ====================================================================
# 5. lib/gui.cin 封装 (无窗口路径) 与 lib/key.cin 扩展键码
# ====================================================================

@needs_native
def test_gui_lib_no_window(workdir):
    src = '''
import "gui.cin"
function main() -> int {
    if (g_active() != 0) { return 1 }
    if (g_update() != -1) { return 2 }
    if (g_clear(0xFF0000) != -1) { return 3 }   // 从未 g_new: 尺寸未知
    if (g_mouse_left() != 0) { return 4 }
    if (g_mouse_right() != 0) { return 5 }
    if (g_close() != 0) { return 6 }
    if (g_closed() != 0) { return 7 }
    return 0
}'''
    path = os.path.join(workdir, 'gui_lib.cin')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    cpu = run_cin_file(path, use_native=True)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


KEY_EXT_SRC = '''
import "key.cin"
function main() -> int {
    if (K_F11 != 1031 || K_F12 != 1032) { return 1 }
    if (K_CTRL_RIGHT != 1104 || K_SHIFT_TAB != 1109) { return 2 }
    if (k_is_special(K_F12) != 1) { return 3 }
    if (k_is_special(K_CTRL_UP) != 1) { return 4 }
    if (k_is_special('a') != 0) { return 5 }
    if (k_is_function(K_F1) != 1 || k_is_function(K_F12) != 1) { return 6 }
    if (k_is_function(K_UP) != 0) { return 7 }
    if (k_is_modified_arrow(K_SHIFT_RIGHT) != 1) { return 8 }
    if (k_is_modified_arrow(K_F5) != 0) { return 9 }
    return 0
}'''


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_key_lib_extended_constants(workdir, use_native):
    """5.8.0 新增键码: F11/F12 与 Ctrl/Shift 修饰组合, 纯常量两条路径一致。"""
    path = os.path.join(workdir, 'key_ext.cin')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(KEY_EXT_SRC)
    cpu = run_cin_file(path, use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0
