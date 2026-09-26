---
description: "Code CIN 远程调试协议 (v1): --debug-server 的 TCP 换行文本协议、状态机、命令与响应细则、nc 会话示例与 v2 路线图。"
---

# 远程调试协议

本页固化 `--debug-server <port>` 使用的**换行文本协议**（v1），实现位置为 `codecin/debugger.py` 的 `DebugServer.drive()` / `_burst()` / `_process_command()`，入口为 `codecin/cli.py` 的 `--debug-server` 与 `codecin/cpu.py` 的 `CPU._run_remote()`。目标是让 IDE、VS Code 或网页调试前端可以按此协议接入。

- 本机键盘驱动的交互式调试（`--step`、断点命中）见 [/tools/debugger](/tools/debugger)；
- 协议集成测试 `tests/test_features_remote.py` 可直接作为客户端范例。

## 快速开始

::: tabs

== 启动服务端

```bash
# 终端 1: 启动远程调试 (程序加载后等待客户端连接)
python cpu.py examples/control_flow.cin --debug-server 9999
```

== 连接客户端

```bash
# 终端 2: 连接 (任选一种)
nc localhost 9999
```

:::

连接建立后服务端立即发送一行欢迎语（`DebugServer.drive()` 的第一条 `_send`）：

```text
Code CIN remote debug ready (step/continue/break/delete/watch/regs/mem/pc/history/info/quit)
```

在 `--debug-server` 模式下：

- 程序**不自动运行**，全部由客户端命令驱动（`CPU._run_remote()` 把 `cpu.running` 初始化为 `False`）；
- 该模式走**纯解释路径**，不启用 Go 原生库与 JIT（`CPU.run()` 在 `debug_server_port` 非空时直接进入 `_run_remote()`）；
- 服务端日志（INFO 级）打印 `Remote debug server on port <port> (waiting for client)`，连接后打印 `Debug client connected from ('127.0.0.1', <port>)`。

## 传输约定

- **TCP**，绑定 `localhost`（`socket.bind(('localhost', self.port))`），`SO_REUSEADDR` 已开启；
- **单客户端、单会话**：`accept()` 一次后进入会话循环，直到客户端断开或 `quit`；会话结束后 `drive()` 返回，进程随即结束，**不会**回到监听状态；
- 每条命令一行，以 `\n` 结尾；`\r\n` 亦可（读取时逐字节丢弃 `\r`）；
- 编码 **UTF-8**；命令按空白拆分，首词小写后匹配（`parts[0].lower()`）；
- **空行被忽略，且不产生任何响应**（源码 `if not cmd: continue`）；
- 除下述两处例外，每条命令回一行响应，同样以 `\n` 结尾：

| 例外 | 行为 |
|------|------|
| `info break` | 多行响应：首行 `Breakpoints:`，其后每行一个断点（含条件与命中数） |
| `continue` | 两条响应行：先 `OK continuing`，burst 结束后再追加状态行 |

## 状态机

```text
[连接] --welcome--> IDLE
IDLE --step--> 执行一条指令 -> 回 OK pc=.. | HALTED(停机)
IDLE --continue--> 执行至断点/停机/指令上限 -> 回 PAUSED pc=.. | HALTED | LIMIT
PAUSED/HALTED --break/delete/watch/regs/mem/pc/history/info--> 保持原状态
HALTED --step/continue--> ERROR: program halted
任意状态 --quit--> BYE, 关闭
```

| 状态 | 含义 |
|------|------|
| `IDLE` | 程序已加载、未执行；可先 `break` 设断点再 `continue` |
| `PAUSED` | 因断点/条件断点暂停；PC 指向**待执行**的断点指令 |
| `HALTED` | 程序结束：HALT、PC 越界、指令上限或运行时错误；此后仅查询类命令可用 |
| `LIMIT` | 达到 `--max-instructions` 上限（服务端同样把会话标记为已停机） |

::: warning 断点处重复 continue 会重复暂停
`drive()` 的 `continue` 分支不设置本地会话的 `_resume_bp_pc` 豁免，`_burst()` 只在 `cpu._resume_bp_pc` 已被设置时才跳过断点判断。因此停在断点后直接再发 `continue`，会立刻再次回 `PAUSED pc=…`。要继续执行请先 `step`，或 `delete <addr>` 后再 `continue`。
:::

## 命令一览

| 命令 | 作用 |
|------|------|
| `step` / `s` | 单步执行一条指令；返回 `OK pc=0x…` 或 `HALTED` |
| `continue` / `c` / `run` / `r` | 自由运行至断点/停机；返回 `PAUSED pc=0x…` / `HALTED` / `LIMIT` |
| `break <addr>` | 设断点（16/10 进制均可，如 `break 0x10`） |
| `break <addr> <cond>` | 设条件断点（条件为 Python 表达式，命名空间含 `regs/pc/sp/pstate`） |
| `delete <addr>` | 删除断点（含条件断点） |
| `watch <addr> [r\|w\|rw]` | 设内存观察点（页粒度保护位，默认 `rw`） |
| `regs` | 返回 32 个通用寄存器列表（Python 列表文本，如 `[0, 3, 0, …]`） |
| `pc` | 返回当前 PC，如 `PC: 0x3` |
| `mem <addr>` | 读一字节：`mem[0x..] = 0x..` |
| `mem <addr> <val>` | 写一字节（谨慎，无回滚） |
| `reverse` / `forward` | 在单步历史中回退/前进（仅 `step` 有记录） |
| `history [clear]` | 历史缓冲大小/索引，或清空 |
| `info break` | 列出断点（多行） |
| `info regs` / `info pc` | 与 `regs` / `pc` 等价的查询形式 |
| `quit` / `q` / `exit` | 结束会话，回 `BYE` |

::: info 缩写规则的边界
`quit` / `q` / `exit`、`step` / `s`、`continue` / `c` / `run` / `r` 的缩写由 `drive()` 直接识别；其余命令交给 `_process_command()`，**只匹配完整词**（`regs`、`pc`、`mem`、`watch`、`history`、`info`、`reverse`、`forward`、`break`、`delete`）。输入 `p` 或 `i` 会得到 `ERROR: Unknown command: p`。
:::

## 响应细则

### step

```text
> step
OK pc=0x2          # 已执行 index 0x1 处指令, 新 PC 0x2
> step
HALTED             # 执行了 HALT (或 PC 越界), 程序结束
```

`cpu.step()` 返回 `False` 时（PC 越界或执行了 HALT 类指令）回 `HALTED`，并把会话置为已停机。

### continue

```text
> continue
OK continuing      # 先确认开始运行
PAUSED pc=0x1      # 之后: 命中断点暂停
```

`_burst()` 的三种终止结果：

| 响应 | 触发条件 |
|------|----------|
| `PAUSED pc=0x…` | 命中条件断点或普通断点（并打印 `Breakpoint hit at PC=0x…` 到**服务端本地终端**） |
| `HALTED` | PC 越界，或指令返回 `False`（HALT） |
| `LIMIT` | `cpu.stats.instruction_count >= cpu.config.max_instructions` |

执行期异常在 `drive()` 中被捕获并回 `ERROR: <异常文本>`，随后会话置为已停机。

### break

```text
> break 0x10
OK: Breakpoint at 0x10
> break 0x20 regs[0] == 1
OK: Conditional breakpoint at 0x20: regs[0] == 1
```

条件断点同时写入 `cpu.breakpoints` 与 `conditional_breakpoints`，命中时服务端本地打印 `Conditional breakpoint hit at PC=0x…: <cond>`；条件求值失败只记 `Condition evaluation failed: …` 告警。

### delete / watch

```text
> delete 0x10
OK: Removed breakpoint at 0x10
> watch 0x100 rw
OK: Watchpoint at 0x100 for rw
```

`watch` 复用内存保护位（`FastMemory.set_protection`），不是写-读断点语义；违规访问会抛内存保护错误并终止执行。

### info break

```text
> info break
Breakpoints:
  0x10
  0x20 (cond: regs[0] == 1, hits: 1)
```

普通断点按集合顺序列出，条件断点追加 `(cond: …, hits: N)`。缺少目标（只发 `info`）回 `ERROR: Missing info target`。

### history

```text
> history
OK: History size: 3, index: 2
> history clear
OK: History cleared
```

### 错误

| 响应 | 触发场景 |
|------|----------|
| `ERROR: Unknown command: <cmd>` | 未知命令 |
| `ERROR: program halted` | 停机后再发 `step` / `s` / `continue` / `c` / `run` / `r` |
| `ERROR: Empty command` | `_process_command` 收到空命令（`drive()` 已先行忽略空行） |
| `ERROR: Missing address` | `break` / `delete` / `watch` / `mem` 缺少地址参数 |
| `ERROR: Invalid address` | 地址不是合法的 `int(x, 0)` 数值 |
| `ERROR: Invalid address or value` | `mem <addr> <val>` 的地址或数值非法 |
| `ERROR: Missing info target` | `info` 未带目标 |
| `ERROR: No history` | `reverse` / `forward` 无可用历史 |

## 会话示例 (nc)

以下会话对应 `tests/test_features_remote.py` 固化过的交互序列（程序为两条 `ADDI` 加一条 `HALT`）：

```text
$ nc localhost 9999
Code CIN remote debug ready (step/continue/break/delete/watch/regs/mem/pc/history/info/quit)
break 1
OK: Breakpoint at 0x1
continue
OK continuing
PAUSED pc=0x1
regs
[1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
step
OK pc=0x2
pc
PC: 0x2
continue
OK continuing
HALTED
step
ERROR: program halted
quit
BYE
```

逐条纯单步（不设断点）时的预期序列：

```text
> step
OK pc=0x1
> step
OK pc=0x2
> step
HALTED
```

## 已知限制与 v2 路线图

- 单客户端、单会话；无事件推送，状态变化需要客户端主动轮询 `regs` / `pc`；
- `step` / `continue` 为驱动式执行，不启动 Go 原生路径与 JIT；
- 输出面板（`println` 等程序输出）走服务端本地终端，**不会**回传给客户端；
- `watch` 复用内存保护位，不是写-读断点语义；
- 条件断点在服务端以受限命名空间（`regs` / `pc` / `sp` / `pstate`）求值；
- 断点处重复 `continue` 会重复 `PAUSED`（见上文警告）。

建议的下一步（v2 协议）：

1. **结构化协议**：切换为行 JSON（如 `{"cmd":"continue","id":1}` / `{"type":"paused",…}`），便于 IDE 解析；
2. **事件推送**：断点命中主动通知，支持 `step-over` / `finish` 与栈回溯；
3. **输出回传**：把程序 stdout 与调试面板文本随响应流送回；
4. 可参考本协议的 socket 集成测试 `tests/test_features_remote.py` 作为客户端范例。

::: tip 相关页面
- 本机交互式调试命令与 `print` 目标：[/tools/debugger](/tools/debugger)
- 执行路径选择与原生/JIT 限制：[/guide/execution-paths](/guide/execution-paths)、[/runtime/native](/runtime/native)
- 调试期日志与 `--log-file`：[/tools/logging](/tools/logging)
:::
