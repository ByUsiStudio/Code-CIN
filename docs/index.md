---
layout: home

hero:
  name: Code CIN
  text: 简洁的类 C 语言与多路径运行时
  tagline: CIN / PL / ASM 工具链 · UCPU 字节码 · 解释器 / JIT / Go 原生 VM 三路径一致执行 · 内置 2D 画布与联网音频
  image:
    src: /logo.svg
    alt: Code CIN
  actions:
    - theme: brand
      text: 快速开始
      link: /guide/quickstart
    - theme: alt
      text: pip 安装
      link: /guide/installation
    - theme: alt
      text: GitHub
      link: https://github.com/ByUsiStudio/Code-CIN

features:
  - icon: 🧩
    title: 三语言一条工具链
    details: CIN 高级语言、PL 关键字风格汇编、ASM 汇编, 统一编译/汇编为 UCBC 字节码, 共享同一套 ISA 与 VM。
  - icon: ⚡
    title: 三路径一致执行
    details: Python 解释器 / JIT 基本块编译 / Go 原生 c-shared VM 任选其一, 结果一致, 由脚本与测试持续把关。
  - icon: 🛠️
    title: 112 条指令的 UCPU
    details: Base 28 + ARM64 40 + RISC-V 27 + FP 10 + Vector 6 + SYS 1; 32 通用 + 32 向量寄存器; 可选 MMU 与缓存建模。
  - icon: 📦
    title: 19 个内置标准库
    details: math / str / array / sort / conv / vec / rand / json / time / io / gui / termux 等随 pip 包分发, import 即用。
  - icon: 🔍
    title: 可调试、可分析
    details: 交互式单步调试、TCP 远程调试协议、rich 超详细逐指令追踪、指令级性能统计与缓存建模。
  - icon: 🚀
    title: AOT 独立可执行文件
    details: --build-exe 把 CIN 程序编译成静态单文件, 产物不需要 Python、Go 工具链、libc 或任何动态库。
  - icon: 🎨
    title: 宿主能力开箱可用
    details: 2D 画布导出 PNG、联网音频播放、文件/进程/环境变量访问、Termux API(Android)。
  - icon: 🧪
    title: 严格回归
    details: 指令黄金值、三路径一致性、内存保护、断点回归、打包断言, 全线由 CI 把关。
---

## 一条命令安装

```bash
pip install codecin==5.5.0
codecin --version        # Code CIN 5.5.0
codecin --help           # 完整命令行帮助
```

> 安装时会尝试用**本机的 Go 工具链现场编译原生加速库**。机器上没有 Go 也能装上, 只是会回退纯
> Python 解释执行 (功能完整、速度较慢); 想显式跳过本地编译可以设 `CODECIN_SKIP_NATIVE=1`。
> 详见 [安装 Code CIN](/guide/installation)。

## 同一段程序, 三种写法

::: tabs

== CIN (高级语言)

```c
function main() -> int {
    println("Hello, Code CIN!")
    return 0
}
```

== PL (关键字风格汇编)

```asm
.text
main:
    set x0, 65
    output x0          ; 65 -> 'A'
    output #10         ; 换行
    stop
```

== ASM (汇编)

```asm
.text
main:
    mov x0, #msg
    sys #24            ; PRINT_STR
    out #10
    halt

.data
msg: ASCIZ "Hello, Code CIN!"
```

:::

三种写法最终都会编译成 UCBC 字节码, 交给同一个 VM 执行:

::: tabs

== 解释执行 (纯 Python)

```bash
python cpu.py hello.cin --no-native
```

== JIT 基本块编译

```bash
python cpu.py hello.cin --jit --no-native
```

== Go 原生 VM (最快)

```bash
python cpu.py hello.cin
```

:::

## 文档地图

| 章节 | 内容 | 入口 |
|------|------|------|
| 指南 | 安装、快速开始、命令行、执行路径、架构、示例、FAQ | [安装](/guide/installation) · [快速开始](/guide/quickstart) |
| CIN 语言 | 词法、类型、变量、运算符、控制流、函数、struct、数组、字符串、内建、宿主能力、模块 | [语言总览](/language/) |
| 汇编 / ISA | 汇编语法 (ASM 与 PL 两种风格)、112 条指令语义、指令编码表 | [汇编总览](/asm/) |
| 标准库 | 19 个内置库的逐函数参考 | [标准库总览](/stdlib/) |
| 运行时 | Go 原生运行时、JIT、`.bin`/`.crom` 格式、AOT 独立可执行文件 | [Go 原生运行时](/runtime/native) |
| 工具 | 交互式调试器、远程调试协议、日志与错误、性能分析、内存与缓存 | [交互式调试器](/tools/debugger) |
| 参考 | 指令编码表、寄存器与内存模型、Python 嵌入 API、更新日志 | [寄存器与内存模型](/reference/registers-memory) |
| 开发者 | 项目结构、编译原生库、测试与 CI、打包发布、扩展指令、贡献指南 | [项目结构](/dev/structure) |

## 项目信息

| 项目 | 信息 |
|------|------|
| 当前版本 | **5.5.0** (文档站与发行版同步) |
| 语言实现 | Go 侧是唯一核心 (CIN 编译器 / 字节码 VM / CROM / AOT 运行时), **Go 侧不提供 CLI** |
| 唯一命令行入口 | `python cpu.py <程序> [选项]` 或安装后的 `codecin <程序> [选项]` |
| 依赖 | Python 3.8+ 与 `rich` (唯一第三方运行时依赖); Go 1.26+ 仅用于编译原生库/AOT |
| 代码仓库 | [ByUsiStudio/Code-CIN](https://github.com/ByUsiStudio/Code-CIN) |
| 文档仓库 | [ByUsiStudio/Code-CIN-Docs](https://github.com/ByUsiStudio/Code-CIN-Docs) (本站在 `docs/` 下, 以 git submodule 方式内嵌于主仓库) |
| 开发组织 | ByUsi Studio · 开发者: 北啊呢 · admin@byusistudio.fun |
