---
description: Code CIN 的整体架构：分层设计、Python 与 Go 的分工、编译流水线、指令集分组、产物格式与三路径一致性约束。
---

# 架构总览

Code CIN 是一门**类 C 的高级语言**加一套**跨平台运行时 (VM)**。它从源码出发, 编译成
UCPU 字节码 (UCBC), 再由 Go 原生 VM / JIT / Python 解释器三路径之一执行, 行为一致。
除语言工具链外, 运行时还内置 2D 绘图画布 (导出 PNG)、联网音频播放与系统交互等宿主能力。

## 分层结构

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ 应用层    CLI (codecin / python cpu.py)   交互式调试器   性能分析器        │
├──────────────────────────────────────────────────────────────────────────┤
│ 编译层    CIN 编译器 (cin.py / Go compiler)   汇编器 (assembler.py)        │
│           词法 → 语法 → 代码生成 → UCBC 字节码                             │
├──────────────────────────────────────────────────────────────────────────┤
│ 执行层    Python 解释执行   ·   JIT (基本块编译)   ·   Go 原生 VM (c-shared)│
├──────────────────────────────────────────────────────────────────────────┤
│ 硬件层    寄存器文件 (X0-X31 / V0-V31) · LRU 缓存 · 内存与保护 · MMU       │
├──────────────────────────────────────────────────────────────────────────┤
│ 指令集    Base 28 · ARM64 40 · RISC-V 27 · FP 10 · Vector 6 · SYS 1        │
└──────────────────────────────────────────────────────────────────────────┘
```

## 编译与执行流水线

```text
prog.cin ─┐
prog.pl  ─┼─► 编译器 / 汇编器 ─► UCBC 字节码 (.bin) ─► 缓存 ─► CPU 核心 ─► 输出 / 统计
prog.asm ─┘                              │                        │
                                         └─► CROM v3 内存镜像 ◄───┘  (--save / --crom)
```

1. **词法/语法分析**: `codecin/cin.py` (以及 Go 侧 `codecin/native/compiler/`) 把 CIN 源码
   tokenize、parse 成 IR, 同时处理 `import` 展开 (编译期包含);
2. **代码生成**: 生成 UCBC 指令序列与数据段, 打印编译统计
   (`CIN compiled: N instructions`);
3. **加载**: `.bin` 直接载入内存; `.crom` 会把内存镜像恢复到虚拟机内存;
4. **执行**: 按 [执行路径](/guide/execution-paths) 选择引擎, 逐指令更新寄存器/内存/统计;
5. **收尾**: `HALT` 后输出性能统计 (`--profile`) 或状态 dump (`--debug`), 可选保存 CROM。

## Python 与 Go 的分工

| 侧 | 角色 | 内容 |
|----|------|------|
| Go (`codecin/native/`) | **语言实现的核心** | CIN 编译器 (tokenizer/parser/codegen)、字节码 VM (`engine/`)、CROM 压缩与解包、AOT 产物运行时 (`aot/`)、宿主能力实现 (画布/音频/系统/Termux) |
| Python (`codecin/`) | **唯一 CLI 外壳 + 解释器 + 工具链** | 命令行解析、CIN 编译器 (纯 Python 路径)、汇编器、解释执行、JIT、调试器、日志/统计、ctypes 桥接、AOT 构建器 |

::: warning Go 侧不提供任何 CLI
`codecin/native/` 下唯一的 `package main` 是 cgo 的 **c-shared 库入口** `main.go`。
不存在 `codecin` 命令行二进制 —— 唯一入口是 Python 侧 (`codecin` / `python cpu.py`)。
这样做是为了避免历史上“两套 CLI、两套语义、产物不对应”的问题。
:::

## 模块职责

| 模块 | 职责 |
|------|------|
| `codecin/cli.py` | 命令行唯一入口: argparse 参数表 (`build_parser`) → `Config` → 加载 → 执行 |
| `codecin/config.py` | 运行配置 dataclass 与默认值、`validate()` 归一化 |
| `codecin/cin.py` | CIN 编译器: 词法、语法、代码生成、`import` 解析、内建/宿主内建表 |
| `codecin/assembler.py` | PL / ASM 汇编器 (PL 关键字到助记符的映射、`.equ`/数据指示符、操作数解析) |
| `codecin/isa.py` | 指令集单一真源: `Opcode` 112 条、`Syscall`、`Constants` (参数个数、PL 关键字、名称表) |
| `codecin/cpu.py` | CPU 核心: 解释执行、逐指令追踪、三条路径调度、SYS 宿主调用分发 |
| `codecin/jit.py` | Python JIT: 基本块提取、生成 Python 代码、缓存与统计 |
| `codecin/native.py` | Go 原生库 ctypes 桥接: 候选路径查找、符号绑定、失败回退 |
| `codecin/memory.py` / `registers.py` / `cache.py` | 内存与保护、寄存器文件、LRU 缓存模型 |
| `codecin/stats.py` | 指令/周期/分支/缓存/JIT 统计与报告数据 |
| `codecin/crom.py` | UCBC `.bin` 与 CROM v3 `.crom` 读写 (与 Go 端二进制兼容) |
| `codecin/aot.py` | AOT 构建器: 依赖闭包解析、stub 生成、`go build` 静态链接受理 |
| `codecin/debugger.py` | 交互式调试器与 TCP 远程调试服务 |
| `codecin/console.py` / `logger.py` | rich 适配层与日志 (模块内禁止直接 `print`) |
| `codecin/disasm.py` | `.bin` 反汇编为文本清单 |

完整的目录树与“改一处要同步哪些文件”见 [项目结构](/dev/structure)。

## 指令集分组

| 分组 | 数量 | 编码范围 | 内容 |
|------|------|----------|------|
| Base ISA | 28 | 0–27 | 数据传输 / 算术 / 逻辑 / 控制 / 栈 / IO |
| ARM64 扩展 | 40 | 28–67 | 条件、移位、加载存储、分支、位操作 (WFE/WFI/SEV 无事件模型, 等同 NOP) |
| FP 浮点扩展 | 10 | 68–77 | IEEE-754 浮点运算与转换 |
| Vector 向量扩展 | 6 | 78–83 | 4-lane 向量算术与加载存储 |
| RISC-V 扩展 | 27 | 84–110 | RV64I 风格加载存储 / 立即数 / 分支 / 跳转 |
| SYS 宿主调用 | 1 | 111 | 宿主系统调用 (CIN 内建函数与浮点支撑) |

- 逐条编码表: [指令集编码表](/reference/isa) (由 `python script/gen_isa_docs.py` 自动生成)
- 逐条语义: [指令语义参考](/asm/instructions)
- 寄存器与内存模型: [寄存器与内存模型](/reference/registers-memory)

## 产物与格式

| 产物 | 内容 | 生成 | 运行 |
|------|------|------|------|
| `.bin` | UCBC 字节码 (头部 `UCBC` + 指令流) | `--compile` / `--compile-only` | `codecin prog.bin` |
| `.crom` | CROM v3 内存镜像 (可 zlib 压缩, CRC32 校验) | `--save` | `--crom <file>` 恢复内存 |
| 独立可执行文件 | 内嵌字节码与初始内存的静态单文件 (内置 Go VM) | `--build-exe` | 直接执行, 无需任何运行时 |

细节见 [二进制格式](/runtime/formats) 与 [AOT 独立可执行文件](/runtime/aot)。

## 设计原则

| 原则 | 体现 |
|------|------|
| 完整性 | 从高级语言到机器码的完整工具链 (编译器 / 汇编器 / 调试器 / 分析器 / AOT) |
| 性能 | 三路径可选、JIT 基本块编译、原生 c-shared VM、字节码缓存 |
| 可用性 | rich 终端界面、交互式调试、逐指令追踪、彩色错误面板 |
| 可分析性 | 指令级统计 (CPI/分支/缓存/JIT)、热指令画像 |
| 可扩展性 | 模块化包结构、ISA 单一真源 + 常量自动生成、表驱动宿主内建 |

## 下一步

- [项目结构](/dev/structure) — 目录树与同步关系
- [执行路径](/guide/execution-paths) — 三引擎如何选择
- [CIN 语言总览](/language/) — 语言全貌
