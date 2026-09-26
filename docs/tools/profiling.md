---
description: "Code CIN 性能分析: --profile 报告的统计指标、指令周期表、热点定位方法与执行路径/缓存优化建议。"
---

# 性能分析

`--profile` 让模拟器在执行结束后打印性能报告。报告由 `codecin/stats.py` 的 `Statistics.display_summary()` 生成，数据来自执行期的逐指令记账（`Statistics.record_instruction()` 等）与缓存统计。

- 执行路径的选择与性能差异见 [/guide/execution-paths](/guide/execution-paths)、[/runtime/native](/runtime/native)、[/runtime/jit](/runtime/jit)；
- 缓存与内存参数见 [/tools/memory-cache](/tools/memory-cache)；
- 逐指令追踪（不是统计）见 [/tools/logging](/tools/logging)。

## 启用方式

::: tabs

== 解释执行

```bash
python cpu.py hello.cin --no-native --profile
```

== JIT 路径

```bash
python cpu.py hello.cin --no-native --jit --profile
```

== Go 原生路径

```bash
python cpu.py hello.cin --profile
```

:::

`CPU.run()` 的 `finally` 分支在 `config.profile or config.debug_mode` 为真时，先渲染一次 `Execution Complete` 状态面板，再调用 `stats.display_summary(console, cache_stats, native_used=…)`。因此 `--debug` 也会附带同一份报告。

::: warning 报告不会出现的两种情形
- `--debug-server` 模式：`CPU._run_remote()` 直接返回，**不输出**性能报告；
- 执行中途 `quit`（交互调试）后 CPU 仍会走 `finally`，报告照常输出，但统计只覆盖已执行的部分。
:::

## 报告结构

`display_summary()` 依次输出四张表：

| 顺序 | 表名 | 内容 |
|------|------|------|
| 1 | `Execution Statistics` | 引擎、总指令数、总周期、CPI、执行时间、指令/秒、内存读写、分支准确率、缓存命中率（可选 JIT 行） |
| 2 | `Instruction Usage` | 按执行次数降序的操作码直方图（最多 20 行），含占比 |
| 3 | `Instruction Cycle Profile` | 按累计周期降序的操作码周期分布（最多 20 行），含占比 |
| 4 | `Performance Counters` | 完整的性能计数器字典 |

### Execution Statistics 指标

| 指标 | 来源与说明 |
|------|------------|
| `Engine` | `Go native` 或 `Python interpreter`（由 `native_used` 决定） |
| `Total Instructions` | `Statistics.instruction_count` |
| `Total Cycles` | `InstructionProfiler.get_total_cycles()`，即各操作码延迟之和 |
| `CPI (Cycles/Inst)` | 总周期 ÷ 总指令数（指令数为 0 时不输出该行） |
| `Execution Time` | `Statistics.execution_time`，保留 4 位小数 |
| `Instructions/sec` | 指令数 ÷ 执行时间（执行时间为 0 时不输出该行） |
| `Memory Reads` / `Memory Writes` | 记录到 `Statistics.memory_reads` / `memory_writes` 的次数 |
| `Branch Accuracy` | 分支预测器准确率，`%.1f%%` |
| `Cache Hit Rate` | 缓存命中率，`%.1f%%`（仅当传入缓存统计时输出） |
| `JIT Hit Rate` / `JIT Blocks Compiled` | 仅当调用方传入 `jit_stats` 时输出 |

::: info JIT 行在 CLI 报告中不出现
`display_summary()` 支持 `jit_stats` 参数（读取 `hit_rate` 与 `blocks_compiled`），但 `CPU.run()` 调用时**只传** `cache_stats` 与 `native_used`。因此命令行 `--profile` 不会打印 JIT 行；需要 JIT 指标时请用 Python API 读取 `JITCompiler.get_stats()`（键：`blocks_compiled`、`cached_blocks`、`total_calls`、`cache_hits`、`hit_rate`），参见 [/reference/python-api](/reference/python-api)。
:::

### Instruction Usage 与 Instruction Cycle Profile

两张表结构相同：`Instruction` / `Count`（或 `Cycles`）/ `Percentage`，都是按值降序取前 20 项。

```text
Instruction Cycle Profile
Instruction   Cycles   Percentage
MOV           10       38.5%
SYS           4        15.4%
PUSH          3        11.5%
POP           3        11.5%
OUT           2        7.7%
CALL          1        3.8%
ADD           1        3.8%
RET           1        3.8%
HALT          1        3.8%
```

### Performance Counters

| 指标 | 说明 |
|------|------|
| `Cycles` | `PerformanceCounters.counters['cycles']`（仅由 `add_cycles()` 累加） |
| `Instructions` | 性能计数器侧的指令数 |
| `Branches` | 属于 `Constants.BRANCH_OPS` 的指令数（含 `JMP`/`CALL`/`RET`/`B`/`BEQ` 等） |
| `Branch Mispredictions` | 条件分支预测失败次数 |
| `Cache Hits` / `Cache Misses` | 缓存命中/缺失次数 |
| `Memory Reads` / `Memory Writes` | 内存读写次数 |
| `Stalls` | 停顿计数（预留字段，当前实现不累加） |
| `Flops` | 属于 `Constants.FP_OPS` 的指令数（`FADD`/`FSUB`/`FMUL`/`FDIV`/`VADD`/`VSUB`/`VMUL`/`VDIV`） |
| `Instructions Per Cycle` | `ipc = instructions / cycles` |
| `Branch Accuracy` | 预测正确数 ÷ 预测总数，无预测时为 `100.0%` |
| `Cache Hit Rate` | 命中数 ÷ 总访问数，无访问时为 `0.0%` |

实测（`hello.cin`，30 条编译指令，纯 Python 路径，共执行 26 条指令）：

```text
Performance Counters
Metric                        Value
Cycles                        0
Instructions                  26
Branches                      2
Branch Mispredictions         0
Cache Hits                    0
Cache Misses                  0
Memory Reads                  0
Memory Writes                 0
Stalls                        0
Flops                         0
Instructions Per Cycle        0.00
Branch Accuracy               100.0%
Cache Hit Rate                0.0%
```

::: warning Cycles / IPC 可能为 0
`PerformanceCounters.counters['cycles']` 只由 `add_cycles()` 递增，而当前解释器与原生路径都**没有调用它**，所以 `Cycles` 与 `IPC` 常为 `0`。周期口径请以 `Instruction Cycle Profile` 与 `Execution Statistics` 的 `Total Cycles`（`InstructionProfiler` 延迟之和）为准：上例中该值为 `26`，`CPI = 26 / 26 = 1.00`。

同理，`Branch Accuracy` 只统计经过 `record_branch()` 的条件分支（`BEQ`/`BNE`/`BLT`/`BGE`/`BLTU`/`BGEU`）；程序里没有这类指令时预测总数为 0，准确率显示为 `100.0%`，`Branch Mispredictions` 为 0——这**不代表**分支预测有效，只代表没有可统计的条件分支。
:::

### 原生路径的统计口径

走 Go 原生库时，`_apply_native_state()` 不再逐条调用 `record_instruction()`，而是按原生 VM 返回的步数一次性写入：

- `instruction_count` 设为原生返回的步数；
- `opcode_count` 被清空，因此 `Instruction Usage` 表为空；
- 计数集中到 `hot_instructions['?']` 与 `inst_profiler.cycles['?']`，即周期分布里会出现一行 `?`。

## 指令周期表

周期取值来自 `InstructionProfiler.latency`，**表中未列出的操作码按 1 周期计**：

| 指令 | 周期 | 说明 |
|------|------|------|
| `ADD` / `SUB` | 1 | 整数加减 |
| `MUL` | 3 | 整数乘法 |
| `DIV` | 10 | 整数除法 |
| `LOAD` / `STORE` | 4 | 内存加载 / 存储 |
| `FADD` | 3 | 浮点加法 |
| `FMUL` | 5 | 浮点乘法 |
| `FDIV` | 10 | 浮点除法 |
| `VADD` | 2 | 向量加法 |
| `VMUL` | 4 | 向量乘法 |
| `VDIV` | 8 | 向量除法 |
| `LSL` / `LSR` / `AND` / `OR` / `XOR` | 1 | 逻辑与移位 |
| `LD` / `SD` / `LDR` / `STR` / `LB` / `LH` / `LW` / `SB` / `SH` / `SW` | 4 | 定宽访存全家 |

> 该表是**建模延迟**，不是实测墙钟时间；它决定 `Total Cycles`、`CPI` 与 `Instruction Cycle Profile` 的排序。

## 如何读报告

1. **先看 `Engine`**：若期望原生加速却显示 `Python interpreter`，说明原生库未加载或被开关禁用（`--no-native`、`--debug`、`--step`、`--bounds-check`、`--mmu` 都会禁用）。加载失败时 WARNING 日志会给出原因。
2. **看 `Total Instructions` 与 `CPI`**：这两个数一起给出“做了多少事”和“平均每条指令要几个周期”。若 `CPI` 明显大于 1，说明周期开销集中在高延迟指令上。
3. **看 `Instruction Cycle Profile`**：第一行就是最大周期消耗者。占比高且周期数属于访存（4）或除法（10）类的操作码，是首个优化目标。
4. **看 `Instruction Usage`**：次数最多但单周期低的指令（如 `MOV`）属于调用/搬运开销，通常通过减少冗余搬运或改用更紧凑的表达来优化。
5. **看 `Cache Hit Rate` 与 `Memory Reads/Writes`**：命中率低意味着访存模式跳变频繁，可考虑调整 `--cache-size` / `--cache-assoc`。
6. **看 `Instructions/sec`**：这是纯墙钟指标，受调试开关影响最大；对比不同执行路径时应保持其它开关一致。

### 定位热点指令

- 命令行：`Instruction Cycle Profile` 前几行即热点；用 `--debug` 可以拿到每条指令的 PC 与操作数，配合 `PC=0x…` 定位到具体循环体。
- Python API：`Statistics.get_hot_instructions(top_n=10)` 返回 `[(opcode, count), …]`，按执行次数降序；`Statistics.opcode_count` 提供全量直方图，`Statistics.inst_profiler.cycles` 提供按操作码的周期累计。示例见 [/reference/python-api](/reference/python-api)。

## 优化建议

::: tabs

== 选执行路径

```bash
python cpu.py bench.cin --profile              # Go 原生 (默认, 需已构建原生库)
python cpu.py bench.cin --no-native --jit --profile   # Python JIT
python cpu.py bench.cin --no-native --profile # 纯解释 (基准线)
```

优先使用原生库；原生库不可用时再开 `--jit`（基本块动态编译，命中缓存的块整块执行并批量记账）。纯解释路径作为对照基准。

== 按需开 JIT

```bash
python cpu.py bench.cin --no-native --jit --profile
```

`--jit` 与调试开关互斥：`--debug`、`--step` 下不会创建 JIT 编译器；遇到不支持的指令时 JIT 会回退解释执行该条指令。

== 调整缓存参数

```bash
python cpu.py bench.cin --cache-size 128 --cache-assoc 8 --profile
python cpu.py bench.cin --mem-size 262144 --profile
```

`--cache-size` 默认 64 行、`--cache-assoc` 默认 4 路（行大小固定 16 字节，组数 = 行数 ÷ 关联度，最小 8 行）。扩大容量或提高关联度可减少冲突缺失，报告里的 `Cache Hit Rate` 会直接反映效果。

:::

- **不要为了跑分留下调试开关**：`--debug`、`--step`、`--bounds-check`、`--mmu`、断点与 `--execution-interval` 都会让 CPU 留在解释器上，并可能禁用快路径。
- **`--execution-interval` 只用于演示减速**：它按 `time.sleep(interval)` 逐指令暂停，会显著拉低 `Instructions/sec`。
- **`--max-instructions` 不是性能旋钮**：用尽即抛 `instruction limit reached (N steps)` 并以退出码 1 结束，属失败而非“跑完了”。
- **同一份报告要同口径比较**：切换执行路径前后，保持 `--cache-size`/`--cache-assoc`/`--mem-size`/输入数据一致。
