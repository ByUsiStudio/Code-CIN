"""AOT 独立可执行文件构建测试 (Windows / Linux / macOS 静态编译)。

覆盖:
  * 产物可独立运行, 输出与解释器/Go CLI 一致;
  * 交叉编译能产出目标平台产物;
  * Linux 产物是静态链接 (ELF 无 PT_INTERP), 即不依赖 libc/动态库;
  * 目标解析与错误处理 (非法 --target 报 AotError);
  * 临时构建目录的生命周期 (清理失败必须告警、陈旧残留会被 sweep、不误删)。
"""

import os
import shutil
import struct
import subprocess
import time

import pytest

from codecin import aot
from codecin.errors import CompilerError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NATIVE_DIR = os.path.join(ROOT, 'codecin', 'native')

#: 构建需要 Go 工具链
needs_go = pytest.mark.skipif(shutil.which('go') is None,
                              reason='未安装 Go 工具链')

#: 交叉编译用例各需要为目标平台重编一遍 Go 标准库 (首次 1~2 分钟),
#: 因此默认只跑本机构建; CI 的 integration 作业设置 CODECIN_AOT_TESTS=1 全跑。
needs_aot_all = pytest.mark.skipif(
    os.environ.get('CODECIN_AOT_TESTS') != '1',
    reason='交叉编译用例较慢; 设置 CODECIN_AOT_TESTS=1 启用')

PROGRAM = '''
import "stat.cin"

function main() -> int {
    int s = 0
    for (int i = 1; i <= 10; i = i + 1) {
        s = s + i
    }
    int a[5] = {1, 2, 3, 4, 5}
    println("sum=" + int_to_str(s) + " stat=" + int_to_str(stat_sum(a, 5)))
    return 0
}'''


def _write(workdir, name, text):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def _run(exe, timeout=120):
    return subprocess.run([exe], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout)


def _elf_is_static(path):
    """ELF 是否静态链接: 无 PT_INTERP 段。"""
    with open(path, 'rb') as f:
        data = f.read()
    assert data[:4] == b'\x7fELF', '不是 ELF 文件'
    e_phoff = struct.unpack_from('<Q', data, 0x20)[0]
    e_phentsize = struct.unpack_from('<H', data, 0x36)[0]
    e_phnum = struct.unpack_from('<H', data, 0x38)[0]
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        if struct.unpack_from('<I', data, off)[0] == 3:   # PT_INTERP
            return False
    return True


# ---------------- 目标解析 (不需要 Go) ----------------

def test_parse_target_valid():
    assert aot.parse_target('linux/amd64') == ('linux', 'amd64')
    assert aot.parse_target('windows/arm64') == ('windows', 'arm64')


@pytest.mark.parametrize('bad', ['linux', 'linux/', '/amd64', '', 'a/b/c'])
def test_parse_target_invalid(bad):
    with pytest.raises(aot.AotError):
        aot.parse_target(bad)


def test_host_target_looks_valid():
    goos, goarch = aot.parse_target(aot.host_target())
    assert goos in ('windows', 'linux', 'darwin')
    assert goarch


def test_exe_suffix():
    assert aot.exe_suffix('windows') == '.exe'
    assert aot.exe_suffix('linux') == ''


def test_stub_template_is_shared_with_go():
    """Python 侧与 Go 侧必须共用同一份 main.go 模板 (防漂移)。"""
    src = aot.stub_source()
    assert 'codecin-native/aot' in src
    assert '//go:embed program.ucbc' in src
    assert '//go:embed program.mem' in src
    go_src = os.path.join(ROOT, 'codecin', 'native', 'aot', 'aot.go')
    with open(go_src, encoding='utf-8') as f:
        assert 'stub_main.go.txt' in f.read(), 'Go 侧未引用共享模板'


# ---------------- 实际构建 ----------------

@needs_go
def test_build_host_executable(workdir):
    """产物脱离 Python 独立运行, 输出与解释器一致。"""
    src = _write(workdir, 'prog.cin', PROGRAM)
    out = os.path.join(workdir, 'prog_aot' + aot.exe_suffix(
        aot.parse_target(aot.host_target())[0]))
    built = aot.build_program(src, out=out)
    assert os.path.isfile(built) and os.path.getsize(built) > 0

    r = _run(built)
    assert r.returncode == 0, r.stderr
    # 同时验证标准库 import 已在编译期展开 (产物不需要 codecin/lib 目录)
    assert 'sum=55 stat=15' in r.stdout

    # 对照组: Go CLI 直接运行同一程序 (编译+执行链路必须一致)
    go_cli = _go_cli()
    if go_cli:
        ref = subprocess.run([go_cli, src], capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=180)
        assert ref.returncode == 0, ref.stderr
        assert ref.stdout.strip() == r.stdout.strip()


def _go_cli():
    for rel in ('codecin/native/codecin.exe', 'codecin/native/codecin',
                'codecin/codecin.exe', 'codecin/codecin'):
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            return p
    return None


@needs_go
def test_build_respects_error_exit_code(workdir):
    """运行期错误必须以非 0 退出码报告 (独立可执行文件的语义)。"""
    src = _write(workdir, 'boom.cin', '''
function main() -> int {
    println("before")
    int x = 1
    int y = 0
    return idiv(x, y)
}''')
    out = os.path.join(workdir, 'boom' + aot.exe_suffix(
        aot.parse_target(aot.host_target())[0]))
    built = aot.build_program(src, out=out)
    r = _run(built)
    assert 'before' in r.stdout
    assert r.returncode != 0, '运行期错误必须返回非 0 退出码'
    assert 'runtime error' in r.stderr.lower()


@needs_go
@needs_aot_all
@pytest.mark.parametrize('target', ['linux/amd64', 'linux/arm64', 'darwin/arm64'])
def test_cross_compile_targets(workdir, target):
    """交叉编译: 各平台都能产出可执行文件。"""
    src = _write(workdir, 'prog.cin', PROGRAM)
    out = os.path.join(workdir, 'prog-' + target.replace('/', '-'))
    built = aot.build_program(src, out=out, target=target)
    assert os.path.isfile(built)
    with open(built, 'rb') as f:
        head = f.read(4)
    if target.startswith('linux'):
        assert head == b'\x7fELF'
        assert _elf_is_static(built), 'Linux 产物必须静态链接 (无 PT_INTERP)'
    elif target.startswith('darwin'):
        assert head[:4] in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe')


@needs_go
def test_cross_compile_linux_amd64_is_static(workdir):
    """关键声明: Linux 产物静态链接 (无 PT_INTERP, 不依赖 glibc)。

    这条是 AOT 的核心保证, 默认就运行 (仅一次交叉编译)。
    """
    if aot.host_target() == 'linux/amd64':
        pytest.skip('本机即 linux/amd64, 已由本机构建用例覆盖')
    src = _write(workdir, 'prog.cin', PROGRAM)
    out = os.path.join(workdir, 'prog-linux-amd64')
    built = aot.build_program(src, out=out, target='linux/amd64')
    with open(built, 'rb') as f:
        assert f.read(4) == b'\x7fELF'
    assert _elf_is_static(built)


@needs_go
def test_build_rejects_bad_target(workdir):
    src = _write(workdir, 'prog.cin', PROGRAM)
    with pytest.raises(aot.AotError):
        aot.build_program(src, out=os.path.join(workdir, 'x'), target='linux')


def test_build_reports_compile_error(workdir):
    """语法/语义错误应报 CompilerError, 而不是留下半成品。"""
    src = _write(workdir, 'bad.cin', 'function main() -> int { return foo() }')
    with pytest.raises((CompilerError, aot.AotError)):
        aot.build_program(src, out=os.path.join(workdir, 'bad'))


# ---------------- 临时构建目录的生命周期 (§3.3) ----------------
#
# 说明: 不用 tmp_path (本机沙箱下 pytest 的 tmp_path 会 PermissionError), 全部
# 走 conftest 的 workdir 夹具。真正的 codecin/native/ 在本机沙箱下**不可写**
# (建/删目录都被拒), 因此 build()/sweep() 的用例把 aot._NATIVE_DIR 指向
# workdir 内的假 native/; 另有一条例行的「仓库守卫」只读地检查真目录。

STALE = 99 * 3600            # 远超默认 6 小时
FRESH = 0.0                  # mtime = 现在


class _RecordingLogger:
    """最小 logger 替身: 记录 (level, message)。"""

    def __init__(self):
        self.records = []

    def _add(self, level, msg):
        self.records.append((level, msg))

    def debug(self, msg):
        self._add('debug', msg)

    def info(self, msg):
        self._add('info', msg)

    def warning(self, msg):
        self._add('warning', msg)

    def error(self, msg):
        self._add('error', msg)

    def warnings(self):
        return [m for lvl, m in self.records if lvl == 'warning']


class _Proc:
    """subprocess.CompletedProcess 的替身。"""

    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture()
def fake_native(workdir, monkeypatch):
    """把 aot 的模块目录指向 workdir 内带 go.mod 的假 native/。"""
    d = os.path.join(workdir, 'native')
    os.makedirs(d, exist_ok=True)
    _write(d, 'go.mod', 'module codecin-native\n')
    monkeypatch.setattr(aot, '_NATIVE_DIR', d)
    return d


def _make_temp_dir(root, name, age_seconds=FRESH):
    """在 root 下造一个临时目录, 用 os.utime 把 mtime 调旧 (不等待)。"""
    path = os.path.join(root, name)
    os.makedirs(path, exist_ok=True)
    _write(path, 'main.go', 'package main\n')
    if age_seconds:
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
    return path


def _fake_go_build():
    """_run_go_build 替身: 不调用 go, 直接按 -o 参数写出一个假产物。"""
    def _run(cmd, mod, env, logger):
        out = cmd[cmd.index('-o') + 1]
        with open(out, 'wb') as f:
            f.write(b'fake-exe')
        return _Proc(0), ''
    return _run


def _temp_entries(root):
    return sorted(n for n in os.listdir(root)
                  if n.startswith(aot.TEMP_DIR_PREFIXES))


def test_repo_native_has_no_aot_leftovers():
    """仓库守卫: codecin/native/ 下不得有 .aotbuild-* / .aotprobe-* 残留。"""
    left = _temp_entries(NATIVE_DIR)
    assert left == [], f'codecin/native/ 存在 AOT 残留目录: {left}'


@pytest.mark.parametrize('name', ['.aotbuild-abc123', '.aotprobe-abc123'])
def test_is_temp_dir_name_accepts_own_prefixes(name):
    assert aot._is_temp_dir_name(name)


@pytest.mark.parametrize('name', [
    '', '.', '..', 'aotbuild-abc123', '.aotbuildX-abc123',
    '../evil', '..\\evil', 'sub/.aotbuild-x', 'sub\\.aotbuild-x',
    'C:\\evil\\.aotbuild-x', '/tmp/.aotbuild-x',
])
def test_is_temp_dir_name_rejects_traversal_and_foreign_names(name):
    """只认自己的前缀, 且显式拒绝 .. / 路径分隔符 / 绝对路径。"""
    assert not aot._is_temp_dir_name(name)


def test_sweep_removes_stale_dirs(fake_native):
    """陈旧目录 (含历史遗留的 .aotprobe-) 会被删除。"""
    stale = _make_temp_dir(fake_native, '.aotbuild-deadbeef', age_seconds=STALE)
    probe = _make_temp_dir(fake_native, '.aotprobe-cafe01', age_seconds=STALE)
    log = _RecordingLogger()

    res = aot.sweep_stale_build_dirs(max_age_seconds=6 * 3600, logger=log)

    assert set(res.removed) == {stale, probe}
    assert res.failed == []
    assert not os.path.exists(stale) and not os.path.exists(probe)
    assert _temp_entries(fake_native) == []


def test_sweep_keeps_fresh_dirs(fake_native):
    """刚建的目录是并发构建的, 绝不能被误删。"""
    fresh = _make_temp_dir(fake_native, '.aotbuild-fresh01', age_seconds=FRESH)
    log = _RecordingLogger()

    res = aot.sweep_stale_build_dirs(max_age_seconds=6 * 3600, logger=log)

    assert res.removed == [] and res.failed == []
    assert os.path.isdir(fresh)


def test_sweep_only_touches_own_direct_children(fake_native, workdir):
    """前缀不符 / 不是目录 / 不在本目录下的一律不动。"""
    log = _RecordingLogger()
    plain_file = os.path.join(fake_native, '.aotbuild-i-am-a-file')
    with open(plain_file, 'w', encoding='utf-8') as f:
        f.write('not a dir')
    other_prefix = _make_temp_dir(fake_native, 'aotbuild-nodot', age_seconds=STALE)
    no_dash = _make_temp_dir(fake_native, '.aotbuildX-old', age_seconds=STALE)
    outside = _make_temp_dir(workdir, '.aotbuild-outside', age_seconds=STALE)

    res = aot.sweep_stale_build_dirs(max_age_seconds=6 * 3600, logger=log)

    assert res.removed == [] and res.failed == []
    assert os.path.isfile(plain_file)
    assert os.path.isdir(other_prefix)
    assert os.path.isdir(no_dash)
    assert os.path.isdir(outside), 'sweep 越界删除了 native/ 之外的目录'


def test_sweep_reports_undeletable_dir_without_raising(fake_native, monkeypatch):
    """删不掉的目录: 不抛异常, 记 warning 并出现在 failed 里。"""
    stale = _make_temp_dir(fake_native, '.aotbuild-locked', age_seconds=STALE)
    log = _RecordingLogger()

    def _denied(path, *args, **kwargs):
        raise PermissionError(5, 'Access is denied')

    monkeypatch.setattr(aot.shutil, 'rmtree', _denied)
    res = aot.sweep_stale_build_dirs(max_age_seconds=6 * 3600, logger=log)

    assert res.removed == []
    assert res.failed == [stale]
    assert os.path.isdir(stale)
    warns = log.warnings()
    assert any(stale in m for m in warns), warns
    assert any('Remove-Item' in m and 'rm -rf' in m for m in warns), warns


def test_sweep_missing_root_is_reported_not_raised(workdir, monkeypatch):
    """扫描根不存在: 不抛异常, 但要有告警 (不静默)。"""
    monkeypatch.setattr(aot, '_NATIVE_DIR', os.path.join(workdir, 'nope'))
    log = _RecordingLogger()

    res = aot.sweep_stale_build_dirs(logger=log)

    assert res.removed == [] and res.failed == []
    assert log.warnings(), '扫描失败被静默吞掉了'


def test_build_leaves_no_temp_dir(fake_native, monkeypatch, workdir):
    """构建结束后 native/ 里不残留本次的 .aotbuild-* 目录。"""
    out = os.path.join(workdir, 'prog_exe')
    monkeypatch.setattr(aot, '_run_go_build', _fake_go_build())

    built = aot.build(b'\x01\x02\x03', b'\x00' * 16, out,
                      logger=_RecordingLogger())

    assert built == os.path.abspath(out)
    assert os.path.isfile(built)
    assert _temp_entries(fake_native) == []
    assert os.listdir(fake_native) == ['go.mod']


def test_build_sweeps_stale_leftover_first(fake_native, monkeypatch, workdir):
    """build() 在创建新临时目录之前会清掉陈旧残留。"""
    stale = _make_temp_dir(fake_native, '.aotbuild-veryold', age_seconds=STALE)
    out = os.path.join(workdir, 'prog_exe2')
    monkeypatch.setattr(aot, '_run_go_build', _fake_go_build())

    aot.build(b'\x01\x02', b'\x00' * 8, out, logger=_RecordingLogger())

    assert not os.path.exists(stale)
    assert _temp_entries(fake_native) == []


def test_build_warns_when_cleanup_fails(fake_native, monkeypatch, workdir):
    """rmtree 失败必须 warning (含绝对路径 + 手动删除提示), 且不掩盖构建结果。"""
    out = os.path.join(workdir, 'prog_exe3')

    def _denied(path, *args, **kwargs):
        raise PermissionError(5, 'Access is denied')

    monkeypatch.setattr(aot, '_run_go_build', _fake_go_build())
    monkeypatch.setattr(aot.shutil, 'rmtree', _denied)
    log = _RecordingLogger()

    built = aot.build(b'\x01', b'\x00' * 8, out, logger=log)

    assert built == os.path.abspath(out)
    warns = [m for m in log.warnings() if '.aotbuild-' in m]
    assert warns, log.records
    msg = warns[-1]
    assert fake_native in msg, f'告警里没有残留目录的绝对路径: {msg}'
    assert 'Remove-Item' in msg and 'rm -rf' in msg, msg
    assert _temp_entries(fake_native), 'rmtree 失败后目录本应仍在'
