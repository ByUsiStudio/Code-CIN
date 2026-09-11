import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .console import Console, Table
from .isa import Constants


class BranchPredictor:

    def __init__(self):
        self.saturating_counters: Dict[int, int] = {}
        self.correct_predictions = 0
        self.total_predictions = 0

    def predict(self, pc: int) -> bool:
        return self.saturating_counters.get(pc, 2) >= 2

    def update(self, pc: int, taken: bool) -> None:
        counter = self.saturating_counters.get(pc, 2)
        if taken:
            counter = min(3, counter + 1)
        else:
            counter = max(0, counter - 1)
        self.saturating_counters[pc] = counter

    def record_prediction(self, predicted: bool, actual: bool) -> None:
        self.total_predictions += 1
        if predicted == actual:
            self.correct_predictions += 1

    def get_accuracy(self) -> float:
        if self.total_predictions == 0:
            return 1.0
        return self.correct_predictions / self.total_predictions


class PerformanceCounters:
    def __init__(self):
        self.counters = {
            'cycles': 0,
            'instructions': 0,
            'branches': 0,
            'branch_mispredictions': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'memory_reads': 0,
            'memory_writes': 0,
            'stalls': 0,
            'flops': 0,
        }
        self.branch_predictor = BranchPredictor()

    def record_instruction(self, opcode: str) -> None:
        self.counters['instructions'] += 1
        if opcode in Constants.BRANCH_OPS:
            self.counters['branches'] += 1
        if opcode in Constants.FP_OPS:
            self.counters['flops'] += 1

    def record_branch_result(self, pc: int, taken: bool, predicted: bool) -> None:
        self.branch_predictor.record_prediction(predicted, taken)
        if taken != predicted:
            self.counters['branch_mispredictions'] += 1
        self.branch_predictor.update(pc, taken)

    def record_cache(self, hit: bool) -> None:
        if hit:
            self.counters['cache_hits'] += 1
        else:
            self.counters['cache_misses'] += 1

    def record_memory_read(self) -> None:
        self.counters['memory_reads'] += 1

    def record_memory_write(self) -> None:
        self.counters['memory_writes'] += 1

    def add_cycles(self, n: int) -> None:
        self.counters['cycles'] += n

    def get_ipc(self) -> float:
        if self.counters['cycles'] == 0:
            return 0
        return self.counters['instructions'] / self.counters['cycles']

    def get_stats(self) -> Dict[str, Any]:
        stats = self.counters.copy()
        stats['ipc'] = self.get_ipc()
        stats['branch_accuracy'] = self.branch_predictor.get_accuracy()
        total_cache = stats['cache_hits'] + stats['cache_misses']
        stats['cache_hit_rate'] = stats['cache_hits'] / total_cache if total_cache > 0 else 0
        return stats

    def display(self, console: Console) -> None:
        stats = self.get_stats()
        table = Table(title="Performance Counters")
        table.add_column("Metric")
        table.add_column("Value")
        for key, value in stats.items():
            if key in ('branch_accuracy', 'cache_hit_rate'):
                table.add_row(key.replace('_', ' ').title(), f"{value * 100:.1f}%")
            elif key == 'ipc':
                table.add_row("Instructions Per Cycle", f"{value:.2f}")
            else:
                table.add_row(key.replace('_', ' ').title(), str(value))
        console.print(str(table))


class InstructionProfiler:

    def __init__(self):
        self.cycles: Dict[str, int] = defaultdict(int)
        self.latency: Dict[str, int] = {
            'ADD': 1, 'SUB': 1, 'MUL': 3, 'DIV': 10,
            'LOAD': 4, 'STORE': 4, 'FADD': 3, 'FMUL': 5,
            'FDIV': 10, 'VADD': 2, 'VMUL': 4, 'VDIV': 8,
            'LSL': 1, 'LSR': 1, 'AND': 1, 'OR': 1, 'XOR': 1,
            'LD': 4, 'SD': 4, 'LDR': 4, 'STR': 4,
            'LB': 4, 'LH': 4, 'LW': 4, 'SB': 4, 'SH': 4, 'SW': 4,
        }

    def record(self, opcode: str) -> None:
        self.cycles[opcode] += self.latency.get(opcode, 1)

    def get_total_cycles(self) -> int:
        return sum(self.cycles.values())

    def display_report(self, console: Optional[Console] = None) -> None:
        if console is None:
            return
        total = self.get_total_cycles()
        table = Table(title="Instruction Cycle Profile")
        table.add_column("Instruction")
        table.add_column("Cycles")
        table.add_column("Percentage")
        for op, cycles in sorted(self.cycles.items(), key=lambda x: x[1], reverse=True)[:20]:
            pct = (cycles / total * 100) if total > 0 else 0
            table.add_row(op, str(cycles), f"{pct:.1f}%")
        console.print(str(table))


class Statistics:
    def __init__(self):
        self.reset()

    def reset(self):
        self.instruction_count = 0
        self.opcode_count: Dict[str, int] = defaultdict(int)
        self.execution_time = 0.0
        self.start_time: Optional[float] = None
        self.memory_reads = 0
        self.memory_writes = 0
        self.inst_profiler = InstructionProfiler()
        self.hot_instructions: Dict[str, int] = defaultdict(int)
        self.performance_counters = PerformanceCounters()

    def start(self):
        self.start_time = time.time()

    def stop(self):
        if self.start_time:
            self.execution_time = time.time() - self.start_time

    def record_instruction(self, opcode: str):
        self.instruction_count += 1
        self.opcode_count[opcode] += 1
        self.inst_profiler.record(opcode)
        self.hot_instructions[opcode] += 1
        self.performance_counters.record_instruction(opcode)

    def record_memory_read(self):
        self.memory_reads += 1
        self.performance_counters.record_memory_read()

    def record_memory_write(self):
        self.memory_writes += 1
        self.performance_counters.record_memory_write()

    def record_cache(self, hit: bool):
        self.performance_counters.record_cache(hit)

    def record_branch(self, pc: int, taken: bool, predicted: bool):
        self.performance_counters.record_branch_result(pc, taken, predicted)

    def get_hot_instructions(self, top_n: int = 10) -> List[Tuple[str, int]]:
        return sorted(self.hot_instructions.items(),
                      key=lambda x: x[1], reverse=True)[:top_n]

    def display_summary(self, console: Optional[Console] = None,
                        cache_stats: Optional[Dict] = None,
                        jit_stats: Optional[Dict] = None,
                        native_used: bool = False) -> None:
        if console is None:
            return

        table = Table(title="Execution Statistics")
        table.add_column("Metric")
        table.add_column("Value")
        table.add_row("Engine", "Go native" if native_used else "Python interpreter")
        table.add_row("Total Instructions", str(self.instruction_count))
        table.add_row("Total Cycles", str(self.inst_profiler.get_total_cycles()))
        if self.instruction_count > 0:
            table.add_row("CPI (Cycles/Inst)",
                          f"{self.inst_profiler.get_total_cycles() / self.instruction_count:.2f}")
        table.add_row("Execution Time", f"{self.execution_time:.4f}s")
        if self.execution_time > 0:
            table.add_row("Instructions/sec",
                          f"{self.instruction_count / self.execution_time:,.0f}")
        table.add_row("Memory Reads", str(self.memory_reads))
        table.add_row("Memory Writes", str(self.memory_writes))

        perf_stats = self.performance_counters.get_stats()
        table.add_row("Branch Accuracy", f"{perf_stats['branch_accuracy'] * 100:.1f}%")

        if cache_stats:
            table.add_row("Cache Hit Rate", f"{cache_stats.get('hit_rate', 0) * 100:.1f}%")

        if jit_stats:
            table.add_row("JIT Hit Rate", f"{jit_stats.get('hit_rate', 0) * 100:.1f}%")
            table.add_row("JIT Blocks Compiled", str(jit_stats.get('blocks_compiled', 0)))

        console.print(str(table))

        if self.opcode_count:
            op_table = Table(title="Instruction Usage")
            op_table.add_column("Instruction")
            op_table.add_column("Count")
            op_table.add_column("Percentage")
            total = sum(self.opcode_count.values())
            for op, count in sorted(self.opcode_count.items(),
                                    key=lambda x: x[1], reverse=True)[:20]:
                pct = (count / total * 100) if total > 0 else 0
                op_table.add_row(op, str(count), f"{pct:.1f}%")
            console.print(str(op_table))

        self.inst_profiler.display_report(console)
        self.performance_counters.display(console)
