"""CPU: native-only 执行编排。

v5.9.0 起纯 Python 解释器与 JIT 已删除, 全部程序由 Go 原生引擎
(codecin-native 动态库 / AOT 静态可执行文件) 执行:
- CPU 负责编译/装载 (.cin/.asm/.pl/.bin/.crom) 与执行编排;
- 内存为稀疏分页 FastMemory (逻辑 1 GiB, 按需分配 4 KiB 页);
- 执行结果 (寄存器/向量/NZCV/脏内存段/输出) 由原生库一次性回传。

动态库缺失时抛 CPUSimulatorError, 提示重建原生库 (不再有解释器回退)。
"""

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from .assembler import Assembler
from .console import Colors, Console, Panel
from .errors import CPUSimulatorError, ExecutionError
from .isa import Constants
from .logger import Logger
from .memory import FastMemory
from .registers import RegisterFile, VectorRegisterFile
from .stats import Statistics

Operand = Tuple[Any, ...]
Instruction = Tuple[str, List[Operand]]

MASK64 = 0xFFFFFFFFFFFFFFFF

NATIVE_HINT = (
    "原生引擎不可用: 未找到 codecin-native 动态库。\n"
    "请先构建原生库: python script/build_native.py "
    "(或运行 codecin/native/build.ps1 / build.sh), 然后重试。")


class CPU:
    def __init__(self, config, filename: Optional[str] = None,
                 crom_file: Optional[str] = None,
                 from_bin: bool = False,
                 console: Optional[Console] = None):

        self.console = console or Console()
        self.config = config
        self.filename = filename

        self.logger = Logger(self.console, config.log_level)
        if config.log_file:
            self.logger.set_log_file(config.log_file)
        # 内存访问日志 (DEBUG 级别生效)
        self.memory = FastMemory(config.mem_size)
        self.memory.attach_logger(self.logger)

        self.regs = RegisterFile(self.console)
        self.vec_regs = VectorRegisterFile()

        self.pstate: Dict[str, bool] = {'N': False, 'Z': False,
                                        'C': False, 'V': False}
        self.pc = 0
        self.sp = (config.mem_size - Constants.STACK_SLOT) & ~0x7
        self.heap_base = config.mem_size // 2
        self.heap_ptr = self.heap_base

        self.logger.dump("CPU 初始化", {
            'memory': f"0x{config.mem_size:x} bytes (sparse)",
            'sp_init': f"0x{self.sp:x}",
            'heap_base': f"0x{self.heap_ptr:x}",
            'sandbox': config.sandbox_mode,
            'log_level': config.log_level,
        })

        self.instructions: List[Instruction] = []
        self.labels: Dict[str, int] = {}
        self.data_labels: Dict[str, int] = {}
        self.entry_pc = 0

        self.stats = Statistics()
        self.running = False

        self.input_buffer: str = ""
        self._input_pos = 0
        self.output_buffer: List[str] = []
        self._capture_output = False
        self.native_engine = None
        self.native_used = False
        # 执行期是否发生过错误 (供 CLI 决定退出码)
        self.execution_failed = False

        if filename:
            self.load_program(filename, crom_file=crom_file, from_bin=from_bin)

    # ==================== 程序装载 ====================

    def load_program(self, filename: str, crom_file: Optional[str] = None,
                     from_bin: bool = False) -> None:
        if not os.path.exists(filename):
            raise CPUSimulatorError(f"File '{filename}' not found")

        ext = os.path.splitext(filename)[1].lower()

        if ext == '.cin':
            from .cin import CINCompiler
            compiler = CINCompiler(self.console, logger=self.logger)
            result = compiler.compile(filename,
                                      bounds_check=self.config.bounds_check)
            self.instructions = result.instructions
            self.labels = result.labels
            self.data_labels = result.data_labels
            for addr, data in result.data_writes:
                self.memory.write_block(addr, data)
            # pc 0 为 bootstrap (CALL main; HALT), main 标签为函数体入口
            self.entry_pc = 0
            self.pc = 0
            self.logger.info(f"CIN compiled: {len(self.instructions)} instructions")
            return

        if from_bin or ext == '.bin':
            from . import crom as crom_mod
            crom_mod.load_bin(self, filename)
            self.logger.info(f"Binary loaded: {len(self.instructions)} instructions")
            return

        # .pl / .asm
        if crom_file is None:
            base = os.path.splitext(filename)[0]
            candidate = base + '.crom'
            if os.path.exists(candidate):
                crom_file = candidate
        if crom_file and os.path.exists(crom_file):
            from . import crom as crom_mod
            crom_mod.load_crom(self.memory, crom_file, self.logger)

        asm = Assembler(self.memory, self.console, strict=self.config.strict_mode,
                        logger=self.logger)
        self.instructions, self.labels, self.data_labels = asm.assemble_file(filename)
        self.entry_pc = self.labels.get('main', 0)
        self.pc = self.entry_pc
        self.logger.info(f"Assembly successful: {len(self.instructions)} instructions")
        if self.logger.is_debug:
            self.logger.dump("汇编标签表", {name: f"0x{pc:x}"
                                        for name, pc in
                                        sorted(self.labels.items())})

    # ==================== I/O ====================

    def _emit_text(self, text: str) -> None:
        if self._capture_output:
            self.output_buffer.append(text)
        else:
            sys.stdout.write(text)
            sys.stdout.flush()

    # ==================== 执行 ====================

    def _try_native_run(self) -> bool:
        """使用 Go 原生库执行整个程序。

        解释器与 JIT 已删除: 这里是唯一执行路径。动态库缺失时抛
        CPUSimulatorError (带重建提示), 不再静默回退。
        """
        try:
            from . import native
        except Exception as e:  # pragma: no cover - 平台相关加载失败
            raise CPUSimulatorError(f"{NATIVE_HINT}\n(加载失败: {e})") from e
        engine = native.get_engine(self.logger)
        if engine is None:
            raise CPUSimulatorError(NATIVE_HINT)

        from .native import encode_program
        all_labels = dict(self.labels)
        all_labels.update(self.data_labels)
        bytecode = encode_program(self.instructions, self.entry_pc, all_labels)
        segments = self.memory.snapshot_segments()
        import time as _time
        t0 = _time.perf_counter()
        result = engine.run_v2(
            bytecode=bytecode,
            segments=segments,
            entry=self.entry_pc,
            sp=self.sp,
            heap_base=self.heap_ptr,
            mem_size=len(self.memory),
            input_data=self.input_buffer.encode('utf-8'),
            max_steps=self.config.max_instructions,
            args=list(self.config.program_args),
            seed=self.config.seed,
            sandbox=self.config.sandbox_mode,
        )
        elapsed = _time.perf_counter() - t0
        if result is None:
            raise CPUSimulatorError(
                "原生引擎返回空结果 (库版本不匹配? 请重建原生库)")

        self.native_used = True
        self.logger.debug(
            f"Native result: status={result.get('status')} "
            f"steps={result.get('steps')} pc={result.get('pc')} "
            f"elapsed={elapsed * 1000:.2f}ms error={result.get('error')}")

        self._apply_native_state(result)
        if result.get('output'):
            self._emit_text(result['output'])
        if result.get('error'):
            raise ExecutionError(result['error'])
        return False

    def _apply_native_state(self, result: dict) -> None:
        """把原生执行结果同步回 Python 侧对象 (寄存器/内存/统计)。"""
        if 'regs' in result:
            self.regs.set_all(list(result['regs'])[:32])
        if 'vec_regs' in result:
            self.vec_regs.set_all(result['vec_regs'])
        if 'flags' in result:
            self.pstate.update(result['flags'])
        if 'sp' in result:
            self.sp = result['sp'] & MASK64
        if 'pc' in result:
            self.pc = result['pc']
        if 'heap_ptr' in result:
            self.heap_ptr = result['heap_ptr'] & MASK64
        if 'segments' in result:
            # 脏段写回稀疏内存 (只触碰被写过的页)
            for addr, data in result['segments']:
                self.memory.write_block(addr, data)
        if 'steps' in result:
            # 原生 VM 整程序执行完毕: 统计一次性批量写入
            steps = int(result.get('steps') or 0)
            counted = min(steps, self.config.max_instructions)
            self.stats.instruction_count = steps
            self.stats.opcode_count.clear()
            self.stats.hot_instructions['?'] += counted
            self.stats.inst_profiler.cycles['?'] += counted
            self.stats.performance_counters.counters['instructions'] += counted

    def run(self) -> None:
        self.running = True
        self.stats.start()
        self.logger.info("Starting program execution (Go native engine)")
        try:
            self._try_native_run()
            self.logger.info("HALT (native)")
        except KeyboardInterrupt:
            self.logger.info("User interrupt")
            self.console.print(f"\n{Colors.colorize('User interrupt', Colors.YELLOW)}")
        except ExecutionError as e:
            self.execution_failed = True
            self.logger.error(f"Execution error: {e}")
            self.console.print(Panel(
                f"{e}", title="Execution Error", border_style='red'))
        except Exception as e:
            self.execution_failed = True
            self.logger.exception(f"Execution error: {e}")
            title = "Error" if isinstance(e, CPUSimulatorError) else "Execution Error"
            self.console.print(Panel(f"{e}", title=title, border_style='red'))
        finally:
            self.running = False
            self.stats.stop()
            self.logger.info("Program execution finished")

            if self.config.auto_save_crom and self.filename:
                from . import crom as crom_mod
                crom_file = os.path.splitext(self.filename)[0] + '.crom'
                crom_mod.save_crom(self.memory, crom_file,
                                   compress=self.config.compress_crom,
                                   logger=self.logger)

    # ==================== 状态显示 ====================

    def display_state(self, title: str = "CPU State",
                      opcode: Optional[str] = None,
                      args: Optional[List[Operand]] = None) -> None:
        extra = {'PC': self.pc, 'SP': self.sp, 'Heap': self.heap_ptr}
        self.regs.display_registers(title, extra_info=extra, console=self.console)
        self.vec_regs.display_vector_registers(console=self.console)
