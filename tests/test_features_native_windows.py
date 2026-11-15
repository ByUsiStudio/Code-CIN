"""8.1.2 回归: cincc 的 Windows (PE32+) 机器码后端。

8.1.2 之前 ``-target x86_64-windows`` 只是一个骨架: 只能编码
``MOV``/``ADD``/``SUB``/``OUT``/``HALT`` 的直线程序, 一碰函数调用、数组或字符串
就报「暂不支持」。这一版把 ``mc/x86_64.go`` 的编码器按目标参数化, Windows 侧只
保留 PE 容器与 ``.data`` 镜像, 因此函数调用、递归、数组、字符串与纯计算 ``SYS``
都能跑。

这里固定三件容易退化的事:

* PE32+ 头的关键字段与**节之间不留 RVA 空洞**（留了会被判
  ``WinError 193 不是有效的 Win32 应用程序``, 且只看头字段看不出来）;
* 端到端行为（递归、数组、浮点打印、退出码）;
* 尚未实现的文件/网络 ``SYS`` 必须报错而不是生成跑一半才崩的 exe。

实际运行 PE 的用例只在 Windows 上跑, 其余用例任何平台都能执行 (只读产物字节)。
"""

import os
import struct
import subprocess

import pytest

from codecin import cli

CINCC = cli._find_cincc()
needs_cincc = pytest.mark.skipif(CINCC is None, reason='cincc 未构建')
needs_windows = pytest.mark.skipif(os.name != 'nt',
                                   reason='只能在 Windows 上执行 PE 产物')

# 综合程序: 递归 + 数组 + const 维度 + 浮点 + 字符串, 退出码取 fib(7)=13。
COMBINED = '''
function fib(int n) -> int {
    if (n < 2) { return n; }
    return fib(n - 1) + fib(n - 2);
}

function main() -> int {
    const int N = 5;
    int a[N];
    int i = 0;
    int sum = 0;
    while (i < N) {
        a[i] = i * i;
        sum = sum + a[i];
        i = i + 1;
    }
    println(sum);
    println(fib(10));
    println(1.0 / 4.0);
    println("ok");
    return fib(7);
}
'''


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _build(workdir: str, text: str, *extra: str):
    """用 cincc 生成 PE, 返回 (完成进程, 产物路径)。"""
    prog = os.path.join(workdir, 'p.cin')
    _write(prog, text)
    out = os.path.join(workdir, 'p.exe')
    cmd = [CINCC, '-o', out, '-target', 'x86_64-windows'] + list(extra) + [prog]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    return proc, out


def _pe_info(path: str) -> dict:
    """解析 PE32+ 头里本文件关心的字段。"""
    with open(path, 'rb') as f:
        data = f.read()
    assert data[:2] == b'MZ', '缺少 DOS 头'
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    assert data[pe:pe + 4] == b'PE\0\0', '缺少 PE 签名'
    machine, nsect = struct.unpack_from('<HH', data, pe + 4)
    opt_size = struct.unpack_from('<H', data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from('<H', data, opt)[0]
    entry = struct.unpack_from('<I', data, opt + 16)[0]
    image_base = struct.unpack_from('<Q', data, opt + 24)[0]
    subsystem = struct.unpack_from('<H', data, opt + 68)[0]
    sect = []
    for i in range(nsect):
        o = opt + opt_size + i * 40
        name = data[o:o + 8].rstrip(b'\0').decode('ascii', 'replace')
        vsize, vaddr, rsize, _roff = struct.unpack_from('<IIII', data, o + 8)
        sect.append({'name': name, 'vsize': vsize, 'vaddr': vaddr,
                     'rsize': rsize})
    return {'machine': machine, 'magic': magic, 'entry': entry,
            'image_base': image_base, 'subsystem': subsystem,
            'sections': sect, 'size': len(data)}


def _run(exe: str):
    proc = subprocess.run([exe], capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    return proc.returncode, proc.stdout


@needs_cincc
def test_pe_image_is_pe32_plus_executable(workdir):
    """产物必须是 PE32+ 的可执行映像 (而不是披着 .exe 的 ELF)。"""
    proc, out = _build(workdir, COMBINED)
    assert proc.returncode == 0, proc.stderr
    info = _pe_info(out)
    assert info['magic'] == 0x20B, '必须是 PE32+ (0x20b), 不是 PE32 (0x10b)'
    assert info['machine'] == 0x8664, 'Machine 必须是 x86-64 (0x8664)'
    assert info['subsystem'] == 3, '必须是控制台子系统 (3)'
    assert info['entry'] == info['sections'][0]['vaddr'], \
        '入口点必须落在 .text 起点'


@needs_cincc
def test_pe_sections_have_no_rva_gap(workdir):
    """节与节之间不能留 RVA 空洞。

    加载器对「VirtualSize 没铺到下一节起点」的映像直接判无效
    (``WinError 193 不是有效的 Win32 应用程序``), 而 objdump 看头字段又都是
    合法的, 所以这条必须由用例守住。
    """
    proc, out = _build(workdir, COMBINED)
    assert proc.returncode == 0, proc.stderr
    sect = _pe_info(out)['sections']
    assert [s['name'] for s in sect][:2] == ['.text', '.data']
    for prev, nxt in zip(sect, sect[1:]):
        assert prev['vaddr'] + prev['vsize'] >= nxt['vaddr'], (
            f"{prev['name']} 与 {nxt['name']} 之间留了 RVA 空洞: "
            f"{prev['vaddr'] + prev['vsize']:#x} < {nxt['vaddr']:#x}")


@needs_cincc
@pytest.mark.parametrize('fname', ['io.cin', 'net.cin'])
def test_pe_reports_unimplemented_syscall(workdir, fname):
    """尚未实现的宿主能力必须显式报错, 不能生成跑到一半才崩的 exe。"""
    proc, out = _build(workdir, f'import "{fname}"\n'
                                'function main() -> int { return 0 }\n')
    assert proc.returncode != 0
    assert 'Windows 后端暂未实现' in proc.stderr
    assert not os.path.isfile(out), '失败时不应留下半成品'


@needs_cincc
@needs_windows
def test_pe_runs_combined_program(workdir):
    """递归 + 数组 + const 维度 + 浮点 + 字符串 端到端。"""
    proc, out = _build(workdir, COMBINED)
    assert proc.returncode == 0, proc.stderr
    rc, stdout = _run(out)
    assert stdout == '30\n55\n0.25\nok\n'
    assert rc == 13, '退出码必须是 main 的返回值 fib(7)'


@needs_cincc
@needs_windows
def test_pe_float_print_is_decimal_not_truncated(workdir):
    """FTOA 输出真实小数: 旧实现截断成整数, 0.5 会打成 0。"""
    proc, out = _build(workdir, 'function main() -> int {\n'
                                '    println(0.5);\n'
                                '    println(-2.75);\n'
                                '    println(1.0 / 3.0);\n'
                                '    println(3.0);\n'
                                '    return 0;\n'
                                '}\n')
    assert proc.returncode == 0, proc.stderr
    rc, stdout = _run(out)
    assert stdout.splitlines() == ['0.5', '-2.75', '0.333333333333333', '3']
    assert rc == 0


@needs_cincc
@needs_windows
def test_pe_platform_syscalls(workdir):
    """OSNAME / TIMEMS / SLEEPMS 走 kernel32 分支而不是 Unix 系统调用。"""
    proc, out = _build(workdir, 'function main() -> int {\n'
                                '    println(os_name());\n'
                                '    int t = time_ms();\n'
                                '    sleep_ms(10);\n'
                                '    if (time_ms() > t) { println("tick"); }\n'
                                '    return 0;\n'
                                '}\n')
    assert proc.returncode == 0, proc.stderr
    rc, stdout = _run(out)
    assert stdout.splitlines() == ['windows', 'tick']
    assert rc == 0


@needs_cincc
@needs_windows
def test_native_cli_prefers_pe_on_windows(workdir, monkeypatch):
    """``--native`` 在 Windows 上直接出 PE, 不必绕 WSL。

    ``-target`` 必须排在入口文件之前: Go 的 flag 解析遇到第一个位置参数就停,
    排到后面会被当成多余参数, cincc 只回一行用法。
    """
    recorded = {}

    class _Proc:
        returncode = 0
        stdout = ''
        stderr = ''

    def fake_run(cmd, **kwargs):
        recorded['cmd'] = list(cmd)
        return _Proc()

    monkeypatch.setattr(subprocess, 'run', fake_run)
    prog = os.path.join(workdir, 'n.cin')
    _write(prog, 'function main() -> int { return 0 }\n')
    assert cli.main([prog, '--native-out',
                     os.path.join(workdir, 'n.exe'),
                     '--log-level', 'ERROR']) == 0
    cmd = recorded['cmd']
    assert cmd[-1] == prog
    assert cmd[cmd.index('-target') + 1] == 'x86_64-windows'
    assert cmd.index('-target') < cmd.index(prog)
