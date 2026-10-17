"""CLI (建议 3): argparse 参数解析、帮助与退出码。"""

import os
import sys

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


def test_debug_flag_forces_debug_and_prints_final_state(workdir, capsys):
    """--debug: 即使显式给出 --log-level ERROR 也强制 DEBUG, 并输出收尾现场。"""
    path = os.path.join(workdir, 'debug.cin')
    with open(path, 'w', encoding='utf-8') as f:
        f.write("""
function main() -> int {
    int x = 21 * 2
    return x
}""")
    code = cli.main([path, '--debug', '--log-level', 'ERROR'])
    assert code == 0
    out = capsys.readouterr().out
    assert 'Execution Complete' in out
    assert 'Execution Statistics' in out
    # DEBUG 级别生效: 原生执行结果只在 debug 日志里输出
    assert 'Native result' in out


def test_debug_mode_forces_debug_logger():
    """程序化构造 Config(debug_mode=True) 也应强制 DEBUG 级别。"""
    from codecin import Config
    from codecin.cpu import CPU
    cpu = CPU(Config(debug_mode=True, log_level='ERROR'))
    assert cpu.logger.is_debug


def test_build_backend_defaults_to_auto():
    ns = cli.build_parser().parse_args(['prog.cin'])
    assert ns.build_backend == 'auto'
    assert ns.build_cache is True
    ns = cli.build_parser().parse_args(['prog.cin', '--build-backend', 'vm'])
    assert ns.build_backend == 'vm'
    ns = cli.build_parser().parse_args(['prog.cin', '--no-build-cache'])
    assert ns.build_cache is False


def test_emit_object_and_link_asm_cli(workdir, monkeypatch):
    """--emit-object + --link: 两个 ASM 对象独立编译后链接运行。"""
    import subprocess
    main = os.path.join(workdir, 'main.asm')
    lib = os.path.join(workdir, 'lib.asm')
    with open(main, 'w', encoding='utf-8') as f:
        f.write('.text\nmain:\n    CALL helper\n'
                '    SYS #22\n    SYS #24\n    OUT #10\n    HALT\n')
    with open(lib, 'w', encoding='utf-8') as f:
        f.write('.text\nhelper:\n    MOV x0, #42\n    RET\n')
    mco = os.path.join(workdir, 'main.co')
    lco = os.path.join(workdir, 'lib.co')
    exe = os.path.join(workdir, 'linked.exe')
    assert cli.main(['--emit-object', main, '-o', mco]) == 0
    assert cli.main(['--emit-object', lib, '-o', lco]) == 0
    assert os.path.isfile(mco) and os.path.isfile(lco)
    assert cli.main(['--link', mco, '--link', lco, '-o', exe,
                     '--optimize', '3', '--gc-sections']) == 0
    r = subprocess.run([exe], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == '42'

    # 第二次链接应命中对象链接缓存, 不应再调用 build_native。
    import codecin.aot as aot
    def _boom(*args, **kwargs):
        raise AssertionError('cache hit expected')
    monkeypatch.setattr(aot, 'build_native', _boom)
    assert cli.main(['--link', mco, '--link', lco, '-o', exe,
                     '--optimize', '3', '--gc-sections']) == 0


def test_emit_object_and_link_cin_cli(workdir):
    """CIN: extern 库 + main 独立编译对象, 链接后运行。"""
    import subprocess
    lib = os.path.join(workdir, 'lib.cin')
    main = os.path.join(workdir, 'main.cin')
    with open(lib, 'w', encoding='utf-8') as f:
        f.write('function helper() -> int {\n    return 7\n}\n')
    with open(main, 'w', encoding='utf-8') as f:
        f.write('extern function helper() -> int\n\n'
                'function main() -> int {\n'
                '    println(int_to_str(helper()))\n'
                '    return 0\n}\n')
    mco = os.path.join(workdir, 'main.co')
    lco = os.path.join(workdir, 'lib.co')
    exe = os.path.join(workdir, 'cin_link.exe')
    assert cli.main(['--emit-object', main, '-o', mco]) == 0
    assert cli.main(['--emit-object', lib, '-o', lco]) == 0
    assert cli.main(['--link', mco, '--link', lco, '-o', exe]) == 0
    r = subprocess.run([exe], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == '7'


def test_emit_archive_and_link(workdir):
    """--emit-archive: 把对象打包为 .colib, --link 只拉取需要的成员。"""
    import subprocess
    main = os.path.join(workdir, 'main.asm')
    lib = os.path.join(workdir, 'lib.asm')
    unused = os.path.join(workdir, 'unused.asm')
    with open(main, 'w', encoding='utf-8') as f:
        f.write('.text\nmain:\n    CALL helper\n    HALT\n')
    with open(lib, 'w', encoding='utf-8') as f:
        f.write('.text\nhelper:\n    MOV x0, #42\n    RET\n')
    with open(unused, 'w', encoding='utf-8') as f:
        f.write('.text\nunused_fn:\n    RET\n')
    co = {}
    for name, src in (('main', main), ('lib', lib), ('unused', unused)):
        co[name] = os.path.join(workdir, name + '.co')
        assert cli.main(['--emit-object', src, '-o', co[name]]) == 0
    archive = os.path.join(workdir, 'lib.colib')
    assert cli.main(['--emit-archive', '--link', co['lib'],
                     '--link', co['unused'], '-o', archive]) == 0
    exe = os.path.join(workdir, 'archive_app.exe')
    assert cli.main(['--link', co['main'], '--link', archive,
                     '-o', exe]) == 0
    r = subprocess.run([exe], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=60)
    assert r.returncode == 0, r.stderr


def test_link_cin_sources_directly(workdir):
    """--link 直接接受 .cin 源文件, 即时编译对象并链接。"""
    import subprocess
    lib = os.path.join(workdir, 'lib.cin')
    main = os.path.join(workdir, 'main.cin')
    with open(lib, 'w', encoding='utf-8') as f:
        f.write('function helper() -> int { return 7 }\n')
    with open(main, 'w', encoding='utf-8') as f:
        f.write('extern function helper() -> int\n'
                'function main() -> int {\n'
                '    println(int_to_str(helper()))\n'
                '    return 0\n}\n')
    exe = os.path.join(workdir, 'src_link.exe')
    assert cli.main(['--link', main, '--link', lib, '-o', exe]) == 0
    r = subprocess.run([exe], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == '7'


def test_parser_exposes_all_expected_options():
    parser = cli.build_parser()
    actions = {a.dest for a in parser._actions}
    for expected in ('program', 'show_help', 'build_info', 'json_output',
                     'list_libs', 'debug', 'log_level', 'log_file', 'sandbox',
                     'mem_size', 'max_instructions', 'compile_to_bin',
                     'compile_only', 'output', 'crom', 'auto_save_crom',
                     'compress_crom', 'optimize', 'strict', 'seed',
                     'bounds_check', 'disasm', 'emit_object', 'link',
                     'emit_archive', 'gc_sections', 'build_exe', 'build_target',
                     'build_keep_temp', 'build_backend', 'build_cache',
                     'build_debug'):
        assert expected in actions, f'missing option dest: {expected}'
