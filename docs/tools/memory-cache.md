---
description: "Code CIN 内存模型、LRU 缓存、MMU 分页与运行时行为开关: --mem-size/--max-instructions/--cache-*/--mmu/--bounds-check/--seed/--sandbox/--no-io/--execution-interval。"
---

# 内存、缓存与 MMU

本页说明 UCPU 的平坦内存模型、LRU 缓存、可选 MMU 分页，以及改变运行时行为的命令行开关。默认值以 `codecin/config.py` 的 `Config` 与 `codecin/cli.py` 的 `build_parser()` 为准，实现位于 `codecin/memory.py` 与 `codecin/cache.py`。

- 寄存器与内存语义参考见 [/reference/registers-memory](/reference/registers-memory)；
- 内存/缓存相关的性能指标见 [/tools/profiling](/tools/profiling)；
- 内存错误的日志与退出码见 [/tools/logging](/tools/logging)。

## 内存模型概览

内存是 `bytearray` 支撑的平坦地址空间（`FastMemory`），多字节访问一律**小端**，定宽访问（2/4/8 字节）要求不跨页。

| 区域 | 默认地址 | 说明 |
|------|----------|------|
| 数据段 | 从 `0` 向上 | CIN 全局量/字面量/字符串由编译期 `data_ptr` 从 0 递增分配（按对齐补齐） |
| 堆 / 字符串区 | `mem_size // 2` = `0x8000` | `CPU.heap_ptr` 初值；`SYS` 字符串拷贝等堆操作向上增长 |
| SYS 缓冲区 | `heap_ptr + 2048 + idx * 64` | 8 个 64 字节轮转缓冲（`_sys_buffers`） |
| 栈 | 从 `(mem_size - 8) & ~0x7` 向下 | 默认栈顶 `0xfff8`，`PUSH` 先减 8 再写入 qword |
| 栈保护线 | `heap_ptr + 4096` = `0x9000` | `SP` 低于该值即抛 `Stack overflow (collides with heap)` |

::: tabs

== 默认布局 (64 KiB)

```bash
python cpu.py hello.cin --no-native
```

`--debug` 的 CPU 初始化 dump 会打印实际取值：

```text
DEBUG               CPU 初始化
                    memory    0x10000 bytes
                    cache     64 lines x 4-way
                    sp_init   0xfff8
                    heap_base 0x8000
```

== 自定义内存大小

```bash
python cpu.py hello.cin --no-native --mem-size 262144
```

内存变大时栈顶与堆基址同步上移（`sp_init`、`heap_base` 都会变化）。

:::

栈的越界检查在运行期同步进行：`PUSH` 触发 `Stack overflow (collides with heap)`，在栈顶之上 `POP` 触发 `Stack underflow`。

## 内存相关开关

| 选项 | 默认值 | 作用 |
|------|--------|------|
| `--mem-size <BYTES>` | `65536`（64 KiB） | 内存总大小；小于 `256` 时被 `Config.validate()` 抬到 `256` |
| `--max-instructions <N>` | `100000000` | 指令数上限；小于 `1` 时被抬到 `1` |
| `--execution-interval <SEC>` | `0.0` | 每条指令后 `time.sleep(SEC)`，仅用于演示减速 |

::: danger 不要把内存设得过小
`heap_ptr = mem_size // 2`、`sp_init = (mem_size - 8) & ~0x7`、栈保护线为 `heap_ptr + 4096`。当 `mem_size` 小于约 `0x9000` 时，栈顶会直接落在保护线以下，第一次 `PUSH` 就会报 `Stack overflow`。需要缩小内存做实验时，建议不低于默认的 64 KiB。
:::

`--max-instructions` 用尽是**失败**而非正常结束：解释路径抛 `ExecutionError: instruction limit reached (N steps)`，原生路径同样返回失败，CLI 退出码为 `1`（`tests/test_cli.py` 固化了该行为）。调试服务端的 `continue` 会把它反映为 `LIMIT` 响应，见 [/tools/remote-debug](/tools/remote-debug)。

## LRU 缓存

`Cache` 是组相联、组内 LRU（`OrderedDict`）的**统计模型缓存**：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--cache-size <N>` | `64` | 缓存行数；小于 `8` 时被抬到 `8`；构造时还会取 `max(assoc, size)` |
| `--cache-assoc <N>` | `4` | 关联度（每组的行数） |
| 行大小 | 固定 `16` 字节 | 不可通过命令行修改 |
| 组数 | `size // assoc`（至少 1） | 默认 `64 / 4 = 16` 组 |

地址映射：

```text
set = (addr // 16) % num_sets
tag = addr // (16 * num_sets)      # 默认 num_sets=16 => tag = addr // 256
```

命中/缺失统计由 `Cache.get_stats()` 给出，字段为 `hits`、`misses`、`hit_rate`、`miss_rate`、`total_accesses`、`dirty_writes`：

| 行为 | 结果 |
|------|------|
| 命中 | 该 tag 移到组尾（MRU），`hits += 1`；写命中额外 `writes += 1` |
| 缺失 | 插入组尾，超出关联度时逐出组首（LRU），`misses += 1` |

- `access()` 返回 `True` 表示命中、`False` 表示缺失；
- 解释器在 `LOAD`/`STORE`/`LDR`/`STR`/`LB`/`LH`/`LW`/`SB`/`SH`/`SW` 等访存指令上调用 `record_cache()` 记账；
- `Cache.warmup(instructions)` 会按 `pc * 16` 预热，但**只有 `.pl` / `.asm` 加载路径调用**；`.cin` 与 `.bin` 路径不预热，因此首次访问必然是缺失；
- `CPU.run()` 结束时（以及远程调试会话结束）会 `cache.flush()` 清空缓存内容，统计值保留用于报告。

```bash
python cpu.py bench.cin --no-native --cache-size 128 --cache-assoc 8 --profile
```

`--profile` 报告里的 `Cache Hit Rate` 直接反映参数效果，调参思路见 [/tools/profiling](/tools/profiling)。

## MMU 分页 (`--mmu`)

```bash
python cpu.py hello.cin --no-native --mmu
```

`--mmu` 会给内存挂上 `Mmu`（页大小 `4 KiB`，`PAGE_BITS = 12`）：

| 行为 | 说明 |
|------|------|
| 默认映射 | **懒 identity**：`vpn == ppn`，权限 `rwx`；首次访问某页时自动入表 |
| `map(vpn, ppn=None, perms='rwx')` | 显式映射；省略 `ppn` 即 identity |
| `map_page(vaddr, paddr, perms)` | 按地址映射整页（`>> 12`） |
| `unmap(vaddr)` | 取消映射并记入黑名单（禁止 identity 回填） |
| `protect(vaddr, perms)` | 修改已映射页的权限；对已 unmap 的页调用会抛 `MemoryAccessError` |

错误表现：

| 异常 | 消息格式 | 触发条件 |
|------|----------|----------|
| `PageFaultError` | `Page fault at virtual 0x… (page N unmapped)` | 访问未映射（且不在 identity 回填范围）的页 |
| `PageFaultError` | `Physical address 0x… out of memory (size 0x…)` | 翻译后的物理地址超出内存 |
| `MemoryAccessError` | `Page protection violation at 0x… for 'w'` | 权限位不含该访问类型（`r`/`w`/`x`） |
| `MemoryAccessError` | `Cannot protect unmapped page N (0x…)` | 对已 unmap 的页调用 `protect()` |

跨页访问由 `FastMemory._chunks()` 按页切片处理，块读写可以横跨页边界（见 `tests/test_features_mmu.py::test_memory_block_across_pages`）。

::: warning --mmu 关闭原生路径
`CPU.run()` 只在未开启 `mmu`/`bounds_check`/`debug_mode`/`step_mode` 时才尝试 Go 原生库，因此 `--mmu` 会回退到解释执行。缺页会终止程序并打印红色面板，退出码 `1`。
:::

### .crom 中的页表元数据

内存镜像 `.crom`（v3）可以携带 MMU 状态：文件头 flags 的 `CROM_FLAG_MMU = 0x02` 位表示载荷尾部附带页表元数据，序列化格式为：

```text
identity(1B) | entry_count(4B) | [vpn(4B) ppn(4B) perms(3B)]* | blacklist_count(4B) | [vpn(4B)]*
```

- 保存：`save_crom()` 在内存挂有 MMU 时追加该尾部（并把解压上限放宽到 `mem_size + 4 MiB`）；
- 加载：只有同时传 `--mmu` 时才会恢复页表并挂载 MMU；
- 文件带 MMU 但未启用时记录 INFO 日志 `.crom 含 MMU 页表但未启用 (enable_mmu=False), 跳过`，物理内容照常加载。

## --bounds-check：CIN 数组越界检查

```bash
python cpu.py arrays.cin --no-native --bounds-check
```

该开关在 CIN 代码生成阶段为**定长数组**的每次下标访问插入运行期检查（`0 <= index < size`），检查失败时走运行期中止：

| 消息 | 触发条件 |
|------|----------|
| `bounds-check: negative array index` | 下标为负 |
| `bounds-check: index >= length (N)` | 下标不小于数组长度 |

代价是每条数组访问多出 `CMP` + 条件跳转，并且**禁用 Go 原生路径**（强制解释执行）。数组语法见 [/language/arrays](/language/arrays)。

## 内存保护与越界错误

`FastMemory` 维护一张**逐字节**保护表（`set_protection(addr, perms, size=1)`，默认 `rwx`），所有访问入口都会检查：单字节、定宽（`read/write_word/dword/qword`、`read/write_float/double`）、块（`read_block`/`write_block`/`load_bytes`/`write_string`）。

| 错误消息 | 触发条件 |
|----------|----------|
| `Address 0x… out of bounds (memory size 0x…, access width N)` | 地址或访问宽度越出 `[0, mem_size)` |
| `Memory protection violation at 0x… for 'w'` | 单字节/定宽访问命中不允许该操作的受保护字节 |
| `Memory protection violation at 0x… for 'r' (range 0x…+N)` | 块读写范围内包含不允许该操作的受保护字节 |

::: tabs

== 越界访问

```bash
# 越界读写会抛 MemoryAccessError 并终止程序
python cpu.py prog.cin --no-native
```

```text
Address 0x10004 out of bounds (memory size 0x10000, access width 8)
```

== 保护违例

```python
from codecin.memory import FastMemory

m = FastMemory(1024)
m.set_protection(8, 'r')     # 8 号字节只读
m.write_float(8, 1.5)        # -> MemoryAccessError: Memory protection violation ...
m.write_float(16, 1.5)       # 未保护地址可写
```

```text
Memory protection violation at 0x8 for 'w'
```

:::

浮点与块访问同样受保护约束（`tests/test_memory_protection.py` 覆盖）：`write_float`/`write_double` 不可绕过只读位，`read_block`/`write_block`/`load_bytes`/`write_string` 只要范围覆盖到受保护字节就会被拒绝，除非该字节的权限允许对应操作。

交互式调试器可以用 `watch <addr> [r\|w\|rw]` 在运行中设置观察点（内部即 `set_protection`），见 [/tools/debugger](/tools/debugger)。

## 其它运行时开关

| 选项 | 默认 | 行为 |
|------|------|------|
| `--seed <N>` | `None`（随机） | 以 `random.Random(N)` 初始化模拟器 RNG，使 `SYS RAND`（随机数）可复现；`SYS SRAND` 可在程序内重新播种 |
| `--sandbox` | 关 | 写入 `config.sandbox_mode`，用于标记受限宿主访问；当前版本该标志在解释器的内存/IO 路径上不产生额外行为，需要硬性禁止宿主 I/O 请用 `--no-io` |
| `--no-io` | 关 | `allow_io = False`：`IN` 指令不再读取输入（目标寄存器保持原值），`OUT` 指令直接返回、不产生任何输出 |
| `--execution-interval <SEC>` | `0.0` | 在解释循环中每条指令后 `time.sleep(SEC)`；会禁用紧凑快路径，仅用于演示减速 |

::: tabs

== 确定性执行

```bash
python cpu.py dice.cin --no-native --seed 12345
```

同一 `--seed` 下 `SYS RAND` 序列一致，适合做可复现的回归与测试。

== 禁止宿主 I/O

```bash
python cpu.py prog.cin --no-native --no-io --log-level ERROR
```

程序仍会跑完，但 `IN`/`OUT` 都静默失效，stdout 上只剩日志（若级别允许）。

== 演示减速

```bash
python cpu.py prog.cin --no-native --execution-interval 0.05
```

每条指令暂停 50 ms，便于肉眼观察寄存器/内存面板的变化。

:::

## 常见问题

- **`--mem-size` 改大后程序行为变了？** 栈顶与堆基址都随内存大小移动，程序若硬编码了地址（内嵌 CPU 指令/绝对地址访存）需要同步调整。
- **缓存命中率一直是 0？** `.cin` / `.bin` 路径不做预热，且只有访存指令才会记账；纯算术程序本来就没有缓存访问。`Cache Hit Rate` 为 `0.0%` 时先确认程序确实读写过内存。
- **`--mmu` 之后程序报缺页？** 默认是 identity 懒映射，不会平白缺页；只有在显式 `unmap`（例如从带页表元数据的 `.crom` 恢复）之后才会。可用 `Mmu.is_mapped()` 检查某个虚拟地址所在页是否可访问。
- **`--bounds-check` 让程序变慢？** 这是预期代价：每次定长数组下标多一次比较与分支，并且不能再走原生路径。
- **栈溢出 / 栈下溢**：分别对应 `Stack overflow (collides with heap)` 与 `Stack underflow`，前者通常是递归过深或 `--mem-size` 过小，后者通常是 `RET`/`POP` 多于 `PUSH`。
