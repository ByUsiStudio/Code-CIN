---
description: "Code CIN 打包与发布：sdist/wheel 构建、setup.py 安装期原生库编译、MANIFEST/package-data 规则与 Release 资产。"
---

# 打包与发布

Code CIN 的发行路径统一为 **pip 包**: 代码通过 sdist 进入 PyPI, 原生加速库在**用户安装时
现场编译**, 预编译库作为 Release 资产单独提供。

## 版本号单一真源

```python
# codecin/__init__.py
__version__ = "5.5.0"
```

```toml
# pyproject.toml
[project]
name = "codecin"
dynamic = ["version"]

[tool.setuptools.dynamic]
version = { attr = "codecin.__version__" }
```

- 只改 `codecin/__init__.py` 一处, 打包 / `--help` / `--version` 全部跟着变;
- `tests/test_version.py` 与 `tests/test_packaging.py` 会守住“单一真源”与 SPDX license 写法;
- Release 工作流还会断言 tag 与包版本一致。

## 构建

::: tabs

== 本地构建 (sdist + wheel)

```bash
python -m pip install build
python -m build
# dist/codecin-5.5.0.tar.gz
# dist/codecin-5.5.0-py3-none-any.whl
```

== 只构建 sdist

```bash
python -m build --sdist
```

== 发布脚本 (sdist + twine)

```bash
sh build.sh              # Linux / macOS / Termux
build.bat                # Windows
```

`build.sh` / `build.bat` 会依次执行: 清空 `dist/` → 导出
`CODECIN_SKIP_NATIVE=1` → `python -m build --sdist` → `twine check dist/*` →
`twine upload dist/*.tar.gz`。

:::

## 为什么只发布 sdist

::: warning wheel 会绕过安装期编译
原生库**不打进 PyPI 包**: 它由 `setup.py` 在安装阶段用**用户机器的 Go 工具链**编译。
如果同时发布 wheel, `pip` 会优先装 wheel 而不执行构建, 用户就拿不到原生加速 ——
所以发布脚本刻意只上传 `.tar.gz`。
:::

没有 Go 工具链的用户可以从 GitHub Release 下载预编译库放进包目录
(见 [安装 Code CIN](/guide/installation))。

## 安装期都发生了什么

`setup.py` 把 `build_py` 换成了 `BuildPyWithNative`:

```text
pip install codecin==5.5.0
        │
        ▼
BuildPyWithNative.run()
        ├── build_native_lib()          # 调 codecin/native/build.ps1|build.sh
        │       (Windows → powershell build.ps1, 其它平台 → bash build.sh)
        ├── super().run()               # 常规 build_py: 收集 Python 包与 package-data
        └── _install_built_libs()       # 把编译出的库单独拷进安装目录
```

| 环境变量 | 作用 |
|----------|------|
| `CODECIN_SKIP_NATIVE=1` | 完全跳过原生库编译与安装 |
| `CODECIN_NO_GO=1` | 同上 (语义化别名, 用于无 Go 环境) |
| `CODECIN_FORCE_REBUILD=0` | 若包目录里已有原生库则跳过重建 (默认 `1` 总是重建) |

`go` 不在 `PATH` 时只打印提示并跳过, **不会让安装失败**。原生库文件名为
`codecin_native.dll` (Windows)、`libcodecin_native.so` (Linux)、
`libcodecin_native.dylib` (macOS)。

## 打包内容规则

| 位置 | 内容 | 由谁负责 |
|------|------|----------|
| `codecin/lib/*.cin` | 19 个内置标准库 (**必须进包**) | `package-data` + `MANIFEST.in` |
| `codecin/native/**` | Go 源码 / `go.mod` / 构建脚本 (安装时要编译) | `MANIFEST.in` |
| `docs/**/*.md` | 文档 (仓库文档 + 文档站页面) | `MANIFEST.in` (`prune docs/node_modules`、`prune docs/.vitepress`) |
| `examples/**`、`script/*.py`、`misc/**` | 示例、工具脚本、编辑器配置 | `MANIFEST.in` |
| `*.dll` / `*.so` / `*.dylib` | **刻意不入包** | `tests/test_packaging.py` 断言拦截 |

```ini
# MANIFEST.in (节选)
recursive-include codecin/lib *.cin
recursive-include codecin/native *.go *.txt go.mod go.sum build.sh build.ps1
recursive-include docs *.md
prune docs/node_modules
prune docs/.vitepress
recursive-include examples *.cin *.asm *.pl
recursive-include script *.py
```

## CI 与 Release 的打包断言

`.github/workflows/ci.yml` 的 `dist` 作业 (用 `CODECIN_SKIP_NATIVE=1` 构建) 会:

1. 断言 wheel 与 sdist 里各有 **≥ 19 个** `codecin/lib/*.cin`;
2. 断言两份产物里**没有任何** `.so` / `.dll` / `.dylib`;
3. 在临时 venv 里 `pip install` wheel, 真跑一个使用内置标准库的程序。

`.github/workflows/release.yml` 另外为**五种平台组合**构建 c-shared 原生库并作为资产上传:

| 资产 | 目标平台 |
|------|----------|
| `libcodecin_native-linux-x64.so` | linux/amd64 |
| `libcodecin_native-linux-arm64.so` | linux/arm64 |
| `libcodecin_native-macos-x64.dylib` | darwin/amd64 |
| `libcodecin_native-macos-arm64.dylib` | darwin/arm64 |
| `codecin_native-windows-x64.dll` | windows/amd64 |

每个库构建完都会用 `go version -m <库>` 读取真实 `GOOS`/`GOARCH` 并**断言与资产名一致**,
避免把错架构的库发出去。`windows/arm64` 暂未纳入 (runner 仍为 preview)。

## 发布检查清单

- [ ] `codecin/__init__.py` 的 `__version__` 已更新, 且等于目标 tag (`vX.Y.Z`)
- [ ] `CHANGELOG.md` 已记录该版本 (release 工作流会校验)
- [ ] 本地回归全绿: `python -m pytest`、`ruff check codecin cpu.py script tests`
- [ ] 指令集同步: `python script/gen_isa_docs.py --check` 与
      `python script/gen_native_isa.py --check`
- [ ] 文档站构建通过 (`cd docs && npm run docs:build`)
- [ ] `sh build.sh` (或 `build.bat`) 只上传 `dist/*.tar.gz`
- [ ] Release 资产齐备 (五平台组合), 且架构断言通过

## 相关页面

- [项目结构](/dev/structure) — 包与目录布局
- [编译 Go 原生库](/dev/build-native) — 构建脚本与静态链接
- [测试与 CI](/dev/testing) — 本地复现 CI 的命令
- [安装 Code CIN](/guide/installation) — 安装期行为与预编译库
