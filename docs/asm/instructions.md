---
description: "Code CIN 汇编 112 条指令语义参考: 分组总览表、逐条语义与最常用指令的独立小节和汇编示例。"
---

# 指令语义参考

本页覆盖 UCPU 的全部 **112 条指令**。分组数量与 `codecin/isa.py` 的 `Opcode` 枚举、
`tests/test_isa_dispatch.py` 中的分组断言完全一致; 每条指令的**语义**取自
`codecin/cpu.py` 的 `_op_*` 处理器, **操作数个数**取自 `Constants.ARG_COUNTS`。

## 分组总览

| 组 | 取值区间 | 条数 | 说明 |
| --- | --- | --- | --- |
| Base | 0 – 27 | 28 | 基础指令: 传送、算术、逻辑、跳转、栈、I/O |
| ARM64 | 28 – 67 | 40 | ARM64 风格: 标志位算术、移位、成对访存、条件选择、位操作 |
| FP | 68 – 77 | 10 | 浮点: 四则、比较、转换、绝对值、访存 |
| Vector | 78 – 83 | 6 | 向量: 4 通道浮点四则与访存 |
| RISC-V | 84 – 110 | 27 | RISC-V 风格: 宽度访存、立即数算术、寄存器分支、跳转链接 |
| SYS | 111 | 1 | 宿主系统调用入口 |
| **合计** | 0 – 111 | **112** | 每条指令都有对应的 `_op_*` 处理器 |

指令编码 (操作码编号与操作数类型码) 见 [指令集编码表](/reference/isa), 该页由
`script/gen_isa_docs.py` 从 `codecin/isa.py` 自动生成。

完整助记符清单 (按操作数个数归类):

| 操作数个数 | 指令 |
| --- | --- |
| 0 | `RET` `HALT` `NOP` `WFE` `WFI` `SEV` |
| 1 | `INC` `DEC` `JMP` `JZ` `JNZ` `JE` `JL` `JG` `PUSH` `POP` `CALL` `IN` `OUT` `BL` `BR` |
| 2 | `MOV` `LOAD` `STORE` `ADD` `SUB` `MUL` `DIV` `AND` `OR` `XOR` `SHL` `SHR` `CMP` `MVN` `LDR` `STR` `CBZ` `CBNZ` `SXTB` `SXTH` `SXTW` `UXTB` `UXTH` `CLZ` `CLS` `RBIT` `REV` `FCMP` `FCVT` `FABS` `FNEG` `LDRS` `STRS` `VLD1` `VST1` `LB` `LH` `LW` `LD` `SB` `SH` `SW` `SD` `JAL` `LUI` `AUIPC` |
| 3 | `ADDS` `SUBS` `ADDC` `SUBC` `LSL` `LSR` `ASR` `ROR` `EOR` `BIC` `ORN` `LDP` `STP` `TBZ` `TBNZ` `FADD` `FSUB` `FMUL` `FDIV` `VADD` `VSUB` `VMUL` `VDIV` `ADDI` `SLTI` `SLTIU` `XORI` `ORI` `ANDI` `SLLI` `SRLI` `SRAI` `BEQ` `BNE` `BLT` `BGE` `BLTU` `BGEU` |
| 4 | `CSEL` `CSINC` `CSINV` `CSNEG` |
| 变长 | `B` `JALR` `SYS` |

::: warning `WFE` / `WFI` / `SEV` 是 `NOP` 别名
本 ISA 没有中断与多核事件模型, 这三条指令在 `codecin/cpu.py` 的 `_OP_ALIASES` 中直接
指向 `_op_nop`, 语义等价于空操作。
:::

## Base 组 (28 条)

| 助记符 | 操作数 | 语义 | 影响标志 / 备注 |
| --- | --- | --- | --- |
| `MOV` | 2 | `rd = 源` (`_op_mov`) | 无标志; 源可为寄存器、立即数、标签地址或内存 |
| `LOAD` | 2 | `rd = mem32[地址]` (`_op_load`) | 读 32 位; 走缓存统计 |
| `STORE` | 2 | `mem32[地址] = rs` (`_op_store`) | 写 32 位; 走缓存统计 |
| `ADD` | 2 | `rd = rd + 源` (`_op_add`) | 目标即第一个操作数, 原地累加 |
| `SUB` | 2 | `rd = rd - 源` (`_op_sub`) | 原地相减 |
| `MUL` | 2 | `rd = rd * 源` (`_op_mul`) | 原地相乘 |
| `DIV` | 2 | `rd = rd / 源` (`_op_div`) | 有符号 64 位; 除零抛 `Division by zero` |
| `AND` | 2 | `rd = rd & 源` (`_op_and`) | — |
| `OR` | 2 | `rd = rd \| 源` (`_op_or`) | — |
| `XOR` | 2 | `rd = rd ^ 源` (`_op_xor`) | — |
| `SHL` | 2 | `rd = rd << (源 & 63)` (`_op_shl`) | 移位量取低 6 位 |
| `SHR` | 2 | `rd = rd >> (源 & 63)` (`_op_shr`) | 逻辑右移 (高位补 0) |
| `INC` | 1 | `rd = rd + 1` (`_op_inc`) | 无标志 (不设 Z/N) |
| `DEC` | 1 | `rd = rd - 1` (`_op_dec`) | 无标志 |
| `CMP` | 2 | 按 `a - b` 设置全部标志 (`_op_cmp`) | 写 N/Z/C/V; 结果不保存 |
| `JMP` | 1 | `pc = 目标` (`_op_jmp`) | 无条件跳转 |
| `JZ` | 1 | `Z=1` 时跳转 (`_op_jz`) | 依赖标志 |
| `JNZ` | 1 | `Z=0` 时跳转 (`_op_jnz`) | 依赖标志 |
| `JE` | 1 | `Z=1` 时跳转 (`_op_je`) | 与 `JZ` 判定相同 |
| `JL` | 1 | `N!=V` 时跳转 (`_op_jl`) | 有符号小于 |
| `JG` | 1 | `Z=0 且 N=V` 时跳转 (`_op_jg`) | 有符号大于 |
| `PUSH` | 1 | 压栈: `sp -= 8; mem64[sp] = 源` (`_op_push`) | 栈溢出抛 `Stack overflow (collides with heap)` |
| `POP` | 1 | `rd = mem64[sp]; sp += 8` (`_op_pop`) | 栈空抛 `Stack underflow` |
| `CALL` | 1 | 压入 `pc` 后跳转 (`_op_call`) | 返回地址入栈 |
| `RET` | 0 | `pc = mem64[sp]; sp += 8` (`_op_ret`) | 与 `CALL` 配对 |
| `IN` | 1 | 读入一个整数到 `rd` (`_op_in`) | `--no-io` 下不生效; 解析失败或 EOF 得 0 |
| `OUT` | 1 | 输出源 (`_op_out`) | 字节值 10 输出为换行; 浮点/向量输出浮点文本 |
| `HALT` | 0 | 停机, 返回 `False` 终止执行 (`_op_halt`) | — |

::: tip `CMP` 与 `SUBS` 的区别
`CMP a, b` 只设置标志, 不写回结果; `SUBS rd, rn, b` 既设置标志也把 `rn - b` 写入 `rd`。
Base 组的 `ADD`/`SUB` **不设置标志**, 需要标志时请用 `CMP` 或 ARM64 组的 `ADDS`/`SUBS`。
:::

## ARM64 组 (40 条)

| 助记符 | 操作数 | 语义 | 影响标志 / 备注 |
| --- | --- | --- | --- |
| `ADDS` | 3 | `rd = rn + 源`, 设置标志 (`_op_adds`) | 写 N/Z/C/V |
| `SUBS` | 3 | `rd = rn - 源`, 设置标志 (`_op_subs`) | 写 N/Z/C/V |
| `ADDC` | 3 | `rd = rn + 源 + C` (`_op_addc`) | 进位输入来自 C 标志 |
| `SUBC` | 3 | `rd = rn - 源 - (1 - C)` (`_op_subc`) | 借位输入来自 C 标志 |
| `LSL` | 3 | `rd = rn << (源 & 63)` (`_op_lsl`) | 逻辑左移 |
| `LSR` | 3 | `rd = rn >> (源 & 63)` (`_op_lsr`) | 逻辑右移 |
| `ASR` | 3 | 有符号右移 (`_op_asr`) | 高位补符号位 |
| `ROR` | 3 | 循环右移 (`_op_ror`) | 移位量为 0 时原值返回 |
| `MVN` | 2 | `rd = ~源` (`_op_mvn`) | 按位取反 |
| `EOR` | 3 | `rd = rn ^ 源` (`_op_eor`) | — |
| `BIC` | 3 | `rd = rn & ~源` (`_op_bic`) | 位清除 |
| `ORN` | 3 | `rd = rn \| ~源` (`_op_orn`) | — |
| `LDR` | 2 | `rd = mem32[地址]` (`_op_ldr`) | 32 位加载, 同 `LOAD` |
| `STR` | 2 | `mem32[地址] = rs` (`_op_str`) | 32 位存储, 同 `STORE` |
| `LDP` | 3 | `rt = mem64[地址]; rt2 = mem64[地址+8]` (`_op_ldp`) | 成对加载 64 位 |
| `STP` | 3 | `mem64[地址] = rt; mem64[地址+8] = rt2` (`_op_stp`) | 成对存储 64 位 |
| `CBZ` | 2 | `rd == 0` 时跳转 (`_op_cbz`) | 不看标志 |
| `CBNZ` | 2 | `rd != 0` 时跳转 (`_op_cbnz`) | 不看标志 |
| `TBZ` | 3 | 位 `bit` 为 0 时跳转 (`_op_tbz`) | 位号取低 6 位 |
| `TBNZ` | 3 | 位 `bit` 为 1 时跳转 (`_op_tbnz`) | 位号取低 6 位 |
| `B` | 变长 | 无条件跳转; 带条件后缀时按标志判定 (`_op_b`) | 支持 16 个条件后缀 |
| `BL` | 1 | 压入 `pc` 后跳转 (`_op_bl`) | 同 `CALL` |
| `BR` | 1 | `pc = rn` (`_op_br`) | 寄存器间接跳转, 不保存返回地址 |
| `NOP` | 0 | 空操作 (`_op_nop`) | — |
| `WFE` | 0 | 空操作 (`_op_nop` 别名) | 无事件模型 |
| `WFI` | 0 | 空操作 (`_op_nop` 别名) | 无中断模型 |
| `SEV` | 0 | 空操作 (`_op_nop` 别名) | 无多核模型 |
| `CSEL` | 4 | 条件成立取 `rn`, 否则取第 3 个源 (`_op_csel`) | 第 4 个操作数是条件码 |
| `CSINC` | 4 | 条件成立取 `rn`, 否则取 `源 + 1` (`_op_csinc`) | 条件码为第 4 个操作数 |
| `CSINV` | 4 | 条件成立取 `rn`, 否则取 `~源` (`_op_csinv`) | 条件码为第 4 个操作数 |
| `CSNEG` | 4 | 条件成立取 `rn`, 否则取 `-源` (`_op_csneg`) | 条件码为第 4 个操作数 |
| `SXTB` | 2 | 8 位符号扩展 (`_op_sxtb`) | — |
| `SXTH` | 2 | 16 位符号扩展 (`_op_sxth`) | — |
| `SXTW` | 2 | 32 位符号扩展 (`_op_sxtw`) | — |
| `UXTB` | 2 | 取低 8 位 (`_op_uxtb`) | — |
| `UXTH` | 2 | 取低 16 位 (`_op_uxth`) | — |
| `CLZ` | 2 | 前导零个数 (`_op_clz`) | 输入 0 时结果为 64 |
| `CLS` | 2 | 前导符号位个数 (`_op_cls`) | — |
| `RBIT` | 2 | 按位翻转 (`_op_rbit`) | 64 位完全反转 |
| `REV` | 2 | 按字节翻转 (`_op_rev`) | 小端 ↔ 大端 |

## FP 组 (10 条)

浮点指令使用**向量寄存器** `v0`–`v31` 的 0 号通道 (`write_scalar` / `read_scalar`)。
整数与浮点之间的搬运需要 `FCVT`, **不能**用 `mov v0, 1.5` (运行期会报
`unsupported operand type(s) for &=: 'float' and 'int'`)。

| 助记符 | 操作数 | 语义 | 影响标志 / 备注 |
| --- | --- | --- | --- |
| `FADD` | 3 | `vd = vn + vm` (`_op_fadd`) | 通道 0 |
| `FSUB` | 3 | `vd = vn - vm` (`_op_fsub`) | 通道 0 |
| `FMUL` | 3 | `vd = vn * vm` (`_op_fmul`) | 通道 0 |
| `FDIV` | 3 | `vd = vn / vm` (`_op_fdiv`) | 除零抛 `Float division by zero` |
| `FCMP` | 2 | 比较两浮点并写标志 (`_op_fcmp`) | `Z=(a==b)`, `N=(a<b)`, `C=(a>=b)`, `V=False` |
| `FCVT` | 2 | 浮点↔整数转换 (`_op_fcvt`) | 源是向量/通道则浮点→整数, 否则整数→浮点 |
| `FABS` | 2 | `vd = abs(vn)` (`_op_fabs`) | — |
| `FNEG` | 2 | `vd = -vn` (`_op_fneg`) | — |
| `LDRS` | 2 | `vd = mem_float[地址]` (`_op_ldrs`) | 读 32 位浮点 |
| `STRS` | 2 | `mem_float[地址] = vs` (`_op_strs`) | 写 32 位浮点 |

::: warning `FCVT` 的方向由源操作数类型决定
`fcvt x0, v1` 是浮点→整数 (`int(v1)`), `fcvt v0, x1` 是整数→浮点。两个方向的助记符和
操作数个数完全相同, 容易写反, 请按目标寄存器类型判断。
:::

## Vector 组 (6 条)

向量寄存器有 **4 个通道** (`Constants.VECTOR_LANES = 4`), 每个通道是 float64。
`VADD`/`VSUB`/`VMUL`/`VDIV` 对 4 个通道逐通道运算。

| 助记符 | 操作数 | 语义 | 影响标志 / 备注 |
| --- | --- | --- | --- |
| `VADD` | 3 | 4 通道逐通道相加 (`_op_vadd`) | 通道数由 `VECTOR_LANES` 决定 |
| `VSUB` | 3 | 逐通道相减 (`_op_vsub`) | — |
| `VMUL` | 3 | 逐通道相乘 (`_op_vmul`) | — |
| `VDIV` | 3 | 逐通道相除 (`_op_vdiv`) | 任一通道除零抛 `Vector division by zero` |
| `VLD1` | 2 | 从内存读 16 字节按 `<4f` 解包 (`_op_vld1`) | 单精度 float32 装入 4 个通道 |
| `VST1` | 2 | 把 4 个通道按 `<4f` 打包写 16 字节 (`_op_vst1`) | 单精度 float32 写回内存 |

::: warning `VLD1` 是 32 位浮点格式
`VLD1`/`VST1` 使用 `struct.unpack('<4f')`, 即 **4 个 float32**;
而 `FADD` 等标量浮点指令在寄存器内部按 float64 运算。混用两者时注意数据宽度差异。
:::

## RISC-V 组 (27 条)

| 助记符 | 操作数 | 语义 | 影响标志 / 备注 |
| --- | --- | --- | --- |
| `LB` | 2 | 加载 1 字节并符号扩展 (`_op_lb`) | 8 位 |
| `LH` | 2 | 加载 2 字节并符号扩展 (`_op_lh`) | 16 位 |
| `LW` | 2 | 加载 4 字节并符号扩展 (`_op_lw`) | 32 位 |
| `LD` | 2 | 加载 8 字节 (`_op_ld`) | 64 位, 不扩展 |
| `SB` | 2 | 存储低 8 位 (`_op_sb`) | 截断 |
| `SH` | 2 | 存储低 16 位 (`_op_sh`) | 截断 |
| `SW` | 2 | 存储低 32 位 (`_op_sw`) | 截断 |
| `SD` | 2 | 存储 64 位 (`_op_sd`) | — |
| `ADDI` | 3 | `rd = rs1 + 立即数` (`_op_addi`) | 不设标志 |
| `SLTI` | 3 | 有符号 `rs1 < 立即数` 则 `rd = 1`, 否则 0 (`_op_slti`) | — |
| `SLTIU` | 3 | 无符号比较 (`_op_sltiu`) | 两侧都按 64 位无符号解释 |
| `XORI` | 3 | `rd = rs1 ^ 立即数` (`_op_xori`) | — |
| `ORI` | 3 | `rd = rs1 \| 立即数` (`_op_ori`) | — |
| `ANDI` | 3 | `rd = rs1 & 立即数` (`_op_andi`) | — |
| `SLLI` | 3 | 逻辑左移 (`_op_slli`) | 移位量取低 6 位 |
| `SRLI` | 3 | 逻辑右移 (`_op_srli`) | — |
| `SRAI` | 3 | 算术右移 (`_op_srai`) | 高位补符号位 |
| `BEQ` | 3 | 两寄存器相等则跳转 (`_op_beq`) | 不看标志; 两个比较操作数必须是寄存器 |
| `BNE` | 3 | 不等则跳转 (`_op_bne`) | 不看标志; 比较操作数必须是寄存器 |
| `BLT` | 3 | 有符号小于则跳转 (`_op_blt`) | 不看标志; 比较操作数必须是寄存器 |
| `BGE` | 3 | 有符号大于等于则跳转 (`_op_bge`) | 不看标志; 比较操作数必须是寄存器 |
| `BLTU` | 3 | 无符号小于则跳转 (`_op_bltu`) | 不看标志; 比较操作数必须是寄存器 |
| `BGEU` | 3 | 无符号大于等于则跳转 (`_op_bgeu`) | 不看标志; 比较操作数必须是寄存器 |
| `JALR` | 变长 | `rd = pc; pc = rs1 + 偏移` (`_op_jalr`) | 单寄存器形式视为 `ret`; 无寄存器时用 `x31` |
| `JAL` | 2 | `rd = pc; pc = 目标` (`_op_jal`) | 首个操作数不是寄存器时用 `x31` |
| `LUI` | 2 | `rd = (立即数 & 0xFFFFF) << 12` (`_op_lui`) | 只保留低 20 位作为高位 |
| `AUIPC` | 2 | `rd = pc + ((立即数 & 0xFFFFF) << 12)` (`_op_auipc`) | 相对当前 `pc` |

::: danger RISC-V 分支与 PL 分支关键字的操作数
`BEQ`/`BNE`/`BLT`/`BGE`/`BLTU`/`BGEU` 都**需要 3 个操作数**:
`rs1, rs2, 目标`。它们直接比较两个**寄存器**, 不读标志位。
PL 关键字形式 (例如 `branch_not_equal`) 同样需要 3 个操作数, 只写标签会在
`--strict` 下报 `Argument count mismatch`, 非严格模式下运行期抛
`list index out of range`。
:::

## SYS 组 (1 条)

| 助记符 | 操作数 | 语义 | 影响标志 / 备注 |
| --- | --- | --- | --- |
| `SYS` | 变长 | 按立即数功能号调用宿主能力 (`_op_sys`) | 第一个操作数必须是立即数, 否则抛 `SYS requires an immediate call id` |

参数通过 `x0`–`x2` 传递, 返回值写入 `x0`; 浮点参数与结果以 float64 位模式存放在 `x0`/`x1`。
功能号定义在 `codecin/isa.py` 的 `Syscall` 枚举中, 共 80 个 (0–79)。
下表列出最常用的功能号:

| 功能号 | 名称 | 语义 |
| --- | --- | --- |
| 2 | `ABS` | `abs(x0)` |
| 15 | `TIME` | 返回 Unix 时间戳 |
| 16 | `STRLEN` | `strlen(x0)` |
| 21 | `PRINT_FLOAT` | 按浮点格式打印 `x0` 的位模式 |
| 22 | `ITOA` | 整数转字符串, 返回静态缓冲指针 |
| 23 | `FTOA` | 浮点转字符串, 返回静态缓冲指针 |
| 24 | `PRINT_STR` | 打印 `x0` 指向的字符串 (即 `OUT str` 的系统调用形式) |
| 27 | `ABORT` | 抛 `ExecutionError` (供 `assert` / 边界检查使用) |
| 35 | `ATOI` | 字符串转整数, 失败得 0 |

```asm
.text
main:
    mov x0, #msg
    sys 24                  ; PRINT_STR
    mov x0, 55
    sys 22                  ; ITOA -> x0 指向结果缓冲
    sys 24                  ; PRINT_STR
    out 10
    halt

.data
msg: ASCIZ "Sum 1..10 = "
```

```text
Sum 1..10 = 55
```

::: tip 完整功能号列表
`Syscall` 枚举覆盖数学函数、字符串处理、内存分配、音频播放、2D 画布、文件系统、进程执行、
环境变量与 Termux API。完整列表请直接查阅 `codecin/isa.py` 的 `Syscall` 类定义。
:::

## 最常用指令详解

同一条逻辑 (求和 1..10) 可以用不同分组的指令写出, 三者输出都是 `55`:

::: tabs

== ARM64 风格

```asm
.equ LIMIT, 10

.text
main:
    mov x0, 0
    mov x1, 1
loop:
    add x0, x1
    inc x1
    cmp x1, LIMIT + 1
    b.lt loop
    out x0
    out 10
    halt
```

== RISC-V 风格

```asm
.equ LIMIT, 10

.text
main:
    mov x0, 0
    mov x1, 1
    mov x2, LIMIT
    addi x2, x2, 1          ; x2 = LIMIT + 1
loop:
    add x0, x1
    addi x1, x1, 1
    blt x1, x2, loop        ; BLT 的两个比较操作数都必须是寄存器
    out x0
    out 10
    halt
```

== PL 关键字风格

```asm
.equ LIMIT, 10

.text
main:
    set x0, 0
    set x1, 1
loop:
    add x0, x1
    increment x1
    compare x1, LIMIT + 1
    b.lt loop
    output x0
    output 10
    stop
```

:::

### `MOV` —— 传送与取地址

两个操作数: 目标寄存器 + 源。源可以是寄存器、立即数、标签 (解析为地址) 或内存操作数。

```asm
.equ N, 10

.text
main:
    mov x0, 5               ; 立即数
    mov x1, x0              ; 寄存器
    mov x2, #arr            ; 数据标签地址
    mov x3, N               ; .equ 常量
    out x0
    out 10
    out x2
    halt

.data
arr: .dq 42
```

```text
5
0
```

### 算术: `ADD` / `SUB` / `MUL` / `DIV` / `ADDI`

Base 组的 `ADD`/`SUB`/`MUL`/`DIV` 都是**两操作数原地运算**: `rd = rd op 源`。
需要"两寄存器相加写入第三个寄存器"时, 先 `mov` 再用原地运算, 或者用 `ADDI` (寄存器 + 立即数)。

```asm
.text
main:
    mov x0, 12
    mov x1, 5
    add x0, x1              ; x0 = 12 + 5
    out x0
    out 10
    mov x2, 20
    sub x2, x1              ; x2 = 20 - 5
    out x2
    out 10
    mov x3, 6
    mul x3, x1              ; x3 = 6 * 5
    out x3
    out 10
    mov x4, 35
    div x4, x1              ; x4 = 35 / 5
    out x4
    out 10
    addi x5, x1, 100        ; x5 = x1 + 100 (三操作数, 不破坏 x1)
    out x5
    halt
```

```text
17
15
30
7
105
```

### 逻辑与移位: `AND` / `OR` / `XOR` / `SHL` / `SHR`

```asm
.text
main:
    mov x0, 0xFF00
    mov x1, 0x0FF0
    and x0, x1              ; x0 = 0x0F00 = 3840
    out x0
    out 10
    mov x2, 0x00FF
    or x2, x1               ; x2 = 0x0FFF = 4095
    out x2
    out 10
    mov x3, 0x00FF
    xor x3, x1              ; x3 = 0x0F0F = 3855
    out x3
    out 10
    mov x4, 1
    shl x4, 10              ; x4 = 1024
    out x4
    out 10
    mov x5, 256
    shr x5, 4               ; x5 = 16
    out x5
    halt
```

```text
3840
4095
3855
1024
16
```

### `CMP` 与分支族: `JZ` / `JNZ` / `JE` / `JL` / `JG`

`CMP` 计算 `a - b` 并设置 `N`/`Z`/`C`/`V`, 不保存结果。之后用条件跳转分流:

| 判断 | 指令 | 依据 |
| --- | --- | --- |
| 相等 | `JE` / `B.EQ` | `Z=1` |
| 不等 | `JNZ` / `B.NE` | `Z=0` |
| 有符号小于 | `JL` / `B.LT` | `N!=V` |
| 有符号大于 | `JG` / `B.GT` | `Z=0 且 N=V` |
| 有符号大于等于 | `B.GE` | `N=V` |
| 有符号小于等于 | `B.LE` | `Z=1 或 N!=V` |
| 无符号大于 | `B.HI` | `C=1 且 Z=0` |
| 无符号小于等于 | `B.LS` | `C=0 或 Z=1` |
| 结果为零 | `JZ` / `B.EQ` | `Z=1` |
| 结果非零 | `JNZ` / `B.NE` | `Z=0` |
| 结果为正 | `B.PL` | `N=0` |
| 结果为负 | `B.MI` | `N=1` |

```asm
.text
main:
    mov x1, 3
    mov x2, 7
    cmp x1, x2
    jl .Lless               ; 有符号 3 < 7
    mov x0, 0
    b .Ldone
.Lless:
    mov x0, 1
.Ldone:
    out x0
    out 10

    mov x3, 7
    mov x4, 7
    cmp x3, x4
    je .Lequal              ; 相等
    mov x0, 0
    b .Lend
.Lequal:
    mov x0, 1
.Lend:
    out x0
    halt
```

```text
1
1
```

实际标志位取值 (由 `_set_flags_sub` 计算):

| `CMP a, b` | `N` | `Z` | `C` | `V` |
| --- | --- | --- | --- | --- |
| `CMP 5, 5` | 0 | 1 | 1 | 0 |
| `CMP 3, 7` | 1 | 0 | 0 | 0 |
| `CMP 7, 3` | 0 | 0 | 1 | 0 |

::: tip `--strict` 下 `INC` / `DEC` 不设标志
`INC` / `DEC` 只做加减, **不更新标志位**。`inc x1` 之后紧跟 `jz` 会读到上一次的标志,
必须用 `cmp` 重新设置。
:::

### `JMP` 与 `B` / `BR`

- `JMP 目标`: 无条件跳转到标签地址。
- `B 目标`: 等价的无条件跳转; `B.<cond> 目标` 为条件跳转 (单操作数)。
- `BR rn`: 寄存器间接跳转, 目标地址来自寄存器。

```asm
.text
main:
    mov x0, 0
    mov x1, 1
.Lloop:
    add x0, x1
    inc x1
    cmp x1, 11
    b.lt .Lloop             ; 单操作数条件跳转
    mov x2, #target
    br x2                   ; 寄存器间接跳转
    out 0                   ; 不会执行
    halt
target:
    out x0
    out 10
    halt
```

```text
55
```

### `PUSH` / `POP` / `CALL` / `RET` —— 子程序调用

`CALL` 把**当前 `pc`** 压栈后跳转; `RET` 从栈顶弹出返回地址。`PUSH`/`POP` 用于保护寄存器,
每次操作 **8 字节** (`Constants.STACK_SLOT`)。栈从内存高地址向低地址生长, 栈指针与堆之间保留
4096 字节隔离区, 越界抛 `Stack overflow (collides with heap)`。

```asm
.text
main:
    mov x0, 5
    call fact
    out x0
    out 10
    halt

; 递归阶乘: 输入 x0 = n, 输出 x0 = n!
fact:
    cmp x0, 1
    jg .Lrecurse
    mov x0, 1
    ret

.Lrecurse:
    push x0                 ; 保护 n
    dec x0                  ; n - 1
    call fact               ; x0 = (n-1)!
    pop x1                  ; 恢复 n
    mul x0, x1              ; n * (n-1)!
    ret
```

```text
120
```

子程序返回多个值时, 用栈槽位传递 (返回值放入 `x0`/`x1`, 被破坏的寄存器先 `push` 再 `pop`):

```asm
.text
main:
    mov x0, 38              ; 被除数
    mov x1, 7               ; 除数
    call divmod
    out x0                  ; 商
    out 10
    out x1                  ; 余数
    out 10
    halt

; 输入: x0 = 被除数, x1 = 除数
; 输出: x0 = 商, x1 = 余数
divmod:
    push x2
    push x3
    mov x3, x0              ; x3 = 被除数
    mov x2, x0
    div x2, x1              ; x2 = 商
    mov x0, x2              ; x0 = 商 (先保存)
    mul x2, x1              ; x2 = 商 * 除数
    sub x3, x2              ; x3 = 余数
    mov x1, x3              ; x1 = 余数
    pop x3
    pop x2
    ret
```

```text
5
3
```

::: danger `push` / `pop` 的配对顺序
`push A` 之后 `push B`, 必须 `pop B` 再 `pop A`。顺序写反会把两个寄存器的值互换。
:::

### 数组遍历

`lsl` 把下标换算成字节偏移, 再用 `ld`/`sd` 配合 `[reg]` 间接寻址访问元素。
本汇编器**不支持** `[基址, 变址寄存器]` 形式, 变址必须先算进地址寄存器。

```asm
.equ COUNT, 5

.text
main:
    mov x0, #arr            ; 数组基址
    mov x1, 0               ; 下标
    mov x2, 0               ; 累加和
.Lnext:
    mov x3, x1
    lsl x3, x3, 3           ; x3 = 下标 * 8 (每个元素 8 字节)
    add x3, x0              ; x3 = 元素地址
    ld x4, [x3]             ; 取元素
    add x2, x4              ; 累加
    inc x1
    cmp x1, COUNT
    jl .Lnext
    out x2
    out 10
    halt

.data
arr: .dq 10, 20, 30, 40, 50
```

```text
150
```

### 常数偏移访存: `LD` / `SD` / `LW` / `SW` / `LB` / `SB`

宽度与符号扩展是选择指令的唯一依据:

| 指令 | 宽度 | 符号扩展 | 典型用途 |
| --- | --- | --- | --- |
| `LB` | 8 位 | 有 | 读 `int8` |
| `LH` | 16 位 | 有 | 读 `int16` |
| `LW` | 32 位 | 有 | 读 `int32` |
| `LD` | 64 位 | 否 | 读 `int64` / 指针 |
| `SB` / `SH` / `SW` / `SD` | 8 / 16 / 32 / 64 位 | — | 写对应宽度, 高位截断 |

```asm
.text
main:
    mov x0, #buf
    mov x1, 0x0102030405060708
    sd x1, [x0]             ; 写入 8 字节
    lb x2, [x0]             ; 读最低字节
    out x2
    out 10
    lb x2, [x0, 7]          ; 读最高字节
    out x2
    out 10
    lw x3, [x0]             ; 读低 32 位
    out x3
    halt

.data
buf: .dq 0, 0
```

```text
8
1
84281096
```

### `LDR` / `STR` 与 `LOAD` / `STORE`

这四条指令都按 **32 位** 读写内存, 区别只是分组与命名:

- `LDR` / `STR` (ARM64 组): `rd = mem32[地址]`、`mem32[地址] = rs`。
- `LOAD` / `STORE` (Base 组): 语义完全相同。

需要 64 位读写时用 `LD` / `SD`, 需要更窄宽度时用 `LB` / `LH` / `LW`。

### `CBZ` / `CBNZ` —— 与零比较的分支

这两条指令**不看标志位**, 直接检查寄存器是否为零, 常用于循环终止与空指针判定:

```asm
.text
main:
    mov x0, 5
    mov x1, 0
.Lloop:
    add x1, x0
    dec x0
    cbnz x0, .Lloop         ; x0 != 0 继续
    out x1
    out 10

    mov x2, 0
    cbz x2, .Liszero        ; x2 == 0 跳转
    out 0
    halt
.Liszero:
    out 1
    halt
```

```text
15
1
```

### `ADDI` —— 寄存器加立即数

`ADDI rd, rs1, 立即数` 是 Base 组 `ADD` 的**三操作数**形式, 不会破坏源寄存器:

```asm
.equ BASE, 100

.text
main:
    mov x0, 7
    addi x1, x0, BASE       ; x1 = 107, x0 不变
    addi x2, x1, -7         ; 支持负立即数, x2 = 100
    out x1
    out 10
    out x2
    out 10
    out x0
    halt
```

```text
107
100
7
```

### `SYS` —— 宿主系统调用

`SYS` 的第一个操作数必须是立即数功能号, 参数放在 `x0`–`x2`, 返回值写入 `x0`。

```asm
.text
main:
    mov x0, -42
    sys 2                   ; ABS -> x0 = 42
    out x0
    out 10
    mov x0, #text
    sys 16                  ; STRLEN -> x0 = 长度
    out x0
    out 10
    halt

.data
text: ASCIZ "hello"
```

```text
42
5
```

### `HALT` 与 `IN` / `OUT`

- `HALT` 是唯一的正常停机指令, 处理器返回 `False` 终止执行循环。
- `IN rd` 读入一个整数; 输入不可解析或遇到 EOF 时得到 0; `--no-io` 下不生效。
- `OUT 源` 输出: 整数值 10 输出为换行, 其他整数按十进制字符串输出, 浮点与向量按浮点文本输出。

```asm
.text
main:
    out 65                  ; 'A' 的码点按十进制输出
    out 10                  ; 换行
    out 10                  ; 再来一个换行
    out 66
    out 67
    halt
```

```text
65

6667
```

::: warning `OUT` 打印的是十进制数值, 不是字符
`OUT 65` 输出字符串 `65` 而不是字母 `A`。要输出字符串请把字符串地址放入 `x0` 后调用
`sys 24` (PRINT_STR)。
:::

## 相关页面

- [汇编总览](/asm/)
- [汇编语法参考](/asm/syntax)
- [指令集编码表](/reference/isa)
- [寄存器与内存模型](/reference/registers-memory)
- [交互式调试器](/tools/debugger)
- [扩展指令 / 系统调用](/dev/extend)
