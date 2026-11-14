"""命令行入口: argparse 参数解析 -> 加载程序 -> 运行。

建议 3: 采用 argparse 统一参数表 (不再三份手写解析), 全部选项由
build_parser() 单一来源定义, --help 与解析行为自动保持一致。
"""

import argparse
import contextlib
import os
import sys
import time
from typing import List, Optional

from . import __version__
from .config import Config
from .console import Colors, Console, Panel

HELP_INTRO = f"""{Colors.colorize(f'Code CIN v{__version__}', Colors.CYAN, True)}

{Colors.colorize('Usage:', Colors.YELLOW)}
  codecin [options] <program.[cin|pl|asm|bin]> [args...]

{Colors.colorize('Program arguments:', Colors.YELLOW)}
  program 之后的裸参数原样传给 CIN 程序 (arg_count()/arg(i) 读取);
  CLI 选项放在 program 之前或之后均可识别, 形如选项的参数请用 `--` 显式分隔。

{Colors.colorize('Supported formats:', Colors.YELLOW)}
  .cin   CIN 高级语言 (函数/struct/数组/浮点/字符串)
  .pl    Code CIN 汇编语言 (PL 关键字风格)
  .asm   Code CIN 汇编
  .bin   UCBC 字节码 (由 --compile 生成)

{Colors.colorize('Toolchain (v6.2.0):', Colors.YELLOW)}
  mkn   项目构建/依赖 (Maven 风格)
  pic   去中心化包管理 (pip 风格, 安装后可直接 import)
  cvm   CVM 虚拟机/模块运行时 (JVM 风格)
"""

def _stdout_is_tty() -> bool:
    """stdout 是否为真实终端 (仅此时打印运行前进度状态)。"""
    try:
        return bool(sys.stdout is not None and sys.stdout.isatty())
    except (OSError, ValueError, AttributeError):
        return False


def build_parser() -> argparse.ArgumentParser:
    """参数表单一来源 (与 Config 字段一一对应)。"""
    p = argparse.ArgumentParser(
        prog='codecin',
        description='Code CIN - 高级语言与运行时 (CIN/PL/ASM 工具链 + 多执行路径)',
        add_help=False,
    )
    p.add_argument('program', nargs='?', default=None,
                   metavar='program.[cin|pl|asm|bin]',
                   help='程序源文件/字节码/镜像')
    p.add_argument('--help', '-h', action='store_true', dest='show_help',
                   help='显示帮助')
    from . import __version__
    p.add_argument('--version', '-V', action='version',
                   version=f'Code CIN {__version__}',
                   help='显示版本号并退出')
    p.add_argument('--build-info', action='store_true', dest='build_info',
                   help='显示构建/运行环境信息 (版本、解释器、平台、原生库、'
                        '包路径) 后退出; 与 --json 合用输出机器可读 JSON')
    p.add_argument('--json', action='store_true', dest='json_output',
                   help='配合 --build-info 输出 JSON (需与 --build-info 同时使用)')
    p.add_argument('--libs', action='store_true', dest='list_libs',
                   help='列出内置标准库并标注执行路径要求 '
                        '(纯 CIN / C·C++·Go 兼容层 / 需原生运行时) 后退出')

    # 日志与调试
    p.add_argument('--debug', action='store_true',
                   help='调试模式 (强制 DEBUG 日志, 执行结束输出 CPU 状态与统计, '
                        '异常打印完整堆栈)')
    p.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR',
                                           'CRITICAL'],
                   help='日志级别 (默认 INFO)')
    p.add_argument('--log-file', metavar='FILE', help='日志输出到文件')
    p.add_argument('--sandbox', action='store_true',
                   help='沙箱模式 (在 Go 引擎侧拦截全部宿主能力系统调用)')

    # 资源限制
    p.add_argument('--mem-size', type=int, metavar='BYTES',
                   help='内存大小 (默认 1073741824 = 1 GiB, 动态分配, '
                        '只占实际写入的物理内存)')
    # --max-instructions 已弃用: 默认不限制指令数 (保留为隐藏参数以免破坏脚本)
    p.add_argument('--max-instructions', type=int, metavar='N',
                   help=argparse.SUPPRESS)

    # 编译 / CROM
    p.add_argument('--compile', action='store_true', dest='compile_to_bin',
                   help='编译为 .sdgcc 产物后继续执行')
    p.add_argument('--compile-only', action='store_true',
                   help='仅编译为 .sdgcc 不执行')
    p.add_argument('-o', '--output', metavar='FILE',
                   help='指定输出文件 (.crom/.sdgcc/.bin)')
    p.add_argument('--crom', metavar='FILE', help='加载指定 .crom 内存镜像')
    p.add_argument('--save', action='store_true', dest='auto_save_crom',
                   help='执行后保存 .crom 内存镜像')
    p.add_argument('--no-compress', action='store_false', dest='compress_crom',
                   help='.crom 不压缩 (默认压缩)')
    p.add_argument('--sdgcc-compress', action='store_true', dest='sdgcc_compress',
                   help='.sdgcc 的 META/SYMS/SRCS 段启用 zlib 压缩 (版本升为 2)')

    # 语言/汇编
    p.add_argument('--optimize', type=int, metavar='0-3',
                   help='IR 优化级别 (0 关闭; 1 窥孔; 2 常量折叠; '
                        '3 不可达代码删除; 默认 0)')
    p.add_argument('--strict', action='store_true', help='严格汇编模式')

    # 运行时行为 (A1/A2)
    p.add_argument('--seed', type=int, default=None, metavar='N',
                   help='随机种子 (确定性执行; 默认随机)')
    p.add_argument('--bounds-check', action='store_true',
                   help='CIN 数组越界运行时检查 (编译期注入)')
    p.add_argument('--disasm', action='store_true',
                   help='反汇编 .bin 字节码为文本清单后退出')
    p.add_argument('--emit-object', action='store_true', dest='emit_object',
                   help='仅把 CIN/ASM/PL 编译为可重定位对象 (.co) 后退出')
    p.add_argument('--link', action='append', metavar='INPUT', default=None,
                   help='链接 .cin/.asm/.pl/.co/.colib 输入为可执行文件 (可重复)')
    p.add_argument('--emit-archive', action='store_true', dest='emit_archive',
                   help='把 --link 输入打包为 .colib 静态库归档')
    p.add_argument('--gc-sections', action='store_true', dest='gc_sections',
                   help='链接时删除不可达函数/代码段 (默认关闭)')

    # AOT: 编译成独立静态可执行文件
    p.add_argument('--native', action='store_true',
                   help='直接用 AST→C 原生后端编译为设备本机可执行文件并运行 '
                        '(不经过 UCPU 指令集); 超出语言子集会明确报错')
    p.add_argument('--native-out', metavar='OUT', default=None,
                   help='同 --native, 但只构建不运行, 产物写到 OUT')
    p.add_argument('--build-exe', nargs='?', const='', default=None,
                   metavar='OUT',
                   help='把 CIN/ASM/PL/BIN/CO 编译成独立静态可执行文件 '
                        '(不依赖 Python/动态库) 后退出; '
                        '省略路径时输出到 <程序名>[.exe]')
    p.add_argument('--build-target', default=None, metavar='OS/ARCH',
                   help='AOT 交叉编译目标 (默认当前平台): '
                        'windows/amd64, linux/amd64, linux/arm64, '
                        'darwin/amd64, darwin/arm64')
    p.add_argument('--build-keep-temp', action='store_true',
                   help='AOT 构建时保留临时目录 (排查 go build 失败)')
    p.add_argument('--build-static', action='store_true', dest='build_static',
                   help='C 后端产物静态链接 (macOS 目标会自动忽略)')
    p.add_argument('--build-strip', action='store_true', dest='build_strip',
                   help='C 后端产物去掉符号表 (体积更小)')
    p.add_argument('--build-keep-c', action='store_true', dest='build_keep_c',
                   help='保留 C 后端生成的 program.c (<产物>.c)')
    p.add_argument('--build-verify', action='store_true', dest='build_verify',
                   help='构建后校验 ELF/PE 头部与目标架构是否一致')
    p.add_argument('--build-backend', choices=['auto', 'c', 'native', 'vm'],
                   default='auto',
                   help='AOT 后端: auto = 优先 C 后端, 不支持时回退 Go/VM (默认); '
                        'c = 生成 C 并由 GCC/Clang 编译 (不依赖 Go); '
                        'native = 旧 Go 代码生成后端; '
                        'vm = 内嵌 UCBC 字节码由 Go VM 执行')
    p.add_argument('--no-build-cache', action='store_false', dest='build_cache',
                   default=True,
                   help='禁用 AOT 增量构建缓存 (默认启用)')
    p.add_argument('--build-debug', action='store_true', dest='build_debug',
                   help='保留符号表/DWARF (不传 -s -w), 便于调试')
    return p


def _apply_namespace(config: Config, ns: argparse.Namespace) -> None:
    """把 argparse Namespace 映射到 Config 字段。"""
    config.sandbox_mode = ns.sandbox
    config.debug_mode = ns.debug
    config.auto_save_crom = ns.auto_save_crom
    config.compress_crom = ns.compress_crom
    config.sdgcc_compress = ns.sdgcc_compress
    config.compile_to_bin = ns.compile_to_bin
    config.compile_only = ns.compile_only
    config.output_file = ns.output
    config.strict_mode = ns.strict
    config.seed = ns.seed
    config.bounds_check = ns.bounds_check

    if ns.log_level is not None:
        config.log_level = ns.log_level
    if ns.log_file is not None:
        config.log_file = ns.log_file
    if ns.mem_size is not None:
        config.mem_size = ns.mem_size
    if ns.max_instructions is not None:
        config.max_instructions = ns.max_instructions
    if ns.optimize is not None:
        config.optimize = ns.optimize

    # --debug 是比 --log-level 更强的开关: 与旧版一致, 始终强制 DEBUG
    if config.debug_mode:
        config.log_level = 'DEBUG'


def _find_cincc():
    """定位 Go 原生编译器 cincc (Python 只负责调用)。"""
    import shutil
    explicit = os.environ.get("CODECIN_CINCC")
    if explicit and os.path.isfile(explicit):
        return explicit
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("cincc.exe", "cincc"):
        candidate = os.path.join(here, "native", name)
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("cincc")


def _run_native(ns: argparse.Namespace, console, program_file: str,
                config) -> int:
    """--native / --native-out: 调用 Go 编译器 cincc 直接出机器码。"""
    import subprocess
    import tempfile

    cincc = _find_cincc()
    if cincc is None:
        console.print(Panel(
            "未找到 cincc (Go 机器码编译器)。先构建：\n"
            "  cd codecin/native && go build -o cincc ./cmd/cincc",
            title="Native Build Error", border_style="red"))
        return 1

    target = None
    if ns.build_target:
        mapping = {"linux/amd64": "x86_64-linux",
                   "linux/x86_64": "x86_64-linux"}
        target = mapping.get(ns.build_target)
        if target is None:
            console.print(Panel(f"cincc 暂不支持目标 {ns.build_target}",
                                title="Build Error", border_style="red"))
            return 1

    build_only = ns.native_out is not None
    if build_only:
        out = ns.native_out or (os.path.splitext(program_file)[0] + ".exe")
    else:
        tmpdir = tempfile.mkdtemp(prefix=".native-")
        out = os.path.join(tmpdir, os.path.splitext(
            os.path.basename(program_file))[0])

    cmd = [cincc, "-o", out, program_file]
    if target:
        cmd += ["-target", target]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        console.print(Panel((proc.stderr or proc.stdout).strip(),
                            title="Native Build Error", border_style="red"))
        return 1

    if build_only:
        console.print(Colors.colorize(f"Built native: {out}", Colors.GREEN))
        return 0

    argv = [out] + list(config.program_args)
    if os.name == "nt" and target != "x86_64-windows":
        import shutil as _shutil
        wsl = _shutil.which("wsl")
        if not wsl:
            console.print(Panel(
                "产物是 Linux ELF，Windows 无法直接运行。\n"
                "用 --native-out 保留产物并在 WSL/Linux 中执行，或安装 WSL。",
                title="Run Error", border_style="red"))
            return 1
        drive = out[0].lower()
        wsl_path = "/mnt/" + drive + out[2:].replace("\\", "/")
        argv = [wsl, "-e", wsl_path] + list(config.program_args)
    try:
        run = subprocess.run(argv)
        return int(run.returncode)
    except OSError as e:
        console.print(Panel(str(e), title="Run Error", border_style="red"))
        return 1


def toolchain_error():
    from .toolchain import ToolchainError
    return ToolchainError


def _run_aot_build(ns: argparse.Namespace, console, program_file: str,
                   config) -> int:
    """AOT: 把 CIN/ASM/PL/BIN/CO 编译成独立静态可执行文件 (--build-exe)。"""
    from . import aot
    from .errors import CPUSimulatorError

    if not os.path.isfile(program_file):
        console.print(Panel(f"File '{program_file}' not found",
                            title='Build Error', border_style='red'))
        return 1

    target = ns.build_target or aot.host_target()
    try:
        goos, _goarch = aot.parse_target(target)
    except aot.AotError as e:
        console.print(Panel(str(e), title='Build Error', border_style='red'))
        return 1

    out = ns.build_exe or (os.path.splitext(program_file)[0]
                           + aot.exe_suffix(goos))
    console.print(Colors.colorize(
        f"AOT build: {program_file} -> {out} "
        f"(静态链接, 目标 {target}, 后端 {ns.build_backend})",
        Colors.CYAN))
    try:
        # 依赖检查与嵌入由 build_program 内部完成; 这里先把清单展示给用户
        deps = aot.program_dependencies(program_file)
        embedded = [os.path.basename(p) for p in deps[1:]]
        if embedded:
            console.print(Colors.colorize(
                f"依赖库 {len(embedded)} 个 (编译期嵌入产物): "
                f"{', '.join(embedded)}", Colors.CYAN))
        built = aot.build_program(program_file, out=out, target=target,
                                  keep_temp=ns.build_keep_temp,
                                  mem_size=config.mem_size,
                                  backend=ns.build_backend,
                                  max_instructions=config.max_instructions,
                                  optimize=config.optimize,
                                  use_cache=ns.build_cache,
                                  debug=ns.build_debug,
                                  c_static=ns.build_static,
                                  c_strip=ns.build_strip,
                                  c_keep_c=ns.build_keep_c)
    except (aot.AotError, CPUSimulatorError) as e:
        console.print(Panel(str(e), title='Build Error', border_style='red'))
        return 1
    except Exception as e:                       # 兜底: 不再向上抛裸 traceback
        console.print(Panel(str(e), title='Build Error', border_style='red'))
        if config.debug_mode or config.log_level.upper() == 'DEBUG':
            console.print_exception()
        return 1
    if getattr(ns, 'build_verify', False):
        from . import cbackend
        try:
            info = cbackend.inspect_binary(built, target)
        except ValueError as e:
            console.print(Panel(str(e), title='Verify Error', border_style='red'))
            return 1
        console.print(Colors.colorize(
            'Verify OK: ' + ' '.join(f'{k}={v}' for k, v in info.items()),
            Colors.GREEN))
    console.print(Colors.colorize(f"AOT build 完成: {built}", Colors.GREEN))
    return 0


def _extract_program_args(args: List[str], parser: argparse.ArgumentParser):
    """把程序文件之后的裸参数透传给 CIN 程序 (arg_count()/arg(i))。

    规则 (向后兼容): 第一个非选项 token 是程序文件; 此后遇到已知 CLI 选项
    (及其取值) 仍归 CLI 解析, 其余裸 token 归程序参数。`--` 分隔的参数已在
    main 里摘出, 优先级更高。返回 (cli_args, program_args)。
    """
    takes_value = {}
    for act in parser._actions:  # noqa: SLF001 - argparse 无公开枚举接口
        for s in getattr(act, 'option_strings', []):
            takes_value[s] = getattr(act, 'nargs', None) != 0
    cli_args: List[str] = []
    prog_args: List[str] = []
    seen_prog = False
    i, n = 0, len(args)
    while i < n:
        tok = args[i]
        # --opt=value 形式按其选项名识别
        optname = tok.split('=', 1)[0] if tok.startswith('--') else tok
        if optname in takes_value:
            cli_args.append(tok)
            if takes_value[optname] and '=' not in tok:
                i += 1
                if i < n:
                    cli_args.append(args[i])
            i += 1
            continue
        if not seen_prog:
            cli_args.append(tok)
            seen_prog = True
            i += 1
            continue
        prog_args.append(tok)
        i += 1
    return cli_args, prog_args


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # `--` 之后的参数原样传给 CIN 程序 (arg_count()/arg(i) 读取), 不参与
    # CLI 自身解析; 与 Go `os.Args[1:]`、C `argv[1..]` 的习惯一致。
    if '--' in args:
        sep = args.index('--')
        program_args = args[sep + 1:]
        args = args[:sep]
    else:
        program_args = []

    # 中文 Windows 默认 stdout/stderr 是 GBK, 输出非 GBK 字符 (emoji 等) 会直接
    # UnicodeEncodeError; 且重定向产物字节与 Go 路径 (恒 UTF-8) 不一致。
    # 统一切成 UTF-8, 无法编码的字符以 ? 替换而不是崩溃。
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, 'reconfigure'):
            with contextlib.suppress(ValueError, OSError):
                _stream.reconfigure(encoding='utf-8', errors='replace')

    if '--help' in args or '-h' in args:
        sys.stdout.write(HELP_INTRO + "\n")
        sys.stdout.write(build_parser().format_help())
        return 0

    console = Console()
    parser = build_parser()

    # 程序文件之后的裸参数透传给 CIN 程序 (问题反馈 #3): 已知 CLI 选项
    # (含取值) 仍归 argparse, 其余 token 归 arg_count()/arg(i)。
    cli_args, direct_args = _extract_program_args(args, parser)
    if direct_args:
        for tok in direct_args:
            if tok.startswith('-') and tok not in ('-',) \
                    and not tok.lstrip('-').split('=')[0].isdigit():
                sys.stderr.write(
                    f"note: {tok!r} 不是 CLI 选项, 已作为程序参数传递 "
                    f"(arg_count/arg); 如需强制传参请用 `-- {tok}`\n")

    try:
        ns = parser.parse_args(cli_args)
    except SystemExit as e:
        # argparse 错误 (未知选项/非法数值): 已打印 usage, 返回其退出码
        return int(e.code) if e.code is not None else 2

    # 版本设施 (workstream C): --build-info 不需要程序文件, 输出后立刻退出。
    # 用 sys.stdout 直写: JSON 必须机器可读, 不能混入 rich 的 ANSI 装饰。
    if ns.json_output and not ns.build_info:
        sys.stderr.write("--json 只能与 --build-info 一起使用。\n")
        return 2
    if ns.build_info:
        from .version import build_info_json, format_build_info
        if ns.json_output:
            sys.stdout.write(build_info_json() + "\n")
        else:
            sys.stdout.write(format_build_info() + "\n")
        return 0

    if ns.list_libs:
        from .stdlib import format_library_report
        sys.stdout.write(format_library_report() + "\n")
        return 0

    if ns.emit_archive:
        if not ns.link:
            console.print(Panel("--emit-archive 需要至少一个 --link 对象",
                                title='Error', border_style='red'))
            return 1
        from .object import ObjectError, read_input_object, write_archive
        config = Config()
        _apply_namespace(config, ns)
        config.validate()
        try:
            objects = [read_input_object(p) for p in ns.link]
            out = config.output_file or (
                os.path.splitext(ns.link[0])[0] + '.colib')
            written = write_archive(objects, out)
        except (ObjectError, OSError) as e:
            console.print(Panel(str(e), title='Archive Error',
                                border_style='red'))
            return 1
        console.print(Colors.colorize(f"Archive: {written}", Colors.GREEN))
        return 0

    if ns.link:
        from . import aot
        from .object import ObjectError, link_files
        config = Config()
        _apply_namespace(config, ns)
        config.validate()
        try:
            linked = link_files(ns.link, gc_sections=ns.gc_sections)
        except (ObjectError, OSError) as e:
            console.print(Panel(str(e), title='Link Error', border_style='red'))
            return 1
        out = config.output_file
        if not out:
            target = ns.build_target or aot.host_target()
            goos, _ = aot.parse_target(target)
            out = (os.path.splitext(ns.link[0])[0]
                   + aot.exe_suffix(goos))
        try:
            cache_key = None
            if ns.build_cache:
                cache_key = aot.link_cache_key(
                    ns.link, ns.build_target, ns.build_backend,
                    config.optimize, linked['mem_size'],
                    config.max_instructions, debug=ns.build_debug,
                    gc_sections=ns.gc_sections)
            built = aot.build_from_ir(
                linked['instructions'], linked['labels'],
                linked['data_labels'], linked['segments'],
                entry_pc=linked['entry_pc'],
                mem_size=linked['mem_size'],
                sp=linked['sp'], heap_base=linked['heap_base'],
                out=out, target=ns.build_target,
                backend=ns.build_backend,
                max_instructions=config.max_instructions,
                optimize=config.optimize,
                program_name='linked',
                keep_temp=ns.build_keep_temp,
                debug=ns.build_debug,
                use_cache=(ns.build_cache and not (ns.build_static
                                                   or ns.build_strip
                                                   or ns.build_keep_c)),
                cache_key=cache_key,
                c_static=ns.build_static,
                c_strip=ns.build_strip,
                c_keep_c=ns.build_keep_c,
                logger=None)
        except (aot.AotError, OSError) as e:
            console.print(Panel(str(e), title='Link Error', border_style='red'))
            return 1
        if getattr(ns, 'build_verify', False):
            from . import cbackend
            try:
                info = cbackend.inspect_binary(
                    built, ns.build_target or aot.host_target())
            except ValueError as e:
                console.print(Panel(str(e), title='Verify Error',
                                    border_style='red'))
                return 1
            console.print(Colors.colorize(
                'Verify OK: ' + ' '.join(f'{k}={v}' for k, v in info.items()),
                Colors.GREEN))
        console.print(Colors.colorize(f"Link 完成: {built}", Colors.GREEN))
        return 0

    if ns.emit_object:
        if ns.program is None:
            console.print(Panel("--emit-object 需要 CIN/ASM/PL 源文件",
                                title='Error', border_style='red'))
            return 1
        ext = os.path.splitext(ns.program)[1].lower()
        if ext not in ('.cin', '.asm', '.pl'):
            console.print(Panel("--emit-object 仅支持 .cin/.asm/.pl",
                                title='Error', border_style='red'))
            return 1
        from .object import ObjectError, compile_asm_object, compile_cin_object, write_object
        try:
            if ext == '.cin':
                obj = compile_cin_object(ns.program)
            else:
                obj = compile_asm_object(ns.program)
            out = ns.output or os.path.splitext(ns.program)[0] + '.co'
            written = write_object(obj, out)
        except (ObjectError, OSError) as e:
            console.print(Panel(str(e), title='Object Error',
                                border_style='red'))
            return 1
        console.print(Colors.colorize(f"Object: {written}", Colors.GREEN))
        return 0

    if ns.program is None:
        console.print(Panel("No program file specified.\n"
                            "Use --help for usage information.",
                            title="Error", border_style='red'))
        return 1

    config = Config()
    _apply_namespace(config, ns)
    # 直传参数与 `--` 后参数合并 (直传在前, 位置顺序与命令行一致)
    config.program_args = direct_args + program_args
    config.validate()

    program_file = ns.program
    crom_file = ns.crom
    default_ext = '.sdgcc'
    output_file = (config.output_file
                   or os.path.splitext(program_file)[0] + default_ext)

    if ns.disasm:
        from .disasm import disassemble_file
        try:
            lines = disassemble_file(program_file)
        except Exception as e:
            from .errors import CPUSimulatorError
            title = ("Disasm Error" if isinstance(e, CPUSimulatorError)
                     else "Error")
            console.print(Panel(str(e), title=title, border_style='red'))
            return 1
        sys.stdout.write("\n".join(lines))
        return 0

    if ns.native or ns.native_out:
        return _run_native(ns, console, program_file, config)

    if ns.build_exe is not None:
        return _run_aot_build(ns, console, program_file, config)

    # 运行前进度/状态只在真实终端且未静音 (log-level ERROR) 时显示,
    # 管道/重定向与"保持示例输出干净"的场景不受影响。
    tty = _stdout_is_tty() and config.log_level.upper() not in ('ERROR', 'CRITICAL')
    if tty:
        console.print(Colors.colorize(
            f"Loading {os.path.basename(program_file)} ...", Colors.CYAN))
    load_started = time.perf_counter()
    try:
        from .cpu import CPU
        cpu = CPU(config, program_file, crom_file=crom_file)
    except Exception as e:
        from .errors import CPUSimulatorError
        if isinstance(e, CPUSimulatorError):
            console.print(Panel(str(e), title="Load Error", border_style='red'))
        else:
            console.print(Panel(str(e), title="Error", border_style='red'))
            if config.debug_mode or config.log_level.upper() == 'DEBUG':
                # 超详细: rich 彩色完整堆栈
                console.print_exception()
        return 1

    if tty:
        console.print(Colors.colorize(
            f"Loaded {len(cpu.instructions)} instructions in "
            f"{(time.perf_counter() - load_started) * 1000:.1f} ms",
            Colors.GREEN))

    try:
        # 管道/重定向输入预读到 input_buffer: 让 input() 在原生与解释器路径下
        # 都能读到 `"42" | codecin t.cin` 这样的标准输入 (交互 TTY 不预读)。
        try:
            if sys.stdin is not None and not sys.stdin.isatty():
                cpu.input_buffer = sys.stdin.read() or ""
        except (OSError, ValueError, AttributeError):
            pass  # pytest 等捕获环境禁止读 stdin, 保持为空

        if config.compile_to_bin or config.compile_only:
            from . import sdgcc as sdgcc_mod
            sdgcc_mod.save_program(cpu, output_file, multi_file=False,
                                   sources=[program_file],
                                   compress=bool(config.sdgcc_compress),
                                   meta={'build': {
                                       'entry': os.path.basename(program_file),
                                       'optimize': int(config.optimize),
                                       'mem_size': len(cpu.memory),
                                       'bin_version': 3,
                                   }})
            console.print(Colors.colorize(
                f"Compiled to {output_file}", Colors.GREEN))
            if config.compile_only:
                return 0

        if tty:
            console.print(Colors.colorize("Running ...", Colors.CYAN))
            sys.stdout.flush()  # 状态行先落盘, 程序实时输出紧随其后
        cpu.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        from .errors import CPUSimulatorError
        if isinstance(e, CPUSimulatorError):
            console.print(Panel(str(e), title="Error", border_style='red'))
        else:
            console.print(Panel(str(e), title="Unexpected Error",
                                border_style='red'))
            console.print_exception()
        return 1
    # CPU.run 内部会捕获执行期异常并记录日志, 这里据失败标志决定退出码
    if getattr(cpu, 'execution_failed', False):
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
