"""运行配置与命令行参数解析 (native-only)。"""

from dataclasses import dataclass, field
from typing import List, Optional

# 默认程序内存: 1 GiB (Go 引擎 make([]byte, memSize), OS 懒提交;
# Python 侧稀疏内存按 4 KiB 页按需分配)。只有真正写入的部分才占用
# 物理内存, 因此默认值可以放心放大。
DEFAULT_MEM_SIZE = 1 << 30


@dataclass
class Config:
    mem_size: int = DEFAULT_MEM_SIZE
    auto_save_crom: bool = False
    max_instructions: int = 100_000_000
    sandbox_mode: bool = False
    log_level: str = 'INFO'
    log_file: Optional[str] = None
    strict_mode: bool = False
    output_file: Optional[str] = None
    optimize: int = 0
    compress_crom: bool = True
    compile_to_bin: bool = False
    compile_only: bool = False
    # A2: 确定性随机种子 (None = 随机; 0 亦表示随机)
    seed: Optional[int] = None
    # A1: CIN 运行时断言/边界检查开关 (编译期注入)
    bounds_check: bool = False
    # 传给 CIN 程序的命令行参数 (cli 里 `--` 之后的参数; 供 arg_count()/arg(i))
    program_args: List[str] = field(default_factory=list)

    # 命令行解析请使用 codecin/cli.py build_parser() (argparse, 单一来源)。
    # Config 仅承载运行配置, 由 cli._apply_namespace 填充。

    def validate(self) -> None:
        if self.mem_size < 256:
            self.mem_size = 256
        if self.mem_size > (1 << 40):
            self.mem_size = 1 << 40
        if self.max_instructions < 1:
            self.max_instructions = 1
        if self.optimize < 0:
            self.optimize = 0
        if self.optimize > 3:
            self.optimize = 3
