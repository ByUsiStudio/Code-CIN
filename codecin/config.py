"""运行配置与命令行参数解析。"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Config:
    mem_size: int = 64 * 1024
    stack_size: int = 1024
    step_mode: bool = False
    debug_mode: bool = False
    auto_save_crom: bool = False
    execution_interval: float = 0.0
    max_execution_time: float = 60.0
    interactive_mode: bool = True
    sandbox_mode: bool = False
    max_instructions: int = 100_000_000
    allow_io: bool = True
    log_level: str = 'INFO'
    log_file: Optional[str] = None
    show_memory_bytes: int = 32
    show_vector_regs: bool = True
    show_timings: bool = True
    strict_mode: bool = False
    output_file: Optional[str] = None
    optimize: int = 0
    enable_jit: bool = False
    use_native: bool = True          # 允许使用 Go 原生库加速
    cache_size: int = 64
    cache_assoc: int = 4
    profile: bool = False
    compress_crom: bool = True
    compile_to_bin: bool = False
    compile_only: bool = False
    # A2: 确定性随机种子 (None = 随机)
    seed: Optional[int] = None
    # A1: CIN 运行时断言/边界检查开关
    bounds_check: bool = False
    # B1: MMU/分页 (默认 identity 映射; unmap/protect 需测试或宿主侧设置)
    mmu: bool = False
    # A4: 远程调试服务端口 (None = 不启动)
    debug_server_port: Optional[int] = None

    # 命令行解析请使用 ucpu/cli.py build_parser() (argparse, 单一来源)。
    # Config 仅承载运行配置, 由 cli._apply_namespace 填充。

    def validate(self) -> None:
        if self.mem_size < 256:
            self.mem_size = 256
        if self.max_instructions < 1:
            self.max_instructions = 1
        if self.optimize < 0:
            self.optimize = 0
        if self.optimize > 3:
            self.optimize = 3
        if self.cache_size < 8:
            self.cache_size = 8
