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
    r = subprocess.run([GO_CLI, path], capture_output=True, cwd=ROOT)
    out = r.stdout.decode('utf-8', 'replace')
    err = r.stderr.decode('utf-8', 'replace')
    return out, r.returncode, err


def main(argv):
    if argv:
        files = argv
    else:
        ex_dir = os.path.join(ROOT, 'examples')
        files = [os.path.join(ex_dir, f) for f in sorted(os.listdir(ex_dir))
                 if f.endswith('.cin')]
        # 注: basic.cin 位于仓库根目录 (不在 examples/ 下), 且使用 srand(time())
        #     播种, 输出依赖运行时钟, 因此不纳入逐字节比较
        #     (由 tests/test_go_compiler.py::test_go_cli_basic_demo 做标记位校验)。
        #     本脚本比较的是程序 stdout, 不含编译产物字节级比对。
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
        print(f"[ok]   {name}")
    print(f"\n{len(files) - failed}/{len(files)} passed")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
