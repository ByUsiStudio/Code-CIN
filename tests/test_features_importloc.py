"""③ import 行级源映射: 错误定位精确到模块 file:line。"""

import os

import pytest

from codecin.cin import CINCompiler
from codecin.errors import CompilerError
from tests.helpers import run_cin_file


def _write(workdir, name, text):
    p = os.path.join(workdir, name)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)
    return p


def test_tokenizer_error_reports_module_file_and_line(workdir):
    mod = _write(workdir, 'badmod.cin', (
        'function mod(int a) -> int {\n'
        '    return a\n'
        '}\n'
        'int x = ?\n'))                     # 第 4 行词法错误
    main = _write(workdir, 'main.cin', (
        'import "./badmod.cin"\n'
        'function main() -> int { return mod(1) }\n'))
    with pytest.raises(CompilerError) as exc:
        CINCompiler().compile(main)
    msg = str(exc.value)
    assert os.path.basename(mod) in msg
    assert ':4' in msg
    assert '?' in msg


def test_parser_error_reports_module_file_and_line(workdir):
    # 模块内表达式非法 token ';' -> Unexpected token, 错误 token 定位到模块第 2 行
    mod = _write(workdir, 'pmod.cin', (
        'function helper(int a) -> int {\n'
        '    int b = ;\n'                  # 第 2 行语法错误
        '    return b\n'
        '}\n'))
    main = _write(workdir, 'main.cin', (
        'import "./pmod.cin"\n'
        'function main() -> int { return helper(2) }\n'))
    with pytest.raises(CompilerError) as exc:
        CINCompiler().compile(main)
    msg = str(exc.value)
    assert os.path.basename(mod) in msg
    assert ':2' in msg
    assert 'SEMI' in msg or ';' in msg


def test_assert_in_module_reports_module_line(workdir, capsys):
    mod = _write(workdir, 'amod.cin', (
        'function checked(int v) -> int {\n'
        '    assert(v > 0, "positive required")\n'   # 第 2 行
        '    return v * 2\n'
        '}\n'))
    main = _write(workdir, 'amain.cin', (
        'import "./amod.cin"\n'
        'function main() -> int { return checked(-5) }\n'))
    cpu = run_cin_file(main)
    del cpu
    out = capsys.readouterr().out
    assert os.path.basename(mod) in out
    assert ':2' in out
    assert 'positive required' in out


def test_single_file_location_unaffected(workdir, capsys):
    p = _write(workdir, 'single.cin',
               'function main() -> int { assert(false) }\n')
    cpu = run_cin_file(p)
    del cpu
    out = capsys.readouterr().out
    assert os.path.basename(p) in out and ':1' in out
