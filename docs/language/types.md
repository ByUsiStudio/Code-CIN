---
description: CIN 类型系统：int/float/bool/string 与别名、struct、固长与指针数组、默认值、提升与截断规则
---

# 类型系统

CIN 是静态类型语言：每个变量、参数与返回值都有确定类型，编译器据此选择指令
（整数用 `ADD/MUL`，浮点用 `FADD/FMUL`，字符串拼接用 `STR_CONCAT`）。
值模型很简单 —— **所有标量都占一个 64 位槽**，所以没有 C 那样的宽度陷阱。

## 类型一览

| 类型 | 说明 | 默认值 |
|------|------|--------|
| `int` | 64 位有符号整数 | `0` |
| `char` / `short` / `long` | 整数语法别名（存储仍是 64 位槽） | `0` |
| `unsigned int`（及 `unsigned char/short/long`） | 无符号修饰（同 64 位槽，主要用于大数值字面量） | `0` |
| `float` | 64 位 IEEE754 浮点 | `0.0` |
| `bool` | 布尔 | `false` |
| `string` | NUL 结尾字符串指针 | `""` |
| `void` | 仅函数返回类型 | - |
| `StructName` | 用户定义 struct | 全零 |
| `T[n]` / `T[n][m]` | 固长数组（值语义） | 全零 |
| `T[]` / `int[][]` | 指针形式数组（参数/返回） | 空指针 |

::: tip 一句话记住宽度
`char`、`short`、`long`、`unsigned` 都只是**语法别名**：`char c = 300` 合法且 `c` 就是 `300`，
不存在 8 位截断或溢出回绕。需要"小整数"语义时自己用位运算或 `%` 约束范围。
:::

## 整数类型与别名

```c
function int_aliases() -> int {
    char c = 'A'              // 65
    char big = 300            // 合法: char 与 int 同宽
    short s = 2
    long l = 3
    unsigned int u = 4000000000
    unsigned long ul = 0xFFFFFFFF

    int total = c + big + s + l + (u % 1000)
    return total
}
```

| 写法 | 解析结果 | 备注 |
|------|----------|------|
| `int` | `int` | 缺省整数类型 |
| `char` / `short` / `long` | `int` | 解析阶段直接折叠为 `int` |
| `unsigned` | `int` | 缺省 `unsigned` 单独出现也合法 |
| `unsigned char/short/int/long` | `int` | 修饰词被接受但值模型不变 |

::: warning 不要指望 `char` 做字节运算
`char` 不是字节类型。要处理字节请用 `int` + 位运算（`& 0xFF`），
读字符串得到的也是 0–255 的 `int`（见 [字符串](/language/strings)）。
:::

## 浮点

```c
float pi = 3.14159265
float tiny = 1e-5
float forced = 100f          // 100.0
float result = sqrt(16)      // 4 (打印时省略 .0)
```

- `float` 是 64 位 IEEE754 双精度，字面量支持小数与科学计数法；
- `float_to_str(100.0)` 打印为 `100`（去掉尾部 `.0`），`float_to_str(1.5)` 打印为 `1.5`；
- `/` 运算**恒为浮点除**（见下文提升规则），整数除法用内建 `idiv(a, b)`。

## 布尔

```c
bool ok = true
bool zero = 0                // false
bool five = 5                // true (非零即真)
println("ok = " + ok)        // ok = true
println("num = " + (true + true))   // num = 2
```

| 上下文 | `true` | `false` |
|--------|--------|---------|
| 打印 / 字符串拼接 | `"true"` | `"false"` |
| 数值运算（加减乘、比较、下标） | `1` | `0` |
| 条件（`if` / `while` / `?:` / `&&` / `\|\|`） | 真 | 假 |
| 赋值给 `int` / `float` | `1` / `1.0` | `0` / `0.0` |
| 把 `int` 赋给 `bool` | 非零 → `true` | `0` → `false` |

## 字符串

`string` 变量持有指向 NUL 结尾字节序列的指针：

```c
string s = "hello"
int n = strlen(s)            // 5
int first = s[0]             // 104 ('h'), 只读
string t = s + " world"      // 拼接产生新堆块
```

- 字符串**不可原位修改**：`s[0] = 'H'` 编译报错，要用 `strcpy` / `substr` / `upper` 等产生新串；
- 字符串字段在 struct 里也是指针（见 [struct](/language/structs)）；
- 字符串内建函数清单见 [内建函数](/language/builtins)。

## void

`void` 只能作为函数返回类型，不能声明 `void` 变量：

```c
function greet() {           // 省略 -> void
    println("hi")
}

function greet2() -> void {  // 显式写 void
    println("hi")
}
```

## struct 类型

```c
struct Point {
    float x
    float y
}

struct Student {
    Point info               // 嵌套 struct (值内嵌)
    int grades[5]            // 固长数组字段
    float gpa
}
```

| 规则 | 说明 |
|------|------|
| 字段类型 | 标量、嵌套 struct、固长数组；**不能是 `T[]` 变长指针数组** |
| 默认值 | 全零（`int` 为 0、`float` 为 0.0、`bool` 为 false、数组全零） |
| 语义 | 值语义拷贝（可整体赋值 / 传参 / 返回） |
| 访问 | `p.x`、`s.info.name` 链式成员访问 |

详见 [struct](/language/structs)。

## 数组类型

### 固长数组 `T[n]`

```c
int a[5]                     // 一维, 全零
float m[3][4]                // 二维 (行主序)
int init[4] = {1, 2, 3, 4}   // 字面量初始化
int ident[2][2] = { {1, 0}, {0, 1} }
```

- 长度是类型的组成部分，声明后不可改变；
- 下标从 0 开始，多维逐维下标 `m[row][col]`；
- 作参数时写 `int[5]`，传入后**衰减为指针形式**（见 [数组](/language/arrays)）。

### 指针形式数组 `T[]`

用于函数参数与返回值：

```c
function sum(int[] arr, int n) -> int {
    int s = 0
    for (int i = 0; i < n; i++) {
        s += arr[i]
    }
    return s
}

function make(int n) -> int[] {
    int[] r
    for (int i = 0; i < n; i++) {
        r[i] = i * i
    }
    return r
}
```

`int[]` 没有长度信息，**必须另外传长度**，否则越界不会被检查（默认无边界检查，
可用 `--bounds-check` 打开，此时强制解释执行）。

### 类型对照速查

::: tabs

== CIN

```c
int a[5]                     // 固长
function sum(int[] arr) -> int {
    return arr[0]
}
```

== C

```c
int a[5];                    // 固长
int sum(int *arr) {          // 退化为指针
    return arr[0];
}
```

== Go

```go
var a [5]int                 // 数组 (值语义)
func sum(arr []int) int {    // 切片 (引用语义)
    return arr[0]
}
```

:::

## 默认值表

| 位置 | 默认值 | 是否可靠 |
|------|--------|----------|
| 全局变量 | `int`/`char`/`short`/`long`/`unsigned` → 0，`float` → 0.0，`bool` → false，`string` → 空串，struct/数组 → 全零 | 可靠：数据区由运行时建好后全零 |
| 固长数组（全局或局部声明） | 全零 | 声明为固长数组即可靠 |
| struct 变量（未赋值） | 字段全零 | 全局可靠；局部 struct 由堆分配，见下方警告 |
| 局部标量变量 | 框架内的残留值 | **不可靠**，见下方警告 |

::: danger 局部变量不要依赖"默认值"
编译器在函数序言里只做栈指针下移，**不发射清零代码**。栈槽在同一进程内会被复用，
所以未赋值的局部变量可能读到上一次调用留下的数据：

```c
function f() -> int {
    int p
    int q
    p = 777
    q = 888
    return 0
}

function g() -> int {
    int p
    int q
    println("p=" + int_to_str(p) + " q=" + int_to_str(q))   // 可能是 p=777 q=888
    return 0
}

function main() -> int {
    f()
    g()
    return 0
}
```

**规则：局部变量总是先赋值再使用。** 需要确定初值时显式写 `int total = 0`。
:::

## 类型提升与截断

| 运算 | 结果类型 | 说明 |
|------|----------|------|
| `int` 与 `float` 混算 | `float` | 整数侧自动 `ITOF` 提升 |
| `bool` 与 `float` 混算 | `float` | `true` → `1.0` |
| `int` `+` `-` `*` `%` `int` | `int` | 纯整数运算 |
| 任意 `/` | **恒为 `float`** | 即使两侧都是整数，也是浮点除 |
| 移位 `<<` `>>` `&` `\|` `^` `~` | `int` | 只接受整数操作数 |
| 比较 `== != < > <= >=` | `bool` | 结果作为值使用时是 `1` / `0` |
| `&&` `\|\|` `!` | `bool` | 短路求值 |
| `?:` 三目 | 两侧同为 `int` → `int`；任一侧 `float` → `float`；同为 `string` → `string` | 不允许 string 与数值混合 |
| `min` / `max` | 任一侧 `float` → `float`，否则 `int` | |

### 截断规则

| 转换 | 行为 |
|------|------|
| `float` → `int`（赋值 `int i = f`，或函数返回值/参数） | **向零截断**：`2.7` → `2`，`-2.7` → `-2` |
| `int` → `bool` | 非零为 `true`，零为 `false`（无指令开销） |
| `bool` → `int` / `float` | `true` → `1` / `1.0` |
| `int` / `bool` → `float` | `ITOF` 提升 |
| `float` → `string` | 需要显式内建 `float_to_str(f)`（拼接/打印时会自动字符串化） |

```c
function conversions() -> int {
    float a = -2.7
    float b = 2.7
    int ia = a                  // -2  (向零截断)
    int ib = b                  //  2

    int q = -7 / 2              // -3  (浮点除 -3.5 再截断)
    int m = -7 % 2              // -1  (整除取模, 仅整数)

    bool t = 5                  // true
    bool f = 0                  // false
    int ti = t                  // 1
    float tf = t                // 1.0

    println("ia=" + int_to_str(ia) + " ib=" + int_to_str(ib)
            + " q=" + int_to_str(q) + " m=" + int_to_str(m))
    println("t=" + bool_to_str(t) + " ti=" + int_to_str(ti)
            + " tf=" + float_to_str(tf))
    return ia + ib + q + m
}
```

```text
ia=-2 ib=2 q=-3 m=-1
t=true ti=1 tf=1
```

### 除法与取模对照

::: tabs

== CIN：`/` 是浮点除

```c
float x = 7 / 2              // 3.5
int y = 7 / 2                // 3   (3.5 截断)
int z = idiv(7, 2)           // 3   (显式整数除)
int r = -7 % 2               // -1
```

== C

```c
double x = 7 / 2;            // 3.0  (整数除后再提升!)
int y = 7 / 2;               // 3
int r = -7 % 2;              // -1
```

== Go

```go
x := 7.0 / 2                 // 3.5
y := 7 / 2                   // 3
r := -7 % 2                  // -1
```

:::

::: warning 浮点取模会编译失败
`7.5 % 2.0`、`f %= 2.0` 都直接报 `Float modulo not supported`。
需要浮点取余时先用 `floor` 取整，或改用整数运算。
:::

## 常见类型错误

| 报错信息 | 原因 | 修正 |
|----------|------|------|
| `Float modulo not supported` | 对 float 用 `%` | 改用整数，或先 `floor`/`ceil` |
| `Bitwise operator '&' requires integer operands (got float and int)` | 位运算混入 float / string | 位运算仅限整数 |
| `Bitwise NOT '~' requires an integer operand` | `~` 作用于 float / string | 同上 |
| `Switch expression must be integer, got: float` | `switch` 选择器是 float | 先转 `int`（截断）再 switch |
| `Cannot mix string and numeric in '?:'` | 三目两侧一侧 string 一侧数值 | 统一转成 string 或数值 |
| `Type mismatch ...` | 赋值 / 传参类型不兼容 | 显式转换或用 `int_to_str` / `float_to_str` |
| `Cannot apply '&=' to float` | 对 float 用位运算复合赋值 | 改用 `+ - * /` 复合赋值 |

## 相关页面

- 字面量的写法：[词法规则](/language/lexical)
- 声明与作用域：[变量与作用域](/language/variables)
- 优先级与三目：[运算符](/language/operators)
- 数组细节：[数组](/language/arrays)、[struct](/language/structs)
- 转换内建：[内建函数](/language/builtins)
