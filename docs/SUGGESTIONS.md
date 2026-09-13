# Code CIN (UCPU) 更新建议与优化建议

> 审查对象：`D:\ByUsi\Projects\UCPU`，HEAD = `4cc4748`  
> 审查日期：2026-09-13  
> 方式：4 路并行代码审查 + 在本机实测复现（Python 3.14.6 / go1.26.5 / ruff 0.16.3 / Windows）  
> 除标注「未复核」的条目外，所有结论均有实测输出或代码位置支撑。  
> 使用AI工具辅助审查，确保建议的准确性和完整性。

---

## 实施状态（批次 A + B + C 已应用）

测试规模：**413 项 Python 测试 + 2 个 Go 测试包**（原 158 项 Python、0 个 Go 测试）；
ruff（已扩充规则集）、`gofmt -l`、`go vet`、`go test -race`、ISA 门禁、产物级差分测试全部通过。

### 批次 A — 止血

| 建议 | 状态 | 落地内容 |
|------|------|----------|
| §2.1(a) 12MB DLL 出库 | ✅ | `git rm --cached codecin/codecin_native.dll`（工作区文件保留）；`.gitignore` 新增 `/codecin/*.dll`、`*.so`、`*.dylib` |
| §2.1(b) 修 `.gitignore` 通配 | ✅ | 删除 `*cache*` / `*tmp*`，改为锚定条目；`tests/test_cache.py`、`test_asm.asm` 已补回仓库 |
| §2.1(c) CI 接入 Go 与差分 | ✅ | 新增 `integration` 作业：编译 Go CLI + 原生库 → 断言原生库可加载（否则红灯）→ 全量 `pytest -rs` → 覆盖率报告 → `diff_go_python.py`；另加"被忽略源码"守卫 |
| §2.1(d) Go 侧测试 | ✅ | `native` 作业新增 `go vet`、`gofmt -l` 门禁与 `go test -race`；新增 `compiler`、`engine` 两个测试包 |
| §2.3 go.mod 与文档对齐 | ✅ | `go.mod` → `go 1.26`（不再锁补丁级）；8 处文档/脚本统一「Go 1.26+」 |
| §2.3 文档其余硬伤 | ✅ | 文档索引补 `docs/ISA.md`；架构图去掉 "C++ 生成"；指令集分组补 SYS；纠正 `basic.cin 400 行`；生成文件路径纠正为 `engine/isa_gen.go` |
| §3.1.1 汇编器 `#0x1F` | ✅ | 先判进制再剥后缀；新增 16 项参数化回归测试 |
| §3.2.1 原生路径批量记账 | ✅ | O(指令数) → O(1)；620k 指令实测 **~838ms → ~14ms（约 60 倍）**，0.4M → 45M instr/s；新增"原生路径不得逐条记账"断言 |
| §3.3 三路径恒真断言 | ✅ | `native_used in (True, False)` 改为按原生库可用性分支断言 |

### 批次 B — 正确性与加固

| 建议 | 状态 | 落地内容 |
|------|------|----------|
| §3.1.2 Go `genSwitch` 负 case / 标签错位 | ✅ | 按无符号位模式比较；`labels` 与 `branches` 严格一一对应；删除占位死代码；非常量 case 报编译错误 |
| §3.1.3 `switch` + `continue` 栈泄漏 | ✅ | 两端在 continue 目标处先弹选择器；新增 19 项 switch 语义/泄漏测试（含 Go CLI 对照） |
| §3.1.4 Go 静默吞错 | ✅ | 引入粘性 `compiler.err` + `failf`，覆盖未知函数、未定义变量、成员/下标、位运算、浮点取模、复合赋值、内置参数个数、循环外 break/continue 等；新增 50 项双端报错一致性测试 |
| §3.1.5 步数上限伪装正常结束 | ✅ | 原生 VM 与解释器统一报 `instruction limit reached`；CLI 退出码改为 1 |
| §3.1.6 不可信字节码 + CGO 边界 | ✅ | `decodeBytecode` 校验版本 / `count` / 参数个数 / 操作码；5 个 `//export` 全部加 `recover()` 与空指针守卫；`codecin_version` 改为静态常量（不再每次泄漏） |
| §3.1.7 CROM zip bomb | ✅ | 两端统一上限 `mem_size + 4MiB`；Python 侧不再把任意文件当旧格式载入；10 项加固测试 |
| §3.1.8 数值字面量 | ✅ | 两端先取数字再吃后缀；十进制 64 位范围检查；12 项字面量一致性测试 |
| §3.1.9 BOM import | ✅ | Python `utf-8-sig`、Go `TrimPrefix("\ufeff")`；双端测试 |
| §3.3/§3.4 差分升级 + 用例扩充 | ✅ | 新增 `--dump-bytecode`，差分脚本先比 UCBC 字节再比 stdout；6 个示例产物**逐字节一致**；新增 Go 侧测试包 |

### 批次 C — 工程化

| 建议 | 状态 | 落地内容 |
|------|------|----------|
| §2.2 版本单一真源 | ✅ | `pyproject.toml` 改 `dynamic = ["version"]`（源自 `codecin.__version__`）；Go 侧由生成器产出 `engine.BuildVersion` 并由 `--check` 校验；新增 `--version`；原生库不再自报 `1.0` |
| §2.2 打包配置 | ✅ | 新增 `[build-system]`、`[project.scripts]`、`[tool.setuptools]`；PyInstaller 两个 spec 补 `lib/`、`misc/vim` 与平台原生库（缺库时不阻断打包） |
| §2.4 发布流程 | ✅ | 新增 `release.yml`：tag 与版本一致性校验 → 5 平台 Go CLI + 三平台原生库 + sdist/wheel → Release 资产 |
| §2.4 编辑器/换行配置 | ✅ | 新增 `.gitattributes`（统一 LF，`*.bat` CRLF，二进制标记）与 `.editorconfig` |
| §3.2.2/§3.2.3 解释器性能 | ✅ 部分 | 新增快路径（无断点/单步/JIT/追踪时紧凑循环），解释执行 **1.15x**；统计记账仍是最大单项（见下方遗留项） |
| §3.4 ISA 单一真源扩展 | ✅ | 新增守卫测试：`ARG_COUNTS`、`stats.latency`、`jit._JIT_OPS` 与 `Opcode` 表必须一致（116 项） |
| §3.4 ruff 规则集 | ✅ | `select` 从 4 组扩到 `E4/E5/E7/E9/F/I/UP/B/SIM/C4`，并清理全部 79 项违规（含 49 项自动修） |

### 仍遗留 / 需你决定

1. **`requires-python >= 3.8` 的下界策略**：CI 矩阵仍是 3.9/3.11/3.13，未验证 3.8；要么加 `3.8` 到矩阵（`ubuntu-22.04` 才稳），要么把 `requires-python` 与文档提到 `>=3.9`。
2. **`codecin/native/tmpdump/`**：仍是未跟踪、无引用、无参数校验的调试程序（CI 已豁免它的忽略检查）。建议删除或改成 `go test`。
3. **覆盖率门槛**：CI 已跑 `--cov` 报告但未设 `--cov-fail-under`（本地无 pytest-cov，未能先测基线）。设阈值前请先看一次 CI 输出。
4. **解释器统计记账**：`stats.record_instruction` 仍占解释执行约 17%（cProfile）；进一步优化需把 `inst_profiler`/`performance_counters` 改为可选或批量。
5. **原生调用边界拷贝**：整块内存被 C→Go→Python 复制 3~4 次；削减它需要改 ABI（风险较高，未做）。
6. **`go.mod` 是否降到 1.21**：已确认 Go 代码未使用 1.21 之后的 stdlib/语言特性，理论上可以降；但本地无旧工具链，未验证。

---

## 0. 实测基线（先确认现状是健康的）

| 项目 | 结果 |
|------|------|
| 测试套件 | **158 passed / 23.5s** |
| Go/Python 差分 | **6/6 passed** |
| ISA 文档门禁 | `gen_isa_docs.py --check`、`gen_native_isa.py --check` 均通过 |
| 文档化 CLI 路径 | `--no-native` / `--jit` / `--mmu` / `--bounds-check` / `.asm` / `basic.cin` 全部 exit 0 |
| 三个 remote | gitee(origin) / github / codeberg，`push.bat` 同步推送 |

**结论：项目不是"有问题"，而是"工程化与结果可信度"明显落后于功能完成度。** 功能跑得通，但支撑"功能确实正确"的那套机制（CI 覆盖、产物可追溯、单一真源）缺口很大；另有若干会**静默产生错误结果**的缺陷，测试目前测不到。

### 性能实测（`.tmp_bench.cin`，620,042 条指令）

| 路径 | 耗时 | 吞吐 |
|------|------|------|
| 纯 Python 解释 | 6,298 ms | 98k instr/s |
| Python + JIT | 2,521 ms | 246k instr/s |
| Go 原生（现状） | 1,435 ms | 432k instr/s |
| **Go 原生（去掉 Python 侧统计循环）** | **130~155 ms** | **4.0~4.7M instr/s** |

最后一行是本次审查最大的发现：**原生路径约 85% 的时间花在 Python 侧的记账循环上**（两轮独立复现，详见 §3.2.1）。

---

## 1. 优先修复清单（按影响排序）

| # | 问题 | 影响 | 位置 | 工作量 |
|---|------|------|------|--------|
| 1 | 原生路径每指令调用 Python 记账 | 原生**慢约 7 倍**（实测 1000ms → 130~155ms） | `codecin/cpu.py:1436-1440` | ~10 行 |
| 2 | 汇编器把 `#0x1F` 解析成 `1` | **静默错误机器码**（实测） | `codecin/assembler.py:345-346` | ~5 行 |
| 3 | Go `genSwitch` 负 case 被丢弃、非整数 case 标签错位 | **静默错误分支**（实测 `case -1` → 走 default） | `codecin/native/compiler/codegen.go:503-539` | ~30 行 |
| 4 | `switch` 内 `continue` 泄漏 8B 栈/次 | 长循环必崩（栈堆碰撞） | `codegen.go:486/543`、`cin.py:1464/1498` | ~20 行 |
| 5 | 12MB DLL 入库 + 与源码不对应 | clone 体积 / 测试跑的是旧二进制 | `codecin/codecin_native.dll` | 见 §2.1 |
| 6 | CI 里约 20 个原生/Go 用例永久 skip | 核心卖点零覆盖 | `.github/workflows/ci.yml:22-31` | ~15 行 |
| 7 | `.gitignore` 的 `*cache*`/`*tmp*` 吞掉真实测试文件 | `tests/test_cache.py` 不在仓库里 | `.gitignore:14-15` | 1 行 |
| 8 | 无输出大小上限 / CROM 解压无上限 | OOM、zip bomb | `vm.go:58`、`crom.py:133` | ~30 行 |

---

## 2. 更新建议（面向 5.4.0）

### 2.1 最高优先：让"仓库内容"与"声称的功能"重新对齐

**(a) 移除入库的构建产物。**

已核实：`codecin/codecin_native.dll`（12,307,968 B）被 git 跟踪，是全仓最大文件；`.git` 因此膨胀到约 41.8 MB。

更严重的是**它和源码不对应**——`go version -m` 给出了铁证：

```
$ go version -m codecin/codecin_native.dll
  build  vcs.revision=9a3087cd19ba9fa9458fad5cc01577e4a35e85ac
  build  vcs.modified=true          ← 从脏工作树构建
$ git rev-parse HEAD
  4cc4748                           ← 落后 3 个提交

$ go version -m codecin/native/codecin.exe      # 本地 Go CLI 同样是旧版
  build  vcs.revision=9a3087c...  vcs.modified=true
```

也就是说：**任何 clone 这个仓库的人，跑测试时用的是 3 个提交之前的、脏工作树构建的二进制**。我用 HEAD 源码重建后对比：

```
$ go build -buildmode=c-shared -ldflags '-linkmode external -extldflags "-static"' -o fresh.dll .
committed: 68BA77BB...
fresh    : 02784971...   identical: False
# 两者都 158 passed —— 说明当前行为尚可，但"产物等价"这件事无人能验证
```

建议：

```powershell
git rm --cached codecin/codecin_native.dll
# .gitignore 追加
/codecin/*.dll
/codecin/*.so
/codecin/*.dylib
```

然后三选一：①安装脚本现场构建（`install.ps1`/`install.sh` 已经在做，只是同时又提交了产物）；②CI 产物作为 Release 资产；③保留一个 `script/fetch_native.ps1` 从 Release 拉取并校验哈希。若要缩小 clone 体积，需 `git filter-repo` 清史（全员重新 clone）。

**(b) 修 `.gitignore` 的两个危险通配。**

```gitignore
# 现在（.gitignore:14-15）—— 匹配任意路径子串
*cache*
*tmp*
```

已实测后果：`tests/test_cache.py`（4 个真实缓存单测）**不在 git 里**，新 clone 直接丢失这块覆盖，而 `docs/BUILDING.md:375` 还把它列在测试清单中。

```
$ git check-ignore -v tests/test_cache.py
.gitignore:14:*cache*   tests/test_cache.py
$ git ls-files tests/ | Select-String test_cache      # 无输出
```

替换为锚定条目：

```gitignore
/.pytest_cache/
/.pytest_tmp/
/.ruff_cache/
/.gocache/
/.gotmp/
/.venv/
*.egg-info/
/codecin/*.dll
/codecin/*.so
/codecin/*.dylib
```

并 `git add -f tests/test_cache.py` 把它补回仓库。建议再加一道 CI 守卫，防止"磁盘上有、git 里没有"再次发生：

```yaml
- name: Detect accidentally-ignored sources
  run: |
    git status --porcelain --ignored=matching -- '*.py' '*.go' '*.cin' | grep '^!!' && exit 1 || true
```

**(c) 让 CI 真正跑到原生与 Go 路径。**

已核实：`ci.yml` 的 `test` 作业只装 Python 依赖，而 Go/原生测试全部以"文件存在/库可加载"为 skip 条件：

```python
# tests/test_go_compiler.py:14-22
GO_CLI = next((p for p in _CANDIDATES if os.path.exists(p)), None)
needs_cli = pytest.mark.skipif(GO_CLI is None, reason="Go CLI not built")
```

`codecin/native/codecin.exe` 被 `.gitignore` 忽略、不在仓库里 → **CI 上这个文件永远不存在 → 9 个用例全 skip**；`test_libs.py` / `test_cin_host.py` / `test_cin_system.py` 同样的模式，合计约 **20 个用例在 CI 中从不执行**。`script/diff_go_python.py` 也从未被 CI 调用。

而 `test_go_cli_matches_python_compiler` 正是 README 反复宣称的"Go/Python 逐字节等价"的**唯一自动化证据**。建议：

```yaml
  test:
    steps:
      # ... setup-python / pip install 之后追加
      - uses: actions/setup-go@v5
        with:
          go-version-file: codecin/native/go.mod
      - name: Build Go CLI + native lib
        working-directory: codecin/native
        run: |
          go build -o codecin ./cmd/codecin
          go build -buildmode=c-shared -o ../libcodecin_native.so .
      - run: python -m pytest
      - run: python script/diff_go_python.py     # 目前完全没接入
```

并把 `skipif` 改成"CI 里缺工具链就红灯"：`skipif(GO_CLI is None and not os.environ.get('CI'), ...)`。

**(d) 补 Go 侧测试。** 全仓 `*_test.go` 数量为 **0**。约 5.8k 行 Go（VM、CROM、编码器、编译器）只被 `go build` 覆盖。建议新增 job：

```yaml
  go-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version-file: codecin/native/go.mod, cache: true }
      - run: go vet ./... && gofmt -l .
        working-directory: codecin/native
      - run: go test ./... -race -count=1
        working-directory: codecin/native
```

### 2.2 版本与发布（当前是三方漂移）

| 位置 | 现值 |
|------|------|
| `pyproject.toml:3` | `5.3.0` |
| `codecin/__init__.py:1` | `5.3.0` |
| `codecin/native/main.go:133` | **`codecin-native 1.0 (Go)`** |
| `CHANGELOG.md:8` | `5.3.0` |
| CLI | **没有 `--version`** |

`codecin/native.py:317` 会把这个 `1.0` 写进日志，用户看到的版本号和包版本对不上。建议：

```toml
# pyproject.toml
[build-system]                       # 目前完全没有，uv sync / pip install . 依赖自动发现
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
dynamic = ["version"]
[project.scripts]                    # 目前没有入口点，pip install 后没有 codecin 命令
codecin = "codecin.cli:main"

[tool.setuptools]
packages = ["codecin"]
[tool.setuptools.dynamic]
version = { attr = "codecin.__version__" }
```

- Go 侧版本改为构建时注入：`go build -ldflags "-X main.version=$(VERSION)"`，或由 `script/gen_native_isa.py` 同源生成（该脚本已有生成 Go 常量的先例）。
- CLI 加 `--version`（`argparse` 的 `action='version'`）。
- 目前唯一的入口是 `python cpu.py`，而 `codecin/native/codecin.exe` 也叫 `codecin` —— 加 `[project.scripts]` 后要明确区分"Python CLI"与"Go CLI"。

### 2.3 文档必须改的几处（都是可复现的误导）

| 位置 | 问题 | 实测证据 |
|------|------|----------|
| `README.md:425` | 称差分校验"含 `basic.cin` 400 行综合示例" | `basic.cin` 实际 **635 行**，且差分脚本 `diff_go_python.py:60-65` 与测试都**显式排除**它（srand(time()) 输出依赖时钟） |
| `docs/BUILDING.md:61,108,385,392` | 4 处让用户跑 `test_asm.asm` | 该文件被 `.gitignore:9 /*.asm` 忽略，**不在仓库中**，新 clone 必然报错 |
| `go.mod:3` + 11 处文档 | `go.mod` 要求 `go 1.26.5`，README/BUILDING/install.sh/install.ps1/build.sh/build.ps1 全写 "Go 1.21+" | 照文档装 1.21 会在 `go build` 第一步失败；建议 `go.mod` 写 `go 1.26`（不锁补丁级）+ 文档统一为 1.26+，或在安装脚本里显式校验 `go env GOVERSION` |
| `README.md:21-25` | 文档索引表漏了 `docs/ISA.md` | 而 `README.md:211,413` 都链接了它 |
| `README.md:73,348,660` | 架构图仍写 "C++ 生成" / "原生C++" | 全仓无任何 C++ 后端 |
| `README.md:137-141` | mermaid 里 5 组指令数 28+40+27+10+6=111，未含 SYS | `Opcode` 实际 112 个（`docs/ISA.md:17` 是对的） |
| `script/gen_native_isa.py:7`、`BUILDING.md:424` | 写作 `codecin/native/isa_gen.go` | 实际是 `codecin/native/engine/isa_gen.go` |
| `CHANGELOG.md:18` | 称 `diff_go_python.py` 保证"产物逐字节等价" | 该脚本只比较 **stdout 文本**（`diff_go_python.py:79`），不比较字节码 |
| `script/diff_go_python.py:64-65` | 注释解释排除 `basic.cin` | `examples/` 目录里根本没有 `basic.cin`，注释已过时 |
| `script/install_termux.sh` + `README.md:381,384` + `CHANGELOG.md:22` | 文档称该脚本编译 Go CLI | HEAD(`4cc4748`) 已把该行注释掉（"不能正常使用"），文档未同步 |

建议把指令数改成自动生成（在 README 里用 `<!-- ISA-COUNTS:BEGIN -->` 标记区块，由 `gen_isa_docs.py --check` 一并校验），避免第二次写错（`tests/test_isa_dispatch.py:34` 的注释"原文档误写 28"说明已经发生过一次）。

### 2.4 缺失的工程基建

- **无 Release 工作流**：`.github/` 下只有 `ci.yml`。项目有 3 个 remote、有 PyInstaller spec、有跨平台安装脚本，值得加 `release.yml`（tag → 建 Go CLI/原生库 → 打包 → 上传 Release 资产）。
- **无 `CONTRIBUTING.md` / `.editorconfig` / `.gitattributes`**。本机 `core.autocrlf=true`，而仓库里有 `.sh` 脚本 —— 加 `.gitattributes`（`*.sh text eol=lf`、`*.bat text eol=crlf`、`*.dll binary`）能避免换行事故。
- **PyInstaller spec 缺数据文件**：`codecin.spec:6` 与 `codecin_linux.spec:6` 都是 `datas=[]`，13 个 `lib/*.cin` 标准库不会进包，而 `cin.py:374-385` 依赖父目录的 `lib/` → 冻结产物里 `import "lib/math.cin"` 必然失败。`codecin_linux.spec:5` 还是 `binaries=[]`（Linux 包连原生库都没有），且该文件在全仓文档中零引用，而 `BUILDING.md:296` 宣称 `codecin.spec` 是"唯一入口"。
  ```python
  datas = [(os.path.join(ROOT, 'lib'), 'lib'), (os.path.join(ROOT, 'misc', 'vim'), 'misc/vim')]
  ```
- **仓库根目录残留旧项目产物**：`dist/ucpu/ucpu.exe`、`dist/ucpu/ucpu_r_v5.2.0.zip`（旧名 ucpu、旧版本 5.2.0），与 `codecin.spec` 的 `name='codecin'` 不符，建议删除。
- **`codecin/native/tmpdump/`** 是未跟踪（被 `*tmp*` 吞掉）、无引用、无参数校验（`os.Args[1]` 越界即 panic）的调试程序，建议删除或改成真正的 `go test`。

### 2.5 功能路线建议（按投入产出比）

1. **`--dump-bytecode`**（Go CLI + Python）：这是把"逐字节等价"从口号变成可验证事实的前提，也是 §3.4 差分测试升级的基础。约 30 行。
2. **错误定位到列号**：`Token` 目前只有 `line`，且 `errors.py:30-42` 定义的 `line_num/filename` 字段在 38 处 `raise CompilerError` 中**一次都没传**——codegen 阶段的类型错误、未定义变量完全没有位置信息。补 `col` + 透传位置，是"能当正经语言用"的门槛。
3. **`codecin fmt`**（基于已有 tokenizer/parser 做格式化）：编辑器体验的关键一环，实现成本低，因为词法/语法已经现成。
4. **能力/沙箱开关**：`exec / file_* / audio_play / dir_list` 让 CIN 程序可以执行任意命令、读写任意文件、联网。作为教学/嵌入式语言这是合理设计，但建议显式提供 `--sandbox`（禁 exec + 限制文件根目录 + 禁网络），并在文档里写明默认不是沙箱。
5. **`--max-steps` 语义修正**：目前步数用尽返回 `StatusDone`，和正常结束无法区分（见 §3.1.5）。
6. **benchmark 套件**：`script/bench.py` 固定输出三路径的 instr/s（本次审查用的 `.tmp_bench.cin` 思路），纳入 CI 并把结果贴进 PR/CHANGELOG，才能守住性能不回退。
7. **REPL / LSP**：排在 fmt 之后。当前 `--step` 交互调试器已经是不错的底子。

---

## 3. 优化建议

### 3.1 P0：会静默产生错误结果的缺陷

#### 3.1.1 汇编器把十六进制立即数的尾字母 F 当类型后缀吃掉 ✅实测

`assembler.py:345-346` 在判断进制**之前**剥离后缀，而 `f/F` 本身就是合法十六进制数字：

```python
# codecin/assembler.py:344-346
# 数值后缀 (u/U/l/L 及 f/F) 在 64 位槽模型下无宽度差异, 直接忽略
while val and val[-1] in 'uUlLfF':
    val = val[:-1]
```

实测：

```
#0x1F      -> 1            （应为 31）
#0x1FF     -> 1            （应为 511）
#0x0F      -> 0            （应为 15）
#0xABCDEF  -> 703710       （应为 11259375）
#0x1F0     -> 496          （正确，尾字符不是 F）
#0xFF      -> ValueError   （落到 _eval_expr 反而正确）
```

而 `.equ N, 0x1F` 走 `_eval_expr` 得到 31 —— **同一个字面量在 `.equ` 与立即数位置结果不同**，`DB 0x1F` 会写入 `\x01`。

改法（先判进制，只在数字字符集之外剥后缀）：

```python
m = re.fullmatch(r'(0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\d[\d_]*)([uUlL]*[fF]?)', val)
if not m:
    raise ValueError(f"invalid immediate: {val}")
digits, _suffix = m.group(1), m.group(2)
```

并补断言用例：`#0x1F / #0xFF / #0xABCDEF / DB 0x1F / .equ 0x1F` 五条。

#### 3.1.2 Go `genSwitch` 负 case 丢失、非常量 case 标签错位 ✅实测

`codegen.go` 用 `-1` 当 default 哨兵，并在 `513-516` 跳过 `raw < 0` 的 case（负常量永远匹配不上）；`534-539` 又用**`branches` 的下标**去取 `labels[i]`，而 `503-506` 在常量求值失败时 `continue`、不往 `labels` 里补位——此后下标全部错位。

实测（`switch(x)` 且 `x == -1`）：

```
Go  (HEAD 源码重建)  ->  DEF      ← 错：case -1 被丢弃
Python               ->  NEG      ← 对
```

`case y:`（y 是变量，C 语义应报错）实测：

```
Go     ->  A          ← 把 case 1 的分派跳进了 case y 的代码体
Python ->  Compiler error（拒绝）
```

另外 `528-532` 是自认的占位死代码（`// placeholder, corrected below`），当所有 case 都非常量且无 default 时 `labels` 为空 → `labels[len(labels)-1]` = `labels[-1]` → **panic**。

改法：①比较值改用 `raw & 0xFFFFFFFFFFFFFFFF`，删除 `raw < 0` 的跳过；②`labels` 与 `branches` 等长（或用 `[]struct{lbl string; br *Node}` 绑定）；③删除 `528-532` 死代码；④`constValue` 失败直接返回编译错误（与 Python 对齐）。

#### 3.1.3 `switch` 内 `continue` 泄漏选择器栈槽 ✅（Go 报告 + 代码位置已核对）

`genSwitch` 先 `PUSH` 选择值（`codegen.go:486` / `cin.py:1464`），弹出写在 `lEnd` **之后**（`codegen.go:543` / `cin.py:1498`），而 `continue` 跳的是外层循环的 continue 标签 —— 绕过弹出，**每次迭代泄漏 8 字节**，长循环最终栈堆碰撞。

改法：把"丢弃选择值"下沉到每个分支体出口，或在 `continueLbls` 入栈时记录需要弹出的临时栈槽数，`continue` 分支先补 `ADDI SP,SP,#n` 再跳。补测：`while { switch { case X: continue } }` 与两层嵌套 switch 的 continue。

#### 3.1.4 Go 编译期大面积静默吞错（两端接受集不一致）

Go 的 `genValue/genStmt/genBinop` 没有 error 返回通道，语义错误只 `return`/`continue`，于是**漏发射指令、带着上一次的 x0 继续生成**；Python 在相同位置 `raise CompilerError`。报告给出的实测对照（未复核）：

| 输入 | Go | Python |
|------|----|--------|
| `println(foo())` | 输出 `0` | 报 `Unknown function: foo` |
| `a % b`（float） | 输出 `7.5` | 报错 |
| `~1.5` | 输出 `4609434218613702656` | 报错 |
| `s += "b"`（string） | 静默无操作 | 报错 |
| `increment zzz;`（未定义） | 正常输出 | 报错 |
| `switch(f)`（float 选择器） | 静默吞掉整条语句 | 报错 |

建议：把 `genValue/genBinop/genCall/...` 改为返回 `(*Type, error)`（或引入 `c.err` 统一在末尾检查），错误文本对齐 Python —— 这既是正确性修复，也是让差分测试有意义的前提（否则"两端都错但错法不同"永远测不出来）。

#### 3.1.5 步数上限与 PC 越界都伪装成正常结束

`vm.go:145-151` 把"步数用尽 / `pc<0` / `pc>=len(prog)`"统一 `finish(StatusDone, "")`，而 `native.py:254-262` 把 `status == 0` 记作 `halted=true`。于是**死循环或错误跳转会被当成正常退出**，退出码 0。建议新增 `StatusStepLimit` / `StatusPcOutOfRange`（至少填 `ErrMsg`），CPU 侧按错误处理。

顺带：`cpu.py:1420-1422` 的 `return not result.get('halted', False) or result.get('error') is None` 里，`or ... is None` 会让"未 halt 且无错误"也返回真，语义不够清晰，建议改成显式三分支。

#### 3.1.6 `decodeBytecode` 完全信任输入头部（原生库）

`vm.go:82-85` 把头部里的 `count` 直接当 `make` 容量（`count=0xFFFFFFFF` 时是 137GB 预分配），`argc`/`version` 不校验，而 `execute` 无条件索引 `args[0..2]`（`vm.go:401-409`）。建议：`count` 先用 `(len(bc)-13)/19` 夹取；补 `argc` 表校验；校验 `bc[4] == bcVersion`。

**配套建议：给 5 个 `//export` 函数各加 `defer recover()`。** 我已确认全仓 `recover()` 数量为 **0**，CGO 边界上的 panic 会直接终止 Python 宿主进程。我构造了 5 组畸形输入（空、随机、坏 magic、超大长度字段、截断）实测**当前都安全返回 null**，所以这是**加固建议而非已复现缺陷** —— 但这层保护不应依赖"恰好没踩到"。

#### 3.1.7 CROM 解压无上限 + 旧格式路径接受任意文件

`crom.py:133` 的 `zlib.decompress(body)` 不设 `max_length`（报告实测：65 KB 文件可解出 64 MiB，放大 1028×）；首 4 字节不是 `CROM` 时无条件按旧格式加载任意文件。建议：

```python
d = zlib.decompressobj()
raw = d.decompress(body, mem_size)          # 用头部 mem_size 作为硬上限
if d.unconsumed_tail or len(raw) != mem_size:
    raise CPUSimulatorError("CROM 解压长度与头部不符")
```

legacy 分支加显式开关（默认拒绝），`_restore_mmu_trailer` 每步校验剩余长度。

#### 3.1.8 Python 数值字面量后缀处理（两端不一致，且抛原生异常）✅部分实测

实测 `int a = 0xFFu`：

```
Go     -> a=255
Python -> Error: invalid literal for int() with base 0: '0xFFu'    ← 裸 ValueError，非 CompilerError
```

`cin.py:243/263` 在消费后缀之后才截取数值文本，且 `int()` 没有 try/except（无行列信息）。同时 Python 任意精度整数与 Go 的 64 位溢出策略相反（`int x = 9223372036854775808;`：Go 报错、Python 静默截断成 `-9223372036854775808`）。建议：先截数值文本、后缀单独消费；`int()/float()` 包 try/except 转 `CompilerError`；补 64 位范围检查。

#### 3.1.9 带 BOM 的源文件 `import` 失效（Windows 高发）

BOM 只在 tokenizer 里被容忍，而 import 预处理是按行正则（`codegen.go:1802`、`cin.py:400-403`），BOM 让 `^import` 匹配不上，然后报一个与真实原因无关的解析错误。改法：Python 用 `encoding='utf-8-sig'`；Go 读入后先 `TrimPrefix(text, "\ufeff")` 再 split。

### 3.2 P1：性能（含实测量化）

#### 3.2.1 ★ 最大杠杆：把原生路径的逐指令记账改成批量赋值 ✅实测

```python
# codecin/cpu.py:1436-1440
if 'steps' in result:
    for _ in range(min(result['steps'], self.config.max_instructions)):
        self.stats.record_instruction('?')
    self.stats.instruction_count = result['steps']
    self.stats.opcode_count.clear()
```

`Statistics.record_instruction`（`stats.py:165-170`）每次要更新 4 个结构：

```python
self.instruction_count += 1
self.opcode_count[opcode] += 1          # 随后被 clear() 丢掉
self.inst_profiler.record(opcode)       # 记录 62 万次无意义的 '?'
self.hot_instructions[opcode] += 1      # 同样只是把 '?' 累加 62 万次
self.performance_counters.record_instruction(opcode)   # 两次集合成员判断
```

`stats.py:123` 的 `inst_profiler.record` 还要再转一次 `instruction_count` 与 `opcode_count`。**而 `opcode_count` 在紧接着的 1440 行就被 clear 了** —— 整段循环算出的数据一处都没用上。

实测（`.tmp_bench.cin` 同构程序，620,042 步，同一进程 A/B，两轮独立复现）：

```
第 1 轮：record per instruction = 999.9 ms  |  bulk / 不逐条记账 = 154.7 ms   （620k → 4.0M instr/s）
第 2 轮：record per instruction = 927.6 ms  |  bulk / 不逐条记账 = 131.6 ms   （668k → 4.7M instr/s）
```

微基准：**1,000,000 条指令 → 1.87s 纯 Python 记账**（≈1.9µs/指令）。也就是说原生执行越成功（指令越多），这个税越重，恰好抵消了"用 Go 加速"的全部意义。

改法（约 10 行）：

```python
if 'steps' in result:
    steps = result['steps']
    self.stats.instruction_count = steps
    self.stats.opcode_count.clear()
    self.stats.hot_instructions.clear()
    self.stats.performance_counters.counters['instructions'] = steps
    # 不逐条调用 record_instruction
```

如果确实想要原生路径的指令统计，正确做法是**让 Go VM 返回聚合好的 opcode 直方图**（VM 里本来就有完整的解码循环，加一个 `[112]uint64` 计数器的成本可以忽略），而不是回到 Python 里数数。

顺带修一个语义不一致：现在 `opcode_count` 被清空、而 `hot_instructions['?']` 保留 62 万条，两个统计面板的数据互相矛盾。

#### 3.2.2 解释器里的记账同样是主要开销 ✅实测

`cProfile` 跑 620k 指令（profiler 下的 18.5s）：

```
ncalls   tottime  cumtime  function
620042   1.487    3.216    stats.py:165(record_instruction)     ← 17.4% 累计
620042   0.807    1.122    stats.py:123(record)                ← inst_profiler
620042   0.607    0.607    stats.py:54(record_instruction)     ← performance_counters
620042   1.932   16.081    cpu.py:1335(execute)
      1  2.137   18.526    cpu.py:1514(_run_interpreted)
```

`record_instruction` 一条链就吃掉约 17% 的解释时间（还有 154 万次 `dict.get`）。建议：把统计降级为可选（`--stats` 打开，或至少把 `inst_profiler` 与 `hot_instructions` 合并成一次 dict 更新），并在 `--debug` 之外的默认路径使用轻量计数器。

#### 3.2.3 解释器主循环缺少"快路径"

`cpu.py:1514-1563` 每条指令都要做：`_resume_bp_pc` 判断、`debug_server.check_conditional_breakpoints()`、`self.pc in self.breakpoints`、`config.step_mode`、`self.jit is not None`、`self._trace` 判断……这些在常规运行下全为 false，却逐指令付出属性查找与字典查找。

建议把断点/单步/JIT/追踪全部收进一个 `self._slow_path: bool`，在 `run()` 入口按配置算一次：

```python
def _run_interpreted(self):
    if not self._slow_path:
        return self._run_fast()      # 只保留 pc 校验 + execute + pc 推进
    ... 现有循环
```

`_run_fast` 里还可以把 `self.execute`、`self.pc`、`self.instructions` 预绑成局部变量（避免每指令的属性查找），这是纯 Python 解释器的常规优化手段。

#### 3.2.4 原生调用边界的拷贝与分配 ✅（代码位置已核对）

- `cpu.py:1433-1435` 用 `self.memory.write_block(0, bytes(mem[:len(self.memory)]))` 把整块内存逐段写回 —— 即使程序只改了 8 字节。
- `native.py:208-210` 每次调用都 `create_string_buffer` 复制 bytecode 与整块 memory；`main.go:31-32,51-52,90-93` 在 Go 侧再 `C.malloc` + copy 一遍 —— 同一份内存被复制 3~4 次。
- `vm.go:85-105` 每条指令 `append` 出一个 `args` 切片（≥1 次堆分配/指令），且 `Run` 每次重新解码全部字节码。
- `native.py:227-247` 的 `_parse_result` 用大量 `ctypes.string_at` 小片段拷贝拼装结果。

建议优先级：①只在需要时回写内存（Go 侧返回 dirty 页/区间，或比较后再写）；②`instruction` 改成 `{opcode uint8; nargs uint8; args [3]operand}` 或扁平 arena，把解码分配从 O(指令数) 降到 O(1)；③结果直接写进 `C.malloc` 的缓冲，去掉中间 Go 缓冲。

#### 3.2.5 包级可变状态（音频/画布）无锁

`audio.go:21-29`、`canvas.go:19-22` 的 `audioActive/audioTemp/curCanvas/...` 都是包级变量，整个 `native/` 目录**没有一处 `sync.` 或 `Lock(`。两个 `codecin_run` 并发时，A 的 `audioStop` 会删掉 B 正在播放的临时 WAV。建议把这些宿主对象收进 `vmState`，或至少加包级 mutex，并在文档里明确"`codecin_run` 可重入，但不共享音频/画布状态"。

#### 3.2.6 资源泄漏

- `audio.go:66-91`：每次 `audioPlay` 新建临时 WAV，`audioTemp` 被覆盖，旧文件无人删除（失败路径也不删）。
- `audio_other.go:23,35`、`canvas.go:270`：`cmd.Start()` 之后没有 `Wait()` → Unix 上留僵尸进程。
- `main.go:132-134` + `native.py:195-201`：`codecin_version` 每次 `C.CString`，Python 以 `c_char_p` 取走并 decode，**永不 free**（每次调用泄漏）。

### 3.3 P2：正确性与健壮性（值得排期）

- **反汇编不可回汇编**：`disasm.py:26,29` 用空格分隔操作数，而 `assembler.py:363-386` 只按逗号切分；`debugger.py:25-45` 的 `fmt_operand` 没有 float 分支（会打印出 `('float', 1.5)` 这种 Python 元组）。建议改逗号分隔 + 补 float/str 分支，并加 `asm → encode → disasm → asm → encode` 字节相等的 round-trip 测试。
- **损坏输入抛原生异常**：实测 `disassemble_bytes(b'CPUSA')` → `IndexError`，`b'UCBC'` → `struct.error`，`decode_program`（opcode=200）→ `ValueError`。建议统一转成 `CPUSimulatorError`。
- **深递归**：Python 约 1000 帧报 `RecursionError`（裸异常），Go 会增长到 1GB 后 **`fatal error: stack overflow` 直接终止进程**（报告实测深度 200000）。建议在词法阶段就用已有的括号深度计数（`cin.py:340-354`）设上限（如 200）并报 `CompilerError`。
- **`max_instructions` 与 `--bounds-check` 的可达性**：`tests/test_cli.py:33` 用 `--max-instructions 2000` 跑 635 行的 `basic.cin` 并只断言"退出码 0"——若实际指令数接近上限，这条断言会把"被截断"当成成功。建议断言 stdout 内容而非仅退出码。（未复核）
- **恒真断言**：`tests/test_three_paths.py:46` 的 `assert b.native_used in (True, False)` 恒为真（布尔量），且第 43 行只比 6 个字段、不比 `mem`（而 `helpers.py:82` 的 `snapshot()` 里含 `mem`）。改成按 `native.get_engine() is not None` 分支断言 `is True` / `is False`，并补内存比较。同类：`test_libs.py:216`、`test_cin_system.py:82` 的 `assert ... in (10, 20)`（注释"均视为正常"）——Termux 探测即使反转也发现不了。
- **`--jit` 的收益需要重新评估**：实测 JIT 246k instr/s 确实比解释器 98k 快 2.5 倍，但原生（修好记账后）是 6.2M。三条路径的维护成本不低，建议在文档里明确各自定位，而不是并列宣传。

### 3.4 让回归网能抓住上面这些问题

当前差分测试只比较 **stdout**、只覆盖 6 个示例：

- 升级为**产物级**比较：给 Go CLI 加 `--dump-bytecode`，`diff_go_python.py` 先比 `encode_program` 的字节（差异时打印首个不同偏移），再比 stdout。
- 把本次发现的每条缺陷都变成定向用例：`#0x1F`（.asm）、`case -1`、`case <变量>`、`0xFFu`、`9223372036854775808`、`float % float`、`~1.5`、`string +=`、`println(foo())`、`switch(float)`、`switch` 内 `continue`、带 BOM 的 `import`。
- `run_go` 加 `timeout=`（现在 `subprocess.run` 无超时），并对"两端都应失败"的用例比较错误关键字。
- 修 `diff_go_python.py:64-65` 的过时注释，并把 `basic.cin` 以"标记位校验"的方式正式纳入（现在只靠 `test_go_compiler.py` 里的一条断言）。

---

## 4. 建议的落地顺序

**批次 A（1~2 天，止血）**
1. 修 `.gitignore`（`*cache*`/`*tmp*`）+ `git add -f tests/test_cache.py` + `git rm --cached codecin/codecin_native.dll`
2. 修 `assembler.py:345`（十六进制尾 F）
3. 修 `cpu.py:1436-1440`（批量记账，原生立快 10 倍）
4. CI 补 Go 工具链 + 跑 `diff_go_python.py`；`skipif` 在 CI 下改为失败
5. 文档 9 处硬伤（§2.3）

**批次 B（1 周，正确性）**
6. `genSwitch` 负 case / 非常量 case / `switch`+`continue` 栈泄漏（Go 与 Python 两侧）
7. Go 编译错误通道（`(*Type, error)`），对齐两端接受集
8. `decodeBytecode` 头部校验 + 5 个 `//export` 加 `recover()`
9. CROM 解压上限 + 输出上限（`vm.out` / `FetchResource` 用 `io.LimitReader`）
10. 数值字面量（后缀 + 64 位范围）、BOM import
11. 恒真断言修正；`--dump-bytecode` + 差分用例扩充

**批次 C（2~4 周，工程与质量）**
12. 版本单一真源 + `[project.scripts]` + `[build-system]` + `--version`
13. `release.yml`、PyInstaller `datas`、`.gitattributes`/`.editorconfig`
14. Go 侧单测 + `go test -race`、覆盖率门槛、ruff 规则集扩充（现在 `select = ["E9","F63","F7","F82"]` 基本等于"能否 import"，且 `line-length = 100` 因未选 `E501` 而完全不生效）
15. 解释器快路径拆分、原生边界拷贝削减、ISA 表单一真源扩展（`stats.py` 的 latency 表与 `jit.py` 的 `_JIT_OPS` 也应纳入 `--check`）

**验收标准**：CI 绿灯必须意味着"原生与 Go 路径真的跑过"；`go test ./...` 有实质用例；任一版本号只有一处真源；`git status` 在三个平台跑完构建脚本后依然干净。

---

## 5. 我未能核实 / 需要注意的边界

1. **本报告的性能数字是在本机（Windows，机械/SSD 未知）测的**。我最初测到原生路径偶发 27~28 秒，追查后确认是**当时 4 个并行审查进程 + Go 编译争抢资源导致的测量污染**，不是程序缺陷；在无负载条件下原生路径稳定在 0.2~0.5s（`CPU.run`）。请勿引用那组 27s 数据。
2. **差分测试 6/6 通过是用过期二进制跑出来的**：`codecin/native/codecin.exe` 由 `9a3087c`（落后 HEAD 3 个提交、`vcs.modified=true`）构建。我另外用 HEAD 源码重建了 CLI（`.tmpbuild/clicodecin.exe`）复现 §3.1.2 的问题，结论未受影响；但这也正好说明 §2.1 的必要性。
3. **提交进 git 的 DLL 我重建后行为一致**（158 passed 两种都通过），所以它"过时"但不"损坏"。
4. 以下来自子审查、**我未逐条复核**：`genSwitch` 只在特定输入下才触发（我复核了 `case -1` 与 `case <变量>` 两例）；CROM zip bomb 的具体放大倍数；Go 深递归 200000 层导致 `fatal error`；`assert` 消息在 import 场景的文件名差异；汇编器注释剥离与 `DB "s"` 的 NUL 行为。
5. `docs/BUILDING.md` 的 `uv sync` / setuptools flat-layout 风险、`rich>=13,<15` 上界是否过窄，属**推断**，未实测（本机未装 rich、不便执行 `uv sync`）。
6. 本次审查**未改动任何仓库文件**（唯一新增文件就是本报告）。所有探针脚本与测试输入都以被 `.gitignore` 覆盖的 `.tmp*` / `.tmpbuild/` 形式临时创建，审查结束后已全部删除，`git status` 干净。若想复跑其中的实验，可按 §3.2.1 的 A/B 描述重写十行脚本即可。
