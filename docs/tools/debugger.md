---
description: "Code CIN 交互式调试器: --step 单步模式与断点会话的实际命令集、print 目标、条件断点与完整会话示例。"
---

# 交互式调试器

Code CIN 的交互式调试由 `codecin/debugger.py` 的 `DebugSession` 类实现，**单步模式**与**断点命中**共用同一套命令集。前端入口在 `codecin/cpu.py` 的 `_run_interpreted()`：`--step` 每执行一条指令前进入 `run_step_mode()`，断点命中则进入 `run()`。

- 实现了 `DebugSession` 命令集的入口有两个：`--step` 单步会话、断点命中会话；
- 远程驱动式调试（`--debug-server`）用同一 `DebugServer`，但由 TCP 客户端驱动：[/tools/remote-debug](/tools/remote-debug)；逐指令超详细日志（非交互）：[/tools/logging](/tools/logging)。

## 启动方式

::: tabs

== 单步模式 (--step)

```bash
# 每条指令执行前暂停, 提示符为 step>
python cpu.py examples/control_flow.cin --step --no-native
```

== 断点会话 (需预先设断点)

```python
from codecin.cpu import CPU
from codecin.config import Config

cpu = CPU(Config(use_native=False), 'examples/control_flow.cin')
cpu.add_breakpoint(0x2)   # 命中后才出现 dbg> 提示符
cpu.run()
```

== 远程调试 (--debug-server)

```bash
python cpu.py examples/control_flow.cin --debug-server 9999
```

:::

`dbg>` 会话只由**命中断点**触发（`CPU.debug_command_loop()` ← `DebugSession.run()`）：解释循环里没有“运行时凭空产生断点”的路径，所以纯命令行运行若没有断点就永远不会进入该会话，首个断点需要宿主经 Python API 预置（见 [/reference/python-api](/reference/python-api)）。只想从零开始单步，`--step` 更直接。

::: warning 调试模式与加速路径互斥
`--step`、`--debug` 都会把 `config.interactive_mode` 置为 `True`，并让 CPU 放弃 Go 原生路径与 JIT：

- `CPU.run()` 只有在未开启 `debug_mode`/`step_mode`/`bounds_check`/`mmu` 时才尝试原生库执行；
- JIT 编译器仅在未开启 `debug_mode`、`step_mode` 时创建；
- 快路径 `_run_fast()` 要求无断点、无调试服务、无 JIT、无逐指令追踪、无执行节流，且 `config.interactive_mode` 为假（CLI 默认保持为真，因此快路径实际只服务 Python API 嵌入场景）。

因此调试会话一定在纯 Python 解释器上运行，单步语义与原生路径一致但速度较慢。执行路径细节见 [/guide/execution-paths](/guide/execution-paths) 与 [/runtime/jit](/runtime/jit)。
:::

## 状态面板

两种会话都通过 `display_state()` 渲染同一块面板（先清屏再重绘）：

| 区域 | 内容 |
|------|------|
| 分隔标题 | `console.rule()` 居中标题，单步模式为 `Step Execution` |
| 当前指令 | 单步模式下额外输出 `Current Instruction` 面板，如 `ADD X0, X1, #2` |
| 通用寄存器 | 表 `General Registers (X0-X31)`：X0–X30 各十进制/十六进制，XZR 恒 0 |
| 附加信息 | 同一张表追加 `PSTATE`（N/Z/C/V）、`SP`、`PC`，有断点时追加 `BREAKPOINTS` |
| 向量寄存器 | 表 `Vector Registers (V0-V31)`（受 `config.show_vector_regs` 控制，默认开启） |
| 内存与缓存 | 表 `Memory (first 64 bytes)`（地址 / Hex / ASCII / Prot），随后一行 `Cache: N hits, M misses (X.X% hit rate)` |

操作数在面板与追踪日志中统一由 `fmt_operand()` 格式化：`X3`、`V2`、`V2.1`、`#42`、`[X1, #8]`、`[#4096]`、条件名（`EQ`）、字符串 `"@0x1000"`。

## 命令表

命令按会话类型区分，**以 `DebugSession` 实际分支为准**。

### 单步模式（提示符 `step>`）

| 命令 | 缩写 | 说明 |
|------|------|------|
| 空行 | — | 执行一条指令并重绘面板 |
| `step` | `s` | 同上 |
| `continue` | `c`、`r`、`run` | 退出单步、自由运行（内部把 `step_mode` 置为 `False`） |
| `quit` | `q` | 停止运行并结束（`cpu.running = False`） |
| `print <target>` | `p` | 打印寄存器/内存/缓存等，见下节 |
| `break <addr> [cond]` | `b` | 设置断点或条件断点 |
| `delete <addr>` | `d` | 删除断点 |
| `list` | `info`、`l` | 列出断点与条件断点 |
| `help` | `h`、`?` | 打印命令帮助 |

::: warning 缩写只在单步模式下生效
`step>` 模式只识别单字母 `b` / `d`：输入 `break 0x10` 或 `delete 0x10` 会得到 `Unknown command`。`watch`、`reverse`、`forward` 在该模式下同样不被识别。
:::

### 断点会话（提示符 `dbg>`）

| 命令 | 缩写 | 说明 |
|------|------|------|
| `help` | `h` | 打印命令帮助（`DEBUG_HELP` 文本） |
| `continue` | `c` | 退出调试器继续自由运行，并豁免当前 PC 一次 |
| `step` | `s` | 执行一条指令并重绘面板 |
| `reverse` | — | 回退一步（依赖单步历史） |
| `forward` | — | 前进一步（依赖单步历史） |
| `print <target>` | `p` | 打印寄存器/内存/缓存等 |
| `break <addr> [cond]` | — | 设置断点或条件断点 |
| `delete <addr>` | — | 删除断点 |
| `watch <addr> [r\|w\|rw]` | — | 设置内存保护观察点（页粒度保护位） |
| `list` | `info` | 列出断点与条件断点 |
| `quit` | `q` | 结束模拟器运行（优雅退出） |

::: info `help` 文本与实现的两处差异
`DEBUG_HELP` 里列出了 `run / r`（退出单步模式），但 `dbg>` 会话并未实现该分支，输入 `run` 会得到 `Unknown command`；`b` / `d` / `l` 缩写同样只在 `step>` 模式可用。以本页表格为准。
:::

### 中断行为

`Ctrl+C` / `Ctrl+D`（EOF）在 `step>` 下结束运行（等价 `quit`），在 `dbg>` 下退出调试器继续自由运行（等价 `continue`）。

## print 目标

`print`（缩写 `p`）后跟目标名，大小写不敏感（寄存器名会被转为大写显示）。

| 目标 | 输出格式 | 说明 |
|------|----------|------|
| `X0`–`X31` | `X0 = 42 (0x2a)` | 单个通用寄存器，十进制 + 十六进制；`X31` 为 XZR，恒 0 |
| `regs` | 寄存器表 | `RegisterFile.display_registers()` 全表 |
| `mem` | 内存表 | `Memory Dump`，从地址 0 起 32 字节 |
| `mem <addr>` | `mem[0x100] = 42 (0x2a)` | 按 qword（8 字节）读取 |
| `cache` | `Cache stats: {...}` | `Cache.get_stats()` 的字典文本 |
| `pc` | `PC: 0x10` | 当前程序计数器 |
| `sp` | `SP: 0xfff8` | 栈指针 |

- 地址与数值支持 `0x` 十六进制与十进制：`int(parts[2], 0)`；
- 缺少目标输出 `Missing argument`，地址非法输出 `Invalid address`，未知目标输出 `Unknown target`；
- SP 是 32 号伪寄存器（`Constants.SP_REG = 32`），所以 `print X32` 与 `print sp` 取到同一个值。

## 断点与条件断点

```text
dbg> break 0x10
Breakpoint set at 0x10
dbg> break 0x20 regs[0] == 1
Conditional breakpoint set at 0x20: regs[0] == 1
dbg> list
Breakpoints:
  0x10
  0x20 (cond: regs[0] == 1, hits: 1)
```

- 普通断点写入 `cpu.breakpoints` 集合；`break <addr>` 之后每个经过该 PC 的循环迭代都会命中。
- 条件断点由 `ConditionalBreakpoint` 记录地址、条件文本、命中次数；只有 `PC == 地址` 时才求值。
- 条件在**受限命名空间**中求值（`eval(cond, {"__builtins__": {}}, namespace)`），可用名字：

| 名字 | 含义 |
|------|------|
| `regs` | 32 个通用寄存器列表，如 `regs[0]` |
| `pc` | 当前程序计数器 |
| `sp` | 栈指针 |
| `pstate` | 标志位字典，如 `pstate['Z']` |

- 条件写法支持多词（`break 0x20 regs[0] == 1 and pstate['Z']`），因为条件是把 `break` 之后的剩余词拼接而成；
- 求值抛异常时只记一条 `Condition evaluation failed: …` 告警，**不会**暂停执行；
- `delete <addr>` 同时从 `cpu.breakpoints` 与条件断点表中移除该地址。

## 执行历史：reverse / forward

`DebugServer` 为每次单步保存快照（PC、32 个寄存器、SP、PSTATE、时间戳），最多 `history_limit = 1000` 条，超出后丢弃最旧一条。只有 `step` 会记录快照，`continue`/自由运行不记录；`reverse` 回退一步、`forward` 前进一步并重绘面板；历史为空时输出 `No history available` / `No forward history`。

## 会话示例

下例假设宿主已按上一节预先在 `0x2` 设了断点。输出为 rich 表格/面板，这里按文本形式记录并省略边框字符；具体地址与数值随程序而变，**格式与实际渲染一致**。

```text
$ python cpu.py examples/control_flow.cin --no-native
08:37:15 INFO     CIN compiled: 30 instructions
         INFO     Starting program execution
Breakpoint hit at PC=0x2
Debug mode (type help for commands)
dbg> p pc
PC: 0x2
dbg> p sp
SP: 0xfff8
dbg> p X0
X0 = 0 (0x0)
dbg> p X0
X0 = 0 (0x0)
dbg> help
Debug Commands:            # DEBUG_HELP 全表, 内容见上文两节命令表
dbg> break 0x10
Breakpoint set at 0x10
dbg> c
Breakpoint hit at PC=0x10
Debug mode (type help for commands)
dbg> p mem 0x0
mem[0x0] = 72 (0x48)
dbg> p cache
Cache stats: {'hits': 0, 'misses': 1, 'hit_rate': 0.0, 'miss_rate': 1.0, 'total_accesses': 1, 'dirty_writes': 0}
dbg> s
Step Execution
dbg> reverse
Reversed one step
Reverse Step
dbg> delete 0x10
dbg> q
```

`step>` 模式无需任何前置断点，是纯命令行即可完整复现的交互记录（结构与 `dbg>` 一致，仅提示符与首屏不同）：

```text
$ python cpu.py examples/control_flow.cin --step --no-native
Step Execution
Current Instruction
  ADD X0, X1, #2
step> p X1
X1 = 1 (0x1)
step> c
```

## 与 --debug-server 的关系

::: tabs

== 本地交互会话 (DebugSession)

```bash
python cpu.py prog.cin --step            # 提示符 step> (无需断点)
```

```python
cpu.add_breakpoint(0x10); cpu.run()      # 预先设断点, 命中后提示符 dbg>
```

- 由本机键盘驱动，直接读写 CPU 上的 `DebugServer`（条件断点、单步历史持久保留）；
- `continue` 会设置 `cpu._resume_bp_pc = cpu.pc`，豁免当前 PC 一次，**不会**在原地反复命中同一断点。

== 远程驱动会话 (DebugServer.drive)

```bash
python cpu.py prog.cin --debug-server 9999
```

- 由 TCP 客户端逐行发命令，程序加载后不自动运行；
- 协议见 [/tools/remote-debug](/tools/remote-debug)；响应格式为 `OK` / `ERROR` / `PAUSED` / `HALTED` / `LIMIT`；
- 远程 `continue` **不设置** `_resume_bp_pc` 豁免，因此在断点处反复 `continue` 会反复回 `PAUSED pc=…`，需要先 `step` 或 `delete` 该地址。

:::

两者共用 `DebugServer` 的断点与历史数据；一次运行只会走其中一条路径（`CPU.run()` 先判断 `debug_server_port`，为真则直接进入 `_run_remote()`）。

## 常见问题

### quit 之后程序会怎样退出？

现在 `quit` 只把 `cpu.is_debugging` 与 `cpu.running` 置为 `False`，**不再调用 `sys.exit(0)`**（这是 `tests/test_debugger.py::test_step_mode_quit_is_graceful` 固化的行为）。解释循环退出后走 `finally` 分支：输出统计（若开启 `--profile`/`--debug`）、刷新缓存，进程以退出码 0 结束。要区分“用户中途退出”与“程序正常跑完”，看日志最后一行是否有 `HALT instruction executed`。

### 断点为什么会反复重入？

早期实现在断点处 `continue` 会立刻再次命中同一 PC，形成原地循环。现在 `continue` 会把当前 PC 记入 `cpu._resume_bp_pc`，解释循环在下一轮跳过该 PC 的断点判断并清空豁免，因此**至少执行一条指令**（与 GDB 语义一致，见 `tests/test_debugger.py::test_breakpoint_continue_resumes_without_rehit`）。条件断点在解释循环中同样享受该豁免。远程驱动路径未加豁免，见上一节。

### 为什么单步时加速路径没生效？

这是设计约束而非故障：`--step`、`--debug`、断点、`--bounds-check`、`--mmu` 中任意一项开启都会让 CPU 留在解释器上。想让程序跑快，请去掉这些开关并参考 [/tools/profiling](/tools/profiling) 选择执行路径。另需说明：调试器只能通过显式命令改状态（`break`/`delete`/`watch`），寄存器与内存**没有**写入命令——远程协议里的 `mem <addr> <val>` 是唯一写入口，且无回滚。
