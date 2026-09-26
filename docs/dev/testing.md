---
description: "Code CIN 的测试与 CI: 安装开发依赖、pytest、ruff、两个文档/常量一致性检查、三路径验收、tests/ 逐文件覆盖点、CI 作业与本地复现命令。"
---

# 测试与 CI

Code CIN 的回归体系分四层: **单元/指令级 pytest**、**两条一致性门禁**
(`gen_isa_docs.py --check` 与 `gen_native_isa.py --check`)、**三执行路径验收**
(`script/check_paths.py`)、以及 **GitHub Actions CI** (`test` / `integration` /
`lint` / `native` / `dist` 五个作业)。

## 安装开发依赖

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` 的内容是 `-r requirements.txt` (只有 `rich>=13,<15`) 加上:

| 依赖 | 用途 |
|------|------|
| `pytest>=7` | 测试运行器 |
| `pytest-cov>=5` | 覆盖率 (CI 的 integration 作业用, `--cov-fail-under=70`) |
| `ruff>=0.5` | 静态检查 |

用 `pip install -e ".[dev]"` 也可以 (见 `pyproject.toml` 的
`[project.optional-dependencies] dev`)。

## 运行测试

```bash
python -m pytest                                   # 全量
python -m pytest tests/test_three_paths.py         # 单个文件
python -m pytest -k "mmu or crom" -v               # 按关键字
python -m pytest -rs                               # 显示 skip 原因 (CI integration 用)
python -m pytest --cov=codecin --cov-report=term-missing --cov-fail-under=70
```

`pyproject.toml` 已配置 `testpaths = ["tests"]`、`addopts = "-ra"`、
`pythonpath = ["."]`, 所以在仓库根目录直接 `python -m pytest` 就是全量。

::: info 临时目录与 native skip
`tests/conftest.py` 提供会话级的 `workdir` 夹具, 落在仓库内的 `.pytest_tmp/`
(会话结束清理), 这样在沙箱环境里也能安全写文件。

依赖原生库的用例 (`test_three_paths.py`、`test_cin_host.py`、`test_cin_system.py`、
`test_libs.py`、`test_native_hardening.py`) 用
`pytest.mark.skipif(native.get_engine() is None)` 跳过。**没编译原生库时它们会静默
skip**, 所以 CI 的 integration 作业会先断言库真的能加载。
:::

## 静态检查 (ruff)

```bash
ruff check codecin cpu.py script tests
ruff check --fix codecin cpu.py script tests      # 自动修可修的
ruff format --check codecin cpu.py script tests   # 可选: 只检查格式
```

`pyproject.toml` 的 ruff 配置:

| 项 | 值 |
|----|-----|
| `target-version` | `py38` |
| `line-length` | `100` |
| `lint.select` | `E4, E5, E7, E9, F, I, UP, B, SIM, C4` |
| `lint.ignore` | `E501` (行宽交给 `line-length` 之外的风格判断) |

注意 CI 只跑 `ruff check`, 不含 `ruff format`; 提交前至少让 `ruff check` 干净。

## 两条一致性门禁

```bash
python script/gen_isa_docs.py --check     # docs/ISA.md 与 docs/reference/isa.md 与 isa.py 一致
python script/gen_native_isa.py --check   # Go 常量生成物与 isa.py / __version__ 一致
```

```text
$ python script/gen_isa_docs.py --check
ISA docs up to date.

$ python script/gen_native_isa.py --check
native isa_gen.go up to date.
compiler syscalls.go up to date.
native version_gen.go up to date.
```

| 脚本 | 生成物 | 失败提示 |
|------|--------|----------|
| `gen_isa_docs.py` | `docs/ISA.md`、`docs/reference/isa.md` | `ISA doc OUT OF DATE: <path>` / `ISA doc missing` |
| `gen_native_isa.py` | `engine/isa_gen.go`、`compiler/syscalls.go`、`engine/version_gen.go` | `<label> OUT OF DATE` / `换行不是 LF (CRLF)` / `missing` |

修复方式永远是重新生成, 然后**重新编译原生库**:

```bash
python script/gen_isa_docs.py
python script/gen_native_isa.py
cd codecin/native && sh build.sh && cd ../..
python -m pytest
```

::: warning 生成物必须 gofmt 干净且是 LF
`gen_native_isa.py --check` 按**字节**比对, Windows 下写出的 CRLF 会被判为不一致。
CI 的 `native` 作业另有 `gofmt -l .` 门禁 —— 生成器已按 gofmt 规则对齐映射字面量,
所以正常情况下 `gofmt -l` 应该是空的。
:::

## 三路径一致性验收

三路径一致是这个项目的核心约束: 同一份 UCBC 字节码在解释器 / JIT / Go 原生 VM 下
必须给出相同的 stdout、退出码与终态。

```bash
python script/check_paths.py                        # 默认检查 examples/*.cin
python script/check_paths.py examples/control_flow.cin
python -m pytest tests/test_three_paths.py tests/test_paths_consistency.py
```

`check_paths.py` 的三条路径与命令行参数:

| 路径 | 参数 |
|------|------|
| 解释器 | `--no-native` |
| JIT | `--no-native --jit` |
| Go 原生 | (无额外参数) |

输出形如:

```text
[ok]   bitwise_builtins.cin (三条路径输出一致, 412 字节)
[FAIL] modules_demo.cin: 路径不一致 -> JIT
    ...
3/4 passed
```

它**排除** `system_interaction.cin` (按设计只能在原生路径运行, 见脚本里的
`NATIVE_ONLY`), 也刻意不纳入根目录的 `basic.cin` (用了 `srand(time())`, 输出依赖时钟)。

手工三路径:

```bash
python cpu.py examples/control_flow.cin --no-native
python cpu.py examples/control_flow.cin --no-native --jit
python cpu.py examples/control_flow.cin
```

允许的差异只有两类: 与时间/环境相关的输出 (`time()`、`cwd()`、主机名), 以及
Python `math` 与 Go `math` 在超越函数上的末位舍入 (通常 ≤ 1 ulp)。

## Go 侧检查

```bash
cd codecin/native
go build ./...                # 全部包 (Go 侧不含 CLI 入口)
go vet ./...
gofmt -l .                    # 必须无输出
go test ./... -race -count=1
```

交叉编译自检 (不需要 C 交叉工具链):

```bash
cd codecin/native
for t in linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64; do
  GOOS="${t%/*}" GOARCH="${t#*/}" CGO_ENABLED=0 \
    go build ./engine ./ir ./compiler ./aot
done
```

架构门禁 (CI 里以 shell 断言实现, 本地也有对应的 pytest):

```bash
cd codecin/native
# 唯一的 package main 必须是 c-shared 库入口
grep -rl '^package main' --include='*.go' . | grep -v '^\./main\.go$'   # 必须无输出
python -m pytest ../tests/test_no_go_cli.py
```

## `tests/` 逐文件覆盖点

| 文件 | 覆盖点 |
|------|--------|
| `conftest.py` | 会话级工作目录夹具 (`workdir` → 仓库内 `.pytest_tmp/`, 会话结束清理) |
| `helpers.py` | 测试辅助: `reg`/`imm`/`mem` 操作数构造、`new_cpu`、`run_program`、`run_cin_source`、`run_cin_file`、`asm_program`、`snapshot` |
| `test_aot.py` | AOT 产物可独立运行、交叉编译、Linux ELF 无 `PT_INTERP` (静态)、非法 `--target` 报 `AotError` |
| `test_assembler_ext.py` | 汇编器 `.equ` 常量、立即数/偏移表达式、符号算术、数据段表达式 |
| `test_cache.py` | 缓存命中/缺失计数与 `stats` 单元行为 |
| `test_cin_host.py` | CIN 宿主能力 (GUI 画布 / 联网音频); 依赖原生库, 无则 skip |
| `test_cin_new_features.py` | 位运算符、`idiv`、字符串单字符访问、`min/max`、`floor/ceil/round`、`atoi`、`trim/ltrim/rtrim` |
| `test_cin_syntax.py` | 新语法: break/continue、do-while、switch、复合赋值、自增自减、三目、类型别名与字面量、转换内建 |
| `test_cin_system.py` | 系统原生交互 / Termux API 内建; 依赖原生库, 无则 skip |
| `test_cli.py` | argparse 参数解析、帮助文本、退出码 |
| `test_compiler_errors.py` | 非法程序必须被 Go 与 Python 两侧同时拒绝; 合法程序两侧都接受 |
| `test_cpu_interpreted.py` | 解释器指令级黄金值 (指令元组直接注入)、栈/内存往返、向量、异常路径 |
| `test_debugger.py` | 断点 continue 豁免重入、`--step` 与断点共用命令集、graceful quit |
| `test_examples.py` | `examples/` 每个示例在解释路径完整运行且结果确定 |
| `test_features_ab.py` | `--bounds-check` / CIN assert、`--seed` 确定性、`--disasm` 反汇编 |
| `test_features_crom_mmu.py` | CROM v3 + MMU 页表持久化 (roundtrip / 兼容 / 忽略模式) |
| `test_features_import.py` | 字符串内建 (`substr`/`indexof`/`upper`/`lower`) 与 `import`/`lib` 模块化 |
| `test_features_importloc.py` | import 行级源映射: 错误定位精确到模块 `file:line` |
| `test_features_mmu.py` | MMU identity 页表、map / unmap / 权限 / 缺页 + CPU 集成 |
| `test_features_remote.py` | `--debug-server` 远程驱动式调试协议 (socket 集成) |
| `test_isa_dispatch.py` | Opcode 枚举与分组计数、dispatch 自动注册完整性、ISA 文档生成一致性 |
| `test_isa_single_source.py` | ISA 单一真源守卫: 各手写映射表 (含 `jit._JIT_OPS`) 与 `isa.py` 不漂移 |
| `test_libs.py` | 官方标准库 (`codecin/lib/*.cin`) 行为回归; 依赖原生库, 无则 skip |
| `test_libs_ext.py` | 新增标准库 `bits/stat/hash/validate/matrix/queue` 回归 |
| `test_literals_and_bom.py` | 数值字面量与带 UTF-8 BOM 源码中 `import` 的双端一致性 |
| `test_memory_protection.py` | `memory.py` 保护检查统一: 浮点/块读写不可绕过保护 |
| `test_native_hardening.py` | 原生库加固: 步数用尽报错、不可信字节码、CROM 解压上限 |
| `test_native_lib_lookup.py` | 原生库查找顺序: 架构专属文件名优先于通用名 |
| `test_no_go_cli.py` | 架构门禁: 唯一 `package main` 是 c-shared 入口; 源码树无构建产物 |
| `test_packaging.py` | 打包设计: package-data 含内置库不含二进制、MANIFEST 带 Go 源码、只发 sdist、`BuildPyWithNative` |
| `test_paths_consistency.py` | 源码级三路径一致性 (跑 `examples/*.cin` 逐字节比对 stdout) |
| `test_switch_semantics.py` | switch 语义回归 (Go 与 Python 双编译器对齐) |
| `test_three_paths.py` | 解释 / JIT / 原生终态快照一致; 原生批量记账口径与逐条一致 |
| `test_version.py` | 版本单一真源: pyproject 无静态 version、Go `BuildVersion` 同步、semver、`--version` |
| `test_workflows.py` | 工作流静态校验: YAML 可解析、表达式函数白名单 (无 `replace`)、`matrix.*` 已声明、job/step 形状、artifact 名唯一 |

## CI 作业

`.github/workflows/ci.yml` 在 `push`(`master`/`main`)、`pull_request` 与手动
触发时运行, 权限只有 `contents: read`, 并有 concurrency 取消同分支旧运行。
所有作业都 `submodules: recursive` 检出 (文档一致性检查依赖文档站子仓库)。

| 作业 | 名称 | Runner / 超时 | 做什么 |
|------|------|---------------|--------|
| `test` | `pytest (py${{ matrix.python-version }})` | ubuntu-latest / 30 min | Python **3.9 / 3.11 / 3.13** 矩阵: 装 `requirements-dev.txt` → `gen_isa_docs.py --check` → `gen_native_isa.py --check` → `python -m pytest` → 检查是否有源码被 `.gitignore` 意外吞掉 |
| `integration` | `原生/Go 集成 (真实跑原生路径)` | ubuntu-latest / 45 min | Python 3.11 + `setup-go` (版本取自 `codecin/native/go.mod`) → `go build -buildmode=c-shared -o ../libcodecin_native.so .` → **断言 `native.get_engine() is not None`** → `python -m pytest -rs --cov=codecin --cov-report=term-missing --cov-fail-under=70`, 环境变量 `CODECIN_AOT_TESTS=1` |
| `dist` | `打包 (wheel/sdist 必须含内置标准库)` | ubuntu-latest / 30 min | `CODECIN_SKIP_NATIVE=1 python -m build` → 断言 wheel/sdist 各含 ≥19 个 `codecin/lib/*.cin` 且**不含** `.so/.dylib/.dll` → 装 wheel 后跑一个 `import "str.cin"` 的冒烟程序 |
| `lint` | `ruff` | ubuntu-latest / 15 min | `ruff check codecin cpu.py script tests` |
| `native` | `Go native (build check)` | ubuntu-latest / 30 min | `go build ./...`、`go vet ./...`、`gofmt -l .` 门禁、`go test ./... -race -count=1`、断言 Go 侧无额外 `package main`、五次交叉编译自检、Linux c-shared 构建 |

`test` 作业的最后一步值得一提: 它用
`git ls-files --others --ignored --exclude-standard -- '*.py' '*.go' '*.cin' '*.asm'`
列出被忽略的源码文件, 防止新文件因为 `.gitignore` 规则而根本没被提交。

`.github/workflows/release.yml` 是发布流程 (tag `v*` 触发), 含 `version-check` /
`native` / `python-dist` / `release` 四个作业, 见 [打包与发布](/dev/packaging)。

::: tip `test_workflows.py` 为什么存在
GitHub Actions 的表达式函数集里**没有 `replace()`** —— 用了它的工作流在 GitHub 上
会被判为 `Invalid workflow file`, 本地却看不出来。`tests/test_workflows.py` 因此在本地
就把这类错误拦住: 校验 YAML 可解析、表达式函数在官方白名单内、`matrix.<key>` 都在同
job 的 matrix 里声明过、每个 job 有 `runs-on` + `steps`、每个 step 有 `uses` 或 `run`。
:::

## 本地复现 CI

```bash
# 1) 与 test 作业等价 (任选一个 Python 版本)
python -m pip install -r requirements-dev.txt
python script/gen_isa_docs.py --check
python script/gen_native_isa.py --check
python -m pytest
git ls-files --others --ignored --exclude-standard -- '*.py' '*.go' '*.cin' '*.asm'

# 2) 与 lint 作业等价
ruff check codecin cpu.py script tests

# 3) 与 native 作业等价
cd codecin/native
go build ./... && go vet ./... && gofmt -l . && go test ./... -race -count=1
grep -rl '^package main' --include='*.go' . | grep -v '^\./main\.go$'
for t in linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64; do
  GOOS="${t%/*}" GOARCH="${t#*/}" CGO_ENABLED=0 go build ./engine ./ir ./compiler ./aot
done
go build -buildmode=c-shared -o /tmp/libcodecin_native.so .
cd ../..

# 4) 与 integration 作业等价 (真实原生路径 + 覆盖率门槛)
cd codecin/native && go build -buildmode=c-shared -o ../libcodecin_native.so . && cd ../..
python -c "from codecin import native; assert native.get_engine() is not None"
python script/check_paths.py
CODECIN_AOT_TESTS=1 python -m pytest -rs \
  --cov=codecin --cov-report=term-missing --cov-fail-under=70

# 5) 与 dist 作业等价 (本地构建发布物)
CODECIN_SKIP_NATIVE=1 python -m build
python -m pytest tests/test_packaging.py tests/test_version.py
```

Windows PowerShell 下的等价写法 (Go 部分):

```powershell
cd codecin\native
$env:CGO_ENABLED = '1'
go build ./...; go vet ./...; gofmt -l .
go test ./... -race -count=1
go build -buildmode=c-shared -o ..\libcodecin_native.so .
cd ..\..
python -c "from codecin import native; print(native.get_engine())"
```

::: warning 覆盖率门槛只在 integration 作业里生效
`--cov-fail-under=70` 需要原生库就绪 (否则大量用例 skip, 覆盖率自然偏低)。
在没编译原生库的机器上跑带门槛的覆盖率命令会失败, 这是预期行为 —— 先按
[编译 Go 原生库](/dev/build-native) 建好库。
:::

## 相关页面

- [项目结构](/dev/structure) — tests/ 与 script/ 在仓库中的位置
- [编译 Go 原生库](/dev/build-native) — 让 integration 作业前置条件成立的步骤
- [打包与发布](/dev/packaging) — `dist` 作业与 Release 流程
- [执行路径](/guide/execution-paths) — 三路径一致性的语义背景
- [贡献指南](/dev/contributing) — 提交前应跑哪些检查
