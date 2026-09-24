#!/usr/bin/env python3
"""三执行路径一致性检查: 解释器 / JIT / Go 原生 必须产生相同输出。

用法::

    python script/check_paths.py [file.cin ...]

默认检查 ``examples/*.cin``。全部一致返回 0, 否则打印首个分歧并以 1 退出。

注: 根目录的 ``basic.cin`` 使用 ``srand(time())``, 输出依赖运行时钟, 因此
不纳入逐字节比较。
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 三条执行路径: (名称, 额外参数)
PATHS = [
    ('解释器', ['--no-native']),
    ('JIT', ['--no-native', '--jit']),
    ('Go 原生', []),
]

#: 按设计只能在 Go 原生路径运行的示例 (宿主能力: GUI/audio/system/Termux)
NATIVE_ONLY = {'system_interaction.cin'}


def run(path: str, extra):
    """用指定的执行路径跑一个 .cin, 返回 (退出码, stdout 原始字节)。"""
    cmd = [sys.executable, os.path.join(ROOT, 'cpu.py'), path,
           '--log-level', 'ERROR', *extra]
    r = subprocess.run(cmd, capture_output=True, cwd=ROOT, timeout=300)
    return r.returncode, r.stdout


def main(argv):
    if argv:
        files = list(argv)
    else:
        ex_dir = os.path.join(ROOT, 'examples')
        files = [os.path.join(ex_dir, f) for f in sorted(os.listdir(ex_dir))
                 if f.endswith('.cin') and f not in NATIVE_ONLY]

    failed = 0
    for path in files:
        name = os.path.basename(path)
        results = {}
        for label, extra in PATHS:
            try:
                results[label] = run(path, extra)
            except subprocess.TimeoutExpired:
                results[label] = (None, b'<timeout>')
        base_label, base = PATHS[0][0], results[PATHS[0][0]]
        mismatched = [lbl for lbl, _ in PATHS if results[lbl] != base]
        if mismatched:
            failed += 1
            print(f"[FAIL] {name}: 路径不一致 -> {', '.join(mismatched)}")
            for label, _ in PATHS:
                rc, out = results[label]
                print(f"    {label:6s} rc={rc} out={out!r}")
        else:
            print(f"[ok]   {name} (三条路径输出一致, {len(base[1])} 字节)")
    print(f"\n{len(files) - failed}/{len(files)} passed")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
