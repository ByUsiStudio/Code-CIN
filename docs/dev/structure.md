---
description: "Code CIN 仓库结构: Python 包各模块职责、Go 原生库各包、tests/script/examples/misc 目录、三语言输入与三执行路径、常量单一真源与\"改一处要同步哪些文件\"。"
---

# 项目结构

仓库 `UCPU` (项目 Code CIN / codecin 5.5.0) 由三部分组成: **Python 包** `codecin/`
(唯一 CLI 外壳 + 解释器 + JIT)、**Go 原生库** `codecin/native/` (语言实现核心:
CIN 编译器、字节码 VM、CROM、AOT 运行时)、以及 **`docs/` 官方文档站**
(VitePress, 同时是独立仓库 Code-CIN-Docs, 以 git submodule 内嵌)。

## 顶层目录树

```text
UCPU/
├── cpu.py                    # 唯一 CLI 入口 (转发到 codecin.cli: main)
├── codecin/                  # Python 主包
│   ├── lib/                  # 内置标准库 (19 个 .cin, 随包分发)
│   ├── native/               # Go 原生库源码 (module codecin-native)
│   └── ...
├── tests/                    # pytest 套件 (33 个 test_*.py + helpers/conftest)
├── script/                   # 代码生成、一致性检查、安装脚本
├── examples/                 # 示例程序 (.cin / .asm)
├── misc/                     # 编辑器支持 (vim 语法/文件类型检测)
├── docs/                     # 官方文档站 (VitePress, 独立 git 仓库)
├── .github/workflows/        # CI 与 Release 工作流
├── basic.cin                 # CIN 综合示例 (回归基准)
├── test_asm.asm              # 汇编测试样例
├── pyproject.toml            # 项目元数据 + dynamic version + package-data
├── setup.py                  # BuildPyWithNative 构建钩子 (现场编译原生库)
├── MANIFEST.in               # sdist 清单
├── build.sh / build.bat      # PyPI 发布脚本 (只发 sdist)
├── requirements.txt          # 运行时依赖 (rich)
├── requirements-dev.txt      # 开发依赖 (pytest / pytest-cov / ruff)
├── CHANGELOG.md / LICENSE / README.md
└── .editorconfig / .gitattributes / .gitignore
```

## `codecin/` — Python 包

| 模块 | 职责 |
|------|------|
| `__init__.py` | 包导出与**版本号单一真源** `__version__ = "5.5.0"` |
| `cli.py` | 命令行入口: `build_parser()` (argparse) → `Config` → 加载 → 运行; 唯一参数表来源 |
| `config.py` | `Config` dataclass: 全部运行配置字段 + `validate()` 夹取 |
| `cpu.py` | CPU 核心: 指令 dispatch (`_op_*` 自动注册)、解释执行、栈/标志、`SYS` 宿主调用、调试入口 |
| `isa.py` | 指令集定义: `Opcode` (112)、`Syscall` (0–79)、`Cond`、`Constants`、`KIND_*`、`BC_MAGIC` |
| `assembler.py` | ASM / PL 汇编器: `.equ` 常量、表达式、数据段、标签、内存操作数 |
| `cin.py` | CIN 高级语言编译器: 词法/语法/代码生成、import 展开、`HOST_BUILTINS` 表 |
| `jit.py` | Python 级 JIT: 无分支基本块编译为 Python 函数并缓存 |
| `memory.py` | `FastMemory` (平坦字节数组 + 保护 + 可选 MMU) 与 `Mmu` (4 KiB 扁平页表) |
| `registers.py` | `RegisterFile` (X0–X31/XZR) 与 `VectorRegisterFile` (V0–V31 × 4 lane) |
| `cache.py` | LRU 组相联缓存: `size` / `assoc` / `line_size`, 命中率统计 |
| `stats.py` | `Statistics` / `PerformanceCounters` / `InstructionProfiler` / `BranchPredictor` |
| `crom.py` | CROM v3 镜像读写 (zlib + CRC32 + MMU 尾部) 与 CPUSA `.bin` 容器 |
| `native.py` | Go 原生库 ctypes 桥接: 库查找、`codecin_run` 调用、UCBC 编解码 |
| `aot.py` | AOT 编排: 依赖检查 → 编译 → 生成 `main.go` → `go build` 静态产物 |
| `disasm.py` | UCBC / CPUSA 反汇编为文本清单 |
| `debugger.py` | 交互式调试会话、断点、状态渲染、远程调试服务 (`DebugServer`) |
| `console.py` | rich 适配层: `Console` / `Table` / `Panel` / `Colors` (模块禁止直接 `print`) |
| `logger.py` | rich 日志器: DEBUG/INFO/WARNING/ERROR + `trace` / `dump` / `hexdump` |
| `errors.py` | 异常层次: `CPUSimulatorError` 及子类 |

```text
codecin/lib/   (19 个内置标准库, import 时按裸名字解析到这里)
array  bits  conv  gui  hash  io  json  math  matrix  queue
rand   sort  stat  str  termux  test  time  validate  vec
```

## `codecin/native/` — Go 原生库

Go 模块名 `codecin-native`, `go.mod` 声明 `go 1.26`。

```text
codecin/native/
├── go.mod                    # module codecin-native / go 1.26 (无第三方依赖)
├── main.go                   # 唯一的 package main: cgo c-shared 库入口
├── build.sh / build.ps1      # Linux/Termux/macOS 与 Windows 构建脚本
├── ir/                       # 中间表示: Program / Instr / Operand / DataWrite
├── engine/                   # 字节码 VM 与运行时
│   ├── vm.go                 # 原生字节码 VM (engine.Run) + opcodeSupported
│   ├── crom.go               # CROM 打包/解包
│   ├── encode.go             # IR → UCBC 编码
│   ├── isa_gen.go            # 生成物: 操作码/操作数种类/SYS 常量 (勿手改)
│   ├── version_gen.go        # 生成物: BuildVersion (勿手改)
│   ├── canvas.go             # 2D 画布 (image/png, 导出 PNG)
│   ├── audio.go              # 音频公共逻辑 (下载 + WAV 播放)
│   ├── audio_windows.go      # //go:build windows (winmm PlaySoundW)
│   ├── audio_other.go        # //go:build !windows (afplay/aplay/paplay/ffplay)
│   ├── system.go             # 文件/进程/环境/系统信息宿主调用
│   ├── termux.go             # Termux API (termux-* 命令)
│   └── engine_test.go        # Go 侧单元测试
├── compiler/                 # Go 版 CIN 编译器 (与 cin.py 双语对齐)
│   ├── tokenizer.go / parser.go / types.go / codegen.go
│   ├── syscalls.go           # 生成物: SYS 功能号常量 (勿手改)
│   └── compile_test.go
└── aot/                      # AOT 产物运行时
    ├── aot.go                # aot.Main(bytecode, memImage) -> int
    └── stub_main.go.txt      # 生成的入口 shell (Go 与 Python 侧共用同一份)
```

**Go 侧不提供 CLI**: `codecin/native/` 下唯一的 `package main` 就是 c-shared 库入口
`main.go` (见 `tests/test_no_go_cli.py` 与 CI `native` 作业的断言)。历史版本里的
`cmd/codecin/` 全 Go CLI 已移除 —— 唯一的命令行入口是 Python
(`python cpu.py` / 安装后的 `codecin` console script)。

## 三语言输入与三执行路径

```text
输入 (三语言)                     前端                        中间产物
─────────────────────────────────────────────────────────────────────────
.cin  (CIN 高级语言)   ──►  cin.py / compiler/*.go  ──►  UCBC 字节码
.pl   (PL 关键字汇编)  ──►  assembler.py            ──►  UCBC 字节码
.asm  (ASM 汇编)       ──►  assembler.py            ──►  UCBC 字节码
.bin  (CPUSA 容器)     ──►  crom.load_bin           ──►  UCBC 字节码 + 内存镜像
.crom (CROM v3 镜像)   ──►  crom.load_crom          ──►  内存镜像
```

```text
执行 (三路径, 同一份 UCBC 字节码)
─────────────────────────────────────────────────────────
Go 原生 VM  ← 默认优先 (native.py → ctypes → c-shared)
   │ 库缺失 / ABI 不符 / 返回 StatusUnsupported
   ▼
Python JIT  ← --jit (与 --debug/--step 互斥)
   │ 未启用
   ▼
解释执行    ← 兜底, 支持全部调试能力
```

路径选择逻辑集中在 `CPU.run()`: `--debug` / `--step` / `--bounds-check` / `--mmu`
任意一个开启都会关闭原生路径; `--debug` 与 `--jit` 同给时 debug 优先。三条路径必须
给出相同结果, 由 `script/check_paths.py` 与 `tests/test_three_paths.py` 把关。详见
[执行路径](/guide/execution-paths)。

## `tests/` — 测试套件

| 文件 | 覆盖点 |
|------|--------|
| `conftest.py` | 会话级工作目录夹具 (`workdir`, 落在仓库内 `.pytest_tmp/`) |
| `helpers.py` | `new_cpu` / `run_program` / `run_cin_source` / `run_cin_file` / `asm_program` / `snapshot` |
| `test_isa_dispatch.py` | Opcode 总数与分组、dispatch 自动注册完整性、文档生成一致性 |
| `test_isa_single_source.py` | ISA 单一真源守卫: 各手写映射表与 `isa.py` 不漂移 |
| `test_cpu_interpreted.py` | 解释器指令级黄金值、栈/内存往返、向量、异常路径 |
| `test_three_paths.py` | 解释 / JIT / 原生终态快照一致性; 原生批量记账口径 |
| `test_paths_consistency.py` | 源码级三路径一致性 (跑 `examples/*.cin` 逐字节比对 stdout) |
| `test_assembler_ext.py` | 汇编器 `.equ` / 表达式 / 符号算术 / 数据段表达式 |
| `test_cli.py` | argparser 选项、帮助文本、退出码 |
| `test_cache.py` | 缓存命中/缺失计数与 `stats` |
| `test_memory_protection.py` | 保护检查统一 (浮点/块读写不可绕过) |
| `test_debugger.py` | 断点 continue 豁免、`--step` 与断点共用命令集、graceful quit |
| `test_features_ab.py` | `--bounds-check` / `--seed` 确定性 / `--disasm` |
| `test_features_mmu.py` | MMU identity 页表、map/unmap、权限、缺页 + CPU 集成 |
| `test_features_crom_mmu.py` | CROM v3 + MMU 页表持久化 roundtrip / 兼容 / 忽略模式 |
| `test_features_remote.py` | `--debug-server` 远程驱动式协议 (socket 集成) |
| `test_cin_syntax.py` | CIN 新语法: break/continue、do-while、switch、复合赋值、三目、类型别名 |
| `test_cin_new_features.py` | 位运算、`idiv`、字符串单字符、`min/max`、`floor/ceil/round`、`atoi`、`trim` |
| `test_switch_semantics.py` | switch 语义回归 (Go 与 Python 双编译器对齐) |
| `test_literals_and_bom.py` | 数值字面量与 UTF-8 BOM import 的双端一致性 |
| `test_compiler_errors.py` | 非法程序必须被 Go 与 Python 两侧同时拒绝 |
| `test_features_import.py` | 字符串内建 + `import`/`lib` 模块化 |
| `test_features_importloc.py` | import 行级源映射 (`file:line` 精确定位) |
| `test_libs.py` | 官方标准库 19 个模块的行为回归 |
| `test_libs_ext.py` | 新增标准库 `bits/stat/hash/validate/matrix/queue` |
| `test_cin_host.py` | CIN 宿主能力 (画布 / 联网音频), 无原生库时 skip |
| `test_cin_system.py` | 系统原生交互 / Termux API 内建, 无原生库时 skip |
| `test_examples.py` | `examples/` 示例冒烟 (解释路径结果确定) |
| `test_native_hardening.py` | 原生库加固: 步数用尽报错、不可信输入、缓冲上限 |
| `test_native_lib_lookup.py` | 原生库查找顺序: 架构专属名优先于通用名 |
| `test_no_go_cli.py` | 架构门禁: Go 侧只作为库存在, 无额外 `package main` |
| `test_packaging.py` | 打包设计门禁: package-data / MANIFEST / 只发 sdist / `BuildPyWithNative` |
| `test_version.py` | 版本号单一真源: pyproject dynamic / Go `BuildVersion` / semver / `--version` |
| `test_workflows.py` | GitHub Actions 工作流静态校验 (表达式函数、matrix 引用、job/step 形状) |
| `test_aot.py` | AOT 产物可独立运行、交叉编译、Linux 静态链接 (无 `PT_INTERP`)、目标解析 |

## `script/`、`examples/`、`misc/`

| 路径 | 说明 |
|------|------|
| `script/gen_isa_docs.py` | 由 `isa.py` 生成仓库指令表 `docs/ISA.md` 与站点页 `docs/reference/isa.md`; `--check` 校验 |
| `script/gen_native_isa.py` | 由 `isa.py` 生成 `engine/isa_gen.go`、`compiler/syscalls.go`、`engine/version_gen.go`; `--check` 校验 |
| `script/check_paths.py` | 对程序逐字节比较三条执行路径的 stdout (默认跑 `examples/*.cin`) |
| `script/install_termux.sh` | Termux (Android) 安装辅助脚本 |
| `examples/*.cin` | `control_flow` / `literals_types` / `modules_demo` / `stdlib_demo` / `bitwise_builtins` / `system_interaction` |
| `examples/asm_constants.asm` | 汇编器 `.equ` 与表达式示例 |
| `misc/vim/` | Vim 支持: `ftdetect/codecin.vim`、`syntax/cin.vim`、`syntax/codecinasm.vim` |

## 关键约束

### 1. Go 侧不提供 CLI

语言实现全在 Go (`codecin/native/` 下的 CIN 编译器、字节码 VM、CROM), 但 Go 侧
**没有任何命令行入口**。唯一允许的 `package main` 是 cgo 的 c-shared 库入口
`main.go`, 它导出 `codecin_run` / `codecin_free` / `codecin_crom_pack` /
`codecin_crom_unpack` / `codecin_version`, 由 Python ctypes 加载。

把关点: `tests/test_no_go_cli.py` 断言唯一的 `package main` 是
`codecin/native/main.go`; CI 的 `native` 作业用 `grep -rl '^package main'` 做同样的检查,
并额外断言 `main.go` 仍包含 `#include <stdlib.h>`。

### 2. 常量单一真源: `isa.py` → Go

`codecin/isa.py` 是**唯一权威**: `Opcode` 编码、`Syscall` 功能号、`KIND_*` 操作数
类型码、`Constants.ARG_COUNTS`、`__version__` (对 Go 版本常量而言)。
`script/gen_native_isa.py` 把它们生成到 Go 侧, **生成物禁止手工修改**:

| 生成物 | 内容 | 头部标记 |
|--------|------|----------|
| `codecin/native/engine/isa_gen.go` | `op*` 操作码、`kind*`、`opcodeByName`、`argCounts`、`sys*` | `// Code generated by script/gen_native_isa.py ... DO NOT EDIT.` |
| `codecin/native/compiler/syscalls.go` | 编译器用的 `SysXXX` 常量 | 同上 |
| `codecin/native/engine/version_gen.go` | `engine.BuildVersion` | `// ... from codecin/__init__.py` |

生成物必须是 **gofmt 干净的 LF 文件**: CI 的 `native` 作业跑 `gofmt -l .` 门禁,
`gen_native_isa.py --check` 还会显式拒绝 CRLF (见 `.gitattributes` 的
`*.go text eol=lf`)。

### 3. 其他不可漂移的约定

| 约定 | 把关点 |
|------|--------|
| 三条执行路径语义一致 | `script/check_paths.py`、`tests/test_three_paths.py`、`tests/test_paths_consistency.py` |
| CIN 编译器双语对齐 (Python `cin.py` 与 Go `compiler/`) | `tests/test_compiler_errors.py`、`tests/test_switch_semantics.py`、`tests/test_literals_and_bom.py` |
| 版本号只是 `codecin/__init__.py` 一个数 | `tests/test_version.py`、Release 工作流的 `version-check` 作业 |
| PyPI 包不含预编译原生库 | `tests/test_packaging.py`、CI `dist` 作业 |
| 内置标准库必须随包分发 | `pyproject.toml` package-data + `MANIFEST.in`、CI `dist` 作业断言 ≥19 个 `.cin` |

## 改一处要同步哪些文件

| 你改了什么 | 必须同步的地方 | 命令 |
|------------|----------------|------|
| 新增/删除一条指令 | `isa.py` (`Opcode`/`OPCODE_NAMES`/`ARG_COUNTS`/`BRANCH_OPS`/`FP_OPS`) → `cpu.py` 的 `_op_*` → `jit.py` → `engine/vm.go` → `assembler.py` | `python script/gen_isa_docs.py`、`python script/gen_native_isa.py`、重编原生库、`pytest` |
| 新增一个 SYS 功能号 | `isa.py: Syscall` → `cpu.py: _op_sys` → `engine/vm.go: doSyscall` → `cin.py: HOST_BUILTINS` (若暴露给 CIN) + `compiler/codegen.go: hostBuiltins` | 同上 (编号四处必须一致) |
| 改了版本号 | `codecin/__init__.py: __version__` (唯一真源) → 重新生成 `version_gen.go`; `CHANGELOG.md` 加 `## [x.y.z]` 条目 | `python script/gen_native_isa.py`、`pytest tests/test_version.py` |
| 改了内置标准库 | `codecin/lib/*.cin` (package-data 自动覆盖) | `pytest tests/test_libs.py tests/test_libs_ext.py` |
| 改了打包配置 | `pyproject.toml` / `MANIFEST.in` / `setup.py` / `build.sh` / `build.bat` | `pytest tests/test_packaging.py` |
| 改了 CI 工作流 | `.github/workflows/*.yml` | `pytest tests/test_workflows.py` |
| 改了寄存器/内存语义 | `registers.py` / `memory.py` / `cpu.py` **以及** `engine/vm.go` (原生侧) | `pytest tests/test_memory_protection.py tests/test_features_mmu.py`、`check_paths.py` |
| 改了 CIN 语法 | `cin.py` **以及** `compiler/*.go` (双编译器必须同时对) | `pytest tests/test_cin_syntax.py tests/test_switch_semantics.py tests/test_compiler_errors.py` |
| 新增平台相关宿主 API | `engine/audio_*.go` 式的 `//go:build` 拆分 (否则其它平台编不过) | CI `native` 作业的交叉编译自检 |
| 新增文档站页面 | `docs/<section>/<page>.md` + `docs/.vitepress/config.mts` 的 sidebar | `cd docs && npm run docs:build` |

## 相关页面

- [编译 Go 原生库](/dev/build-native) — 三平台构建命令与产物
- [测试与 CI](/dev/testing) — 本地复现 CI 的完整命令
- [打包与发布](/dev/packaging) — 版本真源、构建钩子、Release 资产
- [扩展指令 / 系统调用](/dev/extend) — 逐步改动清单
- [执行路径](/guide/execution-paths) — 三条路径的选择与回退
- [架构总览](/guide/architecture) — 分层与数据流
