from typing import Any, Optional


class CPUSimulatorError(Exception):
    """所有 UCPU 错误的基类。"""

    def __init__(self, message: str, detail: Optional[Any] = None):
        self.message = message
        self.detail = detail
        super().__init__(message)


class AssemblerError(CPUSimulatorError):
    """汇编阶段错误 (.pl / .asm)。"""

    def __init__(self, line: str, detail: str, line_num: Optional[int] = None,
                 filename: Optional[str] = None):
        self.line = line
        self.line_num = line_num
        self.filename = filename
        location = ""
        if filename and line_num:
            location = f"{filename}:{line_num}: "
        elif line_num:
            location = f"Line {line_num}: "
        super().__init__(f"{location}Assembler error: {line.strip()} -- {detail}",
                         detail=detail)


class CompilerError(CPUSimulatorError):
    """CIN 编译阶段错误。"""

    def __init__(self, message: str, line_num: Optional[int] = None,
                 filename: Optional[str] = None):
        self.line_num = line_num
        self.filename = filename
        location = ""
        if filename and line_num:
            location = f"{filename}:{line_num}: "
        elif line_num:
            location = f"Line {line_num}: "
        super().__init__(f"{location}Compiler error: {message}")


class ExecutionError(CPUSimulatorError):
    """指令执行阶段错误 (除零、未实现指令、栈错误等)。"""


class MemoryAccessError(CPUSimulatorError):
    """内存访问错误 (越界 / 保护违例)。"""


class PageFaultError(MemoryAccessError):
    """MMU 缺页/物理越界 (B1 分页)。"""


# 向后兼容别名 (旧代码引用 MemoryError)
MemoryError = MemoryAccessError
