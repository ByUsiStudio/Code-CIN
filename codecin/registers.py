from typing import Any, Dict, List, Optional

from .console import Console, Table
from .isa import Constants
from .errors import ExecutionError

MASK64 = 0xFFFFFFFFFFFFFFFF


class RegisterFile:
    def __init__(self, console: Optional[Console] = None,
                 num_regs: int = Constants.NUM_REGISTERS):
        self.console = console or Console()
        self.num_regs = num_regs
        self._regs = [0] * num_regs

    def read(self, idx: int) -> int:
        if idx == 31:
            return 0
        if not 0 <= idx < self.num_regs:
            raise ExecutionError(f"Invalid register index: X{idx}")
        return self._regs[idx]

    def write(self, idx: int, value: int, pc: int = 0) -> None:
        if idx == 31:
            return
        if not 0 <= idx < self.num_regs:
            raise ExecutionError(f"Invalid register index: X{idx}")
        self._regs[idx] = value & MASK64

    def get_all(self) -> List[int]:
        return self._regs.copy()

    def set_all(self, values: List[int]) -> None:
        for i, v in enumerate(values[:self.num_regs]):
            self._regs[i] = v & MASK64

    def reset(self) -> None:
        self._regs = [0] * self.num_regs

    def display_registers(self, title: str = "Registers",
                          extra_info: Optional[Dict[str, Any]] = None,
                          console: Optional[Console] = None) -> None:
        if console is None:
            console = self.console
        table = Table(title=title)
        table.add_column("Register")
        table.add_column("Value (Dec)")
        table.add_column("Value (Hex)")
        for i in range(16):
            val = self._regs[i]
            table.add_row(f"X{i}", str(val), f"0x{val:x}")
        table.add_row("", "", "")
        for i in range(16, 31):
            val = self._regs[i]
            table.add_row(f"X{i}", str(val), f"0x{val:x}")
        table.add_row("XZR", "0", "0x0")
        if extra_info:
            for key, value in extra_info.items():
                table.add_row(key, str(value),
                              f"0x{value:x}" if isinstance(value, int) else "")
        console.print(str(table))


class VectorRegisterFile:

    def __init__(self, num_regs: int = Constants.NUM_VECTOR_REGISTERS,
                 lanes: int = Constants.VECTOR_LANES):
        self.num_regs = num_regs
        self.lanes = lanes
        self._regs = [[0.0] * lanes for _ in range(num_regs)]

    def read_vector(self, idx: int) -> List[float]:
        if idx == 31:
            return [0.0] * self.lanes
        if not 0 <= idx < self.num_regs:
            raise ExecutionError(f"Invalid vector register index: V{idx}")
        return self._regs[idx].copy()

    def write_vector(self, idx: int, values: List[float]) -> None:
        if idx == 31:
            return
        if not 0 <= idx < self.num_regs:
            raise ExecutionError(f"Invalid vector register index: V{idx}")
        if len(values) != self.lanes:
            raise ExecutionError(f"Need {self.lanes} values, got {len(values)}")
        self._regs[idx] = list(values)

    def read_scalar(self, idx: int, lane: int = 0) -> float:
        if idx == 31:
            return 0.0
        if not 0 <= idx < self.num_regs:
            raise ExecutionError(f"Invalid vector register index: V{idx}")
        if not 0 <= lane < self.lanes:
            raise ExecutionError(f"Invalid lane index: {lane}")
        return self._regs[idx][lane]

    def write_scalar(self, idx: int, value: float, lane: int = 0) -> None:
        if idx == 31:
            return
        if not 0 <= idx < self.num_regs:
            raise ExecutionError(f"Invalid vector register index: V{idx}")
        if not 0 <= lane < self.lanes:
            raise ExecutionError(f"Invalid lane index: {lane}")
        self._regs[idx][lane] = float(value)

    def get_all(self) -> List[List[float]]:
        return [v.copy() for v in self._regs]

    def set_all(self, values: List[List[float]]) -> None:
        for i, v in enumerate(values[:self.num_regs]):
            self._regs[i] = [float(x) for x in v[:self.lanes]]

    def reset(self) -> None:
        self._regs = [[0.0] * self.lanes for _ in range(self.num_regs)]

    def display_vector_registers(self, title: str = "Vector Registers",
                                 console: Optional[Console] = None) -> None:
        if console is None:
            return
        table = Table(title=title)
        table.add_column("Register")
        for lane in range(self.lanes):
            table.add_column(f"Lane {lane}")
        for i in range(self.num_regs):
            vec = self._regs[i]
            if any(v != 0.0 for v in vec):
                table.add_row(f"V{i}", *[f"{v:.4g}" for v in vec])
        if table.rows:
            console.print(str(table))
