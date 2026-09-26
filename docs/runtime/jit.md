---
description: Code CIN 的 Python JIT：--jit 开关、基本块动态编译与 exec 缓存、统计指标、与 --debug 的优先级及适用场景。
---

# JIT 编译

`--jit` 打开 Code CIN 的 **Python 级 JIT**：把无分支的**基本块**（basic block）动态翻译成 Python 函数并缓存起来，之后每次执行到该块起点就直接调用这个函数，省掉逐条取指与分派的开销。

它是纯 Python 实现（`codecin/jit.py`），不生成机器码，也不需要 Go 原生库。默认关闭。

## 启用与优先级

```bash
python cpu.py program.cin --jit              # JIT（原生路径会被跳过）
python cpu.py program.cin --jit --no-native  # 显式声明只用 Python，行为相同
```

| 相关开关 | 作用 |
| --- | --- |
| `--jit` | 启用 JIT（`config.enable_jit = True`） |
| `--no-jit` | 关闭 JIT；与 `--jit` 同时给出时**以 `--no-jit` 为准**（`enable_jit = ns.enable_jit and not ns.disable_jit`）。该开关在 `--help` 里被隐藏（`argparse.SUPPRESS`） |
| `--no-native` | 禁用 Go 原生库；对 JIT 只是"再确认一次" |
| `--debug` / `--step` | **优先级高于 `--jit`**，见下 |

三条硬规则（源码 `codecin/cpu.py`）：

1. **JIT 与原生路径互斥**。`_try_native_run()` 在 `config.enable_jit` 为真时直接返回 `None`。因此 `--jit` 单独使用也不会走 Go 原生库，效果等价于 `--jit --no-native`。
2. **debug / step 优先**。只有当 `not config.debug_mode and not config.step_mode` 时才会构造 `JITCompiler`。注意配置日志里的 `jit` 字段仍是 `True`（那是 CLI 解析结果），但 `cpu.jit` 实际为 `None`：

   ```text
   debug= False  config.enable_jit= True  cpu.jit= JITCompiler
   debug= True   config.enable_jit= True  cpu.jit= NoneType
   ```

3. **JIT 会退出紧凑解释快路径**。`_can_use_fast_path()` 要求 `self.jit is None`；启用 JIT 后走带 JIT 尝试的解释循环，因此 `--jit` 是"用块级编译换取逐指令开销"，而不是叠加两条加速。

::: warning 需要调试就别开 JIT

`--debug` / `--step` 会静默地不启用 JIT（不会报错）。如果你同时在命令行写了 `--jit`，看到的是解释执行的行为——这是刻意的优先级，不是 bug。

:::

## 基本块是怎么编译的

编译单元从当前 `pc` 开始，向下**线性扫描**，遇到下列任一情况立即结束块：

- 指令不在可编译白名单里；
- 指令是控制流（`Constants.BRANCH_OPS`，另加 `HALT`、`SYS`、`IN`、`OUT`）；
- 代码生成器对该指令形态放弃（例如把内存操作数当值用）。

块长度还有硬上限 **32 条指令**（`min(start + 32, len(instructions))`）。控制流指令本身**不进入块**，它让块在它之前结束；执行完块后 `cpu.pc = end`，下一条正好是那条分支。

可编译白名单（`codecin/jit.py` 的 `_JIT_OPS`）：

| 类别 | 指令 |
| --- | --- |
| 数据传送 | `MOV`、`NOP`、`MVN` |
| 算术/逻辑 | `ADD`、`SUB`、`MUL`、`DIV`、`AND`、`OR`、`XOR`、`SHL`、`SHR`、`INC`、`DEC` |
| 立即数形式 | `ADDI`、`XORI`、`ORI`、`ANDI`、`LSL`、`LSR` |
| 访存 | `LDR`、`STR`、`LOAD`、`STORE`、`LB`、`LH`、`LW`、`LD`、`SB`、`SH`、`SW`、`SD` |
| 栈 | `PUSH`、`POP` |

生成的目标代码是一段普通 Python 源码，再交给 `exec` 编译：

```python
def block(cpu, mem, R):
    MASK = 0xFFFFFFFFFFFFFFFF
    # 每条指令翻译出的赋值/读写语句
    return None
```

编译通过 `compile(source, '<jit-block>', 'exec')` 执行，命名空间里只放 `ExecutionError`（供 `DIV` 的除零路径抛出）。几个保证正确性的翻译细节：

- 寄存器 `31` 按零寄存器处理：读为常量 `0`，写被丢弃；
- 寄存器 `32`（`SP_REG`）映射到 `cpu.sp`，每次写入都 `& MASK` 截断；
- `SHR` / `LSR` 先 `& MASK` 再移位，位移量 `& 63`，与解释器的无符号语义一致；
- `DIV` 按**有符号、向零截断**翻译，并显式检查除数为 0（抛 `ExecutionError("Division by zero")`）；
- `LB`/`LH` 读回后做符号扩展；`STR`/`SW` 写回时按位宽掩码。

### 缓存与回退

- 每个块起点 `pc` 对应一个已编译函数，`compile_block()` 命中缓存时只累加 `cache_hits` 并返回块结束 PC——**不会重复编译**。
- 块执行抛异常时（`try_step` 内捕获），**丢弃该块**并返回 `None`，主循环于是对这一条指令回退解释执行。除零这类错误因此仍由解释器抛出，异常语义与纯解释路径一致。
- 当前 `pc` 落在不可编译指令上、或超出指令范围，都返回"不适用"，同样回退解释执行；`pc` 越界时返回 `False`，主循环打印 `Program ended (JIT)` 结束。

## JIT 统计指标

`JITCompiler.get_stats()` 返回五个字段：

| 字段 | 含义 |
| --- | --- |
| `blocks_compiled` | 累计编译的基本块数量 |
| `cached_blocks` | 当前缓存里的块数量（执行失败的块会被剔除） |
| `total_calls` | JIT 分发尝试总次数 |
| `cache_hits` | 块起点命中缓存的次数 |
| `hit_rate` | `cache_hits / total_calls`（无调用时为 `0.0`） |

两个读取方式：

::: tabs

== 日志（DEBUG）

```bash
python cpu.py program.cin --jit --log-level DEBUG
```

```text
JIT compiler enabled
JIT compiled block 0x2-0x5 (3 instrs, total 1)
JIT block source:
    def block(cpu, mem, R):
        ...
JIT cache hit: block @0x2 (hits=1)
```

== Python API

```python
from codecin.config import Config
from codecin.cpu import CPU

cfg = Config()
cfg.enable_jit = True
cfg.use_native = False

cpu = CPU(cfg, "examples/control_flow.cin")
cpu.run()
print(cpu.jit.get_stats())
```

```text
{'blocks_compiled': 25, 'cached_blocks': 25, 'total_calls': 160, 'cache_hits': 30, 'hit_rate': 0.1875}
```

:::

`codecin/stats.py` 的汇总表本身支持 JIT 行（`JIT Hit Rate` 百分比与 `JIT Blocks Compiled`），但当前 CLI 的 `--profile` 汇总调用没有把 `jit_stats` 传进去，所以**命令行不会打印这两行**；要精确读数请用上面的 Python API，或看 `--log-level DEBUG` 的编译/命中日志。

## 什么时候值得开

| 场景 | 建议 |
| --- | --- |
| 长循环、热路径是连续直线代码（数值累加、数组遍历、字符串/内存搬运） | **值得**：块被反复命中，编译开销被摊薄 |
| 分支密集、循环体极短（块只有 1–2 条指令就以分支结束） | 收益有限：块太短，命中率低，JIT 分发本身有开销 |
| 只跑几十条指令的一次性脚本 | 不值得：编译 + `exec` 的开销大于收益 |
| 需要 `--debug` / `--step` / 精确逐指令观测 | 不要开：会被 debug/step 优先覆盖 |
| 原生库可用 | 直接用原生路径；JIT 只适合"必须纯 Python"的场合 |

`--jit --no-native` 与解释执行的对照示例（本机 Windows/x64，`for` 循环 300000 次累加，`--profile --log-level ERROR`）：

```bash
python cpu.py bench.cin --no-native --profile
python cpu.py bench.cin --jit --no-native --profile
```

```text
解释执行                     Execution Time 77.7835s    115,706 instr/s
JIT (--jit --no-native)      Execution Time 30.7369s    292,809 instr/s
```

两者输出完全一致（`sum=45000150000`）；本例约 2.5 倍加速，量级随程序结构变化，请以本机实测为准。同一程序走 Go 原生路径是 `0.5044s`，所以 **JIT 是"没有原生库时的次优解"，不是原生的替代品**（见 [Go 原生运行时](/runtime/native)）。

## 注意事项

::: warning 正确性优先于速度

JIT 块与解释器必须语义一致——包括零寄存器、`SP` 截断、有符号除法、移位掩码、符号扩展、栈增长方向。任何一条翻译错都会让同一程序在两条路径上给出不同结果，这是不可接受的缺陷。三路径一致性由 `script/check_paths.py` 与 `tests/test_three_paths.py`、`tests/test_paths_consistency.py` 覆盖。

:::

::: details 为什么块最大只有 32 条指令

块越长，单次调用的收益越大，但一旦块内出现需要回退的形态，浪费的编译工作也越多；同时 Python 函数体的长度会直接放大 `exec` 的编译成本。32 是"收益/开销"的折中值，写死在 `compile_block()` 里。

:::

::: details 编译失败的块会怎样

`exec` 阶段抛异常时只记一条 `JIT block compile failed at <pc>: <e>` 的 DEBUG 日志并返回"不适用"，该 PC 由解释器执行，程序不会因此失败。执行期抛异常的块则从缓存里删除，下次重新编译。

:::

## 相关页面

- [执行路径](/guide/execution-paths)——三条路径的语义一致性保证
- [Go 原生运行时](/runtime/native)——默认加速路径与宿主能力
- [性能分析](/tools/profiling)——`--profile` 统计表读法
- [交互式调试器](/tools/debugger)——为什么调试会禁用 JIT
- [常见问题 (FAQ)](/guide/faq)
