"""CIN 新宿主 API 测试 (本次新增的 36 个宿主内建)。

覆盖分组:
  * 路径与文件系统扩展 (11): path_join / path_basename / path_dirname /
    path_abs / file_copy / file_move / dir_remove / is_dir / file_mtime /
    temp_dir / chdir
  * 时间与系统信息 (6): time_ms / sleep_ms / cpu_count / arch_name /
    mem_info / is_android
  * 网络 (3): http_get / http_post / download —— 只覆盖失败路径 (不依赖外网)
  * 编码与哈希 (3): sha256 / base64_encode / base64_decode
  * 桌面集成 (4): clipboard_get / clipboard_set / notify / open_url
    —— 只验证可编译 (运行会弹窗 / 抢占剪贴板, 不做运行断言)
  * Android/Termux 扩展 (9): android_intent / termux_call / termux_share /
    termux_torch / termux_volume / termux_brightness / termux_camera_photo /
    termux_fingerprint / termux_sensor —— 非 Termux 平台优雅失败 (-1 / 空串)

原生门禁惯例见 tests/test_cin_system.py: 无原生库时全部跳过。
"""

import os

import pytest

from codecin import native
from codecin.cin import CINCompiler
from tests.helpers import run_cin_source

needs_native = pytest.mark.skipif(
    native.get_engine() is None, reason="native Go library not built")

MINUS_ONE = (1 << 64) - 1

def _run(tpl: str, **kw):
    """替换 @NAME@ 占位符后用原生路径运行, 返回执行完的 CPU。

    两条硬断言防止假通过:
      * native_used  —— 确认真的走了 Go 原生 VM (needs_native 门禁下必须为真);
      * execution_failed —— CPU.run() 会吞掉 ExecutionError, 必须显式检查。
    """
    src = tpl
    for key, val in kw.items():
        src = src.replace('@' + key + '@', str(val))
    cpu = run_cin_source(src)
    assert cpu.native_used, '原生引擎未生效 (needs_native 门禁本应跳过)'
    assert not cpu.execution_failed, '宿主调用执行失败 (ExecutionError 被吞掉)'
    return cpu

# ====================================================================
# 路径与文件系统扩展
# ====================================================================

PATH_TPL = r'''
function main() -> int {
    string d = "@D@"
    string p = path_join(d, "a.txt")
    if (file_exists(p) != 0) { return 1 }
    if (file_write(p, "hello") != 0) { return 2 }
    if (file_exists(p) != 1) { return 3 }              // 拼接结果可被 file_exists 验证
    if (strcmp(path_basename(p), "a.txt") != 0) { return 4 }
    if (strcmp(path_basename(path_join(d, "a.txt")), "a.txt") != 0) { return 5 }
    if (strlen(path_dirname(p)) == 0) { return 6 }
    if (strlen(path_abs(".")) == 0) { return 7 }
    if (strlen(path_abs(p)) == 0) { return 8 }
    if (file_exists(path_abs(p)) != 1) { return 9 }    // 绝对路径指向同一文件
    return 0
}'''

@needs_native
def test_path_join_basename_dirname_abs(workdir):
    d = workdir.replace('\\', '/')
    cpu = _run(PATH_TPL, D=d)
    assert cpu.regs.read(0) == 0
    assert os.path.isfile(os.path.join(workdir, 'a.txt'))

FILE_OPS_TPL = r'''
function main() -> int {
    string d = "@D@/"
    if (file_write(d + "src.txt", "abcde") != 0) { return 1 }
    if (file_copy(d + "src.txt", d + "copy.txt") != 0) { return 2 }
    if (file_exists(d + "copy.txt") != 1) { return 3 }
    if (file_size(d + "copy.txt") != 5) { return 4 }
    if (strcmp(file_read(d + "copy.txt"), "abcde") != 0) { return 5 }
    if (file_move(d + "copy.txt", d + "moved.txt") != 0) { return 6 }
    if (file_exists(d + "copy.txt") != 0) { return 7 }   // 源已不存在
    if (file_exists(d + "moved.txt") != 1) { return 8 }
    if (strcmp(file_read(d + "moved.txt"), "abcde") != 0) { return 9 }
    if (file_mtime(d + "moved.txt") <= 0) { return 10 }  // Unix 秒
    if (file_mtime(d + "no_such_file.txt") != -1) { return 11 }  // 不存在 -> -1
    if (mkdir(d + "sub") != 0) { return 12 }
    if (is_dir(d + "sub") != 1) { return 13 }            // 目录 -> 1
    if (is_dir(d + "moved.txt") != 0) { return 14 }      // 普通文件 -> 0
    if (is_dir(d + "nope") != 0) { return 15 }
    if (file_exists(d + "sub") != 1) { return 16 }
    if (dir_remove(d + "sub") != 0) { return 17 }
    if (file_exists(d + "sub") != 0) { return 18 }       // 目录已消失
    if (file_exists(d + "moved.txt") != 1) { return 19 } // 其他文件不受影响
    return 0
}'''

@needs_native
def test_file_copy_move_isdir_dirremove_mtime(workdir):
    d = workdir.replace('\\', '/')
    cpu = _run(FILE_OPS_TPL, D=d)
    assert cpu.regs.read(0) == 0
    moved = os.path.join(workdir, 'moved.txt')
    assert os.path.isfile(moved)
    with open(moved, encoding='utf-8') as f:
        assert f.read() == 'abcde'
    assert not os.path.exists(os.path.join(workdir, 'copy.txt'))
    assert not os.path.exists(os.path.join(workdir, 'sub'))

@needs_native
def test_temp_dir_nonempty_and_is_dir():
    src = r'''
function main() -> int {
    string t = temp_dir()
    if (strlen(t) == 0) { return 1 }
    if (is_dir(t) != 1) { return 2 }
    return 0
}'''
    assert _run(src).regs.read(0) == 0

CHDIR_TPL = r'''
function main() -> int {
    if (chdir("@D@") != 0) { return 1 }
    if (strlen(cwd()) == 0) { return 2 }
    if (indexof(cwd(), ".pytest_tmp") < 0) { return 3 }
    if (file_exists("marker.txt") != 1) { return 4 }   // 相对路径解析到新 cwd
    return 0
}'''

@needs_native
def test_chdir_and_cwd(workdir):
    """chdir 后 cwd() 变化, 且相对路径按新工作目录解析。

    原生 VM 与 CPython 同进程, chdir 会改变整个进程的工作目录,
    因此必须用 try/finally 切回原目录。
    """
    with open(os.path.join(workdir, 'marker.txt'), 'w', encoding='utf-8') as f:
        f.write('here')
    orig = os.getcwd()
    try:
        cpu = _run(CHDIR_TPL, D=workdir.replace('\\', '/'))
        rc = cpu.regs.read(0)
    finally:
        os.chdir(orig)
    assert rc == 0
    assert os.path.isfile(os.path.join(workdir, 'marker.txt'))

# ====================================================================
# 时间与系统信息
# ====================================================================

@needs_native
def test_time_ms_and_sleep_ms():
    src = r'''
function main() -> int {
    int t = time_ms()
    if (t <= 0) { return 1 }
    if (t < time() * 1000 - 2000) { return 2 }   // 与秒级 time() 一致 (±2s)
    int t0 = time_ms()
    sleep_ms(20)
    int t1 = time_ms()
    if (t1 - t0 < 15) { return 3 }
    return 0
}'''
    assert _run(src).regs.read(0) == 0

@needs_native
def test_cpu_count_and_arch_name():
    src = r'''
function main() -> int {
    if (cpu_count() < 1) { return 1 }
    string a = arch_name()
    if (strlen(a) == 0) { return 2 }
    if (strcmp(a, "amd64") == 0) { return 0 }
    if (strcmp(a, "arm64") == 0) { return 0 }
    if (strcmp(a, "386") == 0) { return 0 }
    if (strcmp(a, "arm") == 0) { return 0 }
    if (strcmp(a, "mips") == 0) { return 0 }
    if (strcmp(a, "mipsle") == 0) { return 0 }
    if (strcmp(a, "mips64") == 0) { return 0 }
    if (strcmp(a, "mips64le") == 0) { return 0 }
    if (strcmp(a, "ppc64") == 0) { return 0 }
    if (strcmp(a, "ppc64le") == 0) { return 0 }
    if (strcmp(a, "riscv64") == 0) { return 0 }
    if (strcmp(a, "s390x") == 0) { return 0 }
    if (strcmp(a, "loong64") == 0) { return 0 }
    return 3
}'''
    assert _run(src).regs.read(0) == 0

@needs_native
def test_mem_info_and_is_android():
    src = r'''
function main() -> int {
    string m = mem_info()
    if (indexof(m, "total_kb") < 0) { return 1 }
    if (indexof(m, "free_kb") < 0) { return 2 }
    int a = is_android()
    if (a == 0) { return 0 }
    if (a == 1) { return 0 }
    return 3
}'''
    assert _run(src).regs.read(0) == 0

# ====================================================================
# 网络 (仅失败路径: 127.0.0.1:9 上无监听者, 立即拒绝, 不依赖外网)
# ====================================================================

NET_TPL = r'''
function main() -> int {
    if (strlen(http_get("http://127.0.0.1:9/none")) != 0) { return 1 }
    if (strlen(http_post("http://127.0.0.1:9/none", "x")) != 0) { return 2 }
    int rc = download("http://127.0.0.1:9/x", "@D@/nope.txt")
    if (file_exists("@D@/nope.txt") != 0) { return 3 }
    if (rc != -1) { return 4 }
    return 0
}'''

@needs_native
def test_network_failures_are_graceful(workdir):
    d = workdir.replace('\\', '/')
    cpu = _run(NET_TPL, D=d)
    assert cpu.regs.read(0) == 0
    assert not os.path.exists(os.path.join(workdir, 'nope.txt'))

# ====================================================================
# 编码与哈希
# ====================================================================

CRYPTO_TPL = r'''
function main() -> int {
    if (strcmp(sha256("abc"),
               "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad") != 0) { return 1 }
    if (strcmp(sha256(""),
               "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855") != 0) { return 2 }
    if (strcmp(base64_encode("hello"), "aGVsbG8=") != 0) { return 3 }
    if (strcmp(base64_decode("aGVsbG8="), "hello") != 0) { return 4 }
    if (strlen(base64_decode("aGVsbG8")) != 0) { return 5 }   // 长度非法
    if (strlen(base64_decode("!!!")) != 0) { return 6 }        // 字符非法
    string zh = "\u4e2d\u6587"
    if (strcmp(base64_decode(base64_encode(zh)), zh) != 0) { return 7 }
    if (strlen(sha256("abc")) != 64) { return 8 }
    return 0
}'''

@needs_native
def test_sha256_and_base64():
    assert _run(CRYPTO_TPL).regs.read(0) == 0

# ====================================================================
# Android / Termux 扩展 (Windows/桌面平台必然优雅失败)
# ====================================================================

TERMUX_TPL = r'''
function main() -> int {
    if (termux_call("10086") != -1) { return 1 }
    if (termux_share("@D@/share.txt") != -1) { return 2 }
    if (termux_torch(1) != -1) { return 3 }
    if (termux_volume("music", 5) != -1) { return 4 }
    if (termux_brightness(10) != -1) { return 5 }
    if (termux_camera_photo("@D@/cam.jpg") != -1) { return 6 }
    if (android_intent("android.intent.action.VIEW", "https://example.com") != -1) { return 7 }
    if (strlen(termux_fingerprint()) != 0) { return 8 }
    if (strlen(termux_sensor("accelerometer")) != 0) { return 9 }
    return 0
}'''

@needs_native
def test_termux_android_extensions_fail_gracefully(workdir):
    d = workdir.replace('\\', '/')
    cpu = _run(TERMUX_TPL, D=d)
    assert cpu.regs.read(0) == 0
    # 优雅失败不得产生副作用文件
    assert not os.path.exists(os.path.join(workdir, 'cam.jpg'))
    assert not os.path.exists(os.path.join(workdir, 'share.txt'))

@needs_native
def test_minus_one_is_masked_64bit():
    """返回值约定: 失败为 -1, 在 64 位寄存器里就是 2**64-1。"""
    src = r'''
function main() -> int {
    return android_intent("android.intent.action.VIEW", "https://example.com")
}'''
    cpu = _run(src)
    assert cpu.regs.read(0) == MINUS_ONE

# ====================================================================
# 桌面集成: 只验证可编译 (运行会弹窗 / 抢占剪贴板)
# ====================================================================

DESKTOP_COMPILE_TPL = r'''
function main() -> int {
    int a = clipboard_set("codecin-test")
    string b = clipboard_get()
    int c = notify("codecin", "test")
    int d = open_url("https://example.com")
    return strlen(b) + a + c + d
}'''

def test_desktop_builtins_compile():
    """clipboard_get/clipboard_set/notify/open_url 只编译不运行 (无副作用)。"""
    res = CINCompiler().compile_source(DESKTOP_COMPILE_TPL)
    assert any(i[0] == 'SYS' for i in res.instructions)

# ====================================================================
# sandbox: 宿主能力 SYS 被 Go 引擎拦截 (v5.9.0)
# ====================================================================

HOST_NEEDS_NATIVE_SRC = r'''
function main() -> int {
    string h = sha256("abc")
    string t = temp_dir()
    return strlen(h) + strlen(t)
}'''

def test_host_builtins_rejected_in_sandbox():
    """沙箱下宿主能力 SYS 被引擎拒绝: 编译通过, 执行置 execution_failed。"""
    res = CINCompiler().compile_source(HOST_NEEDS_NATIVE_SRC)
    assert any(i[0] == 'SYS' for i in res.instructions), \
        '新宿主内建应编译为 SYS 指令'
    cpu = run_cin_source(HOST_NEEDS_NATIVE_SRC, sandbox_mode=True)
    assert cpu.execution_failed is True

