#!/usr/bin/env python3
"""Code CIN 入口。

实现已拆分为 codecin/ 包:
  codecin.cli       命令行解析与启动
  codecin.cpu       CPU 核心 (112 条指令, 含 SYS 宿主调用)
  codecin.assembler 汇编器 (.pl/.asm)
  codecin.cin       CIN 高级语言编译器 (.cin)
  codecin.native    Go 原生库桥接 (可选加速, Win/Linux/Termux)
  codecin.crom      CROM 内存镜像 / UCBC 字节码
  codecin.jit       Python JIT (--jit)
  codecin.debugger  调试器

用法:
  python cpu.py <program.[cin|pl|asm|bin]> [options]
  python cpu.py --help
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from codecin.cli import main

if __name__ == '__main__':
    sys.exit(main())
