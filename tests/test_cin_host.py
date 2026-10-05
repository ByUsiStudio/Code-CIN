"""CIN 宿主能力 (GUI 画布 / 联网音频) 测试: 依赖 Go 原生库, 无原生库时跳过。"""

import os

import pytest

from codecin import native
from tests.helpers import run_cin_source

needs_native = pytest.mark.skipif(
    native.get_engine() is None, reason="native Go library not built")

@needs_native
def test_canvas_renders_png(workdir):
    out = os.path.join(workdir, 'canvas.png').replace('\\', '/')
    src = f'''
function main() -> int {{
    canvas(40, 20)
    set_color(0xFF0000)
    fill_rect(0, 0, 20, 20)
    set_color(0x0000FF)
    fill_circle(30, 10, 8)
    set_color(0x00FF00)
    draw_line(0, 0, 39, 19)
    set_color(0x000000)
    draw_text(2, 2, "HI")
    return save_png("{out}")
}}'''
    cpu = run_cin_source(src)
    assert cpu.regs.read(0) == 0
    assert os.path.exists(out), f"PNG not written to {out}"
    assert os.path.getsize(out) > 0

@needs_native
def test_audio_play_missing_file_returns_neg1():
    src = '''
function main() -> int {
    return audio_play("/nonexistent/no_such_file.wav")
}'''
    cpu = run_cin_source(src)
    assert cpu.regs.read(0) == (1 << 64) - 1  # -1

def test_audio_canvas_builtins_compile_without_native():
    """编译含 GUI/音频内建的源码 (解释路径运行会报错, 但编译本身应成功)。"""
    src = '''
function main() -> int {
    canvas(10, 10)
    audio_stop()
    int r = show_canvas()
    return r
}'''
    # 仅编译 (不运行), 验证内置函数名可解析
    from codecin.cin import CINCompiler
    res = CINCompiler().compile_source(src)
    assert res is not None
    assert any(i[0] == 'SYS' for i in res.instructions)

@needs_native
def test_audio_controls_no_playback_native():
    """音频控制增强: 无播放时查询与控制的约定返回值。"""
    src = '''
function main() -> int {
    if (audio_duration() != -1) { return 1 }
    if (audio_playing() != 0) { return 2 }
    if (audio_level() != 100) { return 3 }
    if (audio_pause() != -1) { return 4 }
    if (audio_resume() != -1) { return 5 }
    return 0
}'''
    cpu = run_cin_source(src)
    assert cpu.regs.read(0) == 0

def test_audio_controls_builtins_compile_without_native():
    """5 个音频控制内建可编译并映射到 SYS 调用。

    只统计音频 SYS (132..136): 帧分配 ALLOCFRAME (137) 等核心机制
    也会产生 SYS 指令, 不属于本测试的关注点。
    """
    src = '''
function main() -> int {
    int a = audio_duration()
    int b = audio_playing()
    int c = audio_level()
    int d = audio_pause()
    int e = audio_resume()
    return a + b + c + d + e
}'''
    from codecin.cin import CINCompiler
    from codecin.isa import Syscall
    res = CINCompiler().compile_source(src)
    assert res is not None
    audio_ids = {Syscall.AUDIODUR, Syscall.AUDIOPLAYING, Syscall.AUDIOPAUSE,
                 Syscall.AUDIORESUME, Syscall.AUDIOLEVEL}
    sys_ids = [i[1][0][1] for i in res.instructions
               if i[0] == 'SYS' and i[1] and i[1][0][0] == 'imm']
    assert set(sys_ids) <= audio_ids | {Syscall.ALLOCFRAME}
    assert sum(1 for x in sys_ids if x in audio_ids) == 5

