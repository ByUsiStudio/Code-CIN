---
description: Code CIN 的 Go 原生运行时：共享库导出符号、ctypes 加载顺序、宿主能力边界与三条执行路径的定位。
---

# Go 原生运行时

Go 原生运行时是 Code CIN 的**加速与宿主能力层**：它把 CIN 编译器、字节码 VM、CROM 打包与 AOT 产物运行时实现成纯 Go 代码，编译为一个 `c-shared` 共享库，由 Python 侧用 `ctypes` 载入。本页说明它的架构定位、导出符号、加载顺序、回退语义与能力边界。

## 架构定位：Go 是语言实现，Python 只是 CLI 外壳

不要把 Go 原生库理解成"一个可选插件"。在 5.5.0 里，语言实现的核心在 Go 侧：

| 层 | 落地文件 | 职责 |
| --- | --- | --- |
| CLI 外壳 | `codecin/cli.py`、`cpu.py` | 参数解析、日志、退出码、编排 |
| 前端（Python） | `codecin/cin.py`、`codecin/assembler.py` | CIN / PL / ASM 源码 → UCBC 字节码 |
| 语言实现核心（Go） | `codecin/native/compiler/`、`codecin/native/engine/` | CIN 编译器、字节码 VM、CROM 编解码 |
| AOT 运行时（Go） | `codecin/native/aot/` | 独立可执行文件内嵌的 VM 入口 |
| ABI 桥 | `codecin/native.py` | `ctypes` 载入共享库、编解码、结果解析 |

**Go 侧不提供任何 CLI**。`codecin/native/` 下唯一允许的 `package main` 是 cgo 的 c-shared 库入口 `codecin/native/main.go`，它只导出 C ABI 符号并提供一个空的 `main()`；`tests/test_no_go_cli.py` 与 CI 都会断言"Go 源码树里没有第二个 `package main`"。唯一的命令行入口是 Python 侧：

```bash
python cpu.py program.cin      # 源码树
codecin program.cin            # pip 安装后的 console script
```

这样设计是为了避免历史上"两套 CLI、两套语义、产物不对应"的问题。AOT（`--build-exe`）产出的是**用户程序的产物**，它自带 Go VM，但不是工具链 CLI。

### 一次原生执行的数据流

```text
program.cin ──codecin/cin.py──► instructions + labels + data_writes
   ──encode_program()──► UCBC bytecode ──┐
   ──memory snapshot──► 初始内存镜像 ────┼─► codecin_run(...)
                                        │   （Go: engine.Run 整程序一次执行）
                                        ▼
        status/pc/sp/heap/steps/regs/vec/mem/output/error  ← 单个 C 缓冲
```

## 导出的 C ABI 符号表

符号定义在 `codecin/native/main.go`（`//export` 注释即导出名），Python 侧的签名声明在 `codecin/native.py` 的 `NativeEngine._configure()`，两边必须一致。

| 符号 | 参数 | 返回值 / 作用 |
| --- | --- | --- |
| `codecin_run` | `bytecode ptr,len`、`mem ptr,len`、`entry i64`、`sp i64`、`heap_base i64`、`input ptr,len`、`max_steps i64` | 执行整程序，返回**结果缓冲指针**（`NULL` 表示分配失败） |
| `codecin_free` | `ptr` | 释放任意由上面接口返回的缓冲；`NULL` 安全 |
| `codecin_crom_pack` | `data ptr,len`、`compress int`、`out_len *int` | 打包为 CROM 字节流，返回缓冲指针，长度写回 `out_len` |
| `codecin_crom_unpack` | `data ptr,len`、`mem_len *int`、`flags *int` | 校验收包，返回载荷指针与长度/标志；失败返回 `NULL` |
| `codecin_version` | 无 | `const char*`，内容形如 `codecin-native 5.5.0 (Go)` |

`codecin_version` 返回的指针是**进程级常量**（`sync.Once` 内 `C.CString`），调用方不要 `free`；Python 侧以 `c_char_p` 取值，不做释放。

### 结果缓冲布局（与 Python 严格一致）

`codecin_run` 返回的是一段紧凑的小端缓冲，`codecin/native.py:_parse_result()` 按同一偏移解析：

```text
offset 0   status      u8   (+3B padding)
offset 4   pc          u64
offset 12  sp          u64
offset 20  heap_ptr    u64
offset 28  steps       u64
offset 36  regs        33 × u64      (32 通用 + SP)
offset 300 vec_regs    32 × 4 × f64
offset 1324 mem_len    u64 | mem ...
           out_len     u64 | out ...
           err_len     u16 | err ...
```

状态码语义：

| `status` | 名称 | Python 侧行为 |
| --- | --- | --- |
| 0 | OK | 继续/正常结束，`halted=true` |
| 1 | Done | 正常停机（HALT） |
| 2 | Unsupported | **同步状态后回退解释执行**（从当前 PC 继续） |
| 3 | Error | 抛出 `ExecutionError`，错误文本取自 `err` 字段 |

两点加固行为值得记住：Go 侧 `recover()` 把 panic 降级为一条 `status=3`、`err="native panic: ..."` 的结果，**绝不让 panic 跨越 CGO 边界**终止宿主进程；步数用尽是错误而不是"正常结束"，错误文本为 `instruction limit reached (N steps)`（`--max-instructions`，默认 `100000000`）。

## ctypes 加载与候选路径查找顺序

`codecin/native.py:get_engine(logger)` 会按顺序尝试候选路径，命中即缓存（模块级 `_LIB_CACHE`），因此同一进程只加载一次。

候选名由平台决定：

| 平台 | 通用名（canonical） | 架构专属名（specific） |
| --- | --- | --- |
| Windows | `codecin_native.dll` | `codecin_native-windows-x64.dll` |
| Linux / Termux | `libcodecin_native.so`、`codecin_native.so` | `libcodecin_native-linux-arm64.so` |
| macOS | `libcodecin_native.dylib`、`codecin_native.dylib` | `libcodecin_native-macos-arm64.dylib` |

其中 `os` 段取 `windows` / `macos` / `linux`，`arch` 段把 `x86_64`/`amd64` 归一为 `x64`，`aarch64`/`arm64` 归一为 `arm64`，`i386`/`i686`/`x86` 归一为 `x86`。

查找顺序（**架构专属名排在通用名之前**，目录顺序其次）：

1. 环境变量 `CODECIN_NATIVE_LIB` 指定的路径；
2. `codecin/native/<架构专属名>`；
3. `codecin/native/<通用名>`；
4. `codecin/<架构专属名>`；
5. `codecin/<通用名>`；
6. 若被 PyInstaller 打包（`sys.frozen`）：`sys._MEIPASS/<名>` 与可执行文件所在目录下的 `<名>`。

本机（Windows / x64）实测候选序列的前两项即 `codecin\native\codecin_native-windows-x64.dll`（架构专属名）与 `codecin\native\codecin_native.dll`（通用名），随后才是 `codecin\` 下的同名两项。

> 为什么顺序不能反：Release 资产按 `libcodecin_native-<os>-<arch>.<ext>` 命名，同一个目录里可能同时躺着两种架构的库。先命中本机架构的那一个，否则会 `dlopen` 到一个"能加载但架构不对/符号不对"的库。`tests/test_native_lib_lookup.py` 专门断言了这条顺序。

### 加载失败与纯 Python 回退

候选逐个 `ctypes.CDLL` 尝试，捕获两类异常后**继续尝试下一个候选**，而不是让整个运行炸掉：`OSError`（架构不符、依赖缺失、根本不是动态库）与 `AttributeError`（能加载但缺导出符号或 ABI 版本不符，典型是旁边放着一个旧版本的库）。全部失败时记一条 warning 并返回 `None`：

```text
原生库不可用, 回退纯 Python 解释执行: <path> (<err>)
```

调用方（`codecin/cpu.py`）拿到 `None` 后自动走纯 Python 解释执行，**程序照常运行**，只是慢，并且宿主能力不可用。想复现纯 Python 行为，显式加 `--no-native`。

## 构建与验证

原生库是构建产物，不进发行包（`pyproject.toml` 的 `package-data` 与 `MANIFEST.in` 都刻意不含 `.dll/.so/.dylib`）。构建需要 Go 1.26+（`codecin/native/go.mod`），Windows 还需要一个 cgo 可用的 C 编译器。

::: tabs

== Windows

```powershell
cd codecin\native
.\build.ps1
```

产物：`codecin\codecin_native.dll`（附带生成的 `.h` 会被脚本删除）。

== Linux

```bash
sudo apt install golang gcc
cd codecin/native
sh build.sh
```

产物：`codecin/libcodecin_native.so`。Termux 同法（`pkg install golang` 后 `sh build.sh`），产物为 Android/arm64 版 `.so`。

== macOS

```bash
xcode-select --install
brew install go
cd codecin/native
sh build.sh
```

产物：`codecin/libcodecin_native.dylib`。

:::

等价的裸命令是在 `codecin/native/` 下执行 `go build -buildmode=c-shared -o ../codecin_native.dll .`。构建脚本默认先尝试用 `-linkmode external -extldflags -static` 静态链入 C 运行时，失败时回退动态链接；macOS 不支持共享库完全静态链接，始终动态。

### 验证命令

```bash
python -c "from codecin import native; print(native.get_engine())"
```

打印出对象即加载成功，例如 `NativeEngine`；打印 `None` 表示回退纯 Python。第二条自查：

```bash
python -c "from codecin import native; e = native.get_engine(); print(e.version() if e else None)"
```

输出的版本串来自**库自身的构建注入**（`codecin_version`）。若它与 `codecin --version` 不一致，说明你目录里放的是旧库——此时 `get_engine()` 通常仍会成功，但请重建以免格式/符号漂移。

## 何时不会走原生路径

`codecin/cpu.py` 在 `run()` 与 `_try_native_run()` 里显式排除若干情况。命中任意一条即回退 Python：

| 条件 | 原因 |
| --- | --- |
| `--no-native` | 用户强制纯 Python |
| `--debug` | 需要逐指令追踪、寄存器/内存/栈/缓存转储 |
| `--step` | 交互式单步 |
| `--jit` | JIT 与原生互斥；`--jit` 单独使用也会跳过原生路径 |
| `--bounds-check` | CIN 数组越界检查由解释器实现 |
| `--mmu` | 分页/缺页语义由 Python 侧实现 |
| `--debug-server <port>` | 远程驱动式调试不复用原生/单步会话 |

另外，即使原生库在场，遇到它未实现的指令会返回 `status=2`：Python 侧先同步寄存器/栈/内存状态，再从当前 PC 继续解释执行，对用户是透明的。

## 宿主能力：只有原生路径可用

音频、画布、文件、进程与 Termux 相关 SYS 调用**只在 Go 原生路径实现**。纯 Python 解释器遇到它们会直接抛错：

```text
host builtins (GUI/audio/system/Termux) require the native Go runtime (run without --no-native)
```

| SYS 区段 | 能力 | Go 侧实现 |
| --- | --- | --- |
| 39–42 | `audio_play` / `audio_stop` / `audio_volume` / `audio_wait` | `engine/audio*.go`（Windows 用 winmm，其它平台走 `afplay`/`aplay`/`paplay`/`ffplay`） |
| 43–50 | 画布：新建、矩形、圆、文本、线、存 PNG、系统查看器打开 | `engine/canvas.go` |
| 51–58 | 文件：读写/追加/存在/删除/大小/递归建目录/列目录 | `engine/system.go` |
| 59–67 | 进程与环境：`exec`、`exec_output`、`getenv`/`setenv`、`os_name`、主机名、用户名、`cwd`、主目录 | `engine/system.go` |
| 68–79 | Termux API：通知、Toast、剪贴板、电量、震动、TTS、定位、WiFi、对话框、短信 | `engine/termux.go`（非 Termux 环境优雅失败） |

因此：如果你的程序用了这些能力，`--no-native` 会失败（报上面的错误），`--debug` 也会因为跳过原生路径而无法使用它们。语言侧的调用方式见 [宿主能力](/language/host-abilities)。

## 三条执行路径与性能定位

::: tabs

== 解释执行

```bash
python cpu.py basic.cin --no-native
```

== JIT

```bash
python cpu.py basic.cin --jit --no-native
```

== Go 原生

```bash
python cpu.py basic.cin
```

:::

| 路径 | 启用 | Python 侧角色 | 覆盖能力 | 相对速度 |
| --- | --- | --- | --- | --- |
| 解释执行 | `--no-native` 或自动回退 | 逐条取指/执行 | 全部调试功能 + 宿主能力（无原生库时宿主能力不可用） | 基准（最慢） |
| Python JIT | `--jit` | 把无分支基本块编译成 Python 函数 | 基本块外的指令回退解释 | 明显快于解释（受基本块命中率限制） |
| Go 原生 | 默认优先（需库） | 只负责编译与解析结果 | 整程序一次执行，含全部宿主能力 | 最快，通常高出解释两个数量级 |

本机实测（Windows / x64，单层 `for` 循环 300000 次累加，`--profile --log-level ERROR`）三条路径输出完全一致（`sum=45000150000`），耗时量级如下——只作为量级参考，不要当作基准测试：

```text
解释执行  (--no-native)        Execution Time 77.7835s    115,706 instr/s
JIT       (--jit --no-native)  Execution Time 30.7369s    292,809 instr/s
Go 原生   (默认)               Execution Time  0.5044s  17,844,649 instr/s
```

定位建议：

- **日常运行优先原生**：什么都不用加。`--profile` 的 `Engine` 行会显示 `Go native` 还是 `Python interpreter`。
- **原生库不可用/不适合时用 JIT**：JIT 只在 Python 层加速，命中率取决于基本块的重复执行次数；分支密集、循环体很短的代码收益有限（见 [JIT 编译](/runtime/jit)）。
- **调试一律用解释执行**：`--debug` / `--step` 会自动禁用原生与 JIT。
- README 的"性能对比"图（`1 / 4 / 8`）是**示意值**，不是基准测试结果。

## 排错

| 症状 | 处理 |
| --- | --- |
| 打印 `None` / 日志出现回退 warning | 确认库位于 `codecin/` 或 `codecin/native/`（或设了 `CODECIN_NATIVE_LIB`）；确认架构与 Python 位数匹配（64 位 Python 配 `x64` 库、不要与 `arm64` 库混放）；用 `--log-level DEBUG` 看 `Failed to load native library <path>: <e>`——`OSError` 多为架构/依赖问题，`AttributeError` 多为旧库缺符号；最后重建（`sh build.sh`，Windows 用 `.\build.ps1`） |
| 构建脚本报 Go / 编译器缺失 | 需要 Go 1.26+ 与 cgo 可用的 C 编译器；Windows 把 MinGW-w64 / TDM-GCC 的 `gcc` 放进 `PATH`，Linux/Termux 用发行版的 `golang` + `gcc` |

::: warning 修改指令集后必须同步三处

Go 侧的操作码/操作数类型/SYS 常量由 `codecin/native/engine/isa_gen.go` 与 `codecin/native/compiler/syscalls.go` 提供，两者都是 `python script/gen_native_isa.py` 从 `codecin/isa.py` **自动生成**的（勿手工改）。正确顺序：改 `codecin/isa.py` → 跑生成器 → 重新编译原生库 → `python -m pytest`；CI 的 `script/gen_native_isa.py --check` 会拦截漂移。

:::

## 相关页面

- [执行路径](/guide/execution-paths)——三条路径的统一语义与一致性保证
- [编译 Go 原生库](/dev/build-native)——构建脚本、静态链接开关与 CI 校验细节
- [JIT 编译](/runtime/jit)——Python 侧的基本块动态编译
- [宿主能力](/language/host-abilities)——SYS 调用的语言侧用法
- [性能分析](/tools/profiling)——`--profile` 统计表与指标含义
- [常见问题 (FAQ)](/guide/faq)
