---
description: CIN 语言总览：语言定位、三路径执行、最小可运行程序、程序结构与语法速览
---

# CIN 语言总览

CIN 是 Code CIN 的高级语言：一门**语法近似 C / Go 的静态类型语言**，源码编译为 UCBC 字节码，
再由运行时执行。它保留了 C 风格的表达式、控制流与块结构，同时去掉了指针算术、头文件与手动内存管理，
用 struct、固长数组、字符串与内建函数覆盖常见编程需求。

- 源文件扩展名 `.cin`，用 `python cpu.py prog.cin` 编译并运行；
- 语句**以换行结尾**（分号可选），字符串用 `+` 自动拼接与字符串化；
- 同一份 `.cin` 源码由**解释器 / Python JIT / Go 原生 VM** 三条路径执行，语义一致。

::: tip 阅读顺序
完全没接触过 CIN 建议先看 [快速开始](/guide/quickstart)；本页负责给出全局地图，
细节分散在同目录的 `lexical` / `types` / `variables` / `operators` / `control-flow` 等页面。
:::

## 最小可运行程序

```c
function main() -> int {
    println("Hello, Code CIN!")
    println("2 + 3 = " + (2 + 3))
    return 0
}
```

::: tabs

== 解释执行（纯 Python）

```bash
python cpu.py hello.cin --no-native
```

== JIT 执行

```bash
python cpu.py hello.cin --no-native --jit
```

== 原生 VM（默认）

```bash
python cpu.py hello.cin
```

:::

```text
Hello, Code CIN!
2 + 3 = 5
```

::: details 三条执行路径到底差在哪？
| 路径 | 命令行 | 说明 |
|------|--------|------|
| Go 原生 VM | 默认 | 最快，宿主能力（绘图/音频/文件/系统交互）只有这条路径完整支持 |
| Python JIT | `--no-native --jit` | 基本块动态编译为 Python 闭包，纯 Python 下最快的选择 |
| 纯解释器 | `--no-native` | 逐指令执行，语义基准，调试与对照用 |

三路径共用同一份字节码与内存模型，正常程序输出应完全一致；差异只在性能与宿主能力。
详见 [执行路径](/guide/execution-paths)。
:::

## 语言特性地图

| 主题 | 你会学到的内容 | 页面 |
|------|----------------|------|
| 词法规则 | 注释、标识符、字面量、转义、语句分隔与续行、BOM | [词法规则](/language/lexical) |
| 类型系统 | `int/float/bool/string/struct`、固长数组、指针形式数组、默认值 | [类型系统](/language/types) |
| 变量与作用域 | 全局变量（数据区）、局部变量、一行多声明、数组字面量初始化 | [变量与作用域](/language/variables) |
| 运算符 | 14 级优先级、算术/比较/逻辑短路、位运算、复合赋值、三目 | [运算符](/language/operators) |
| 控制流 | `if` / `while` / `for` / `do-while` / `switch` / `break` / `continue` | [控制流](/language/control-flow) |
| 函数 | `function` 定义、参数传递、递归、返回值 | [函数](/language/functions) |
| struct | 成员访问、嵌套、值语义、作字段的固长数组 | [struct](/language/structs) |
| 数组 | 多维下标、行主序、数组传参衰减 | [数组](/language/arrays) |
| 字符串 | NUL 结尾、拼接、字节下标、字符串内建 | [字符串](/language/strings) |
| 内建函数 | 数学、字符串、转换、数值工具 | [内建函数](/language/builtins) |
| 宿主能力 | 绘图、音频、文件、系统交互、Termux API | [宿主能力](/language/host-abilities) |
| 模块与标准库 | `import` 解析规则、`codecin/lib/` 函数清单 | [模块与标准库](/language/modules) |
| 内嵌 CPU 指令语句 | `set` / `add` / `multiply` 等 7 条寄存器风格语句 | [内嵌 CPU 指令语句](/language/inline-cpu) |
| 限制与常见错误 | 禁用特性、报错表、排错入口 | [限制与常见错误](/language/errors) |

### 语法速览表

| 特性 | 语法 | 对应页面 |
|------|------|----------|
| 函数定义 | `function add(int a, int b) -> int { return a + b }` | [函数](/language/functions) |
| 变量声明 | `int x = 1`、`float f`、`string s = "hi"` | [变量与作用域](/language/variables) |
| 一行多声明 | `int a = 1, b = 2` | [变量与作用域](/language/variables) |
| 固长数组 | `int a[5]`、`float m[3][4]` | [数组](/language/arrays) |
| 数组字面量 | `int v[4] = {1, 2, 3, 4}` | [变量与作用域](/language/variables) |
| 指针形式数组 | `function sum(int[] arr) -> int` | [数组](/language/arrays) |
| struct | `struct Point { float x\n float y }` | [struct](/language/structs) |
| 成员访问 | `p.x = 1.0`、`r.bottom_right.x` | [struct](/language/structs) |
| 条件 | `if (x > 0) { ... } else { ... }` | [控制流](/language/control-flow) |
| 循环 | `while (n > 0) { ... }`、`for (int i = 0; i < 10; i++) { ... }` | [控制流](/language/control-flow) |
| 后置条件循环 | `do { ... } while (cond)` | [控制流](/language/control-flow) |
| 多分支 | `switch (x) { case 1: ... break default: ... }` | [控制流](/language/control-flow) |
| 三目 | `string s = (score >= 60) ? "pass" : "fail"` | [运算符](/language/operators) |
| 位运算 | `a & b`、`a \| b`、`a ^ b`、`~a`、`a << n`、`a >> n` | [运算符](/language/operators) |
| 字符串拼接 | `"n = " + 42`、`"pi = " + 3.14` | [字符串](/language/strings) |
| 内建调用 | `println(x)`、`strlen(s)`、`sqrt(16)` | [内建函数](/language/builtins) |
| 模块引入 | `import "math.cin"`、`import "./util.cin"` | [模块与标准库](/language/modules) |
| 内嵌 CPU 语句 | `set x 30`、`add x 12` | [内嵌 CPU 指令语句](/language/inline-cpu) |
| 断言 | `assert(x > 0, "x must be positive")` | [限制与常见错误](/language/errors) |

## 程序结构

一个 `.cin` 文件由四类顶层元素组成，顺序不限（但 `import` 必须在文件顶部列首）：

```c
// 1. 模块引入 (可省略, 必须在顶部; 同一行不能带行尾注释)
import "math.cin"

// 2. 全局变量 (进入数据区, 按声明顺序初始化)
int counter = 0
string greeting = "hello"
int primes[5] = {2, 3, 5, 7, 11}

// 3. struct 定义
struct Point {
    float x
    float y
}

// 4. 函数
function distance2(float x, float y) -> float {
    return x * x + y * y
}

function main() -> int {           // 入口函数
    println(greeting + " " + float_to_str(distance2(3.0, 4.0)))
    return 0
}
```

```text
hello 25
```

执行顺序：**先按源码顺序执行全部全局初始化，再进入函数**。约定入口是 `main`；不写 `main`
时程序从第一条指令开始顺序执行（`docs/CIN_GUIDE.md` 第 1 节）。

### 语句与换行

语句以**换行**结尾（推荐），也可用 `;` 显式分隔。注意：换行在花括号块内是**语句终止符**，
不能随意折行；需要折行时用行尾运算符或括号（详见 [词法规则](/language/lexical)）。

```c
function demo() -> int {
    int a = 1; int b = 2        // 分号分隔, 合法
    int c = a + b               // 换行结尾, 推荐
    return c
}
```

## 与 C / Go 的主要差异

| 维度 | CIN 的做法 | C / Go 对照 |
|------|-----------|-------------|
| 指针 | **没有指针与取地址运算**：`*` 只是乘法，`&` 只是位与 | C 有 `*`/`&`；Go 有 `*`/`&` 但无算术 |
| 字符串 | `string` 是 NUL 结尾字节序列的指针，**不可原位修改** | C 同构但可写；Go 的 string 不可变但可切片 |
| 类型宽度 | 所有值都占**一个 64 位槽**，`char/short/long` 只是别名 | C 宽度不同；Go 有 int8/16/32/64 |
| 默认值 | 全局与数组为全零；**局部变量的残留值不可依赖** | Go 保证零值；C 是未定义 |
| 除法 | `/` **恒为浮点除法**，整数除法用内建 `idiv(a, b)` | C/Go 中整数 `/` 是整数除法 |
| 取模 | `%` 仅整数，浮点取模编译报错 | Go 同；C 需 `fmod` |
| 数组 | `int a[5]` 写在**变量名后**，值语义；参数用 `int[]` 指针形式 | C 同写法；Go 是 `[5]int`，切片 `[]int` |
| 语句结尾 | 换行即结尾，`;` 可选 | C/Go 必须 `;`（Go 由编译器插入分号） |
| 模块 | `import "math.cin"` 文本级展开，无独立编译单元 | C 用 `#include`；Go 是按包编译 |
| 汇编互操作 | 7 条内嵌 CPU 语句直接操作变量槽 | 无此特性 |

::: info 与 Go 相似的几点
`function name(args) -> ret` 的返回类型后置写法、`import` 模块化（字符串形式）、
`for` 三段式、`:=` 风格之外的显式声明，都接近 Go 的阅读习惯；但 CIN 没有 goroutine、
接口、泛型、闭包，也没有包级别的可见性关键字。
:::

## 语言限制速览

1. **无指针/取地址运算**：`*` 是乘法、`&` 是位与；"引用"只通过数组 / struct 传参隐式实现。
2. **`/` 恒为浮点除**：整数除法用 `idiv(a, b)`（向零截断）；`%` 仅整数。
3. **位运算仅整数**：`& | ^ << >> ~` 拒绝 float / string；`>>` 为算术右移（符号位扩展）。
4. **递归深度受限**：默认内存 64KB、栈区约 1024 槽，过深递归触发 `Stack overflow`，可用
   `--mem-size` 扩容。
5. **struct 字段不能是变长指针数组**（`T[]`），只允许标量、嵌套 struct 与固长数组。
6. **字符串不可原位修改**：`strcpy` 返回新堆块，`s[i]` 不能作为赋值左值。
7. **函数先定义后使用不强制**：同文件内函数可互相调用（两遍编译）；但**变量必须先声明后使用**。
8. **同一函数内不要重名声明**：同名局部声明会共用同一个槽位（详见 [变量与作用域](/language/variables)）。

::: warning 排错从这里开始
完整报错表、错误面板格式与调试命令见 [限制与常见错误](/language/errors) 与 [交互式调试器](/tools/debugger)。
遇到 `Unknown function` / `Undefined variable` 这类报错，先检查拼写与声明顺序。
:::

## 下一步

- 想直接跑通编译与运行：[快速开始](/guide/quickstart)、[命令行参考](/guide/cli)
- 想了解字节码与汇编对照：[汇编语法参考](/asm/syntax)、[指令语义参考](/asm/instructions)
- 想用官方库函数：[标准库参考](/stdlib/reference)
- 想内嵌指令或看寄存器模型：[内嵌 CPU 指令语句](/language/inline-cpu)、[寄存器与内存模型](/reference/registers-memory)
