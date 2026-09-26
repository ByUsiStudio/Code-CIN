---
description: AOT 独立可执行文件：用 --build-exe 把 CIN 程序编译成静态链接单文件，含交叉编译、依赖嵌入、内存越界与排错。
---

# AOT 独立可执行文件

`--build-exe` 把 CIN 程序编译成**静态链接的独立可执行文件**（Windows / Linux / macOS）。产物内嵌 UCBC 字节码与初始内存镜像，由内置的 Go VM 执行：

- **运行时不需要 Python**，不需要 Go 工具链，不需要 libc 或任何动态库；
- `import` 依赖闭包在**编译期展开**并嵌入产物，产物运行时不读取任何 `.cin`；
- 构建期只需要 Go 工具链 + 仓库里的 Go 源码树。

## 前置条件

| 项目 | 要求 |
| --- | --- |
| Go 工具链 | 1.26+（`codecin/native/go.mod` 声明 `go 1.26`；实测 1.26.5 可用） |
| 源码树 | 必须包含 `codecin/native/`（`go.mod` 与 `aot/stub_main.go.txt` 模板） |
| 输入 | `.cin` 源文件（AOT 走 CIN 编译器，不支持直接喂 `.bin`/`.asm`） |
| 网络 | 不需要（纯本地 `go build`） |

::: warning 不适用于已安装的 wheel

AOT 是**面向源码检出**的构建期功能。发行 wheel / 独立 CLI 里不含 `codecin/native/` 的 Go 源码，因此会报：

```text
找不到 Go 模块目录 <...>\codecin\native (AOT 构建需要仓库中的 Go 源码)
找不到 AOT 模板 <...>\aot\stub_main.go.txt; --build-exe 需要包含 codecin/native/ 的源码树
```

:::

## 基本用法

```bash
python cpu.py program.cin --build-exe program      # 本机平台
python cpu.py program.cin --build-exe              # 省略路径: 输出 <程序名>[.exe]
```

| 选项 | 说明 |
| --- | --- |
| `--build-exe [OUT]` | 编译成独立静态可执行文件后**退出**（不执行）；省略 `OUT` 时输出 `<程序名>` + 平台后缀 |
| `--build-target OS/ARCH` | 交叉编译目标，默认当前平台（`aot.host_target()`） |
| `--build-keep-temp` | 保留 `go build` 的临时包目录，排查失败用 |
| `--mem-size BYTES` | 初始内存镜像大小，默认 `65536` |

两点容易踩的细节：

- **输出名与 `-o` 无关**。`--build-exe` 只认自己的可选参数；Windows 目标若输出名不以 `.exe` 结尾，会**自动补 `.exe`**（`hello-aot` → `hello-aot.exe`）。
- 目标平台列表（`aot.SUPPORTED_TARGETS`）为 `windows/amd64`、`windows/arm64`、`linux/amd64`、`linux/arm64`、`darwin/amd64`、`darwin/arm64`；`--build-target` 只校验 `os/arch` 两段非空，其余组合交给 Go 工具链判断。

成功时的两行日志：

```text
AOT build: program.cin -> program (静态链接, 目标 windows/amd64)
AOT build 完成: D:\...\program.exe
```

失败时打印 `Build Error` 面板并返回退出码 1（未知选项/非法目标、依赖缺失、编译错误、`go build` 失败都走这条路径）。

## 完整示例

::: tabs

== 本机平台 (Windows)

```powershell
python cpu.py hello.cin --build-exe hello-aot
.\hello-aot.exe
```

```text
AOT build: hello.cin -> hello-aot (静态链接, 目标 windows/amd64)
AOT build 完成: D:\...\hello-aot.exe

Hello, Code CIN!
2 + 3 = 5
```

产物实测 **6,500,352 字节（约 6.2 MiB）**；本机另一次构建为 6,543,872 字节。体积主要由 Go 运行时与 VM 组成，随程序与平台略有浮动。

== 交叉编译 Linux

```bash
python cpu.py hello.cin --build-exe hello-linux --build-target linux/amd64
```

产物是 ELF，**无 `PT_INTERP`**：不依赖 glibc，可拷到任意同架构 Linux（含 Alpine/musl）直接运行。

== 交叉编译 macOS

```bash
python cpu.py hello.cin --build-exe hello-mac --build-target darwin/arm64
```

产物是 Mach-O；Go 交叉编译不需要 macOS 本机。

== 带依赖库的程序

```bash
python cpu.py prog.cin --build-exe prog --mem-size 1048576
```

```text
依赖库 1 个 (编译期嵌入产物): stat.cin
AOT build: prog.cin -> prog (静态链接, 目标 windows/amd64)
AOT build 完成: D:\...\prog.exe
```

:::

## 构建流程做了什么

`codecin/aot.py:build_program()` 的顺序（每一步失败都给出明确错误，不留半成品）：

1. **程序文件存在性**：不存在报 `找不到程序文件: <path>`；
2. **依赖完整性检查**：用 `collect_imported_files()` 展开 `import` 闭包，缺失/循环引用报 `依赖检查失败: ...`，缺失文件报 `依赖库缺失: <列表>`——不必等 `go build` 才失败；
3. **编译**：`CINCompiler().compile()` 产出指令、标签与数据段写入，失败报 `编译失败: ...`；
4. **编码字节码**：`encode_program()` 生成 UCBC 段（与 `.bin` 里的字节码段同格式）；
5. **生成初始内存镜像**：默认 65536 字节（`--mem-size` 覆盖），下限 256；数据段写入按地址落到镜像里；
6. **生成临时包**：在 `codecin/native/` 内建 `.aotbuild-<随机十六进制>/`，写入 `main.go`（来自 `codecin/native/aot/stub_main.go.txt`）、`program.ucbc`、`program.mem`；
7. **交叉编译**：`CGO_ENABLED=0` + `GOOS`/`GOARCH` 执行

   ```text
   go build -trimpath -tags netgo,osusergo -ldflags "-s -w" -o <输出> ./.aotbuild-xxxx
   ```

8. **清理**：默认删除临时目录；`--build-keep-temp` 时保留并打印 `AOT 临时目录保留: <tmp>`。

生成的入口 shell 极薄——把两个资源文件 `//go:embed` 进去，然后交给 `aot.Main`：

```go
//go:embed program.ucbc
var bytecode []byte

//go:embed program.mem
var memImage []byte

func main() {
	os.Exit(aot.Main(bytecode, memImage))
}
```

同一份模板被 Go 侧（`codecin/native/aot/aot.go`）与 Python 侧（`codecin/aot.py`）共用，避免模板漂移；`tests/test_aot.py` 断言两边都引用 `stub_main.go.txt`。

### 依赖闭包如何在编译期展开

`import` 的解析规则与普通编译完全一致：

- `import "./x.cin"` / `import "../x.cin"` → 相对**当前 `.cin` 文件**所在目录；
- `import "x.cin"`（裸名字）→ 解析到内置标准库 `codecin/lib/`（`lib/` 前缀保留为兼容写法）；
- 同一文件每次编译只包含一次，循环引用报错。

因为展开发生在编译期，**产物不读任何 `.cin`**。实测：把带 `import "stat.cin"` 的程序构建到临时目录后，在没有 `codecin/lib` 的目录里直接运行产物，输出与解释器一致（`sum=55 stat=15`）。

## 产物行为

| 行为 | 说明 |
| --- | --- |
| 启动 | 从 PC 0 开始执行内嵌字节码（CIN 编译结果的首条是 bootstrap `CALL main; HALT`） |
| 初始状态 | `SP = 内存大小 - 8`（栈从内存末尾向下增长），堆基址 = 内存大小 / 2 |
| 步数上限 | `100000000`，超出按运行期错误处理 |
| stdout | 打印程序输出（`OUT` / `println` 等） |
| stdin | **只在被重定向（管道/文件）时读取**；交互式终端下不会挂起等待 EOF |
| 正常结束 | 退出码 **0** |
| 运行期错误 | 消息写 **stderr**，退出码 **1** |

运行期错误的实测形态（程序先打印了 `before`，随后除零）：

```text
$ ./boom.exe
before
runtime error: Division by zero          ← stderr
$ echo $?
1
```

若原生 VM 返回空结果，stderr 为 `runtime error: no result`；未实现的指令会给出 `runtime error: unsupported instruction (原生 VM 未实现该指令)`。

## 静态链接与体积

| 环节 | 做法 | 效果 |
| --- | --- | --- |
| 静态链接 | `CGO_ENABLED=0` | 不链接 libc/任何动态库 |
| 纯 Go DNS/用户库 | `-tags netgo,osusergo` | 不依赖 glibc 的 NSS/getaddrinfo |
| 路径与符号 | `-trimpath -ldflags "-s -w"` | 去掉本机路径与符号表，缩小体积 |
| 体积 | 单个静态二进制 | 实测约 6.2 MiB（含 Go 运行时与 VM） |

`tests/test_aot.py` 会把产物按 ELF 解析并断言**不存在 `PT_INTERP` 段**——这是"Linux 产物不依赖 glibc"的机器可验证形式。

## 数据段越界与 `--mem-size`

编译期生成内存镜像时，任何落在声明大小之外的数据段写入都会**直接报错**（不会静默丢弃）：

```text
数据段超出内存大小 65536 字节: addr=0x0 size=70001; 用 --mem-size 增大后重试
```

触发方式通常是超过默认内存的数据（例如一个 70000 字符的字符串字面量）。按提示增大后即可通过：

```bash
python cpu.py bigstr.cin --build-exe bigstr2 --mem-size 1048576
```

注意这一点**只在构建期检查数据段**；运行期对栈/堆的越界行为与解释器一致（需要检查时用 `--bounds-check`，见 [CIN 语言限制与常见错误](/language/errors)）。`--mem-size` 小于 256 会被抬到 256。

::: tip 内存大小要一次定对

产物内置的内存大小在构建时固化。数据段放不下时构建直接失败（好事），但运行期才发现的"内存不够"需要重新构建，所以给够余量（例如 `--mem-size 1048576`）。

:::

## 排错

::: details `go build` 失败 / 缓存不可写

受限环境（沙箱、只读 HOME、部分 CI）下 Go 默认构建缓存可能不可写。构建器检测到这类错误时会**自动回退到仓库内 `.gocache` 重试一次**；仍失败则提示：

```text
go build 失败 (目标 <os>/<arch>): ...
提示: 默认 Go 构建缓存不可写且回退失败; 可显式设置 GOCACHE 指向可写目录后重试
```

也可以自己指定：

```bash
GOCACHE=/tmp/gocache python cpu.py prog.cin --build-exe prog
```

:::

::: details 想看 `go build` 到底在做什么

```bash
python cpu.py prog.cin --build-exe prog --build-keep-temp --log-level DEBUG
```

`--build-keep-temp` 会保留 `codecin/native/.aotbuild-<rand>/`，里面有生成的 `main.go`、`program.ucbc` 与 `program.mem`；可以手动在该目录里复现编译命令。DEBUG 级日志会打印完整 `go build` 命令行与 `GOOS`/`GOARCH`/`CGO_ENABLED`。`.aotbuild-*` 以 `.` 开头，Go 工具链会忽略它，因此不影响 `go build ./...`。

:::

::: details 提示找不到 `go` 命令

```text
未找到 go 命令; AOT 构建需要 Go 工具链 (https://go.dev/dl/)
```

安装 Go 1.26+ 并确认 `go version` 在 `PATH` 里可用。`go build` 超时上限为 1800 秒，超时报 `go build 超时`。

:::

::: details 交叉编译很慢

首次为目标平台构建需要重新编译 Go 标准库，1–2 分钟属正常（`tests/test_aot.py` 也因此默认只跑本机构建，交叉编译用例需 `CODECIN_AOT_TESTS=1` 才启用）。

:::

## 与其它路径的关系

| 对比 | `.bin` | AOT 产物 |
| --- | --- | --- |
| 运行需要 | Python + Code CIN | 什么都不需要 |
| 输入形态 | 字节码 + 内存镜像 | 静态单文件（内嵌同样内容） |
| 适用 | 开发期反复运行、反汇编检查 | 分发用户程序 |
| 体积 | 几十 KB 级 | 约 6 MB 级 |

产物里的 VM 与原生路径是**同一份 Go 引擎**（`codecin/native/engine`），因此宿主能力（文件、进程、音频、画布、Termux）在 AOT 产物里同样可用，且不依赖 Python。三条执行路径的语义一致性见 [执行路径](/guide/execution-paths)。

## 相关页面

- [二进制格式](/runtime/formats)——AOT 内嵌的 UCBC 字节码与内存镜像格式
- [Go 原生运行时](/runtime/native)——被内嵌的 Go VM 与宿主能力
- [Python 嵌入 API](/reference/python-api)——`codecin.aot.build_program()` 的编程接口
- [命令行参考](/guide/cli)——`--build-exe` / `--build-target` / `--build-keep-temp`
- [常见问题 (FAQ)](/guide/faq)
