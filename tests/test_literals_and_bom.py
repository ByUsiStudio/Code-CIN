"""数值字面量与 BOM import 的双端一致性 (docs/SUGGESTIONS.md §3.1.8 / §3.1.9)。

修复前:
  * `int x = 0xFFu` 在 Python 侧抛裸 ValueError (Go 输出 255);
  * `1.5f` Python 抛 ValueError; Go 报 Malformed numeric literal;
  * `9223372036854775808` Python 静默截断成负数, Go 报错;
  * 带 UTF-8 BOM 的源文件里 `import` 静默失效 (两端都报无关的解析错误)。
本文件按"双端必须给出相同结果"来断言。
"""

import os
import subprocess

import pytest

from codecin import CPU, Config
from codecin.cin import CINCompiler
from codecin.errors import CompilerError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GO_CLI_CANDIDATES = (
    'codecin/native/codecin.exe', 'codecin/native/codecin',
    'codecin/codecin.exe', 'codecin/codecin',
)


def _go_cli():
    for rel in GO_CLI_CANDIDATES:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            return p
    return None


needs_go_cli = pytest.mark.skipif(_go_cli() is None, reason='Go CLI not built')


def _run_python_source(src: str, use_native: bool = True) -> str:
    res = CINCompiler().compile_source(src)
    cpu = CPU(Config(interactive_mode=False, log_level='ERROR', use_native=use_native))
    cpu.instructions = res.instructions
    cpu.labels = res.labels
    cpu.data_labels = res.data_labels
    for addr, data in res.data_writes:
        cpu.memory.write_block(addr, data)
    cpu.entry_pc = 0
    cpu.pc = 0
    cpu._capture_output = True
    cpu.run()
    return ''.join(cpu.output_buffer)


def _write(path: str, text: str, bom: bool = False) -> None:
    data = text.encode('utf-8')
    if bom:
        data = b'\xef\xbb\xbf' + data
    with open(path, 'wb') as f:
        f.write(data)


def _run_go_file(path: str):
    return subprocess.run([_go_cli(), path], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', cwd=ROOT, timeout=180)


# name, 字面量, 打印函数
LITERALS = [
    ('hex_u_suffix', '0xFFu', 'int_to_str'),
    ('hex_plain', '0xFF', 'int_to_str'),
    ('hex_trailing_f', '0x1F', 'int_to_str'),
    ('bin_u_suffix', '0b1010U', 'int_to_str'),
    ('oct_l_suffix', '0o17L', 'int_to_str'),
    ('dec_u_suffix', '42u', 'int_to_str'),
    ('dec_l_suffix', '100L', 'int_to_str'),
    ('dec_plain', '7', 'int_to_str'),
    ('float_f_suffix', '1.5f', 'float_to_str'),
    ('float_plain', '1.5', 'float_to_str'),
    ('float_dec_suffix', '100f', 'float_to_str'),
    ('hex_max_u64', '0xFFFFFFFFFFFFFFFF', 'int_to_str'),
]


@pytest.mark.parametrize('name,lit,printer', LITERALS, ids=[c[0] for c in LITERALS])
def test_literal_python_compiles(name, lit, printer):
    """两端都必须接受这些字面量 (不再是裸 ValueError)。"""
    src = f'function main() -> int {{ println({printer}({lit})) return 0 }}'
    out = _run_python_source(src)
    assert out.strip(), f'{lit} 未产生输出'


@pytest.mark.parametrize('name,lit,printer', LITERALS, ids=[c[0] for c in LITERALS])
@needs_go_cli
def test_literal_parity_with_go(name, lit, printer, workdir):
    """同一字面量在 Go 与 Python 侧必须打印出完全相同的结果。"""
    src = f'function main() -> int {{ println({printer}({lit})) return 0 }}'
    py_out = _run_python_source(src).strip()
    path = os.path.join(workdir, 'lit.cin')
    _write(path, src)
    r = _run_go_file(path)
    assert r.returncode == 0, f'{lit}: Go 报错 {r.stderr!r}'
    assert py_out == r.stdout.strip(), \
        f'{lit}: python={py_out!r} go={r.stdout.strip()!r}'


def test_int_out_of_64bit_range_rejected_python():
    src = 'function main() -> int { int x = 9223372036854775808 return 0 }'
    with pytest.raises(CompilerError):
        CINCompiler().compile_source(src)


@needs_go_cli
def test_int_out_of_64bit_range_rejected_go(workdir):
    src = 'function main() -> int { int x = 9223372036854775808 return 0 }'
    path = os.path.join(workdir, 'big.cin')
    _write(path, src)
    r = _run_go_file(path)
    assert r.returncode != 0


# ---------------- BOM import ----------------

MODULE = '''
function helper() -> int {
    return 41
}
'''

MAIN_WITH_IMPORT = '''
import "mod.cin"

function main() -> int {
    println("R=" + int_to_str(helper() + 1))
    return 0
}'''


def test_bom_import_python(workdir):
    _write(os.path.join(workdir, 'mod.cin'), MODULE)
    main_path = os.path.join(workdir, 'main.cin')
    _write(main_path, MAIN_WITH_IMPORT, bom=True)

    res = CINCompiler().compile(main_path)
    cpu = CPU(Config(interactive_mode=False, log_level='ERROR', use_native=True))
    cpu.instructions = res.instructions
    cpu.labels = res.labels
    cpu.data_labels = res.data_labels
    for addr, data in res.data_writes:
        cpu.memory.write_block(addr, data)
    cpu.entry_pc = 0
    cpu.pc = 0
    cpu._capture_output = True
    cpu.run()
    assert 'R=42' in ''.join(cpu.output_buffer)


@needs_go_cli
def test_bom_import_go(workdir):
    _write(os.path.join(workdir, 'mod.cin'), MODULE)
    main_path = os.path.join(workdir, 'main.cin')
    _write(main_path, MAIN_WITH_IMPORT, bom=True)
    r = _run_go_file(main_path)
    assert r.returncode == 0, f'Go 侧 BOM import 失败: {r.stderr!r}'
    assert 'R=42' in r.stdout
