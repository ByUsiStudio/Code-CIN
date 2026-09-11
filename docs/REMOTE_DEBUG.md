# Code CIN 远程调试协议 (REMOTE_DEBUG)

> 本文档固化 `--debug-server <port>` 使用的**换行文本协议** (v1)。
> 实现位置: `codecin/debugger.py` → `DebugServer.drive()` / `_burst()` / `_process_command()`;
> 入口: `codecin/cli.py` (`--debug-server`), `codecin/cpu.py` (`CPU._run_remote`)。
> 目标是让 IDE / VS Code / 网页调试前端可以按此协议接入。

---

## 1. 快速开始

```bash
# 终端 1: 启动远程调试 (程序加载后等待客户端连接)
python cpu.py examples/control_flow.cin --debug-server 9999

# 终端 2: 连接 (任选一种)
nc localhost 9999          # 或
python -c "import socket,threading; ..." # 自行封装
```

连接建立后服务端发送一行欢迎语:

```
Code CIN remote debug ready (step/continue/break/delete/watch/regs/mem/pc/history/info/quit)
```

`--debug-server` 模式下程序**不自动运行**, 等待客户端命令驱动; 该模式走纯解释路径
(不启用原生库 / JIT / `--bounds-check` 之外的其它执行优化)。

---

## 2. 传输约定

- **TCP**, 默认绑定 `localhost`, 一次只服务一个客户端 (accept 一个即进入会话);
- 每条命令一行, 以 `\n` 结尾 (`\r\n` 亦可, 服务端会跳过 `\r`);
- 编码 UTF-8; 命令按空白拆分, 首词小写匹配;
- 每条命令服务端回一行响应 (以 `\n` 结尾); 例外: `info break` 的响应为多行,
  用 `\n` 分隔 (见 [5.4](#54-info-break)); `continue` 可能先回 `OK continuing`
  再在 burst 结束后追加状态行 (两条响应行);
- 会话结束: 发送 `quit` 得到 `BYE` 后连接关闭; 客户端断开连接也会结束会话。

---

## 3. 状态机

```
[连接] --welcome--> IDLE
IDLE --step--> 执行一条指令 -> 回 OK pc=.. | HALTED(停机)
IDLE --continue--> 执行至断点/停机/指令上限 -> 回 PAUSED pc=.. | HALTED | LIMIT
PAUSED/HALTED --break/delete/watch/regs/mem/pc/history/info--> 保持原状态
HALTED --step/continue--> ERROR: program halted
任意状态 --quit--> BYE, 关闭
```

- `IDLE`: 程序已加载, 未执行, 可先 `break` 设断点再 `continue`;
- `PAUSED`: 因断点/条件断点暂停, PC 指向待执行的断点指令 (`continue` 后再次命中
  会重复暂停, 与本地调试器语义一致);
- `HALTED`: 程序执行完毕 (HALT / 越界 / 指令上限 / 运行时错误), 此后仅查询类命令可用。

---

## 4. 命令一览

| 命令 | 作用 |
|------|------|
| `step` / `s` | 单步执行一条指令; 返回 `OK pc=0x…` 或 `HALTED` |
| `continue` / `c` / `run` / `r` | 自由运行至断点/停机; 返回 `PAUSED pc=0x…` / `HALTED` / `LIMIT` |
| `break <addr>` | 设断点 (16/10 进制均可, 如 `break 0x10`) |
| `break <addr> <cond>` | 设条件断点 (条件为 Python 表达式, 命名空间含 `regs/pc/sp/pstate`) |
| `delete <addr>` | 删除断点 (含条件断点) |
| `watch <addr> [r\|w\|rw]` | 设内存观察点 (页粒度保护) |
| `regs` | 返回 32 个通用寄存器列表 (Python 列表文本, 如 `[0, 3, 0, …]`) |
| `pc` | 返回当前 PC, 如 `PC: 0x3` |
| `mem <addr>` | 读一字节: `mem[0x..] = 0x..` |
| `mem <addr> <val>` | 写一字节 (谨慎, 无回滚) |
| `reverse` / `forward` | 在单步历史中回退/前进 (仅单步有记录) |
| `history [clear]` | 历史缓冲大小/索引 或清空 |
| `info break` | 列出断点 (多行) |
| `quit` / `q` / `exit` | 结束会话, 回 `BYE` |
| `pc` / `sp` 查询 | `pc` 与 `sp` 目标均支持 |

---

## 5. 响应细则

### 5.1 step

```
> step
OK pc=0x2          # 已执行 index 0x1 处指令, 新 PC 0x2
> step
HALTED             # 执行了 HALT (或越界), 程序结束
```

### 5.2 continue

```
> continue
OK continuing      # 先确认开始运行
PAUSED pc=0x1      # 之后: 命中断点暂停
```

其它终止响应:

- `HALTED` — 程序执行完毕;
- `LIMIT` — 达到 `--max-instructions` 上限。

### 5.3 break

```
> break 0x10
OK: Breakpoint at 0x10
> break 0x20 regs[0] == 1
OK: Conditional breakpoint at 0x20: regs[0] == 1
```

### 5.4 info break

```
> info break
Breakpoints:
  0x10
  0x20 (cond: regs[0] == 1, hits: 1)
```

### 5.5 错误

- 未知命令: `ERROR: Unknown command: …`
- 停机后执行类命令: `ERROR: program halted`
- 缺参/非法地址等: `ERROR: …` (见 `_process_command` 分支)

---

## 6. 会话示例 (nc)

```
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

---

## 7. 已知限制与路线图

- 单客户端、单会话; 无事件推送 (状态变化需要客户端主动轮询 `regs/pc`);
- `step`/`continue` 驱动式执行, 不启动 `--jit`/Go 原生路径;
- 输出面板 (println 等) 走本地终端, 不会回传给客户端;
- `watch` 复用内存保护位, 不是写-读断点语义;
- 条件断点条件使用受限命名空间 (`regs/pc/sp/pstate`), 在服务端求值。

建议的下一步 (v2 协议):

1. **结构化协议**: 切换为行 JSON (如 `{"cmd":"continue","id":1}` / `{"type":"paused",…}`),
   便于 IDE 解析;
2. **事件推送**: 断点命中主动通知, 支持 `step-over`/`finish` 与栈回溯;
3. **输出回传**: 将程序 stdout 与调试面板文本随响应流送回;
4. 可参考本协议的 socket 集成测试 `tests/test_features_remote.py` 作为客户端范例。
