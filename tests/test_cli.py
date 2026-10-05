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
    assert '--sandbox' in out and '--optimize' in out and '--strict' in out


def test_missing_program_returns_error(capsys):
    assert cli.main([]) == 1


def test_list_libs_reports_categories(capsys):
    """--libs: 列出内置库并标注执行路径要求 (纯 CIN / 兼容层 / 需原生)。"""
    assert cli.main(['--libs']) == 0
    out = capsys.readouterr().out
    # 三语言兼容层库
    for lib in ('cstd.cin', 'cppstd.cin', 'gostd.cin'):
        assert lib in out
    assert '兼容层' in out
    # 宿主能力库必须标为"需原生运行时"
    assert 'gui.cin' in out and '需原生运行时' in out
    # gostd 的原生依赖项要有标注
    assert 'go_os_args' in out


def test_unknown_option_returns_error_code(capsys):
    code = cli.main(['--definitely-not-an-option'])
    assert code in (1, 2)


def test_instruction_limit_is_reported_as_failure(capsys, workdir):
    """步数用尽不再伪装成正常结束。

    旧行为: 解释器只 warning + break, 原生 VM 返回 StatusDone,
    于是被截断的程序以退出码 0 报告成功。
    """
    code = cli.main([BASIC_CIN, '--max-instructions', '2000',
                     '--log-level', 'ERROR'])
    assert code == 1
    out = capsys.readouterr().out
    assert 'instruction limit' in out.lower()


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
    for expected in ('program', 'show_help', 'build_info', 'json_output',
                     'list_libs', 'log_level', 'log_file', 'sandbox',
                     'mem_size', 'max_instructions', 'compile_to_bin',
                     'compile_only', 'output', 'crom', 'auto_save_crom',
                     'compress_crom', 'optimize', 'strict', 'seed',
                     'bounds_check', 'disasm', 'build_exe', 'build_target',
                     'build_keep_temp'):
        assert expected in actions, f'missing option dest: {expected}'
