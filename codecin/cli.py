"""命令行入口: argparse 参数解析 -> 加载程序 -> 运行。

建议 3: 采用 argparse 统一参数表 (不再三份手写解析), 全部选项由
build_parser() 单一来源定义, --help 与解析行为自动保持一致。
"""

import argparse
import os
import sys
from typing import List, Optional

from .config import Config
from .console import Colors, Console, Panel

HELP_INTRO = f"""{Colors.colorize('UCPU Simulator v5.2', Colors.CYAN, True)}

{Colors.colorize('Usage:', Colors.YELLOW)}
  python cpu.py <program.[cin|pl|asm|bin]> [options]

{Colors.colorize('Supported formats:', Colors.YELLOW)}
  .cin   CIN 高级语言 (函数/struct/数组/浮点/字符串)
  .pl    UCPU 汇编语言 (PL 关键字风格)
  .asm   UCPU 汇编
  .bin   UCBC 字节码 (由 --compile 生成)
"""


def build_parser() -> argparse.ArgumentParser:
    """参数表单一来源 (与 Config 字段一一对应)。"""
    p = argparse.ArgumentParser(
        prog='cpu.py',
        description='UCPU - 通用CPU模拟器 (CIN/PL/ASM 工具链 + 多执行路径)',
        add_help=False,          # 帮助由 main 以彩色形式打印 (见 --help)
    )
    p.add_argument('program', nargs='?', default=None,
                   metavar='program.[cin|pl|asm|bin]',
                   help='程序源文件/字节码/镜像')
    p.add_argument('--help', '-h', action='store_true', dest='show_help',
                   help='显示帮助')

    # 执行路径
    p.add_argument('--no-native', action='store_true',
                   help='禁用 Go 原生库, 强制纯 Python 解释执行')
    p.add_argument('--jit', action='store_true', dest='enable_jit',
                   help='启用 Python JIT (基本块动态编译)')
    p.add_argument('--no-jit', action='store_true', dest='disable_jit',
                   help=argparse.SUPPRESS)

    # 日志与调试
    p.add_argument('--debug', action='store_true',
                   help='调试模式 (超详细 rich 追踪: 逐指令/寄存器/内存/栈/缓存)')
    p.add_argument('--step', action='store_true',
                   help='交互式单步执行 (step> 命令集, 与断点调试一致)')
    p.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR',
                                           'CRITICAL'],
                   help='日志级别 (默认 INFO)')
    p.add_argument('--log-file', metavar='FILE', help='日志输出到文件')
    p.add_argument('--sandbox', action='store_true',
                   help='沙箱模式 (限制宿主访问)')
    p.add_argument('--no-io', action='store_true',
                   help='禁止 IN/OUT 与宿主 I/O')

    # 性能分析
    p.add_argument('--profile', action='store_true',
                   help='执行后输出性能统计')
    p.add_argument('--cache-size', type=int, metavar='N',
                   help='缓存行数 (默认 64)')
    p.add_argument('--cache-assoc', type=int, metavar='N',
                   help='缓存关联度 (默认 4)')
    p.add_argument('--mem-size', type=int, metavar='BYTES',
                   help='内存大小 (默认 65536)')
    p.add_argument('--max-instructions', type=int, metavar='N',
                   help='指令数上限 (默认 100000000)')
    p.add_argument('--execution-interval', type=float, metavar='SEC',
                   help='每指令间隔秒数 (演示减速用)')

    # 编译 / CROM
    p.add_argument('--compile', action='store_true', dest='compile_to_bin',
                   help='编译为 .bin 字节码后继续执行')
    p.add_argument('--compile-only', action='store_true',
                   help='仅编译为 .bin 不执行')
    p.add_argument('-o', '--output', metavar='FILE',
                   help='指定输出文件 (.crom/.bin)')
    p.add_argument('--crom', metavar='FILE', help='加载指定 .crom 内存镜像')
    p.add_argument('--save', action='store_true', dest='auto_save_crom',
                   help='执行后保存 .crom 内存镜像')
    p.add_argument('--no-compress', action='store_false', dest='compress_crom',
                   help='.crom 不压缩 (默认压缩)')

    # 语言/汇编
    p.add_argument('--optimize', type=int, metavar='0-3',
                   help='优化级别 (0-3, 默认 0)')
    p.add_argument('--strict', action='store_true', help='严格汇编模式')

    # 运行时行为 (A1/A2/A4)
    p.add_argument('--seed', type=int, default=None, metavar='N',
                   help='随机种子 (确定性执行; 默认随机)')
    p.add_argument('--bounds-check', action='store_true',
                   help='CIN 数组越界运行时检查 (强制解释执行)')
    p.add_argument('--mmu', action='store_true',
                   help='启用 MMU 分页 (identity 页表; 未映射页触发缺页错误)')
    p.add_argument('--debug-server', type=int, default=None, metavar='PORT',
                   help='启动 TCP 远程调试服务 (连接后驱动式调试: step/continue/'
                        'break/regs/mem/history)')
    p.add_argument('--disasm', action='store_true',
                   help='反汇编 .bin 字节码为文本清单后退出')
    return p


def _apply_namespace(config: Config, ns: argparse.Namespace) -> None:
    """把 argparse Namespace 映射到 Config 字段。"""
    config.step_mode = ns.step
    config.debug_mode = ns.debug
    if ns.step or ns.debug:
        config.interactive_mode = True
    config.use_native = not ns.no_native
    config.enable_jit = ns.enable_jit and not ns.disable_jit
    config.sandbox_mode = ns.sandbox
    config.profile = ns.profile
    config.auto_save_crom = ns.auto_save_crom
    config.compress_crom = ns.compress_crom
    config.compile_to_bin = ns.compile_to_bin
    config.compile_only = ns.compile_only
    config.output_file = ns.output
    config.allow_io = not ns.no_io
    config.strict_mode = ns.strict
    config.seed = ns.seed
    config.bounds_check = ns.bounds_check
    config.mmu = ns.mmu
    config.debug_server_port = ns.debug_server

    if ns.log_level is not None:
        config.log_level = ns.log_level
    if ns.log_file is not None:
        config.log_file = ns.log_file
    if ns.mem_size is not None:
        config.mem_size = ns.mem_size
    if ns.cache_size is not None:
        config.cache_size = ns.cache_size
    if ns.cache_assoc is not None:
        config.cache_assoc = ns.cache_assoc
    if ns.max_instructions is not None:
        config.max_instructions = ns.max_instructions
    if ns.execution_interval is not None:
        config.execution_interval = ns.execution_interval
    if ns.optimize is not None:
        config.optimize = ns.optimize


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if '--help' in args or '-h' in args:
        sys.stdout.write(HELP_INTRO + "\n")
        sys.stdout.write(build_parser().format_help())
        return 0

    console = Console()
    parser = build_parser()
    try:
        ns = parser.parse_args(args)
    except SystemExit as e:
        # argparse 错误 (未知选项/非法数值): 已打印 usage, 返回其退出码
        return int(e.code) if e.code is not None else 2

    if ns.program is None:
        console.print(Panel("No program file specified.\n"
                            "Use --help for usage information.",
                            title="Error", border_style='red'))
        return 1

    config = Config()
    _apply_namespace(config, ns)
    config.validate()

    program_file = ns.program
    crom_file = ns.crom
    output_file = (config.output_file
                   or os.path.splitext(program_file)[0] + '.bin')

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

    try:
        from .cpu import CPU
        cpu = CPU(config, program_file, crom_file=crom_file)
    except Exception as e:
        from .errors import CPUSimulatorError
        if isinstance(e, CPUSimulatorError):
            console.print(Panel(str(e), title="Load Error", border_style='red'))
        else:
            console.print(Panel(str(e), title="Error", border_style='red'))
            if config.debug_mode:
                # 超详细: rich 彩色完整堆栈
                console.print_exception()
        return 1

    try:
        if config.compile_to_bin or config.compile_only:
            from . import crom as crom_mod
            crom_mod.save_bin(cpu, output_file, logger=cpu.logger)
            console.print(Colors.colorize(
                f"Compiled to {output_file}", Colors.GREEN))
            if config.compile_only:
                return 0

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
    return 0


if __name__ == '__main__':
    sys.exit(main())
