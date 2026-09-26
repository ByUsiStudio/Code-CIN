---
description: Code CIN 常见问题与故障排查：安装、原生库、语言语义、执行路径、调试日志、产物与交付。
---

# 常见问题 (FAQ)

## 安装与运行

### `pip install codecin==5.5.0` 装完没有原生库, 正常吗?

正常。发行包是 sdist, 安装阶段会尝试用**本机 Go 工具链**现场编译原生库; 机器上没有 Go/cgo
时会跳过编译并打印提示, 安装依然成功, 运行时会回退纯 Python 解释执行。

想让安装时编译: 装好 Go 1.26+ 与 C 编译器后重装即可; 不想装 Go: 从 Release 下载对应
平台的预编译库放进包目录 (见 [安装 Code CIN](/guide/installation))。

### 怎么确认原生库到底加载了没有?

```bash
python -c "from codecin import native; print(native.get_engine())"
```

输出非 `None` 即加载成功。也可以在运行时加 `--log-level DEBUG`, 初始化 dump 会打印
`native` 字段的实际取值。想手动指定库路径可以用环境变量 `CODECIN_NATIVE_LIB`。

### `codecin: command not found` / 不是内部或外部命令

console script 的目录不在 `PATH` 里。三种替代写法:

```bash
python -m codecin.cli prog.cin     # 模块方式
python cpu.py prog.cin             # 源码树方式
python -c "from codecin.cli import main; raise SystemExit(main(['prog.cin']))"
```

### 支持哪些平台? Termux 能用吗?

Windows / Linux / macOS 全平台 (原生库为 c-shared, 构建脚本见
[编译 Go 原生库](/dev/build-native))。Android Termux 支持 `pkg install golang` 后现场编译,
Termux:API 应用 + `pkg install termux-api` 后可用 `termux_*` 宿主能力。

## 语言与语义

### 为什么 `1 / 2` 得到 `0.5` 而不是 `0`?

CIN 的 `/` **恒为浮点除法**。需要整数除法时用内建 `idiv(a, b)` (向零截断):

```c
int q = idiv(17, 5)      // 3
int r = idiv(-17, 5)     // -3
```

取模 `%` 仅支持整数, 对浮点使用会报 `Float modulo not supported`。见
[运算符](/language/operators) 与 [内建函数](/language/builtins)。

### 数组越界为什么不报错?

默认不做运行时越界检查 (与 C 一致)。需要检查时加 `--bounds-check` (会强制走解释执行):

```bash
codecin prog.cin --no-native --bounds-check
```

### 递归报 `Stack overflow` 怎么办?

默认内存 64 KiB, 栈区约 1024 槽。加大内存即可:

```bash
codecin prog.cin --mem-size 262144      # 256 KiB
```

同时可以把 `--max-instructions` 调大 (默认 1 亿) 以避免长循环被上限截断。

### 字符串能原地修改吗?

不能。字符串是 NUL 结尾的只读字节序列, `s[i]` 只能读单字节 (`0..255`),
拼接/`strcpy` 会产生新的堆块。见 [字符串](/language/strings)。

### 可以取地址 / 解引用吗?

不可以。`*` 只是乘法、`&` 只是位与; “引用语义”只通过数组与 struct 传参隐式获得。
见 [限制与常见错误](/language/errors)。

### `switch` 的 `case` 会自动跳出吗?

不会, 默认贯穿 (fallthrough, 与 C 一致), 需要用 `break` 跳出整个 `switch`。
见 [控制流](/language/control-flow)。

## 执行路径与性能

### 我的程序到底跑在哪条路径上?

选择顺序: 原生 (默认优先) → JIT (`--jit`) → 解释 (兜底)。`--debug`、`--step`、
`--bounds-check`、`--mmu` 任一开启都会关闭原生路径; `--debug` 与 `--jit` 同时给出时
debug 优先。用 `--log-level DEBUG` 看初始化 dump 里的 `native` / `jit` 取值即可确认。
详见 [执行路径](/guide/execution-paths)。

### 没有 Go 工具链, 怎么让程序跑快一点?

```bash
codecin prog.cin --jit --no-native     # 基本块 JIT (不能与 --debug 同用)
```

或者下载预编译原生库放进包目录, 用默认路径获得最大加速。

### 为什么 `--debug` 下程序明显变慢?

逐指令追踪、内存读写日志、缓存埋点都会真实产生开销, 这是预期行为。做性能测试时
不要开 `--debug`, 用默认路径配合 `--profile` 才是有意义的数字。

### 三条路径结果会不一样吗?

除时间/环境类输出与超越函数末位舍入 (Python 与 Go 的 libm 差异, 通常 ≤ 1 ulp) 外,
三路径必须完全一致, 由 `script/check_paths.py` 与 `tests/test_three_paths.py` 把关。

### 为什么 `--profile` 里 `Cycles` / `IPC` 是 0?

部分统计只在特定路径/实现下才有计数。纯 Python 路径下 `Cycles` 可能为 0,
`IPC` 也随之显示 `0.00`; 这属于统计实现差异, 不代表程序没执行 (可看 `Instructions` 字段)。
见 [性能分析](/tools/profiling)。

## 调试与日志

### 程序输出里混着 `INFO CIN compiled: ...` 怎么办?

日志默认输出到 stdout, 与程序输出同一个流。要只保留程序输出:

```bash
codecin prog.cin --log-level ERROR
```

需要完整落盘时用 `--log-file`:

```bash
codecin prog.cin --log-level DEBUG --log-file codecin.log
```

### 怎么定位运行期崩溃的位置?

```bash
codecin prog.cin --no-native --debug      # 逐指令追踪, 看最后一条 PC 与寄存器
codecin prog.cin --no-native --step       # 交互式单步: b <地址> 设断点, p regs, p mem <地址>
```

`--debug` 会打印 `PC=0x0004 #00000002 ADD X1=0x0(0) X2=0x1(1) SP=0xfff8` 这样的逐指令行,
以及 `MEM WR/RD` 内存访问与 `N/Z/C/V` 标志。

### 调试器命令有哪些? / 能给 IDE 接调试吗?

交互式命令见 [交互式调试器](/tools/debugger); 需要程序化驱动 (编辑器/IDE 集成) 时用
`--debug-server <port>` 的换行文本协议, 见 [远程调试协议](/tools/remote-debug)。

### `Ctrl+C` 中断会怎样?

终端收到 `KeyboardInterrupt` 后打印 `User interrupt` 并结束本次执行 (缓存会被 flush,
内存镜像若开了 `--save` 也会尽量保存)。

## 宿主能力

### 报错 `host builtins ... require the native Go runtime` 是什么情况?

说明当前走的是纯 Python 路径 (例如加了 `--no-native`), 而 `file_*` / `exec` /
`canvas` / `audio_*` / `termux_*` 这类宿主能力**只有 Go 原生实现**。去掉 `--no-native`
并确保原生库可用即可, 退出码为 1。

### 音频/画布在无桌面环境能用吗?

画布本身是纯计算 + 导出 PNG, 可以在无桌面环境使用; `show_canvas()` 需要系统查看器。
音频仅支持 WAV(PCM), 播放依赖各平台后端 (Windows `winmm`, 其它平台 `afplay`/`aplay`/
`paplay`/`ffplay`)。

### `exec` / `file_write` 安全吗?

它们是真实的宿主调用, 具备当前进程的文件与进程权限。运行不可信程序时用
`--sandbox` 与 `--no-io` 限制宿主访问。

## 产物与交付

### `.bin` 和 `.crom` 有什么区别?

| 项 | `.bin` | `.crom` |
|----|--------|---------|
| 内容 | UCBC 字节码 (代码) | 虚拟机内存镜像 (整机状态快照) |
| 生成 | `--compile` / `--compile-only` | `--save` |
| 用途 | 分发/复用编译结果, 可直接运行 | 保存/恢复内存状态 (可选 zlib 压缩 + CRC32) |
| 通用性 | 只有 VM 能执行 | 不能当可执行文件用; `--crom` 仅在 `.pl` / `.asm` 路径生效 |

细节见 [二进制格式](/runtime/formats)。

### 怎么把程序交付给没装 Python 的人?

```bash
codecin prog.cin --build-exe prog                       # 本机平台
codecin prog.cin --build-exe prog --build-target linux/amd64
```

产物是静态链接的单文件, 内嵌字节码与初始内存, 不依赖 Python、Go、libc 或任何动态库。
需要 Go 工具链与源码树 (不适用于已安装的 wheel)。见 [AOT](/runtime/aot)。

### 环境变量有哪些?

`CODECIN_NATIVE_LIB`、`CODECIN_SKIP_NATIVE`、`CODECIN_NO_GO`、`CODECIN_FORCE_REBUILD`、
`CODECIN_STATIC`、`CODECIN_AOT_TESTS`, 完整说明见 [命令行参考 · 环境变量](/guide/cli#环境变量)。

## 开发与文档

### Go 侧为什么没有 `codecin` 命令?

从 5.5.0 起 Go 是语言实现的唯一核心, 但**只以库的形式存在** (c-shared), 唯一命令行入口
是 Python 侧, 避免了两套 CLI / 两套语义的问题。见 [项目结构](/dev/structure)。

### 怎么新增一条指令或系统调用?

需要同步改动 ISA、解释器、JIT、Go VM、汇编器、CIN 内建, 并重新生成常量后回归测试, 步骤见
[扩展指令 / 系统调用](/dev/extend)。

### 怎么改文档 / 本地预览这个文档站?

```bash
cd docs
npm install
npm run docs:dev        # 开发预览 (默认 http://localhost:5173)
npm run docs:build      # 产出静态站点到 .vitepress/dist
npm run docs:preview    # 预览构建产物
```

文档站是独立仓库 (Code-CIN-Docs), 以 git submodule 内嵌于主仓库的 `docs/`,
写作规范与目录约定见 [贡献指南](/dev/contributing)。
