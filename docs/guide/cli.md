---
description: Code CIN 命令行完整参考：位置参数、执行路径、日志调试、性能资源、编译与 CROM、AOT 构建、退出码与环境变量。
---

# 命令行参考

唯一命令行入口是 Python 侧: 安装后的 `codecin`, 或源码树里的 `python cpu.py`。
两者参数表完全相同 (源码中由 `codecin/cli.py` 的 `build_parser()` 单一来源定义, 因此
`--help` 与本文档始终一致)。

```text
Usage:
  python cpu.py <program.[cin|pl|asm|bin]> [options]

Supported formats:
  .cin   CIN 高级语言 (函数/struct/数组/浮点/字符串)
  .pl    Code CIN 汇编语言 (PL 关键字风格)
  .asm   Code CIN 汇编
  .bin   UCBC 字节码 (由 --compile 生成)
```

```bash
codecin --help          # 彩色帮助 (intro + argparse 全量选项)
codecin --version       # Code CIN 5.5.0
```

## 位置参数

| 参数 | 说明 |
|------|------|
| `program` | 程序源文件或字节码: `.cin` / `.pl` / `.asm` / `.bin`。省略时打印错误并返回 1 |

## 执行路径

| 选项 | 默认 | 说明 |
|------|------|------|
| `--no-native` | 关闭 | 禁用 Go 原生库, 强制纯 Python 解释执行 |
| `--jit` | 关闭 | 启用 Python JIT (基本块动态编译); 与 `--debug` 互斥 (debug 优先) |
| `--no-jit` | — | 显式关闭 JIT (隐藏选项, 默认即关闭) |

## 日志与调试

| 选项 | 默认 | 说明 |
|------|------|------|
| `--debug` | 关闭 | 超详细 rich 追踪: CPU 初始化 dump、逐指令/寄存器/内存/栈/缓存/SYS 埋点 (隐含 DEBUG 日志级别) |
| `--step` | 关闭 | 交互式单步执行 (`step>` 命令集, 见 [交互式调试器](/tools/debugger)) |
| `--debug-server <PORT>` | 关闭 | 启动 TCP 远程调试服务, 由客户端驱动 step/continue/break/regs/mem/history |
| `--log-level <LVL>` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `--log-file <FILE>` | 无 | 日志写入文件 (终端仍显示) |
| `--sandbox` | 关闭 | 沙箱模式 (限制宿主访问) |
| `--no-io` | 关闭 | 禁止 `IN`/`OUT` 与宿主 I/O |

::: tip 日志与程序输出在同一流
日志默认输出到 **stdout**。需要只保留程序输出时用 `--log-level ERROR` (或 `CRITICAL`)。
详见 [日志与错误输出](/tools/logging)。
:::

## 性能与资源

| 选项 | 默认 | 说明 |
|------|------|------|
| `--profile` | 关闭 | 执行后输出 `Instruction Cycle Profile` 与 `Performance Counters` 两张统计表 |
| `--cache-size <N>` | `64` | 缓存行数 (最小 8) |
| `--cache-assoc <N>` | `4` | 缓存关联度 |
| `--mem-size <BYTES>` | `65536` | 虚拟机内存大小 (最小 256) |
| `--max-instructions <N>` | `100000000` | 指令数上限 (防死循环; 最小 1) |
| `--execution-interval <SEC>` | `0.0` | 每指令间隔秒数 (演示减速用) |

## 编译与 CROM

| 选项 | 默认 | 说明 |
|------|------|------|
| `--compile` | 关闭 | 编译为 `.bin` 字节码后继续执行 |
| `--compile-only` | 关闭 | 只编译为 `.bin`, 不执行 |
| `-o, --output <FILE>` | `<程序名>.bin` | 指定 `.bin` 字节码输出路径 (`--save` 的 `.crom` 路径固定为 `<程序名>.crom`, 不受此项影响) |
| `--crom <FILE>` | 无 | 加载指定 `.crom` 内存镜像 (仅对 `.pl` / `.asm` 生效; `.cin` / `.bin` 分支会忽略它, 并会自动探测同目录同名的 `<程序名>.crom`) |
| `--save` | 关闭 | 执行后保存 `<程序名>.crom` 内存镜像 |
| `--no-compress` | 压缩开启 | `.crom` 不压缩 (默认 zlib) |
| `--optimize <0-3>` | `0` | 优化级别 (越界自动裁剪到 0–3) |
| `--strict` | 关闭 | 严格汇编模式 |

## 运行时行为

| 选项 | 默认 | 说明 |
|------|------|------|
| `--seed <N>` | 随机 | 随机种子, 保证 `rand()` 序列确定 |
| `--bounds-check` | 关闭 | CIN 数组越界运行时检查 (会强制走解释执行) |
| `--mmu` | 关闭 | 启用 MMU 分页 (identity 页表; 未映射页触发缺页错误) |
| `--disasm` | 关闭 | 反汇编 `.bin` 为文本清单后退出 |

## AOT 构建

| 选项 | 说明 |
|------|------|
| `--build-exe [OUT]` | 编译成独立静态可执行文件后退出; 省略路径时输出到 `<程序名>[.exe]` |
| `--build-target <OS/ARCH>` | 交叉编译目标: `windows/amd64`、`windows/arm64`、`linux/amd64`、`linux/arm64`、`darwin/amd64`、`darwin/arm64` |
| `--build-keep-temp` | 保留 `go build` 临时目录, 便于排查构建失败 |

## 常用组合

::: tabs

== 日常开发

```bash
codecin prog.cin --log-level ERROR          # 干净输出
codecin prog.cin --debug                    # 逐指令追踪
codecin prog.cin --step                     # 交互式单步调试
codecin prog.cin --profile                  # 性能统计
```

== 汇编 / ISA 调试

```bash
codecin prog.asm --no-native --debug        # 汇编 + 逐指令追踪
codecin prog.asm --strict --log-level DEBUG # 严格汇编模式
codecin prog.bin --disasm                   # 反汇编查看
```

== 构建与产物

```bash
codecin prog.cin --compile-only -o prog.bin # 只编译
codecin prog.cin --compile                  # 编译并执行
codecin prog.cin --save --no-compress       # 未压缩内存镜像
codecin prog.cin --build-exe prog           # AOT 单文件
```

== 受控运行

```bash
codecin prog.cin --mem-size 262144          # 256 KiB 内存 (深递归)
codecin prog.cin --max-instructions 1000000 # 防死循环
codecin prog.cin --seed 42                  # 确定性随机
codecin prog.cin --mmu --bounds-check       # 分页 + 越界检查
codecin prog.cin --sandbox --no-io          # 限制宿主访问
```

:::

## 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 正常结束 (`HALT` / 程序自然结束), 或 `--help` / `--version` / `--compile-only` / `--build-exe` 成功 |
| `1` | 加载/汇编/编译/运行错误, 或 AOT 构建失败 (错误以 rich 红色面板打印) |
| `2` | 参数错误 (未知选项、非法枚举值等, 由 argparse 输出 usage) |

```text
┌──────────────────────── Load Error ────────────────────────┐
│ prog.cin:12: Compiler error: Unknown function: printline   │
└────────────────────────────────────────────────────────────┘
```

## 环境变量

| 变量 | 作用 |
|------|------|
| `CODECIN_NATIVE_LIB` | 显式指定原生库路径 (优先于自动查找) |
| `CODECIN_SKIP_NATIVE=1` | 安装时跳过原生库编译 |
| `CODECIN_NO_GO=1` | 安装时跳过 Go 原生构建 |
| `CODECIN_FORCE_REBUILD=0` | 安装时不强制重建原生库 (默认重建) |
| `CODECIN_STATIC` | 原生库构建的链接模式: `auto`(默认, 失败回退动态) / `1`(强制静态) / `0`(动态) |
| `CODECIN_AOT_TESTS=1` | 允许 AOT 交叉编译测试用例参与测试 |

## 还可以用 Python API 控制更多

有些运行配置没有对应的命令行开关 (栈槽数、单次执行超时、显示字节数等), 可以在嵌入
使用 `Config` 时直接设置:

```python
from codecin import CPU, Config

cfg = Config()
cfg.mem_size = 256 * 1024
cfg.stack_size = 4096
cfg.use_native = False
cfg.log_level = 'WARNING'

cpu = CPU(cfg, 'prog.cin')
cpu.run()
```

字段含义见 [Python 嵌入 API](/reference/python-api)。
