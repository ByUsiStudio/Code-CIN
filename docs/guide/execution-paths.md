---
description: Code CIN 的三条执行路径（纯 Python 解释器 / JIT 基本块编译 / Go 原生 VM）的选择规则、能力矩阵、回退顺序与一致性保证。
---

# 执行路径

同一个 UCBC 字节码可以被三种引擎执行: **纯 Python 解释器**、**Python JIT (基本块编译)**、
**Go 原生 VM (c-shared)**。三条路径对同一程序必须给出相同结果, 这是项目的核心约束之一。

## 三条路径一览

| 路径 | 实现 | 启用方式 | 特点 |
|------|------|----------|------|
| 解释执行 | `codecin/cpu.py` 逐指令 dispatch | 默认 (未启用原生/JIT 时) 或 `--no-native` | 支持全部 `--debug` / `--step` / MMU / 越界检查, 速度最慢 |
| JIT | `codecin/jit.py` 把基本块编译成 Python 代码并缓存 | `--jit` (通常配合 `--no-native`) | 热点基本块跳过 dispatch 开销, 与 `--debug` 互斥 |
| Go 原生 | `codecin/native.py` 经 ctypes 调用 c-shared 库里的字节码 VM | 默认优先 (原生库可用时) | 整程序一次交给原生 VM, 速度最快, 也是宿主能力的唯一实现 |

::: tabs

== 解释执行

```bash
codecin prog.cin --no-native
codecin prog.cin --no-native --debug        # 逐指令追踪
```

== JIT

```bash
codecin prog.cin --jit --no-native
codecin prog.cin --jit --no-native --profile
```

== Go 原生

```bash
codecin prog.cin                            # 默认优先使用原生库
python -c "from codecin import native; print(native.get_engine())"   # 确认是否可用
```

:::

## 选择规则与回退顺序

实际的路径选择逻辑在 `codecin/cpu.py: CPU.run()`:

```text
原生 VM  (config.use_native 且 非 --debug 且 非 --step 且 非 --bounds-check 且 非 --mmu)
   │ 不可用 / 加载失败 / 运行返回 unsupported
   ▼
JIT      (config.enable_jit 且 非 --debug 且 非 --step)
   │ 未启用
   ▼
解释执行 (兜底, 永远可用)
```

也就是说:

- `--debug`、`--step`、`--bounds-check`、`--mmu` **任意一个开启都会关闭原生路径**
  (调试需要逐指令介入, 越界检查与分页需要在 Python 内存模型里生效);
- `--debug` 与 `--jit` 同时给出时 **debug 优先**, JIT 被忽略;
- 原生库缺失、架构不符或 ABI 不匹配时, 记一条 warning 后自动回退, 不会让运行失败;
- 原生 VM 遇到未实现的指令会返回 `StatusUnsupported`, Python 侧同样回退到解释执行。

::: tip 怎么确认当前走了哪条路径
加 `--log-level DEBUG` (或 `--debug`), 初始化 dump 会打印 `native` 与 `jit` 的实际取值:

```text
DEBUG   CPU 初始化
        memory    0x10000 bytes
        cache     64 lines x 4-way
        sp_init   0xfff8
        heap_base 0x8000
        native    False
        jit       False
        log_level DEBUG
```
:::

## 能力矩阵

| 能力 | 解释执行 | JIT | Go 原生 |
|------|:--------:|:---:|:-------:|
| 执行任意 CIN / PL / ASM 程序 | ✅ | ✅ | ✅ |
| `--debug` 逐指令 / 内存 / 栈 / 缓存追踪 | ✅ | ❌ (互斥) | ❌ (会关闭原生) |
| `--step` 交互式单步与断点 | ✅ | ❌ | ❌ |
| `--debug-server` 远程调试 | ✅ | ❌ | ❌ |
| `--profile` 性能统计 | ✅ | ✅ | ✅ |
| `--bounds-check` CIN 数组越界检查 | ✅ | — | ❌ (会关闭原生) |
| `--mmu` 分页 / 缺页 | ✅ | — | ❌ (会关闭原生) |
| 宿主能力: 画布 / 音频 / 文件 / 进程 / Termux | ❌ | ❌ | ✅ (唯一实现) |
| 运行 AOT 产物 (独立可执行文件) | — | — | ✅ (产物内置 Go VM) |
| 相对速度 (示意) | 1× | ~4× | ~8× |

::: warning 宿主能力必须走原生路径
纯 Python 路径下调用 `file_*` / `exec` / `canvas` / `audio_*` / `termux_*` 会直接报错并
以退出码 1 结束:

```text
08:42:21 ERROR    Execution error: host builtins (GUI/audio/system/Termux)
                  require the native Go runtime (run without --no-native)
+------------------------------ Execution Error ------------------------------+
| host builtins (GUI/audio/system/Termux) require the native Go runtime (run  |
| without --no-native)                                                        |
+-----------------------------------------------------------------------------+
```
:::

## 一致性保证

三路径一致性由仓库内脚本与测试共同把关:

```bash
python script/check_paths.py            # 对示例程序逐字节比较三条路径的 stdout
python -m pytest tests/test_three_paths.py
```

允许的差异只有两类:

1. 与时间/环境相关的输出 (`time()`、`cwd()`、主机名等);
2. 超越函数在不同 libm 实现下的末位舍入差异 (Python `math` 与 Go `math`, 通常 ≤ 1 ulp)。

除此外, 指令计数、内存终态、寄存器快照、打印内容都必须一致。这也是为什么
**不要用 `--debug`/`--jit` 的结果去推断性能结论** —— 它们只是同一语义的不同实现。

## 怎么选

| 场景 | 建议 |
|------|------|
| 日常开发、跑示例 | 默认 (Go 原生优先) |
| 排查语义 / 崩溃点 | `--no-native --debug` |
| 交互式定位逻辑错误 | `--no-native --step` |
| 需要数组越界检查 | `--no-native --bounds-check` |
| 需要分页/缺页演示 | `--no-native --mmu` |
| 没有 Go 工具链, 但想快一点 | `--jit --no-native` |
| 性能基线 | 不加开关 (原生), 配合 `--profile` |
| 交付给别人运行 | `--build-exe` (见 [AOT](/runtime/aot)) |

## 相关页面

- [Go 原生运行时](/runtime/native) — 原生库的加载、查找顺序与宿主能力边界
- [JIT 编译](/runtime/jit) — 基本块编译与统计
- [性能分析](/tools/profiling) — `--profile` 指标解读
- [日志与错误输出](/tools/logging) — 路径选择与 warning 在哪里出现
