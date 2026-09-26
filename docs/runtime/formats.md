---
description: Code CIN 的两种二进制格式：.bin（CPUSA 容器 + UCBC 字节码）与 .crom v3 内存镜像的字段、命令、兼容性与限制。
---

# 二进制格式

Code CIN 有两条不同的二进制产物线，用途完全不同：

| 格式 | 是什么 | 谁产生 | 能否直接运行 |
| --- | --- | --- | --- |
| `.bin` | **程序**：CPUSA 容器 + UCBC 字节码 + 初始内存镜像 | `--compile` / `--compile-only` | 可以：`python cpu.py prog.bin` |
| `.crom` | **内存镜像**：某个时刻的整块内存快照（v3 带 CRC32，可 zlib 压缩） | `--save` | **不可以**：它要配合 `.pl`/`.asm` 用 `--crom` 载入 |

两者魔数、版本号、头部布局都不一样，**互不通用**：把 `.crom` 当程序喂进去会失败，反之亦然。本页给出两种格式的准确字段、命令与限制。

## `.bin`：CPUSA 容器

`codecin/crom.py:save_bin()` 写出的容器布局（全部小端）：

| 偏移 | 大小 | 字段 | 说明 |
| --- | --- | --- | --- |
| 0x00 | 5 | Magic | `'CPUSA'`（`Constants.MAGIC_NUMBER`） |
| 0x05 | 1 | Version | `0x02`（`Constants.BIN_VERSION`） |
| 0x06 | 4 | Memory Size | 初始内存镜像字节数（u32） |
| 0x0A | 4 | Entry | 入口 PC（u32） |
| 0x0E | 8 | SP | 初始栈指针（u64） |
| 0x16 | 4 | Bytecode Length | 紧随其后的 UCBC 段长度（u32） |
| 0x1A | 8 | Reserved | 保留，写 0 |
| 0x22 | N | Memory Image | 初始内存内容（Memory Size 字节） |
| — | M | Bytecode | UCBC 字节码段（Bytecode Length 字节） |

固定头部 34 字节（`0x22`），因此字节码段起点 = `34 + mem_size`。载入时 `load_bin()` 的行为是：

1. 校验 magic 与 `BIN_VERSION`，不符分别报 `Invalid binary file (bad magic)` 与 `Unsupported binary version: <x>`；
2. 若镜像长度大于当前内存，**扩容内存**并把 `config.mem_size` 同步为镜像长度；
3. 把镜像写回物理地址 0；
4. 解码 UCBC 段，设置 `pc = Entry`、`sp = SP`。

### UCBC 字节码段的编码

头部 13 字节：

```text
magic[4] = 'UCBC' | version u8 (0x01) | entry u32 | instr_count u32
```

随后是 `instr_count` 条指令，每条两字节前缀加若干操作数：

```text
指令:   opcode u8 | argc u8
操作数: kind u8 | value i64 | extra i64        （每个操作数 17 字节, 小端）
```

- `opcode` 是 `codecin/isa.py` 中 `Opcode` 枚举的数值（`MOV=0`、`LOAD=1` … `SYS=111`）；
- `argc` 是操作数个数，必须与指令语义匹配（Go VM 会按 `argCounts` 表校验，声明个数不对直接报错）；
- `i64` 字段以二进制补码写入，Python 侧用 `_i64()` 把无符号掩码值还原为有符号数。

操作数 `kind` 取值与含义：

| kind | 名称 | `value` | `extra` |
| --- | --- | --- | --- |
| 0 | `reg` | 寄存器号 | 未用 |
| 1 | `imm` | 立即数（64 位） | 未用 |
| 2 | `vec` | 向量寄存器号 | 未用 |
| 3 | `veclane` | 向量寄存器号 | lane 下标 |
| 4 | `mem` | base 寄存器号（`-1` 表示无基址） | 偏移（64 位） |
| 5 | `cond` | 条件码（`Cond` 枚举，0–15） | 未用 |
| 6 | `float` | IEEE-754 double 的位模式 | 未用 |
| 7 | `str` | 数据段字符串地址 | 未用 |

几点编码细节：

- 汇编标签（`('label', name)`）在编码时由 `encode_program()` 用 labels 表解析成 `imm`；未定义的标签直接抛 `Undefined label`，不会写出半成品。
- `mem` 的 `base` 为 `-1` 时表示纯偏移寻址。
- `float` 用 `struct.pack('<d', ...)` 取位模式，`decode_operand()` 按同样方式还原。
- `cond` 解码时用 `Cond.NAMES[value & 15]` 还原为 `EQ`/`NE`/… 名字。

## `.crom`：内存镜像 v3

`codecin/crom.py` 与 Go 侧 `codecin/native/engine/crom.go` 实现同一格式，头部 16 字节：

| 偏移 | 大小 | 字段 | 说明 |
| --- | --- | --- | --- |
| 0x00 | 4 | Magic | `'CROM'` |
| 0x04 | 1 | Version | `0x03` |
| 0x05 | 4 | Memory Size | 内存字节数（u32） |
| 0x09 | 1 | Flags | bit0 = zlib 压缩；bit1 = 尾部含 MMU 页表元数据 |
| 0x0A | 4 | Checksum | 载荷的 CRC32（IEEE，`zlib.crc32`） |
| 0x0E | 2 | Reserved | 保留 |
| 0x10 | N | Data | 压缩或原始载荷 |

- **压缩**：`Flags` bit0 置位时载荷是 zlib 流。Python 侧用 `zlib.compress(payload, level=6)`，Go 侧用 `zlib.NewWriter`（`flate.DefaultCompression = 6`），两边级别一致；不压缩时载荷就是原始字节。
- **CRC32 覆盖范围**：`Data` 段的全部字节。若带 MMU 尾部元数据，校验和同样覆盖尾部。
- **MMU 尾部**：`Flags` bit1 置位时，`Data` 在 `Memory Size` 字节的内存之后还跟着一段页表元数据，序列化为 `identity u8 | 条目数 u32 | (vpn u32, ppn u32, perms 3B)* | 黑名单数 u32 | (vpn u32)*`。载入时只有 `enable_mmu=True`（CLI 的 `--mmu`）才会恢复它，否则忽略并仍按物理地址加载内容。
- **余量上限**：载荷允许比 `Memory Size` 多出最多 4 MiB（`CROM_MAX_TRAILER`），这一余量是留给 MMU 尾部的。超出即判为损坏/恶意文件。
- **旧版裸格式**：若前 4 字节不是 `'CROM'`，`load_crom()` 会按"旧版裸镜像"处理——把这 4 字节当 `mem_size`，要求它与文件实际长度自洽，否则报 `Not a .crom file`。任意垃圾文件不会被静默当成内存镜像载入。

`--mmu` 之外，MMU 与页保护的完整语义见 [寄存器与内存模型](/reference/registers-memory)。

## 常用命令

::: tabs

== 编译 .bin

```bash
python cpu.py basic.cin --compile-only -o basic.bin   # 仅编译, 不执行
python cpu.py basic.cin --compile                     # 编译为 .bin 后继续执行
```

```text
INFO  CIN compiled: 16 instructions
INFO  Binary saved to basic.bin (385 bytecode bytes)
Compiled to basic.bin
```

== 运行 .bin

```bash
python cpu.py basic.bin                 # 走默认（原生优先）路径
python cpu.py basic.bin --no-native     # 纯解释执行同一份字节码
```

```text
INFO  Binary loaded: 16 instructions
Hello, Code CIN!
```

== 保存 .crom

```bash
python cpu.py basic.cin --save                 # 运行结束后保存 <程序名>.crom
python cpu.py basic.cin --save --no-compress   # 不压缩
```

```text
INFO  .crom saved to basic.crom (131 bytes, compressed=True, mmu=False)
```

== 加载 .crom

```bash
python cpu.py basic.asm --crom basic.crom
```

:::

::: warning `--save` 的输出路径由源文件名决定

`--save` 写的永远是 `<程序名>.crom`（与源文件同目录），**`-o` 只影响 `.bin` 的输出**。实测 `python cpu.py hello.cin --save -o custom.crom` 仍然生成 `hello.crom`。

:::

### `--crom` 到底作用在哪

`--crom <file>` 是"**把内存镜像恢复到虚拟机内存**"的机制，作用范围比名字听起来窄：

| 输入类型 | `--crom` 是否生效 | 说明 |
| --- | --- | --- |
| `.pl` / `.asm` | **生效** | 恢复镜像 → 再汇编；汇编器的数据段写入会覆盖镜像中同一地址的内容 |
| `.cin` | 不生效 | `codecin/cpu.py` 的 `load_program()` 在 `.cin` 分支直接返回 |
| `.bin` | 不生效 | `.bin` 分支调用 `load_bin()` 后直接返回 |

对 `.pl` / `.asm` 还有一条**自动探测**规则：未显式给 `--crom` 时，若同目录存在同名的 `<程序名>.crom`，会自动加载它：

```text
INFO  Loaded .crom v3: 65536 bytes, compressed=True, mmu=False
```

::: danger 别把 `.crom` 当可执行产物

`.crom` 只是内存快照，不含入口点、不含程序语义。直接 `python cpu.py hello.crom` 会把文件当汇编源解析，最终以解码错误退出（退出码 1）。要跑程序用 `.bin` 或源码；`.crom` 请与 `--save`/`--crom` 成对使用，并注意它恢复的是**内存内容**，不恢复寄存器、PC 与已执行的宿主副作用。

:::

## 反汇编查看：`--disasm`

`--disasm` 接受两种输入：`.bin`（CPUSA 容器）与**裸 UCBC 段**。它属于"看一眼就退出"的模式，成功退出码 0：

```bash
python cpu.py basic.bin --disasm
```

::: tabs

== CPUSA 容器

```text
; CPUSA binary: 16 instructions, mem=65536 bytes, entry=0x0, sp=0xfff8
;
0000: CALL #2
0001: HALT
0002: PUSH X29
0003: MOV X29 X32
```

首行格式为 `; CPUSA binary: <指令数> instructions, mem=<内存字节数> bytes, entry=0x<入口>, sp=0x<栈指针>`，指令行的格式是"四位十六进制指令序号 + 助记符 + 操作数"。

== 裸 UCBC 段

```text
; UCBC disassembly: 16 instructions, entry=0x0
;
0000: CALL #2
0001: HALT
0002: PUSH X29
0003: MOV X29 X32
```

裸段没有内存镜像与 SP，首行只有指令数与入口；`#N` 表示立即数，`X29` 表示寄存器。

给出非 `.bin`/非 `UCBC` 的文件时报错并提示先编译：

```text
--disasm 需要 .bin (CPUSA 容器) 或 UCBC 字节码; 先用 `python cpu.py src.cin --compile-only -o out.bin` 生成
```

:::

## 两种实现的二进制兼容性

Go 与 Python 的 CROM 实现是**格式级兼容**的，可以互相读写：

| 维度 | Python（`codecin/crom.py`） | Go（`codecin/native/engine/crom.go`） |
| --- | --- | --- |
| 头部布局 | 16 字节，同上表 | 相同 |
| 压缩级别 | `zlib.compress(level=6)` | `zlib.NewWriter`（默认级别 6） |
| 校验和 | `zlib.crc32(body)` | `crc32.ChecksumIEEE(payload)` |
| 解压上限 | `mem_size + 4 MiB` | 同一常量 |
| 版本校验 | 不等即报错 | 不等即返回失败 |

打包时会**优先调用原生库**（`engine.crom_pack`），不可用时回退 `zlib`；含 MMU 尾部元数据时跳过 Go 打包，改由 Python 侧排版，以保证两种实现产出完全一致的字节流。`tests/test_native_hardening.py` 覆盖了往返一致与异常输入。

字节码侧同理：Python `encode_program()` 产出的 UCBC 段能被 Go VM 直接执行（原生路径就是这么做的），Go VM 的 `decodeBytecode` 会校验 magic、版本、指令条数、`argc` 与操作数完整性——畸形输入被安全拒绝，不会 OOM 或越界 panic。

## 限制与版本策略

- **版本号是硬门槛，没有迁移逻辑**：`BIN_VERSION = 2`、`BC_VERSION = 1`、`CROM_VERSION = 3`。载入时版本不符分别报 `Unsupported binary version: <x>`、`Unsupported bytecode version: <x>`、`Unsupported .crom version: <x>`。跨版本兼容策略**以源码为准**，不要假设旧产物能在新版本里直接跑。
- **`.bin` 的 `mem_size` 会改变内存布局**：`load_bin()` 在镜像大于当前 `--mem-size` 时会扩容内存，并同步 `config.mem_size`。
- **`.crom` 的载荷大小受头部约束**：解压结果超过 `mem_size + 4 MiB` 视为损坏或 zip bomb；不压缩载荷同样受该上限约束。
- **`.bin` 尾部多余字节被忽略**：`decode_program()` 按 `instr_count` 精确读取，读满即停，不做尾部校验。
- **操作数个数由指令决定**：手改 `.bin` 很容易让 `argc` 与指令语义不匹配，Python 解码会读错偏移、Go VM 会直接拒绝。

::: tip 需要人眼核对时优先用 `--disasm`

它是唯一"零依赖、零执行"的格式检查手段：只解码不运行，因此可以安全地检查来路不明的 `.bin`。

:::

## 相关页面

- [执行路径](/guide/execution-paths)——字节码在三条路径上的执行差异
- [Go 原生运行时](/runtime/native)——原生库如何打包/解包 CROM
- [AOT 独立可执行文件](/runtime/aot)——把 `.bin` 的字节码内嵌进静态单文件
- [寄存器与内存模型](/reference/registers-memory)——MMU 页表与内存布局
- [命令行参考](/guide/cli)——`--compile` / `--save` / `--crom` / `--disasm` 全表
