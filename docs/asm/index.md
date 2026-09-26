---
description: "Code CIN 汇编总览: .cin / .pl / .asm 三条输入路径、统一汇编器入口、最小可运行程序与学习路径。"
---

# 汇编总览

Code CIN 提供三条平行的程序输入路径, 它们**共用同一套 ISA、同一个汇编器入口、同一种字节码**。
无论你写高级语言、PL 关键字风格还是 ARM64 风格汇编, 最终都会被翻译成同构的 `(opcode, operands)`
指令序列, 交给同一个 CPU 执行。

## 三条输入路径

| 扩展名 | 路径 | 风格 | 由谁处理 |
| --- | --- | --- | --- |
| `.cin` | 高级语言 | 类 C, 函数 / `struct` / 数组 / 字符串 | `codecin/cin.py` 的 `CINCompiler` |
| `.pl` | PL 关键字风格汇编 | `set` / `add` / `jump_zero` 等英文关键字 | `codecin/assembler.py` |
| `.asm` | 汇编 | 原生助记符 `MOV` / `ADD` / `CMP`, 兼容 ARM64 / RISC-V 写法 | `codecin/assembler.py` |

`.pl` 与 `.asm` 走的是**完全相同的代码路径**: `codecin/cpu.py` 的 `load_program()` 对二者
不做区分, 都由 `Assembler.assemble_file()` 解析。区别只在于助记符字典:

- `.asm` 直接使用 `Constants.OPCODE_NAME_TO_ENUM` 中的大写助记符 (`MOV`、`ADDI`、`B`…);
- `.pl` 使用 `Constants.PL_KEYWORDS` 中的小写关键字 (`set`、`add_imm`、`branch`…)。

因为 PL 关键字只是一张映射表, 你甚至可以在**同一个文件里混用**两种风格:

```asm
.text
main:
    set x0, 0            ; PL 关键字风格
    mov x1, 1            ; ASM 风格
    MOV x2, 10           ; 助记符大小写不敏感
loop:
    add x0, x1           ; 两种风格共享 ADD
    increment x1         ; PL: increment = INC
    compare x1, 11       ; PL: compare = CMP
    jl loop              ; ASM 条件跳转
    out x0
    halt
```

```text
55
```

## 最小可运行程序

一个能跑起来的汇编程序至少需要 `.text` 段。`main` 标签是约定的入口 (由
`load_program()` 读取 `self.labels.get('main', 0)`), 没有 `main` 时从第 0 条指令开始执行。

```asm
; 最小可运行程序: 输出 7 然后停机
.text
main:
    mov x0, 7
    out x0
    out 10          ; 输出字节 10 等于换行
    halt
```

```text
7
```

`.text` 段里的 `main:` 是标签, `halt` 是唯一的正常停机方式 (不写 `halt` 时程序会一直执行到
指令序列末尾后停止, 见 [汇编语法参考](/asm/syntax) 的"常见报错与排查")。

## 统一汇编器入口

两条汇编路径共用 `codecin/assembler.py` 的 `Assembler` 类:

| 方法 / 成员 | 作用 |
| --- | --- |
| `assemble_file(filename)` | 从文件读取并按 `#include` 预处理后汇编 |
| `assemble_source(source)` | 直接从字符串汇编 (供 CIN 编译器与测试使用) |
| `self.instructions` | 产物: `(opcode_name, operands)` 列表 |
| `self.labels` | `.text` 段标签名到指令下标的映射 |
| `self.data_labels` | `.data` 段标签名到数据地址的映射 |
| `self.equ` | `.equ` / `.set` 定义的常量表 |

汇编分两遍完成: 第一遍扫描段声明、`.equ`、标签与数据指示符, 第二遍才解析指令与操作数,
因此**同一文件内的标签可以前向引用**, 而 `.equ` 的值**必须在其定义行之前已经确定**。

## 运行命令

最简形式是直接给出程序文件, 由扩展名选择路径:

```bash
python cpu.py test_asm.asm              # 汇编
python cpu.py basic.cin                 # 高级语言
python cpu.py prog.pl                   # PL 关键字风格
python cpu.py prog.bin                  # 已编译字节码
```

文档中的示例统一使用纯 Python 解释路径, 以便在任何机器上复现:

```bash
python cpu.py test_asm.asm --no-native --log-level ERROR
```

```text
Sum 1..10 = 55
155
```

常用的相关开关 (完整列表见 `cpu.py --help`):

| 开关 | 作用 |
| --- | --- |
| `--no-native` | 禁用 Go 原生库, 强制纯 Python 解释执行 |
| `--strict` | 严格汇编模式, 校验指令的操作数个数 |
| `--debug` | 逐指令追踪寄存器、内存、栈与缓存 |
| `--step` | 交互式单步调试 |
| `--compile-only` | 只编译为 `.bin` 字节码, 不执行 |
| `--disasm` | 反汇编 `.bin` 字节码并退出 |
| `--log-level ERROR` | 只输出错误, 保持示例输出干净 |

在 Windows PowerShell 中同样直接调用:

```powershell
python cpu.py test_asm.asm --no-native --log-level ERROR
```

## 与 CIN 的关系

三条路径**一致**是 Code CIN 的核心设计约束:

```text
  .cin 高级语言 ──► CINCompiler ──┐
                                  ├──► (opcode, operands) 指令序列
  .pl  PL 关键字 ──► Assembler ───┤            │
  .asm 汇编      ──► Assembler ───┘            ├──► UCPU 解释器 / Go 原生 VM / JIT
                                               └──► UCBC 字节码 .bin
```

- **同一 ISA**: 112 条指令由 `codecin/isa.py` 的 `Opcode` 枚举唯一定义, 三条路径没有各自的私有指令。
- **同一字节码**: `.cin` 与 `.asm` 都能编译成同格式的 `.bin` (`--compile-only`), 并可互相反汇编验证。
- **同一执行器**: 都由 `codecin/cpu.py` 的 `_op_*` 处理器执行, 语义不会因输入路径而分叉。

CIN 还能在函数体里直接内嵌 **PL 关键字风格的 CPU 指令语句** (无逗号), 实现零开销的定点操作:

::: tabs

== ASM 风格

```asm
.text
main:
    mov x0, 0
    mov x1, 1
loop:
    add x0, x1
    inc x1
    cmp x1, 11
    jl loop
    out x0
    halt
```

== PL 关键字风格

```asm
.text
main:
    set x0, 0
    set x1, 1
loop:
    add x0, x1
    increment x1
    compare x1, 11
    jump_less loop
    output x0
    stop
```

== CIN 高级语言

```c
function main() -> int {
    int sum = 0
    int i = 1
    while (i <= 10) {
        sum = sum + i
        i = i + 1
    }
    println("sum = " + sum)
    return 0
}
```

:::

三者输出都是 `55`。内嵌 CPU 指令的 CIN 写法见 [内嵌 CPU 指令语句](/language/inline-cpu):

```c
function main() -> int {
    int i = 0
    int total = 0
    for (int k = 1; k <= 10; k = k + 1) {
        set i i
        add i k
        add total i
    }
    println("total = " + total)
    return 0
}
```

## 学习路径

建议按以下顺序阅读:

1. [汇编语法参考](/asm/syntax) —— 注释、段、数据指示符、标签、操作数形式、`.equ` 表达式与 `--strict`。
2. [指令语义参考](/asm/instructions) —— 112 条指令的分组表与常用指令的独立小节。
3. [指令集编码表](/reference/isa) —— 由 `script/gen_isa_docs.py` 自动生成的编码表, 与 `codecin/isa.py` 同步。
4. [寄存器与内存模型](/reference/registers-memory) —— `x0`–`x31`、`sp`/`fp`/`lr`/`xzr`、向量寄存器与栈布局。
5. [快速开始](/guide/quickstart) —— 安装、命令行与第一个程序。

::: tip 从示例文件入手
仓库里的 `test_asm.asm` 是端到端可运行的权威样例 (循环求和 + 字符串打印 + `.data` 段 + 间接寻址),
`examples/asm_constants.asm` 演示 `.equ` 与表达式立即数。两个文件都可以直接用
`python cpu.py <文件> --no-native` 运行。
:::

::: warning 文档中的旧示例
仓库根目录 `README.md` 里的部分 PL 示例使用了本 ISA **不存在**的 `jle` / `jge` 助记符。
Base ISA 的比较跳转只有 `JMP` / `JZ` / `JNZ` / `JE` / `JL` / `JG`, 请以
[指令语义参考](/asm/instructions) 为准。
:::

## 相关页面

- [汇编语法参考](/asm/syntax)
- [指令语义参考](/asm/instructions)
- [指令集编码表](/reference/isa)
- [寄存器与内存模型](/reference/registers-memory)
- [交互式调试器](/tools/debugger)
- [CIN 语言总览](/language/)
- [快速开始](/guide/quickstart)
