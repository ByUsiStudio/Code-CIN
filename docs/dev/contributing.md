---
description: "为 Code CIN 贡献代码与文档：开发环境、代码规范、提交信息、文档站写作约定与提交流程。"
---

# 贡献指南

欢迎为 Code CIN 提交问题与补丁。本页给出可执行的流程与规范, 目标是把“怎么改、改完跑什么、
怎么提交”说清楚。

## 开发环境

```bash
git clone https://github.com/ByUsiStudio/Code-CIN.git
cd Code-CIN
git submodule update --init --recursive     # 拉取 docs 文档站子仓库

python -m venv .venv                        # 可选, 但推荐
.venv\Scripts\activate                      # Windows
source .venv/bin/activate                   # Linux / macOS

pip install -r requirements-dev.txt         # rich + pytest + pytest-cov + ruff
python -m pytest                            # 基线回归应全绿
```

需要改 Go 侧 (编译器 / 字节码 VM / CROM / AOT 运行时) 时再装 Go 1.26+ 与 cgo 的 C 编译器,
见 [编译 Go 原生库](/dev/build-native)。

## 代码规范

| 项目 | 要求 |
|------|------|
| 风格 | PEP 8; `ruff` 规则集 `E4 E5 E7 E9 F I UP B SIM C4`, 行宽 100 (`E501` 豁免) |
| 类型 | 公共函数写类型提示 (`typing` 导入, Python 3.8 兼容, 不用 `X \| Y` 新语法) |
| 文档 | 模块/公共函数写 docstring, 说明职责与关键约束 |
| 输出 | 模块内**不要直接 `print`**, 统一走 `codecin/console.py` 适配层 (rich) |
| 测试 | 新增/修复行为都要带测试; 优先加在 `tests/` 对应主题文件里 |
| 常量 | 指令/系统调用改 `codecin/isa.py` 单一真源, 生成物不留手改 |

```bash
ruff check codecin cpu.py script tests       # 静态检查
ruff check --fix codecin cpu.py script tests # 自动修复可修项
python -m pytest -q                          # 回归
```

## 提交信息

沿用仓库现有风格: **中文 + 类型前缀**, 一条提交只做一件事。

```text
feat(cin): 支持 do-while 循环
fix(cpu): 修正算术右移的符号扩展
docs: 补充执行路径说明
test: 增加 switch 贯穿语义回归
build(pyproject): 移除过时的 package-data 条目
ci: 打包时设置 CODECIN_SKIP_NATIVE 跳过原生库编译
```

- 类型前缀: `feat` / `fix` / `docs` / `test` / `build` / `ci` / `refactor` / `perf` / `chore`;
- 破坏性变更在正文里写清“迁移方式”, 并在 `CHANGELOG.md` 里记录;
- 涉及指令集/系统调用的改动, 提交前务必跑两个 `--check`。

## 提交前自检

```bash
python -m pytest
ruff check codecin cpu.py script tests
python script/gen_isa_docs.py --check
python script/gen_native_isa.py --check
python script/check_paths.py          # 三路径一致性
cd docs && npm run docs:build         # 改过文档时
```

完整的门禁清单见 [测试与 CI](/dev/testing)。

## 文档站贡献

文档站 (本页面所在的站点) 是独立仓库 `Code-CIN-Docs`, 以 git submodule 内嵌在 `docs/`。

```bash
cd docs
npm install            # Node.js >= 18
npm run docs:dev       # 开发预览, 默认 http://localhost:5173
npm run docs:build     # 构建静态站点 (.vitepress/dist)
npm run docs:preview   # 预览构建产物
```

### 目录约定

| 目录 | 内容 |
|------|------|
| `guide/` | 安装、快速开始、命令行、执行路径、架构、示例、FAQ |
| `language/` | CIN 语言 (词法/类型/运算符/控制流/函数/struct/数组/字符串/内建/宿主能力/模块) |
| `asm/` | 汇编总览、语法参考、指令语义参考 |
| `stdlib/` | 标准库总览与逐函数参考 |
| `runtime/` | 原生运行时、JIT、二进制格式、AOT |
| `tools/` | 调试器、远程调试协议、日志、性能分析、内存与缓存 |
| `reference/` | 指令集编码表 (生成)、寄存器与内存模型、Python API、更新日志 |
| `dev/` | 项目结构、构建、测试、打包、扩展、贡献 |

### 新增一个页面

1. 在对应目录新建 `.md` 文件, 首行 frontmatter 只写一行 `description`
   (含冒号时用引号包住, 否则 YAML 解析失败), 然后用唯一的 `# 一级标题` 开始正文;
2. 在 `docs/.vitepress/config.mts` 的 `sidebar` 对应分组加一条
   `{ text: '侧边栏标题', link: '/目录/文件名' }`;
3. 章节级入口加进 `nav`;
4. 跑 `npm run docs:build` —— **死链检查会让断链直接构建失败**, 顺带验证 frontmatter 与 tabs 语法。

### 写作约定

- 代码块语言: CIN 用 ` ```c `, 汇编用 ` ```asm `, shell 用 ` ```bash ` / ` ```powershell `,
  Python 用 ` ```python `, Go 用 ` ```go `, 输出用 ` ```text `;
- 站内链接用**绝对路径且不带 `.md`** (例如 `/language/functions`);
  不要链接被 `srcExclude` 排除的仓库文档 (`/ISA`、`/CIN_GUIDE`、`/BUILDING`、`/REMOTE_DEBUG`);
- 对照内容优先用标签页 (vitepress-plugin-tabs):

  ````md
  ::: tabs

  == CIN

  ```c
  println("hi")
  ```

  == PL

  ```asm
  set x0, 1
  output x0
  stop
  ```

  :::
  ````

- 多组标签页联动给同一个 `key:` (例如 `::: tabs key:platform`);
- 页面里的命令、输出、API 签名必须**实机/源码核实**, 不要凭记忆写;
- `reference/isa.md` 由 `python script/gen_isa_docs.py` 生成, 不要手工编辑。

### 不要提交的内容

`node_modules/`、`.vitepress/cache/`、`.vitepress/dist/`、任何 `.log` 与本地缓存 ——
这些都已在 `docs/.gitignore` 与仓库根 `.gitignore` 里忽略。

## 沟通

| 渠道 | 信息 |
|------|------|
| 开发组织 | ByUsi Studio |
| 主要开发者 | 北啊呢 |
| 邮箱 | admin@byusistudio.fun |
| 主仓库 | [ByUsiStudio/Code-CIN](https://github.com/ByUsiStudio/Code-CIN) |
| 文档仓库 | [ByUsiStudio/Code-CIN-Docs](https://github.com/ByUsiStudio/Code-CIN-Docs) |

## 相关页面

- [项目结构](/dev/structure) — 目录与模块职责
- [测试与 CI](/dev/testing) — 测试清单与门禁命令
- [扩展指令 / 系统调用](/dev/extend) — 加指令的完整流程
- [打包与发布](/dev/packaging) — 版本管理与发布清单
