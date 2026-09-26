---
description: 用 pip 安装 Code CIN 5.5.0 的完整步骤：安装期原生库编译、预编译库、源码树与 Termux 安装，以及安装验证与排错。
---

# 安装 Code CIN

Code CIN 以 **pip 包**作为唯一发行形式: 包内自带 19 个内置标准库 (`codecin/lib/*.cin`),
Python 侧提供唯一命令行入口 `codecin`, 语言实现 (Go 编译器 / 字节码 VM / CROM / AOT 运行时)
以 c-shared 原生库的形式随包加载。

## 环境要求

| 组件 | 版本 | 必需 | 用途 |
|------|------|------|------|
| Python | 3.8+ | 是 | CLI 外壳、解释器路径、JIT、工具链 |
| rich | 13 – 14 (自动安装) | 是 | 终端彩色输出、表格、面板、彩色 traceback |
| Go | 1.26+ | 否 | 安装时编译原生加速库 (缺失则回退纯 Python) |
| C 编译器 | gcc / clang / MinGW | 否 | Go `cgo` (`-buildmode=c-shared`) 需要 |

> `rich` 是唯一的第三方运行时依赖, `pip` 会自动装上。**没有 Go 工具链也能安装成功**,
> 只是不会生成原生库, 运行时会记一条 warning 并回退到纯 Python 解释执行。

## 安装方式

::: tabs

== pip (推荐)

```bash
pip install codecin==5.5.0
```

发行版是 **sdist** (源码分发包), 因此 `pip` 在安装阶段会调用 `setup.py` 的 `build_py` 钩子,
用**本机的 Go 工具链现场编译原生库**, 并把产物单独拷进安装目录 —— 装完即带原生加速。

```bash
codecin --version     # Code CIN 5.5.0
```

== pip + 跳过原生编译

```bash
# Linux / macOS / Termux
CODECIN_SKIP_NATIVE=1 pip install codecin==5.5.0
```

```powershell
# Windows PowerShell
$env:CODECIN_SKIP_NATIVE=1; pip install codecin==5.5.0
```

适合没有 Go/cgo 工具链、CI 构建发布物、或只想快速试语言的场景。
产物功能完整, 但只用纯 Python 解释执行 (可再用 `--jit` 缓解)。

== 预编译原生库资产

不想在安装时编译, 可以直接从 Release 下载对应平台/架构的库, 放进包的安装目录:

| 资产 | 目标平台 |
|------|----------|
| `libcodecin_native-linux-x64.so` | linux/amd64 |
| `libcodecin_native-linux-arm64.so` | linux/arm64 |
| `libcodecin_native-macos-x64.dylib` | darwin/amd64 |
| `libcodecin_native-macos-arm64.dylib` | darwin/arm64 |
| `codecin_native-windows-x64.dll` | windows/amd64 |

```bash
pip install codecin==5.5.0
cd "$(python -c 'import codecin,os;print(os.path.dirname(codecin.__file__))')"
curl -L -O https://github.com/ByUsiStudio/Code-CIN/releases/latest/download/libcodecin_native-linux-arm64.so
python -c "from codecin import native; print(native.get_engine())"   # 非 None 即生效
```

库的查找顺序是**架构专属名 → 通用名**, 所以同目录下混放多个架构时也会优先选本机架构那个;
架构不符只会产生 warning 并回退纯 Python, 不会让运行失败。

== 源码树 (开发)

```bash
git clone https://github.com/ByUsiStudio/Code-CIN.git
cd Code-CIN

# 可选: 编译 Go 原生加速库 (缺省时自动回退纯 Python)
cd codecin/native && sh build.sh      # Windows: .\build.ps1
cd ../..

python cpu.py basic.cin                # 直接运行, 自动优先使用原生库
python cpu.py --help
pip install -e .                       # 可选: 以可编辑模式装上 codecin 命令
```

源码树方式适合改编译器/VM/加指令, 见 [编译 Go 原生库](/dev/build-native)。

== Termux (Android)

```bash
pkg install python golang
pip install codecin==5.5.0
```

Termux 自带 cgo 工具链, 支持 `-buildmode=c-shared`。安装 Termux:API 应用并
`pkg install termux-api` 后还可使用 `termux_*` 宿主能力 (见 [宿主能力](/language/host-abilities))。

:::

## 验证安装

```bash
# 1) 版本
codecin --version
# Code CIN 5.5.0

# 2) 原生库是否加载成功 (输出非 None 即成功)
python -c "from codecin import native; print(native.get_engine())"

# 3) 内置标准库是否随包分发 (应列出 19 个 .cin)
python -c "import codecin,os,glob;print(len(glob.glob(os.path.join(os.path.dirname(codecin.__file__),'lib','*.cin'))))"

# 4) 跑一个最小程序
codecin --log-level ERROR hello.cin
```

最小验证程序 `hello.cin`:

```c
function main() -> int {
    println("Hello, Code CIN!")
    return 0
}
```

```text
Hello, Code CIN!
```

::: tip 原生库没加载成功也能正常用
`get_engine()` 返回 `None` 时, 所有语言功能仍然可用 (纯 Python 解释执行 / JIT),
只是宿主能力 (画布、音频、文件、进程、Termux) 与 AOT 构建需要原生运行时。
:::

## 升级与卸载

```bash
pip install -U codecin==5.5.0     # 升级/锁定到指定版本
pip install codecin               # 装最新版
pip uninstall codecin             # 卸载
pip cache purge                   # 需要时可清理 wheel/sdist 缓存
```

> 版本号的唯一真源是 `codecin/__init__.py` 的 `__version__`, 打包时由
> `pyproject.toml` 的 `dynamic = ["version"]` 读取, 详见 [打包与发布](/dev/packaging)。

## 安装排错

| 现象 | 原因 | 处理 |
|------|------|------|
| 安装日志出现 `setup.py` 编译警告, 但安装成功 | 本机没有 Go/cgo 工具链, 原生库没编出来 | 可忽略; 需要原生能力时装 Go 后重装, 或下载预编译库 |
| `ImportError: DLL load failed` / `cannot open shared object file` | 原生库依赖的系统 C 运行时缺失 (如 Windows 缺 MinGW 运行库) | 用 `--no-native` 先跑通, 或改装预编译库 |
| `get_engine()` 打印 warning 并返回 `None` | 库的架构/ABI 与当前解释器不符 | 换成对应平台的资产; 运行时不会因此失败 |
| `import "math.cin"` 报 `Import file not found` | 装到了不含内置标准库的旧版本 | `pip install -U codecin==5.5.0` 后重试 |
| `codecin: command not found` | 脚本目录不在 `PATH` | 用 `python -m codecin.cli` 或 `python cpu.py` 运行, 或把 `Scripts`/`bin` 加进 `PATH` |
| 想确认到底走了哪条路径 | — | 加 `--log-level DEBUG`, 初始化 dump 里会打印 `native` / `jit` 取值 |

## 下一步

- [快速开始](/guide/quickstart) — 五分钟写出并运行第一个 CIN 程序
- [命令行参考](/guide/cli) — 全部选项与退出码
- [执行路径](/guide/execution-paths) — 解释 / JIT / Go 原生怎么选
- [编译 Go 原生库](/dev/build-native) — 源码树下的原生库构建
