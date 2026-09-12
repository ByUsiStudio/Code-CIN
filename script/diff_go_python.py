#!/usr/bin/env python3
"""差分测试: 对比 Python 编译器/解释器 与 Go 编译器/VM 对同一 .cin 程序的输出。

用法:
    python script/diff_go_python.py [file.cin ...]

默认对比 examples/*.cin。要求先编译 Go CLI:
    cd codecin/native && go build -o codecin ./cmd/codecin
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GO_CLI = os.path.join(ROOT, 'codecin', 'native', 'codecin.exe')
if not os.path.exists(GO_CLI):
    GO_CLI = os.path.join(ROOT, 'codecin', 'native', 'codecin')

from codecin.cin import CINCompiler          # noqa: E402
from codecin.cpu import CPU                  # noqa: E402
from codecin.config import Config            # noqa: E402


def run_python(path: str):
    """Python 编译 + Go 原生 VM 执行 (与 Go CLI 共用 VM, 以隔离编译器差异)。"""
    res = CINCompiler().compile(path)
    cpu = CPU(Config(interactive_mode=False, log_level='ERROR', use_native=True))
    cpu.instructions = res.instructions
    cpu.labels = res.labels
    cpu.data_labels = res.data_labels
    for addr, data in res.data_writes:
        cpu.memory.write_block(addr, data)
    cpu.entry_pc = 0
    cpu.pc = 0
    cpu._capture_output = True
    try:
        cpu.run()
        return ''.join(cpu.output_buffer), None
    except Exception as e:  # noqa: BLE001
        return ''.join(cpu.output_buffer), f"{type(e).__name__}: {e}"


def run_go(path: str):
    """Go CLI: 编译 + 运行, 返回 (stdout, returncode, stderr)。

    以二进制捕获再显式 UTF-8 解码, 避免 text 模式的换行归一化 (\\r\\n → \\n)。
    """
    r = subprocess.run([GO_CLI, path], capture_output=True, cwd=ROOT, timeout=180)
    out = r.stdout.decode('utf-8', 'replace')
    err = r.stderr.decode('utf-8', 'replace')
    return out, r.returncode, err


def run_python_bytecode(path: str):
    """Python 编译器产物的 UCBC 字节 (与 Go CLI --dump-bytecode 对应)。"""
    from codecin.native import encode_program
    res = CINCompiler().compile(path)
    labels = dict(res.labels)
    labels.update(res.data_labels)
    return encode_program(res.instructions, getattr(res, 'entry_pc', 0) or 0,
                          labels)


def run_go_bytecode(path: str):
    """Go CLI --dump-bytecode 的十六进制输出 -> bytes。"""
    if GO_CLI is None:
        return None, 'Go CLI not built'
    r = subprocess.run([GO_CLI, path, '--dump-bytecode'], capture_output=True,
                       cwd=ROOT, timeout=180)
    if r.returncode != 0:
        return None, r.stderr.decode('utf-8', 'replace').strip()
    try:
        return bytes.fromhex(r.stdout.decode('ascii').strip()), None
    except ValueError as e:
        return None, f'bad hex dump: {e}'


def _first_diff(a: bytes, b: bytes) -> str:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return f'首个差异在第 {i} 字节 (0x{i:x}): py=0x{a[i]:02x} go=0x{b[i]:02x}'
    return f'长度不同: py={len(a)} go={len(b)}'


def main(argv):
    if argv:
        files = argv
    else:
        ex_dir = os.path.join(ROOT, 'examples')
        files = [os.path.join(ex_dir, f) for f in sorted(os.listdir(ex_dir))
                 if f.endswith('.cin')]
        # 注: basic.cin 位于仓库根目录 (不在 examples/ 下), 且使用 srand(time())
        #     播种, 输出依赖运行时钟, 因此不纳入比较
        #     (由 tests/test_go_compiler.py::test_go_cli_basic_demo 做标记位校验)。
    # 产物级比对: 先比编译出的 UCBC 字节, 再比程序 stdout
    compare_bytecode = os.environ.get('DIFF_BYTECODE', '1') != '0'
    failed = 0
    for path in files:
        py_out, py_err = run_python(path)
        go_out, go_rc, go_err = run_go(path)
        name = os.path.basename(path)
        if py_err is not None:
            print(f"[FAIL] {name}: python raised {py_err}")
            failed += 1
            continue
        if go_rc != 0:
            print(f"[FAIL] {name}: go exited {go_rc}: {go_err.strip()}")
            failed += 1
            continue
        if py_out != go_out:
            print(f"[FAIL] {name}: output differs")
            print(f"  python: {py_out!r}")
            print(f"  go    : {go_out!r}")
            failed += 1
            continue
        if compare_bytecode:
            try:
                py_bc = run_python_bytecode(path)
            except Exception as e:  # noqa: BLE001
                print(f"[FAIL] {name}: python bytecode dump failed: {e}")
                failed += 1
                continue
            go_bc, bc_err = run_go_bytecode(path)
            if go_bc is None:
                print(f"[FAIL] {name}: go bytecode dump failed: {bc_err}")
                failed += 1
                continue
            if py_bc != go_bc:
                print(f"[FAIL] {name}: 编译产物字节不同 —— {_first_diff(py_bc, go_bc)}")
                failed += 1
                continue
            print(f"[ok]   {name} (产物 {len(py_bc)} 字节一致)")
        else:
            print(f"[ok]   {name}")
    print(f"\n{len(files) - failed}/{len(files)} passed")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
