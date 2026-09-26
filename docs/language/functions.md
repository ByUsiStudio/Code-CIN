---
description: CIN 函数定义、参数传递规则、返回值与 X0 约定、递归栈深限制与两遍编译机制。
---

# 函数

CIN 用 `function` 关键字定义函数，语法接近 Go/C 的混合体：类型写在参数名之前，返回类型写在 `->` 之后。

## 定义语法

```text
function 函数名(参数表) -> 返回类型 {
    函数体
}
```

- 参数表形如 `int a, int b`，类型在名字前面。
- `-> 返回类型` 可省略，省略时返回类型为 `void`。
- 函数体是花括号块；块内的换行是**语句终止符**，不是装饰。
- 函数体可以完全为空：`function noop() {}`。

::: tabs

== CIN

```c
function add(int a, int b) -> int {
    return a + b
}

// 无返回值: -> void 可省略
function greet() {
    println("hi")
}

function main() -> int {
    println(int_to_str(add(2, 3)))   // 5
    greet()                          // hi
    return add(2, 3)
}
```

== PL

```asm
; PL: set / add / multiply / output / return / stop
main:
    set x0, 2
    set x1, 3
    add x0, x1
    output x0
    return
```

== ASM

```asm
; ASM: MOV / ADD / SYS / OUT / HALT
.text
main:
    MOV x0, #2
    MOV x1, #3
    ADD x0, x1
    SYS #22          ; ITOA: x0 = 十进制字符串
    SYS #24          ; PRINT_STR
    OUT #10          ; 换行
    HALT
```

:::

::: tip 顺序无关
同一文件内的函数**可以先用后定义**（例如 `main` 调用写在文件末尾的 `helper`），编译器做两遍编译，先扫签名再生成代码。详见下文[两遍编译](#两遍编译-先用后定义)。
:::

## 参数传递

### 标量按值传递

`int` / `float` / `bool` 参数按值传递：函数内修改形参不影响调用方的实参。

```c
function double_it(int x) -> int {
    x = x * 2          // 只改副本
    return x
}

function main() -> int {
    int n = 5
    int r = double_it(n)
    println(int_to_str(n) + " " + int_to_str(r))   // 5 10
    return r
}
```

### 数组与 struct 参数退化为引用

数组参数（`T[]`）与 struct 参数传递的是同一块存储，**函数内修改对调用方可见**。

```c
struct Point { int x }

function bump(int[] arr) {
    arr[0] = 42        // 调用方可见
}

function move(Point p) {
    p.x = 99           // 调用方可见
}

function main() -> int {
    int a[3]
    bump(a)
    println(int_to_str(a[0]))   // 42

    Point pt
    pt.x = 1
    move(pt)
    println(int_to_str(pt.x))   // 99
    return 0
}
```

::: warning 注意与 struct 赋值语义的区别
struct **传参/取地址式使用**是引用可见的，但 struct **整体赋值**（`P b = a`）是值拷贝（`b.x = 7` 不影响 `a.x`）。两种语义并存，写代码时按“函数内改 struct 会改到外面”来理解即可。
:::

### 固长数组参数与衰减

固长数组作参数时，维度写在**参数名之后**：`int a[3]`、`int m[2][3]`。传入后统一衰减为指针形式（等价于 `int[]` / `int[][]`），因此数组大小不会随参数传递。

```c
function fill3(int a[3], int v) {
    for (int i = 0; i < 3; i = i + 1) {
        a[i] = v
    }
}

function setm(int m[2][3], int r, int c, int v) {
    m[r][c] = v
}

function main() -> int {
    int a[3]
    fill3(a, 7)
    println(int_to_str(a[2]))     // 7

    int m[2][3]
    setm(m, 1, 2, 5)
    println(int_to_str(m[1][2]))  // 5
    return 0
}
```

::: danger 不支持的写法
`function fill3(int[3] a)` 这种把维度写在类型名上的写法**不会被解析**，会报
`Expected IDENT but got LBRACKET ('[')`。只有 `T[]`（空括号）能紧跟在类型名后。
:::

### 值传递 / 引用可见 对照

::: tabs

== 按值（副本）

```c
function f(int x) -> int { x = 1
    return x }

function main() -> int {
    int n = 9
    f(n)
    println(int_to_str(n))   // 9
    return 0
}
```

== 引用可见（数组）

```c
function f(int[] a) { a[0] = 1 }

function main() -> int {
    int n[1]
    n[0] = 9
    f(n)
    println(int_to_str(n[0]))   // 1
    return 0
}
```

== 引用可见（struct）

```c
struct S { int v }

function f(S s) { s.v = 1 }

function main() -> int {
    S x
    x.v = 9
    f(x)
    println(int_to_str(x.v))   // 1
    return 0
}
```

:::

## 返回值

- `return 表达式` 立即结束函数并把值交给调用方。
- `void` 函数可以写裸 `return`，也可以完全不写；不写时函数自然结束。
- 非 `void` 函数不写 `return` 时会返回 `x0` 里的残留值，请显式 `return`。
- `return` 的类型会按赋值规则转换（`float` 返回值给 `int` 声明会截断）。

### 返回值经 X0 传递

编译器把返回值放到 `x0`，调用方从 `x0` 取值；参数依次放入 `x0..x(n-1)`。这条约定与 ISA 层完全一致，因此 CIN 与汇编可以互相对照。

::: tabs

== CIN

```c
function add(int a, int b) -> int {
    return a + b      // 结果写入 x0
}

function main() -> int {
    return add(2, 3)
}
```

== ASM

```asm
.text
main:
    MOV x0, #2
    MOV x1, #3
    CALL add
    HALT             ; 退出时 x0 = 5

add:
    ADD x0, x1       ; x0 = x0 + x1
    RET
```

:::

::: info 返回类型与数组
函数返回类型**不能**写固长数组（`-> int[3]` 会报 `Expected LBRACE but got LBRACKET`）。
需要返回数组时写指针形式 `-> int[]`。
:::

## 递归与栈深限制

CIN 支持递归。每次调用消耗栈帧（局部变量槽 + 参数槽 + 返回地址），调用栈由 VM 栈承载。

```c
function fact(int n) -> int {
    if (n <= 1) { return 1 }
    return n * fact(n - 1)
}

function fib(int n) -> int {
    if (n < 2) { return n }
    return fib(n - 1) + fib(n - 2)
}

function main() -> int {
    println(int_to_str(fact(10)))   // 3628800
    println(int_to_str(fib(20)))    // 6765
    return 0
}
```

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| 内存总量 `--mem-size` | `65536`（64 KB，可用 `--mem-size` 覆盖） | 栈与堆共享这块内存 |
| 栈槽数 `stack_size` | `1024` 个 qword 槽 | 每槽 8 字节，约 8 KB |
| 指令上限 `--max-instructions` | `100000000` | 防止死循环跑飞 |

递归过深时栈会撞上堆，运行时报：

```text
Stack overflow (collides with heap)
```

加大 `--mem-size` 可以缓解，但**改不了递归算法本身的复杂度**：

```bash
python cpu.py deep.cin --mem-size 1048576
```

::: warning 递归是 O(depth) 栈消耗
`fib(35)` 这类指数递归不只是“栈深”问题，指令数也会爆炸。深度递归建议先改成迭代。
:::

## 两遍编译：先用后定义

`parse_program` 先顺序扫描所有 `function` 与 `struct` 定义收集签名，再生成代码，所以：

- 同文件内的函数**互相调用不受书写顺序限制**；
- 函数可以调用写在它后面的函数；
- 但**变量必须先声明后使用**（见下节）。

```c
function main() -> int {
    return helper(3)        // helper 定义在后面，仍然合法
}

function helper(int x) -> int {
    return x * 2
}
```

## 变量必须先声明

与函数不同，变量没有隐式声明。赋值一个从未声明过的名字会直接编译失败：

```c
function main() -> int {
    zzz = 1                 // Compiler error: Undefined variable: zzz
    return 0
}
```

```text
┌──────────────────────── Load Error ────────────────────────┐
│ prog.cin:2: Compiler error: Undefined variable: zzz        │
└────────────────────────────────────────────────────────────┘
```

正确写法：

```c
function main() -> int {
    int zzz = 1
    return zzz
}
```

> 全局变量只能使用**常量**初始化表达式：`int g = 1 + 2` 合法，`int g = abs(-3)` 会报
> `Non-constant global initializer: call`。需要调用函数请放进 `main` 或某个函数里。

## 完整示例：多函数程序

下面这个程序把值传递、引用可见、固长数组衰减、递归、返回值串联起来，可直接运行：

```c
// stats.cin —— 运行: python cpu.py stats.cin

struct Range {
    int lo
    int hi
}

// 值传递: 不影响调用方
function clamp_int(int v, int lo, int hi) -> int {
    if (v < lo) { return lo }
    if (v > hi) { return hi }
    return v
}

// 数组引用可见: 原地改写
function fill_zero(int a[8]) {
    for (int i = 0; i < 8; i = i + 1) {
        a[i] = 0
    }
}

function fill_range(int a[8], int lo, int hi) {
    for (int i = 0; i < 8; i = i + 1) {
        a[i] = lo + i
        if (a[i] > hi) { a[i] = hi }
    }
}

// 参数是 struct, 引用可见; 返回 int 经 X0
function span(Range r) -> int {
    return r.hi - r.lo
}

// 递归
function sum_to(int n) -> int {
    if (n <= 0) { return 0 }
    return n + sum_to(n - 1)
}

function main() -> int {
    int data[8]

    fill_zero(data)
    println("zero sum = " + int_to_str(data[0] + data[7]))   // 0

    fill_range(data, 30, 33)
    println("data[0]=" + int_to_str(data[0])
            + " data[3]=" + int_to_str(data[3])
            + " data[7]=" + int_to_str(data[7]))             // 30 33 33

    Range r
    r.lo = 10
    r.hi = 42
    println("span = " + int_to_str(span(r)))                 // 32

    println("clamp(99, 0, 10) = "
            + int_to_str(clamp_int(99, 0, 10)))              // 10
    println("sum_to(100) = " + int_to_str(sum_to(100)))      // 5050

    return span(r)
}
```

预期输出：

```text
zero sum = 0
data[0]=30 data[3]=33 data[7]=33
span = 32
clamp(99, 0, 10) = 10
sum_to(100) = 5050
```

## 常见函数相关错误

| 错误信息 | 原因 | 修正 |
|----------|------|------|
| `Unknown function: xxx` | 调用了未定义或拼错的函数 | 检查拼写，或补上函数定义 |
| `Undefined variable: xxx` | 变量未声明就使用 | 先 `int x = 0` 声明 |
| `Expected IDENT but got LBRACKET ('[')` | 参数把维度写在类型名上（`int[3] a`） | 改成 `int a[3]` |
| `Expected LBRACE but got LBRACKET ('[')` | 返回类型用了固长数组 `-> int[3]` | 改成 `-> int[]` |
| `Expected RBRACE ... at line N` | 花括号不配对 / 块内缺换行 | 检查第 N 行附近括号 |
| `Stack overflow (collides with heap)` | 递归过深或栈耗尽 | 减少深度或 `--mem-size` 扩容 |
| `Non-constant global initializer: call` | 全局变量用函数调用初始化 | 改到函数体内赋值 |

## 相关页面

- 类型与默认值：[/language/types](/language/types)
- 变量与作用域：[/language/variables](/language/variables)
- struct 定义与字段：[/language/structs](/language/structs)
- 数组与指针形式：[/language/arrays](/language/arrays)
- 内建函数全表：[/language/builtins](/language/builtins)
- 递归与栈的底层模型：[/reference/registers-memory](/reference/registers-memory)
- 汇编调用约定：[/asm/syntax](/asm/syntax)
