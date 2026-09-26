---
description: "Code CIN 汇编语法参考: 注释、段、数据指示符、标签、操作数、寻址、.equ 表达式与严格模式。"
---

# 汇编语法参考

本页依据 `codecin/assembler.py` 的实现编写。`.pl` (PL 关键字风格) 与 `.asm` (汇编) 使用
**同一套语法**, 差别只在助记符字典, 因此本页同时适用于两种文件。

## 注释与 `#include`

行内出现 `;` 或 `//` 时, 从该位置到行尾的部分是注释。两者完全等价, 可以混用;
双引号字符串内部的 `;` 与 `//` **不会**被当作注释。

```asm
; 整行注释 (分号)
// 整行注释 (斜杠)

.text
main:
    mov x0, 5       ; 行尾注释
    add x0, 1       // 另一种行尾注释
    out x0
    halt
```

```text
6
```

`#` 只在**行首**或**前后都是空白**时才被当作注释起点, 其余情况下它是立即数前缀。
`#include` 是预处理指示符, 必须**顶格写在行首**; 被包含文件按相对路径解析, 同一文件只包含一次。

## 段声明

段声明**独占一行**, 大小写不敏感, 带点与不带点都可以:

| 写法 | 进入的段 |
| --- | --- |
| `.text` / `text` | 代码段 (指令), 也是默认段 |
| `.code` / `code` | 代码段 (`.text` 的同义词) |
| `.data` / `data` | 数据段 (数据指示符) |

```asm
.text
main:
    mov x0, #msg
    sys 24          ; PRINT_STR
    halt

.data
msg: ASCIZ "Hello"
```

```text
Hello
```

段可以**多次切换**。默认段是 `.text`, 所以文件开头直接写数据会占用指令下标
(`examples/asm_constants.asm` 演示了先写 `.equ` 再写 `.text` / `.data` 的组织方式)。

## 数据指示符

数据项写作 `标签: 指示符 值, 值, ...`。指示符名**大小写不敏感**, 带点与不带点等价。

| 指示符 | 别名 | 宽度 | 说明 |
| --- | --- | --- | --- |
| `DB` | `BYTE` | 1 字节 | 逐字节写入, 超出部分截断 |
| `DW` | `WORD` | 2 字节 | 小端写入 |
| `DD` | `DWORD` | 4 字节 | 小端写入 |
| `DQ` | `QWORD` | 8 字节 | 小端写入 |
| `ASCII` | — | 变长 | UTF-8 字节序列, **不**追加 NUL |
| `ASCIZ` | `STRING` | 变长 | UTF-8 字节序列, **追加 NUL** |

```asm
.equ K, 10

.data
b:   .db  1, 2, 3
w:   .word 300
arr: .dq K, K*2+1, 0x20    ; 10, 21, 32
ch:  .db 'A'               ; 65
nl:  .db '\n'              ; 10
raw: ASCII "ABC"           ; 3 字节, 无结尾 NUL
msg: ASCIZ "Sum 1..10 = "  ; 12 字节 + 1 个 NUL
```

```text
b   @  0 : 01 02 03 2C 01        (1,2,3 之后是 300 的小端表示)
arr @  5 : 0A 00 ... 15 00 ... 20 00 ...
ch  @ 29 : 41                    ('A' = 65)
nl  @ 30 : 0A                    ('\n' = 10)
raw @ 31 : 41 42 43              (无结尾 NUL)
msg @ 34 : 53 75 6D 20 31 2E 2E 31 30 20 3D 20 00
```

数据值可以是十进制、`0x` / `0b` / `0o` 字面量、字符字面量或表达式。双引号字符串在数据段里
会**自动追加 NUL**: `.db "hi"` 是 3 字节 (`68 69 00`), `.db "hi", 0` 则是 4 字节。

::: warning `.asciiz` 不是合法写法
`ASCIZ` 是**不带点**的指示符 (也不接受 `.asciiz`)。写 `.asciiz "x"` 会报
`Unknown data directive: ASCIIZ`。请用 `ASCIZ "x"`、`STRING "x"` 或 `.db "x"`。
:::

## 标签与符号

标签写作 `名字:`, 名字必须匹配 `[a-zA-Z_.$][a-zA-Z0-9_.$]*` (以字母、下划线、点或 `$` 开头)。
`.text` 里的标签值是**指令下标**, `.data` 里的标签值是**数据地址 (字节偏移)**。

```asm
.text
main:
    mov x0, #nums
    ld x1, [x0]
    out x1
    halt

.data
nums: .dq 42
```

```text
42
```

**局部标签**用点开头, 例如 `.Lsum` / `.loop`:

```asm
.equ N, 5

.text
main:
    mov x0, 0
    mov x1, N
.Lsum:
    add x0, x1
    dec x1
    cmp x1, 0
    jnz .Lsum
    out x0
    halt
```

```text
15
```

- `.text` 标签与 `.data` 标签存放在**两个独立的表**中, 同名不算冲突。
- 同一段内同名标签重复定义会**静默覆盖**前一个, 不报错。
- 标签名**大小写敏感** (`Main` 与 `main` 不同); 助记符、段名、数据指示符、PL 关键字都**不敏感**。
- `codecin/cpu.py` 用 `self.labels.get('main', 0)` 决定入口, 没有 `main` 就从第 0 条指令开始。

## 操作数形式

| 形态 | 写法 | 说明 |
| --- | --- | --- |
| 寄存器 | `x0` – `x31` | 也接受 `X31`、`r5`、`w5` 等大小写/别名写法 |
| 伪寄存器 | `sp` / `fp` / `lr` / `xzr` | `sp`→内部 32, `fp`→29, `lr`→30, `xzr`→31 |
| 向量寄存器 | `v0` – `v31` | 4 通道浮点寄存器 |
| 向量通道 | `v0.0` – `v0.3` | 通道号只允许 0–3 |
| 立即数 | `5`、`#5`、`-5`、`#-5`、`0x1F` | `#` 前缀可选 |
| 浮点字面量 | `1.5`、`2.0e3` | 只对浮点指令有意义 |
| 标签 / 符号 | `loop`、`arr`、`LIMIT` | 解析为地址或指令下标 |
| 取地址 | `=arr` | 强制按符号表求值 |
| 立即数表达式 | `N*4+1`、`(COLS-5)*8`、`loop+4` | 见"立即数表达式" |
| 内存 | `[x1]`、`[x1, 8]`、`[0x100]` | 见"寻址与偏移" |
| 条件码 | `EQ`、`NE`、`LT` … | 仅 `B.<cond>` 与 `CSEL` 系列使用 |

`x31` / `xzr` 是零寄存器: 读恒为 0, 写入被丢弃。`sp` 是独立的栈指针 (内部编号 32),
**不是** `x31` 的别名。

::: warning 立即数后缀
十进制立即数允许 `u` / `U` / `l` / `L` / `f` / `F` 后缀并被忽略 (`#16f` 等于 `16`)。
`0x` 前缀字面量**只**剥离 `u`/`U`/`l`/`L`, 因为 `f`/`F` 是合法的十六进制数字
(`#0x1F` 是 31, `#0xFFu` 是 255)。
:::

::: warning 哪种形态可用取决于具体指令
上表是**汇编器**能解析的全部形态, 但**指令**未必都接受。例如
`BEQ`/`BNE`/`BLT`/`BGE`/`BLTU`/`BGEU` 的前两个操作数必须是**寄存器**。
请对照 [指令语义参考](/asm/instructions) 中每组指令的"操作数"列。
:::

### 立即数表达式

操作数位置可以写算术表达式, 运算符为 `+` `-` `*` `/` `%` 与圆括号, 操作数是数值字面量或
**已定义符号**; 标签也可以参与算术, 因为第二遍解析时标签已全部就位。

```asm
.equ ROWS, 4
.equ COLS, 8

.text
main:
    mov x0, ROWS * COLS        ; 32
    addi x0, x0, 1             ; 33
    out x0
    out 10
    out (ROWS - 1) * 2 + 1     ; 7
    halt
```

```text
33
7
```

## 寻址与偏移

内存操作数写作 `[基址, 偏移]`。方括号内部的逗号不会与外层参数分隔符混淆 (带深度跟踪的切分器)。

| 写法 | 解析结果 | 有效地址 |
| --- | --- | --- |
| `[x1]` | `('mem', 1, 0)` | `x1 + 0` |
| `[x1, 8]` | `('mem', 1, 8)` | `x1 + 8` |
| `[x1, -8]` | `('mem', 1, -8)` | `x1 - 8` |
| `[x1, #8]` | `('mem', 1, 8)` | `x1 + 8` (`#` 可选) |
| `[x1, 8*4]` | `('mem', 1, 32)` | `x1 + 32` |
| `[buf]` | `('mem', -1, 0)` | 绝对地址 (数据标签地址) |
| `[0x100]` | `('mem', -1, 256)` | 绝对地址 256 |
| `[sp, 8]` | `('mem', 32, 8)` | 栈指针 + 8 |

```asm
.text
main:
    mov x0, #arr
    ld x1, [x0]         ; arr[0]
    add x1, [x0, 8]     ; + arr[1]
    add x1, [x0, 16]    ; + arr[2]
    out x1
    out 10
    halt

.data
arr: .dq 10, 20, 30
```

```text
60
```

方括号内多于两个分量时, 第三个及之后的会被忽略 (`[x1, 8, 4]` 等价于 `[x1, 8]`);
空方括号 `[]` 报 `Empty memory operand`。

::: danger 偏移必须是立即数或表达式, 不能是寄存器
`[x1, x2]` 不合法, 汇编期就报 `Bad memory offset: x2`。需要"基址 + 变址"时, 先把地址算好:

```asm
.text
main:
    mov x0, #arr
    mov x1, 16          ; 字节偏移
    add x0, x1          ; x0 = arr + 16
    ld x2, [x0]         ; arr[2]
    out x2
    out 10
    halt

.data
arr: .dq 10, 20, 30
```

```text
30
```
:::

## `.equ` 常量与符号表达式

`.equ` 与 `.set` 完全等价, **必须带点前缀** (不带点的 `set` 是 PL 关键字, 表示 `MOV`)。
三种写法都支持: `.equ NAME, 表达式`、`.equ NAME = 表达式`、`.equ NAME 表达式`。

```asm
.equ N, 5
.equ BASE = 100
.equ OFF 2*4+1
.equ A, 5
.equ B, A*2
.equ C, (A+B)%7

.text
main:
    mov x0, N
    add x0, BASE
    add x0, OFF
    add x0, C
    out x0
    out 10
    halt
```

```text
115
```

表达式支持 `+` `-` `*` `/` `%`、圆括号、一元正负号、十进制与 `0x`/`0b`/`0o` 字面量,
以及**在此之前已定义**的 `.equ` 常量与标签。约束与陷阱:

- **前向引用会失败**: `.equ X, Y+1` 里的 `Y` 必须更早定义, 否则报 `Bad .equ expression: 'Y+1'`。
- `.equ` 只能引用**在它之前**出现过的标签, 因此不能引用后面的数据标签。
- 不支持位运算与移位: `&`、`|`、`<<`、`>>` 都报 `Bad .equ expression`。
- `/` 是整数除法, 除零报 `Bad .equ expression: '5/0'`。
- 缺少表达式时报 `.equ format: .equ NAME <expr> (例: .equ N, 8*4)`。

::: warning `mov` 取地址, 不取内容
`mov x0, buf` 会把 `buf` 的**地址**放入 `x0`。读取 `buf` 处的内容必须再用加载指令:

```asm
.text
main:
    mov x0, #buf
    ld x1, [x0]
    out x1
    out 10
    halt

.data
buf: .dq 77
```

```text
77
```
:::

## PL 关键字风格与 ASM 风格对照

`Constants.PL_KEYWORDS` 为 **112 条指令逐一提供了 PL 关键字**, 两种风格一一对应。
PL 关键字**全部小写**且用下划线分词。同一文件里可以自由混用:

| ASM 助记符 | PL 关键字 | 操作数 | 语义 |
| --- | --- | --- | --- |
| `MOV` | `set` | 2 | 传送 / 取地址 |
| `ADD` / `SUB` / `MUL` / `DIV` | `add` / `subtract` / `multiply` / `divide` | 2 | 目标寄存器原位算术 |
| `AND` / `OR` / `XOR` | `and` / `or` / `xor` | 2 | 按位与 / 或 / 异或 |
| `SHL` / `SHR` | `shift_left` / `shift_right` | 2 | 左移 / 逻辑右移 |
| `INC` / `DEC` | `increment` / `decrement` | 1 | 自增 / 自减 1 |
| `CMP` | `compare` | 2 | 比较并设置标志 |
| `JMP` / `JZ` / `JNZ` | `jump` / `jump_zero` / `jump_not_zero` | 1 | 跳转族 (按标志) |
| `JE` / `JL` / `JG` | `jump_equal` / `jump_less` / `jump_greater` | 1 | 比较跳转 |
| `PUSH` / `POP` | `push` / `pop` | 1 | 压栈 / 出栈 |
| `CALL` / `RET` | `call` / `return` | 1 / 0 | 子程序调用 / 返回 |
| `IN` / `OUT` / `HALT` | `input` / `output` / `stop` | 1 / 1 / 0 | 读入 / 输出 / 停机 |
| `ADDI` / `ORI` / `ANDI` | `add_imm` / `or_imm` / `and_imm` | 3 | 寄存器与立即数运算 |
| `BEQ` / `BNE` / `BLT` | `branch_equal` / `branch_not_equal` / `branch_less_than` | 3 | 两寄存器比较跳转 |
| `B` / `BL` | `branch` / `branch_link` | 变长 / 1 | 跳转 / 分支并压入返回地址 |
| `LB` / `LH` / `LW` / `LD` | `load_byte` / `load_half` / `load_word` / `load_double` | 2 | 加载 8 / 16 / 32 / 64 位 |
| `SB` / `SH` / `SW` / `SD` | `store_byte` / `store_half` / `store_word` / `store_double` | 2 | 存储 8 / 16 / 32 / 64 位 |
| `SYS` | `syscall` | 变长 | 宿主系统调用 |

完整 112 条见 [指令语义参考](/asm/instructions)。下面这段程序两种风格输出都是 `6`:

::: tabs

== ASM 风格

```asm
.equ LIMIT, 3

.text
main:
    mov x0, 0
    mov x1, 0
count_loop:
    add x0, x1
    inc x1
    cmp x1, LIMIT
    b.le count_loop
    out x0
    out 10
    halt
```

== PL 关键字风格

```asm
.equ LIMIT, 3

.text
main:
    set x0, 0
    set x1, 0
count_loop:
    add x0, x1
    increment x1
    compare x1, LIMIT
    b.le count_loop
    output x0
    output 10
    stop
```

:::

::: warning PL 分支关键字需要 3 个操作数
`branch_not_equal` / `branch_equal` / `branch_less_than` 比较的是两个**寄存器**,
需要 `rs1, rs2, 目标` 三个操作数。只写标签会报
`Argument count mismatch for BNE: expected 3, got 1` (在 `--strict` 下),
非严格模式则运行期抛 `list index out of range`。

按标志位跳转请用 `B` 的条件后缀, 或 `JZ`/`JNZ`/`JE`/`JL`/`JG`:

```asm
.text
main:
    set x1, 0
    set x2, 1
loop:
    add x1, x2
    increment x2
    compare x2, 11
    b.ne loop            ; 条件后缀形式, 单操作数
    output x1
    stop
```

```text
55
```
:::

## 条件后缀

`B` 指令支持 16 个条件后缀, 写作 `B.<cond>` (大小写不敏感), 由标志位 `N` `Z` `C` `V`
组合而成, 与 `CMP` / `SUBS` / `ADDS` / `FCMP` 配合使用:

| 后缀 | 含义 | 判定 | 后缀 | 含义 | 判定 |
| --- | --- | --- | --- | --- | --- |
| `EQ` | 相等 | `Z=1` | `NE` | 不等 | `Z=0` |
| `CS` | 进位 / 无借位 | `C=1` | `CC` | 无进位 / 有借位 | `C=0` |
| `MI` | 负数 | `N=1` | `PL` | 非负数 | `N=0` |
| `VS` | 溢出 | `V=1` | `VC` | 无溢出 | `V=0` |
| `HI` | 无符号大于 | `C=1 且 Z=0` | `LS` | 无符号小于等于 | `C=0 或 Z=1` |
| `GE` | 有符号大于等于 | `N=V` | `LT` | 有符号小于 | `N!=V` |
| `GT` | 有符号大于 | `Z=0 且 N=V` | `LE` | 有符号小于等于 | `Z=1 或 N!=V` |
| `AL` | 总是 | 恒真 | `NV` | 从不 | 恒假 |

```asm
.text
main:
    mov x1, 3
    mov x2, 7
    cmp x1, x2
    b.lt less
    mov x0, 0
    b done
less:
    mov x0, 1
done:
    out x0
    out 10
    halt
```

```text
1
```

同一套条件码也用于 `CSEL` / `CSINC` / `CSINV` / `CSNEG` 的第四个操作数。

::: danger `B.AL` 恒为真
`B.AL` 是无条件跳转, 写在循环里会造成死循环。写条件分支时请确认标志位已被前面的
`CMP` / `SUBS` / `ADDS` / `FCMP` 设置过 —— `INC` / `DEC` **不更新标志位**。
:::

## 严格模式 (`--strict`)

默认情况下汇编器**不检查操作数个数**。加上 `--strict` 后, 每条指令的操作数个数会与
`Constants.ARG_COUNTS` 逐一比对:

```asm
.text
main:
    csel x0, x0, x0        ; CSEL 需要 4 个操作数
    halt
```

```text
Assembler error: csel x0, x0, x0 -- Argument count mismatch for CSEL: expected 4, got 3
```

```bash
python cpu.py prog.asm --strict --no-native
```

标记为"变长"(`ARG_COUNTS` 为 `-1`) 的指令不参与计数检查, 只有 `B`、`JALR`、`SYS` 三条。
建议始终开启 `--strict`: 它能挡住绝大多数"操作数漏写/多写"的错误, 这类错误在非严格模式下
往往要等到运行期才以 `list index out of range` 之类的形式暴露。

## 常见报错与排查

| 报错文本 | 触发原因 | 处理方式 |
| --- | --- | --- |
| `Unknown instruction: xxx` | 助记符不存在 (例如 `JLE`、`JGE`) | 改用 `CMP` + `JG`/`JL`/`JE`, 或 `B.<cond>` |
| `Invalid label: xxx` | 标签名不符合 `[a-zA-Z_.$][a-zA-Z0-9_.$]*` | 改成合法名字 (不能以数字开头) |
| `Undefined label: xxx` | 操作数位置引用了未定义符号 | 检查拼写、段归属与定义顺序 |
| `Undefined symbol: xxx` | `=name` 形式引用了未定义符号 | 同上 |
| `Unknown data directive: XXX` | 数据指示符名错误 (如 `.asciiz`) | 用 `ASCIZ` / `STRING` / `.db` |
| `Malformed memory operand: [x1` | 方括号未闭合 | 补上 `]` |
| `Empty memory operand` | 写成 `[]` | 至少给出基址或绝对地址 |
| `Bad memory base: xxx` | 方括号第一项不可求值 | 改用寄存器或数值/符号 |
| `Bad memory offset: xxx` | 偏移含未定义符号或寄存器 | 偏移只能是立即数或表达式 |
| `Cannot parse operand: xxx` | 既不是寄存器也不是合法数值/符号 | 检查多余空格、非法字符 |
| `Bad immediate: #xxx` | `#` 后既不是字面量也不是已定义符号 | 检查符号定义顺序 |
| `Bad .equ expression: 'xxx'` | 未定义符号 / 位运算符 / 除零 | 见 `.equ` 约束 |
| `.equ format: .equ NAME <expr>` | `.equ` 缺少表达式 | 补上 `, 值` |
| `Empty operand` | 连续逗号或尾随逗号 | 删除多余逗号 |
| `Argument count mismatch for XXX` | 仅 `--strict` 下, 操作数个数不符 | 按 [指令语义参考](/asm/instructions) 补齐 |
| `File 'xxx' not found` | 待汇编文件不存在 | 检查路径 |
| `Include file 'xxx' not found` | `#include` 目标不存在 | 检查相对路径 |
| `#include format error` | `#include` 后没有文件名 | 补上文件名 |

排查顺序建议: 先加 `--strict` 消掉操作数个数问题, 再用 `--log-level DEBUG` (或 `--debug`)
查看标签表与逐指令追踪, 或用 `--step` 进入交互式单步 (见 [交互式调试器](/tools/debugger)),
最后确认标签归属 —— 代码标签只能作跳转目标, 数据标签要先用 `mov` 取地址再用内存指令访问。

::: warning 文件必须是 UTF-8 且不要带 BOM
汇编器以 UTF-8 读取源文件。若用 Windows PowerShell 的 `Set-Content -Encoding UTF8` 生成文件,
会写入 BOM, 首行变成 `\ufeff.text` 并报 `Unknown instruction`。请保存为 **UTF-8 (无 BOM)**。
:::

## 相关页面

- [汇编总览](/asm/)
- [指令语义参考](/asm/instructions)
- [指令集编码表](/reference/isa)
- [寄存器与内存模型](/reference/registers-memory)
- [内嵌 CPU 指令语句](/language/inline-cpu)
