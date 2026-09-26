---
description: "Code CIN 的 rich 日志与错误输出: 日志级别、--debug 超详细追踪清单、--log-file 落盘、红色错误面板与退出码约定。"
---

# 日志与错误输出

Code CIN 的全部日志与错误输出基于 **rich**：彩色面板、表格与完整堆栈回溯。模块**禁止直接 `print`**，统一经 `codecin/console.py` 适配层输出；日志器由 `codecin/logger.py` 的 `Logger` 提供，底层是 `rich.logging.RichHandler`。

- 配置项与命令行开关见 [/guide/cli](/guide/cli)；
- 性能统计表（`--profile`）见 [/tools/profiling](/tools/profiling)；
- 追踪里出现的寄存器与内存模型见 [/reference/registers-memory](/reference/registers-memory)。

## 输出架构

| 组件 | 职责 |
|------|------|
| `codecin/console.py` → `Console` | rich 控制台封装：`print`、`rule`、`clear`、`print_exception`（彩色 traceback） |
| `codecin/console.py` → `Panel` / `Table` / `Colors` | 面板、表格适配器与 ANSI 颜色码；字符串中的 ANSI 自动转 rich `Text` |
| `codecin/logger.py` → `Logger` | 独占 `codecin` logger，`RichHandler` 输出到 `Console.rich`；提供 `debug/info/warning/error/critical/exception` |
| `codecin/logger.py` → `trace/dump/hexdump` | 超详细调试辅助：逐指令追踪、字段表格 dump、内存十六进制转储 |

`Logger` 的 handler 形式为：显示时间（`%H:%M:%S`）、显示级别、不显示文件路径、不启用 rich markup，消息格式为 `%(message)s`。

::: tabs

== 默认: 输出到 stdout

```bash
python cpu.py hello.cin --no-native
```

日志与程序输出**共用同一个 stdout**（程序文本由 `sys.stdout.write` 直接写出），因此重定向或管道里两者交织在一起。

== 落盘: --log-file

```bash
python cpu.py hello.cin --no-native --log-file logs/run.log
```

文件 handler 以覆盖模式（`mode='w'`）写入 UTF-8 文本，目录会自动创建；文件记录**固定为 DEBUG 全量**，与 `--log-level` 无关。

:::

## 日志级别

| 级别 | 内容 |
|------|------|
| `ERROR` | 仅错误面板与错误日志 |
| `WARNING` | + 回退/降级告警（如原生库缺失、条件断点求值失败） |
| `INFO`（默认） | + 编译汇总（指令数）、执行起止、`.crom` 加载信息、统计表 |
| `DEBUG` | **超详细**：全部埋点 + 逐指令追踪 |
| `CRITICAL` | 仅致命错误（`Logger.critical`） |

命令行可选值与内部级别码对应关系：

| `--log-level` | codecin 级别码 | logging 级别 |
|---------------|----------------|--------------|
| `DEBUG` | 0 | 10 |
| `INFO`（默认） | 1 | 20 |
| `WARNING` | 2 | 30 |
| `ERROR` | 3 | 40 |
| `CRITICAL` | 4 | 50 |

`--debug` 会强制把日志级别设为 `DEBUG`（即使同时传了 `--log-level ERROR`），并把 `config.interactive_mode` 置为 `True`。

### 默认（INFO）输出示例

```text
$ python cpu.py hello.cin --no-native
08:37:15 INFO     CIN compiled: 30 instructions
         INFO     Starting program execution
Hello, Code CIN!
2 + 3 = 5
         INFO     HALT instruction executed
         INFO     Program execution finished
```

### 只保留程序输出

```text
$ python cpu.py hello.cin --no-native --log-level ERROR
Hello, Code CIN!
2 + 3 = 5
```

::: tip 脚本 / OA 场景
把日志级别压到 `ERROR`，stdout 就只剩程序自身的输出，便于直接比对或入库；需要审计时再叠加 `--log-file`，落盘文件里仍保留全量 DEBUG 记录。
:::

## --debug 超详细输出

`--debug` 会把 DEBUG 埋点全部打开，典型首批输出（rich 面板 + DEBUG 行）：

```text
$ python cpu.py hello.cin --no-native --debug
DEBUG               CPU 初始化
                    memory    0x10000 bytes
                    cache     64 lines x 4-way
                    sp_init   0xfff8
                    heap_base 0x8000
                    native    False
                    jit       False
                    log_level DEBUG
DEBUG    CIN tokenize: 30 tokens
DEBUG    CIN parse: 0 structs, 0 globals, 1 functions (main)
DEBUG    CIN function main: 0 params
DEBUG    CIN codegen total: 30 instructions, 26 data bytes, 1 labels, 2 data writes
DEBUG    PC=0x0000 #00000000 CALL main->0x2  SP=0xfff8
DEBUG      MEM WR    @0xfff0 w=8 value=0x1
DEBUG      PUSH @0xfff0 <- 0x0000000000000001
DEBUG      => pc=0x0002 N=0 Z=0 C=0 V=0
```

### 逐指令追踪

每条指令固定两行（`Logger.trace`，仅在 DEBUG 生效）：

```text
PC=0x0004 #00000002 ADD X1=0x0(0) X2=0x1(1)  SP=0xfff8
  => pc=0x0005 N=0 Z=0 C=0 V=0
```

- 首行：`PC=0x%04x`、`#%08d` 全局指令序号、操作码、操作数（寄存器/内存附当前值）、`SP=0x…`；
- 次行：执行后的新 PC 与 NZCV 四个标志（`_trace_flags()`）。

操作数与相关埋点的格式：

| 埋点 | 格式示例 |
|------|----------|
| 寄存器操作数 | `X1=0x0(0)`（十六进制 + 十进制） |
| 立即数 / 浮点 | `#42`、`#1.5f` |
| 内存操作数 | `[X1+8]@0x1008=0x2a`、`[abs+4096]@0x1000=0x…` |
| 条件操作数 | `EQ=1{N=0 Z=1 C=0 V=0}` |
| 标签操作数 | `main->0x2` |
| 寄存器写入 | `R[1] 0x0000000000000000 -> 0x0000000000000001 (1)` |
| 标志变化 | `FLAGS <- N=0 Z=0 C=0 V=0 (a=0x1 b=0x2)` |
| 内存读写 | `MEM WR    @0x000c w=1 value=0x0` / `MEM RD    @0x000c w=8 value=0x2a` |
| 栈操作 | `PUSH @0xfff0 <- 0x0000000000000001` / `POP  @0xfff8 -> 0x0000000000000001` |
| 输出指令 | `OUT -> 'Hello, Code CIN!'` |
| 系统调用 | `SYS #2 (ABS) x0=0x1 x1=0x0 x2=0x0` |
| 缓存访问 | `CACHE HIT  R @0x100 (hit_rate=66.7%)` / `CACHE MISS W @0x120 (…)` |

`MEM` 行由 `FastMemory._trace_mem()` 产生，宽度取真实访问宽度（1/2/4/8 字节），覆盖 `LOAD`/`STORE`/`LDR`/`STR`/`LB`…`SW` 以及 `PUSH`/`POP` 触发的 qword 读写。

### 编译与加速路径埋点

| 阶段 | DEBUG 内容 |
|------|------------|
| CIN 前端 | `CIN tokenize: N tokens`、`CIN parse: X structs, Y globals, Z functions`、`CIN function <name>: N params`、`CIN global '<name>': type=… addr=0x… block=…`、`CIN codegen function '<name>' done`、`CIN codegen total: …` |
| 汇编 | `logger.dump("汇编标签表", …)` 表格（标签 → 地址），以及逐行汇编器 debug 输出 |
| 原生库 | `Loaded native library: <path> (<version>)`、`Native run: bytecode=…B mem=…B entry=… sp=0x… heap=0x… max_steps=…`、`Native result: status=… halted=… steps=… pc=… elapsed=…ms error=…`、回退时 `Native engine hit unsupported opcode, falling back to interpreter at PC=0x…` |
| JIT | `JIT block compile failed at <start>: …`、`JIT execution failed: …`、块缓存命中时 `(hits=N)` |

::: warning --debug 会关闭加速路径
原生库与 JIT 都在 `debug_mode` 下被禁用（`_try_native_run()` 直接返回 `None`，JIT 编译器不创建），因此 DEBUG 追踪看到的一定是解释器逐条执行的顺序。想对比原生/JIT 性能请关掉 `--debug`，见 [/runtime/native](/runtime/native) 与 [/runtime/jit](/runtime/jit)。
:::

### 其它调试辅助（宿主/扩展可用）

- `Logger.dump(title, fields)`：以无表头 rich 表格输出键值快照；
- `Logger.hexdump(title, addr, data, width=16)`：十六进制转储，单次最多 256 字节，超出追加 `... (N bytes total)`；
- 两者都只在 DEBUG 级别生效（`is_debug` 为真）。

## --log-file 落盘

```bash
python cpu.py hello.cin --no-native --debug --log-file logs/hello.log
```

```powershell
python cpu.py hello.cin --no-native --debug --log-file logs\hello.log
```

| 项目 | 行为 |
|------|------|
| 模式 | `FileHandler(mode='w', encoding='utf-8')`，同名文件会被覆盖 |
| 行格式 | `[HH:MM:SS] [LEVELNAME] <message>` |
| 级别 | 文件 handler 固定 `logging.DEBUG`，**记录全量**，不受 `--log-level` 影响 |
| 目录 | 自动 `makedirs` 创建父目录 |
| 关闭 | 重复调用 `set_log_file` 会移除并关闭旧 handler；`Logger.close()` 显式关闭 |

## 错误面板与退出码

加载、汇编、编译、运行错误统一输出**红色 rich Panel**；`.` 异常信息里的 `文件:行号` 由 `codecin/errors.py` 拼接（`{filename}:{line_num}: `，无文件名时退化为 `Line {line_num}: `）。

| 面板标题 | 触发场景 |
|----------|----------|
| `Load Error` | 程序文件不存在、`.crom` 校验失败/解压超限、`CPUSimulatorError` 类的加载错误 |
| `Disasm Error` | `--disasm` 反汇编失败 |
| `Error` | 运行期 `CPUSimulatorError`（汇编错误、编译错误、执行错误、内存错误、缺页） |
| `Execution Error` | `CPU.run()` 内未被识别为 `CPUSimulatorError` 的执行期异常 |
| `Unexpected Error` | `cpu.run()` 抛出的其它异常，附带 `print_exception` 彩色 traceback |
| `Build Error` | `--build-exe` AOT 构建失败（依赖检查、目标平台、`go build`） |

```text
┌──────────────────────── Load Error ────────────────────────┐
│ prog.cin:12: Compiler error: Unknown function: printline   │
└────────────────────────────────────────────────────────────┘
```

- 普通模式下运行时错误只记 `ERROR` 日志 + 红色面板；
- `--debug` 下改用 `logger.exception()`，输出 rich 彩色完整堆栈回溯；
- 加载/运行阶段出现非预期异常时，`--debug` 也会附加完整 traceback。

### 退出码约定

| 退出码 | 含义 |
|--------|------|
| `0` | 成功：`--help`、`--version`、`--compile-only`、程序正常结束，或调试会话中 `quit` 优雅退出 |
| `1` | 失败：缺少程序文件、加载/汇编/编译/运行错误、指令上限用尽（`instruction limit reached (N steps)`）、`execution_failed` 为真 |
| `2` | 参数错误：argparse 拒绝未知选项或非法取值（`parser.parse_args` 的 `SystemExit.code`） |

实测样例：

```text
$ python cpu.py --version
Code CIN 5.5.0
$ echo $LASTEXITCODE      # 0
$ python cpu.py --definitely-not-an-option
$ echo $LASTEXITCODE      # 2
$ python cpu.py no_such_file.cin --log-level ERROR
$ echo $LASTEXITCODE      # 1
```

::: info 指令上限按失败处理
`--max-instructions` 用尽不再伪装成正常结束：解释路径抛 `ExecutionError: instruction limit reached (N steps)`，原生路径同样返回失败，CLI 退出码为 1。参见 `tests/test_cli.py` 的两个用例。
:::

## 完整示例命令

::: tabs

== 日常运行

```bash
python cpu.py hello.cin --no-native --log-level ERROR
```

== 排查编译/执行问题

```bash
python cpu.py hello.cin --no-native --debug --log-file logs/hello.log
```

== 只记录不刷屏

```bash
python cpu.py hello.cin --no-native --log-level WARNING --log-file logs/hello.log
```

:::

## 常见问题

- **日志跑到程序输出里了**：这是设计如此，日志默认写 stdout。要分离，用 `--log-file` 或 `--log-level ERROR`。
- **`--debug` 没输出 DEBUG 行**：确认没有用 `--log-level` 之外的机制关闭日志；`--debug` 会强制 DEBUG。若日志仍偏少，检查是否走了原生路径——原生路径只有少量 DEBUG 行，因为逐指令追踪只在解释器里产生。
- **`--log-file` 里没有 DEBUG 行**：文件 handler 固定记录全量 DEBUG，若为空说明该次运行确实没有产生对应埋点（例如未编译 CIN 而是直接载入 `.bin`）。
- **错误面板没有行号**：只有编译/汇编阶段的错误带 `文件:行号`；运行期错误（除零、越界、缺页）本身没有源码位置。
- 更多异常对照见 [/guide/faq](/guide/faq) 与 [/language/errors](/language/errors)。
