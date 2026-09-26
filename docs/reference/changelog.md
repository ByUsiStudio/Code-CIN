---
description: Code CIN 版本更新日志：当前版本 5.5.0 的架构收敛、原生库安装期编译、内置标准库打包与 AOT 加固，以及历史版本主线。
---

# 更新日志

本站与发行版同步, 当前文档对应 **Code CIN 5.5.0**。完整的逐条变更记录见主仓库
[`CHANGELOG.md`](https://github.com/ByUsiStudio/Code-CIN/blob/main/CHANGELOG.md)
(遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/))。

## 版本一览

| 版本 | 日期 | 主线 |
|------|------|------|
| **5.5.0** | 2026-09-24 | 「Python 只做 CLI、Go 是唯一实现」架构收敛; 原生库安装期编译; 内置标准库入包; AOT 依赖加固 |
| 5.4.2 | 2026-09-13 | 消除静默错误 + 工程可信度加固 (测试从 158 项增至 413 项 Python 用例 + 2 个 Go 测试包) |
| 5.3.0 | 2026-09-11 | 项目正式更名为 **Code CIN**, 架构从 Python 优先切换为 **Go 优先**; 扩展 CIN 语法、官方标准库、跨平台宿主能力 |
| 5.2.0 | — | 语言与运行时能力扩充 (详见完整日志) |
| 5.1.0 及更早 | — | UCPU 模拟器工具链初始化: 解释执行 / JIT / Go 原生三路径、模块化 `codecin/` 包结构 |

## 5.5.0 — 架构收敛

当前版本, 以「Python 只做 CLI、Go 是唯一实现」为主线的架构收敛, 配套标准库打包修复与
AOT 依赖加固。

### 新增

- **原生库改为“安装时编译”**: PyPI 包**不再携带任何预编译库** (`package-data` 与
  `MANIFEST.in` 都移除了 `*.dll/*.so/*.dylib`)。`setup.py: BuildPyWithNative` 在构建时
  调用 `codecin/native/build.*`, 用**用户机器的 Go 工具链**现场编译并把产物拷进安装目录 ——
  因此 `pip install codecin` (sdist) 装完即带原生加速。`build.sh` / `build.bat` 相应改为
  **只发布 sdist**: 若同时发布 wheel, pip 会优先装 wheel 而不执行构建, 用户就拿不到原生加速。
  没有 Go 的用户可从 Release 下载预编译库。
- **原生库新增 ARM64 目标**: Release 为**五种平台组合**构建 c-shared 库 —— linux/amd64、
  linux/arm64、darwin/amd64、darwin/arm64、windows/amd64; 资产名带 `平台-架构` 后缀
  (如 `libcodecin_native-linux-arm64.so`), 修掉此前同名产物互相覆盖的问题。
- **产物架构校验**: 每个原生库构建后用 `go version -m` 读出真实 `GOOS`/`GOARCH`,
  断言与资产名一致, 杜绝把错架构的库以 arm64/x64 名义发出。
- **原生库查找支持架构专属名**: `codecin/native.py: _lib_candidates` 按
  **架构专属名 → 通用名** 顺序查找, 同目录混放多架构库时优先选本机的那个。

### 变更

- **`get_engine` 不再因原生库 ABI/符号不符而中断运行**: 现在同时捕获 `AttributeError`
  与 `OSError`, 逐候选继续尝试, 全部失败时记一条 warning 再回退纯 Python。
- **Go 侧不再提供 CLI**: 删除独立 Go CLI (`codecin/native/cmd/codecin/`) 与
  仅供 CLI 调用的构建入口; 语言实现仍在 Go 侧 (CIN 编译器、字节码 VM、CROM、AOT stub),
  但**只以库的形式存在**; CI 新增门禁断言 `codecin/native/` 下不存在 `package main`。
- **唯一 CLI 入口是 Python**: `python cpu.py` / 安装后的 `codecin` console script。
- **内置标准库迁入包内**: `lib/` → `codecin/lib/`, 并通过 `[tool.setuptools.package-data]`
  与新增的 `MANIFEST.in` 进入 wheel/sdist —— 修复「`pip install codecin` 之后所有
  `import "lib/*.cin"` 直接编译失败」的问题。
- **CIN `import` 解析规则**:
  - `import "./x.cin"` / `import "../x.cin"` —— 相对**当前 `.cin` 文件**所在目录;
  - 其余任何形式 (`import "x.cin"`、`import "lib/x.cin"`) —— 直接解析到内置标准库
    `codecin/lib/` (`lib/` 前缀保留为兼容写法)。
- **AOT 依赖检查与嵌入**: `--build-exe` 先解析 import 闭包做依赖完整性检查 (缺失/循环引用
  报 `AotError`, 而不是等到 `go build` 失败), 打印依赖清单, 并在编译期把依赖库全部展开嵌入产物;
  数据段越界不再静默丢弃而是报错并提示 `--mem-size`; Windows 目标未带 `.exe` 的输出路径
  自动补后缀。
- **发布与 CI**: 移除 `release.yml` 的 Go CLI 构建矩阵; 原生库资产补架构维度;
  `workflow_dispatch` 支持 checkout 输入 tag; 新增 `dist` 作业断言 wheel/sdist 内含 19 个
  内置标准库模块, 并实际安装后运行一个使用标准库的程序; 覆盖率合并为一次运行。
- **移除旧的安装/打包脚本**: `install.sh` / `install.ps1` / `codecin.spec` /
  `codecin_linux.spec` / `build_win.bat` 已删除, 安装路径统一为 pip。

### 修复

- `python cpu.py --help` 首行版本号不再停留在旧版本, 改为直接读取 `codecin.__version__`。
- `--build-exe` 在程序文件不存在时不再抛裸 `FileNotFoundError`, 与普通路径一样给出
  `Build Error` 面板。
- `--build-exe` 在 Windows 上不再产生无扩展名、无法执行的输出文件。

## 升级到 5.5.0

```bash
pip install -U codecin==5.5.0
```

::: tip 从 5.3/5.4 升级要注意的三点
1. **Go CLI 已移除**, 脚本里如果调用过独立 Go 二进制, 请改为 `codecin` / `python cpu.py`;
2. **`import "lib/x.cin"` 仍可用, 但推荐裸名字** `import "x.cin"` (自建模块写 `"./x.cin"`);
3. **预编译库不再随包分发**: 升级后原生库会在安装时用本机 Go 重新编译, 或按
   [安装说明](/guide/installation) 放入 Release 资产。
:::
