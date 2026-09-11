"""CLI (建议 3): argparse 参数解析、帮助与退出码。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codecin import cli  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASIC_CIN = os.path.join(ROOT, 'basic.cin')


def test_help_returns_zero(capsys):
    assert cli.main(['--help']) == 0
    out = capsys.readouterr().out
    assert '--sandbox' in out and '--optimize' in out and '--cache-assoc' in out
    assert '--no-native' in out


def test_missing_program_returns_error(capsys):
    assert cli.main([]) == 1


def test_unknown_option_returns_error_code(capsys):
    code = cli.main(['--definitely-not-an-option'])
    assert code in (1, 2)


def test_run_cin_no_native(capsys, workdir):
    code = cli.main([BASIC_CIN, '--no-native', '--max-instructions', '2000',
                     '--log-level', 'ERROR'])
    assert code == 0


def test_compile_only_creates_bin(workdir):
    out = os.path.join(workdir, 'out.bin')
    code = cli.main([BASIC_CIN, '--compile-only', '-o', out, '--log-level',
                     'ERROR'])
    assert code == 0
    assert os.path.exists(out) and os.path.getsize(out) > 0


def test_missing_file_returns_load_error(capsys):
    code = cli.main([os.path.join(ROOT, 'no_such_file.cin'), '--log-level',
                     'ERROR'])
    assert code == 1


def test_parser_exposes_all_expected_options():
    parser = cli.build_parser()
    actions = {a.dest for a in parser._actions}
    for expected in ('program', 'debug', 'step', 'no_native', 'enable_jit',
                     'profile', 'compile_to_bin', 'compile_only', 'output',
                     'crom', 'mem_size', 'max_instructions', 'cache_size',
                     'cache_assoc', 'optimize', 'log_level', 'log_file',
                     'auto_save_crom', 'compress_crom', 'sandbox',
                     'strict', 'no_io'):
        assert expected in actions, f'missing option dest: {expected}'
