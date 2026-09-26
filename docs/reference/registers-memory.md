---
description: Code CIN 的寄存器与内存模型: X0–X31/XZR、V0–V31 向量寄存器、SP/PC/NZCV、别名、内存布局与默认值、栈帧与调用约定、小端字节序、内存保护与 MMU 分页。
---

# 寄存器与内存模型

Code CIN 的 UCPU 是一台 **64 位、小端、load/store 风格**的模拟机: 运算只在寄存器之间
发生, 内存通过显式的加载/存储指令访问。本页描述寄存器文件、内存布局、栈帧与调用约定、
保护与分页, 以及 CIN 类型到 64 位槽的映射。

权威来源: `codecin/registers.py`、`codecin/isa.py` (`Constants`)、`codecin/memory.py`、
`codecin/cpu.py`、`codecin/cin.py`。

## 通用寄存器 X0–X31 与 XZR

| 名称 | 索引 | 说明 |
|------|------|------|
| `X0`–`X30` | 0–30 | 31 个可读写通用寄存器, 每个 64 位 |
| `XZR` | 31 | 零寄存器: **读恒为 0, 写被丢弃** |
| `SP` | 32 (伪寄存器) | 栈指针; 不在通用寄存器文件里, 由 `CPU.sp` 单独保存 |

`RegisterFile` 的语义 (见 `codecin/registers.py`):

- `read(31)` 直接返回 `0`, `write(31, v)` 是空操作 —— `XZR` 因此天然只读;
- `write()` 一律 `& 0xFFFFFFFFFFFFFFFF`, 寄存器里永远是模 2⁶⁴ 的无符号位模式;
- 索引越界抛 `ExecutionError("Invalid register index: X<n>")`;
- `X0`–`X30` 与 `SP` 共 33 个"槽" (`Constants.NUM_REGS_TOTAL = 33`), 原生 VM 的
  `regs` 数组也正好是 `[33]uint64`, 索引 32 即 `SP`。

```python
from codecin import CPU, Config

cpu = CPU(Config(interactive_mode=False, log_level='ERROR'))
cpu.regs.write(0, 0xDEADBEEF)     # X0
cpu.regs.write(31, 12345)         # 无效: XZR 只读
print(cpu.regs.read(0), cpu.regs.read(31))   # 3735928559 0
print(cpu.regs.get_all()[:4])                # X0..X3
```

### 寄存器别名

汇编器 (`codecin/assembler.py`) 接受这些写法, 它们都解析成同一个寄存器编号:

| 汇编写法 | 等价寄存器 | 约定含义 |
|----------|-----------|----------|
| `X0`–`X31`, 以及 `R0`–`R31`、`W0`–`W31` | 同号通用寄存器 | 整机是 64 位槽模型, `W`/`R` 前缀不改变宽度 |
| `XZR` | `X31` | 零寄存器 |
| `SP` (大小写不敏感) | 伪寄存器 32 | 栈指针 |
| `FP` | `X29` | 帧指针 (CIN 编译器用它建立栈帧) |
| `LR` | `X30` | 链接寄存器 (约定, 由 `BL`/`CALL` 压栈语义承担) |

`V0`–`V31` 另有 `V<n>.<lane>` 写法, 见下节。条件码写在指令后缀里 (如 `B.NE`), 或作为
单独操作数, 完整列表见 `Constants.CONDITIONS`。

## 向量寄存器 V0–V31

| 属性 | 值 |
|------|-----|
| 数量 | 32 (`Constants.NUM_VECTOR_REGISTERS`) |
| lane 数 | 4 (`Constants.VECTOR_LANES`) |
| lane 类型 | 64 位浮点 (`float64`) |
| `V31` | 与 `XZR` 一致: 读全 `0.0`, 写丢弃 |

`VectorRegisterFile` 提供: `read_vector(n)` / `write_vector(n, values)` (要求正好 4 个值,
否则 `ExecutionError`)、`read_scalar(n, lane=0)` / `write_scalar(n, value, lane=0)`、
`get_all()` / `set_all()` / `reset()`。

向量指令 `VADD` / `VSUB` / `VMUL` / `VDIV` 按 lane 逐元素运算;
`VLD1` / `VST1` 一次搬运 16 字节, 按 `<4f` (4 个 32 位单精度) 打包解释 —— 注意这与
内部 lane 的 `float64` 表示不同, 是内存侧的紧凑格式。

::: warning 向量指令不在原生 VM 的支持列表里
`codecin/native/engine/vm.go` 的 `opcodeSupported()` 只覆盖整数/内存/控制流/SYS 子集,
向量操作数会直接返回 `StatusUnsupported`, Python 侧自动回退解释执行。因此含向量指令的
程序仍然正确, 只是不会走原生加速。
:::

## SP 与 PC

| 名称 | 初值 | 说明 |
|------|------|------|
| `PC` | `0`, CIN 程序为 `0` (bootstrap), 汇编程序为 `labels['main']` | `codecin/cpu.py: CPU.pc` |
| `SP` | `(mem_size - Constants.STACK_SLOT) & ~0x7` | 默认 `65536 - 8 = 0xFFF8` |
| 堆指针 | `mem_size // 2` | 默认 `0x8000`; `MALLOC` / 字符串操作从这里向上分配 |

`SP` 满递减: `PUSH` 先 `SP -= 8` 再写 `[SP]`, `POP` 先读 `[SP]` 再 `SP += 8`。
栈槽固定 8 字节 (`Constants.STACK_SLOT`)。

关于 `PC` 的语义 (解释器与原生 VM 严格一致): **取指后先 `PC += 1` 再执行**。所以
`CALL` / `BL` 压入的是已经自增过的返回地址 (`codecin/cpu.py: _op_call`)。

## NZCV 标志

四个 1 位条件标志, 保存在 `CPU.pstate` 字典里 (`{'N','Z','C','V'}`, 初值全 `False`)。

| 标志 | 含义 | 写入时机 |
|------|------|----------|
| `N` | 结果的最高位 (bit 63), 即有符号为负 | `ADDS` / `SUBS` / `ADDC` / `SUBC` / `CMP` |
| `Z` | 结果为零 | 同上 |
| `C` | **无借位** (减法) / 进位 (加法) | 同上 |
| `V` | 有符号溢出 | 同上 |

具体规则 (`_set_flags_add` / `_set_flags_sub`, Go 侧 `setFlagsSub` 一致):

- 减法: `C = (a >= b)` (无符号比较, 表示"没有借位"); `V = 有符号差超出 int64`.
- 加法: `C = (a + b) > 0xFFFFFFFFFFFFFFFF`; `V = 有符号和超出 int64`.
- `FCMP` 会写 `Z` / `N` / `C` 并强制 `V = False`。

条件码与标志的对应关系 (`CPU._condition`, 与 Go 侧 `condition()` 完全一致):

| 条件 | 语义 | 条件 | 语义 |
|------|------|------|------|
| `EQ` | `Z` | `NE` | `!Z` |
| `CS` | `C` | `CC` | `!C` |
| `MI` | `N` | `PL` | `!N` |
| `VS` | `V` | `VC` | `!V` |
| `HI` | `C && !Z` | `LS` | `!C \|\| Z` |
| `GE` | `N == V` | `LT` | `N != V` |
| `GT` | `!Z && N == V` | `LE` | `Z \|\| N != V` |
| `AL` | 恒真 | `NV` | 恒假 |

```python
from codecin import CPU, Config
from codecin.errors import ExecutionError

cpu = CPU(Config(use_native=False, interactive_mode=False, log_level='ERROR'))
cpu.instructions = [
    ('MOV',  [('reg', 0), ('imm', 5)]),
    ('CMP',  [('reg', 0), ('imm', 5)]),
    ('HALT', []),
]
cpu.run()
print(cpu.pstate)                  # {'N': False, 'Z': True, 'C': True, 'V': False}
```

## 内存布局

`FastMemory` 是一块**平坦字节数组** (`bytearray`), 大小由 `Config.mem_size` 决定,
默认 `64 * 1024 = 65536` 字节 (`Constants.DEFAULT_MEM_SIZE`)。没有独立的段寄存器:
代码、数据、栈、堆共用同一地址空间, 靠约定划分。

| 区域 | 起始地址 (默认 64 KiB) | 增长方向 | 说明 |
|------|------------------------|----------|------|
| 代码 / 数据段 | `0x0000` | 向上 | 汇编器从 0 开始摆放代码, 数据段紧随其后; CIN 的数据段由编译器排版 |
| 堆 | `0x8000` (`mem_size // 2`) | 向上 | `MALLOC` 与字符串内建在此分配; `heap_ptr` 跟踪高水位 |
| SYS 静态缓冲 | `heap_ptr + 2048 + idx * 64` | 固定 | 8 个 64 字节轮转缓冲, 供 `ITOA` / `FTOA` / `BOOL_STR` 返回字符串 |
| 栈 | `0xFFF8` (`mem_size - 8`) | 向下 | `SP` 初值; 每个 qword 槽 8 字节 |

栈溢出判定: `SP < heap_ptr + 4096` 时 `PUSH` 抛
`ExecutionError("Stack overflow (collides with heap)")` —— 即栈与堆之间强制保留
4 KiB 隔离带。栈下溢 (`POP` 时 `SP >= len(memory) - 8`) 抛
`ExecutionError("Stack underflow")`。

```text
0x0000  ┌──────────────────────┐
        │ 代码 / 数据段         │  汇编器与编译器从这里向上排版
        ├──────────────────────┤
0x8000  │ 堆 (heap_ptr →)      │  MALLOC / 字符串分配, 向上
        │  ↕ 4 KiB 隔离带       │  PUSH 越界即 Stack overflow
0x9000+ │ SYS 静态缓冲 (8×64B)  │  ITOA / FTOA / BOOL_STR 返回值
        ├──────────────────────┤
        │ 未使用                │
        ├──────────────────────┤
0xFFF8  │ 栈 (SP ←, 向下增长)   │  8 字节/qword 槽
0xFFFF  └──────────────────────┘
```

### 字节序与宽度

整台机器是**小端**。所有多字节读写都用小端解码 (`struct.unpack_from('<H' / '<I' /
'<Q' / '<f' / '<d')`), 原生 VM 侧同样使用 `binary.LittleEndian`。

| 方法 | 宽度 | 掩码 |
|------|------|------|
| `read_byte` / `write_byte` | 1 字节 | `0xFF` |
| `read_word` / `write_word` | 2 字节 (小端) | `0xFFFF` |
| `read_dword` / `write_dword` | 4 字节 (小端) | `0xFFFFFFFF` —— `LOAD`/`STORE`/`LDR`/`STR` 用它 |
| `read_qword` / `write_qword` | 8 字节 (小端) | `0xFFFFFFFFFFFFFFFF` —— `LD`/`SD`/`LDP`/`STP`/栈用它 |
| `read_float` / `write_float` | 4 字节 IEEE-754 单精度 | — |
| `read_double` / `write_double` | 8 字节 IEEE-754 双精度 | — |
| `read_block` / `write_block` | N 字节 | 支持 MMU 跨页分块搬运 |
| `read_string(addr, max_len=4096)` / `write_string(addr, text)` | UTF-8 + NUL | 字符串以 `\0` 结尾 |

::: warning 定宽多字节访问不得跨页
`_rw_phys()` 只对**首字节所在**地址做一次边界与保护检查。在开启 MMU 时, 一次
4/8 字节访问不应跨越页边界, 否则检查不完整。块读写 (`read_block` / `write_block`)
会逐页翻译, 是安全的跨页路径。
:::

## 内存保护与越界

`FastMemory` 有三层保护, 全部通过 `MemoryAccessError` 报错:

| 机制 | 接口 | 说明 |
|------|------|------|
| 边界检查 | `_check_bounds(addr, size)` | `0 <= addr <= size - width`, 否则 `Address 0x... out of bounds` |
| 逐字节权限 | `set_protection(addr, perms, size=1)`、`check_access(addr, access)` | `perms` 是 `'r'`/`'w'`/`'x'` 的子串, 默认 `'rwx'` |
| 块范围权限 | `_check_protection_range(addr, size, access)` | `read_block` / `write_block` 入口统一检查, 浮点/块读写无法绕过 |

默认权限是 `'rwx'`, 即不设保护时一切可访问。权限表是稀疏字典, 只记录显式设置过的字节。

```python
from codecin.memory import FastMemory
from codecin.errors import MemoryAccessError

mem = FastMemory(256)
mem.set_protection(0x10, 'r', size=8)      # 把 [0x10, 0x18) 设为只读
try:
    mem.write_qword(0x10, 1)
except MemoryAccessError as e:
    print('保护生效:', e)

try:
    mem.read_qword(0xF8)                    # 8 字节越界
except MemoryAccessError as e:
    print('越界:', e)
```

## MMU 与分页概要

`Config(mmu=True)` 时, `CPU` 会给 `FastMemory` 挂上一个 `Mmu` (`codecin/memory.py`)。

| 属性 | 值 |
|------|-----|
| 页大小 | 4 KiB (`PAGE_BITS = 12`, `PAGE_SIZE = 4096`) |
| 页表结构 | 扁平字典 `vpn -> (ppn, perms)`, 无多级页表 |
| 默认映射 | 懒 identity: 未配置且未 `unmap` 的页按 `vpn == ppn`、`'rwx'` 直接放行 |
| 黑名单 | 显式 `unmap` 过的 `vpn` 不会被 identity 回填 |

| 方法 | 说明 |
|------|------|
| `map(vpn, ppn=None, perms='rwx')` | 建立映射; `ppn` 省略即 identity |
| `map_page(vaddr, paddr, perms='rwx')` | 按地址映射 (内部右移 12 位取页号) |
| `unmap(vaddr) -> bool` | 解除映射, 之后再访问触发 `PageFaultError` |
| `protect(vaddr, perms)` | 改权限; 对已 `unmap` 的页抛 `MemoryAccessError` |
| `is_mapped(vaddr) -> bool` | 是否可访问 |
| `translate(vaddr, access) -> int` | 翻译成物理地址; 权限不符抛 `MemoryAccessError`, 未映射或物理越界抛 `PageFaultError` |
| `reset(mem_size=None)` | 清空页表与黑名单 |

CROM v3 可以把页表与黑名单序列化到镜像尾部 (flags bit1), 由
`load_crom(..., enable_mmu=True)` 恢复; 序列化细节见
[二进制格式](/runtime/formats)。

```python
from codecin import CPU, Config
from codecin.errors import PageFaultError, MemoryAccessError
from codecin.memory import Mmu

cpu = CPU(Config(mmu=True, use_native=False,
                 interactive_mode=False, log_level='ERROR'))
mmu = cpu.memory.mmu
mmu.protect(0x2000, 'r')            # 只读页
try:
    cpu.memory.write_qword(0x2000, 1)
except MemoryAccessError as e:
    print('页保护:', e)
mmu.unmap(0x3000)
try:
    cpu.memory.read_byte(0x3000)
except PageFaultError as e:
    print('缺页:', e)
```

## 栈帧与调用约定

CIN 编译器 (`codecin/cin.py`) 生成的调用约定:

| 约定 | 规则 |
|------|------|
| 参数传递 | 调用方从左到右求值并 `PUSH`, 被调方通过帧指针访问 |
| 返回地址 | `CALL` / `BL` 压入已自增的 `PC`; `RET` 弹出并写回 `PC` |
| 帧指针 | `FP = X29`。prologue 先 `PUSH X29`, 再 `MOV X29, SP` |
| 参数位置 | `fp+16` 是**第一个** (最左) 参数, 其后每 8 字节一个; 参数区在 `fp+8` 的返回地址之上 |
| 返回地址 / 保存的 FP | 位于 `fp+8` (返回地址) 与 `fp+0` (调用方 FP) |
| 局部变量 | 负偏移: `fp - (8 + off)`; 定长数组块基址 `fp - (off + slots*8)` |
| 返回值 | **经 `X0` 返回**; 被调方 epilogue 明确不得破坏 `X0` |
| 调用方清理 | 被调方返回后, 调用方 `SP += nargs * 8` 回收参数 (不得用 `X0`, 它持有返回值) |

帧布局 (由 `cin.py` 的注释与 `_epilogue` 保证):

```text
高地址
  ┌──────────────────────┐
  │ 参数 n-1 … 参数 0     │  fp+16 .. fp+16+(n-1)*8   (参数 0 在最左/最低)
  ├──────────────────────┤
  │ 返回地址 (调用方 PC)  │  fp+8
  ├──────────────────────┤
  │ 保存的调用方 FP       │  fp+0   ← X29 (FP)
  ├──────────────────────┤
  │ 局部变量 / 数组       │  fp-8, fp-16, …           (负偏移)
  └──────────────────────┘
低地址                          ← SP (移动后)
```

epilogue 序列 (`cin.py: _epilogue`): `SP = FP` → `POP X29` → `RET`。返回值已经放在
`X0`, 整个过程不触碰它。

原生 VM 与解释器对栈的语义完全一致: 8 字节 qword 槽、满递减、`SP` 初值由调用方传入、
`CALL`/`BL` 压入自增后的 `PC`。

## CIN 类型 ↔ 64 位槽 ↔ 指令宽度

CIN 是 **64 位槽模型**: 每个标量占一个 qword 槽, 与 `int` / `char` / `short` / `long` /
`unsigned` 等类型别名同宽 (宽度别名只影响语义检查, 不改变存储)。

| CIN 类型 | 占用槽数 | 内存表示 | 典型读写指令 |
|----------|:--------:|----------|--------------|
| `int` / `char` / `short` / `long` / `unsigned` | 1 | 64 位有符号整数位模式 | `LD` / `SD` (8B), `LW`/`SW` (4B) |
| `bool` | 1 | `0` 或 `1` | `LD` / `SD` |
| `float` | 1 | IEEE-754 `float64` 位模式 (运算经 `SYS`) | `LD` / `SD`, `LDRS`/`STRS` (32 位单精度) |
| `string` | 1 | 指向 NUL 结尾 UTF-8 的内存指针 | `LD` / `SD` |
| `struct` 变量 | 1 | 指向堆对象的指针 (浅拷贝语义, 不回收) | `LD` / `SD` |
| `T[n]` 定长数组 | `n * elem_slots` | 就地存放 (数据段 / 栈帧) | `LD` / `SD` 逐元素 |
| `T[]` / `T[][]` 参数数组 | 1 | 指向元素/行指针数组的指针 | `LD` / `SD` |

| 机器层宽度 | 指令 | 用例 |
|------------|------|------|
| 1 字节 | `LB` / `SB`、`read_byte` / `write_byte` | 字符串单字节访问 (`s[i]`) |
| 2 字节 | `LH` / `SH`、`UXTH` / `SXTH` | 半字 |
| 4 字节 | `LOAD` / `STORE`、`LDR` / `STR`、`LW` / `SW` | 32 位数据、`LDRS`/`STRS` 单精度浮点 |
| 8 字节 | `LD` / `SD`、`LDP` / `STP`、`PUSH` / `POP` | 64 位槽、栈槽、`float64` 位模式 |

::: info 为什么浮点是"位模式"
`SYS` 调用 (`SQRT` / `FADD` / `SIN` …) 通过 `X0`/`X1` 传递 `float64` 的**位模式**, 结果
也以位模式写回 `X0` (见 `isa.py: Syscall` 的说明与 `cpu.py: _op_sys` 的
`_f_to_bits` / `_bits_to_f`)。这样浮点运算不需要独立寄存器堆, 也不会与整数寄存器带宽冲突。
:::

## 相关页面

- [指令集编码表](/reference/isa) — 全部 112 条指令的助记符与编码
- [指令语义参考](/asm/instructions) — 逐条指令的行为
- [汇编语法参考](/asm/syntax) — 寄存器/内存操作数的书写方式
- [Python 嵌入 API](/reference/python-api) — 从 Python 直接读写寄存器与内存
- [内存与缓存](/tools/memory-cache) — `--profile` 下的缓存与内存统计
