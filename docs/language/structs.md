---
description: CIN struct 定义、点号成员访问、嵌套 struct 值内嵌、整体赋值与传参的语义、固长数组字段与默认全零实例化。
---

# struct

struct 把若干字段打包成一块**连续的值内嵌存储**。CIN 的 struct 没有指针、没有方法、没有构造函数：`StructName s` 声明即得到一块全零的存储。

## 定义语法

```text
struct 名称 {
    类型 字段名
    类型 字段名[固长维度]
    ...
}
```

```c
struct Point {
    float x
    float y
}

struct Rectangle {
    Point top_left        // 嵌套 struct: 值内嵌
    Point bottom_right
    float area
}
```

字段之间以换行或 `;` 分隔，字段可以带固长数组维度（`int grades[5]`）。struct 定义只描述布局，本身不产生存储；存储来自变量声明。

::: tabs

== CIN

```c
struct Point { int x
    int y }

function main() -> int {
    Point p
    p.x = 3
    p.y = 4
    println(int_to_str(p.x) + "," + int_to_str(p.y))   // 3,4
    return p.x
}
```

== PL

```asm
; PL 层没有 struct: 字段就是一段连续栈槽
main:
    set x0, 3        ; p.x
    set x1, 4        ; p.y
    output x0
    return
```

== ASM

```asm
; ASM 层用 .data 里的标签 + 偏移模拟 struct
.text
main:
    MOV x0, #pt
    MOV x1, #3
    SD x1, [x0]        ; pt.x = 3
    ADDI x0, x0, #8
    MOV x1, #4
    SD x1, [x0]        ; pt.y = 4
    LD x2, [x0]
    MOV x0, x2
    SYS #22
    SYS #24
    HALT

.data
pt: DQ 0
    DQ 0
```

:::

## 成员访问：点号

成员访问统一使用 `.`，支持链式：

```c
struct Point { float x
    float y }

struct Rectangle {
    Point top_left
    Point bottom_right
}

function area(Rectangle r) -> float {
    float w = r.bottom_right.x - r.top_left.x
    float h = r.bottom_right.y - r.top_left.y
    return w * h
}

function main() -> int {
    Rectangle box
    box.top_left.x = 0.0
    box.top_left.y = 0.0
    box.bottom_right.x = 3.0
    box.bottom_right.y = 4.0
    println("area = " + area(box))     // area = 12
    return 0
}
```

- 多层嵌套可以一直点下去：`box.bottom_right.x`。
- 对非 struct 类型取成员会报 `Member access on non-struct type: int`。
- 字段名拼错会报 `Struct X has no field Y`（例如 `Struct P has no field y`）。

## 嵌套 struct：值内嵌

嵌套 struct 的字段**内嵌存储**，不是指针——外层 struct 的槽数包含所有内层字段。

| 定义 | 说明 |
|------|------|
| `Point top_left` | 内嵌一个完整 `Point`（两个 `float` 槽） |
| `Point corners[2]` | 内嵌两个 `Point`（固长数组字段） |
| `string name` | 一个指针槽（字符串本身在堆上） |

```c
struct Inner { int a
    int b }

struct Outer {
    Inner i1
    Inner i2
    int tag
}

function main() -> int {
    Outer o
    o.i1.a = 1
    o.i2.b = 2
    o.tag = 9
    println(int_to_str(o.i1.a) + int_to_str(o.i1.b)
            + int_to_str(o.i2.a) + int_to_str(o.i2.b)
            + int_to_str(o.tag))        // 10029
    return 0
}
```

## 值语义：整体赋值、传参、返回

| 操作 | 语义 | 示例 |
|------|------|------|
| 整体赋值 `P b = a` | **值拷贝**（逐槽复制），之后互不影响 | `b.x = 7` 不改 `a.x` |
| 作为函数参数 | **引用可见**（同一块存储） | 函数内 `p.x = 99` 调用方可见 |
| 作为返回值 | 值拷贝返回（经 `x0`） | `P q = make(4)` |
| 作为数组元素 | 值内嵌 | `P ps[3]` 是 3 份完整 `P` |
| 作为数组参数元素 | 引用可见 | 函数内 `ps[0].x = 1` 可见 |

::: tabs

== 整体赋值 = 值拷贝

```c
struct P { int x }

function main() -> int {
    P a
    a.x = 5
    P b = a          // 拷贝
    b.x = 7
    println(int_to_str(a.x) + " " + int_to_str(b.x))   // 5 7
    return 0
}
```

== 传参 = 引用可见

```c
struct P { int x }

function bump(P p) { p.x = 99 }

function main() -> int {
    P a
    a.x = 1
    bump(a)
    println(int_to_str(a.x))    // 99
    return 0
}
```

== 返回 = 拷贝出来

```c
struct P { int x
    int y }

function make(int v) -> P {
    P p
    p.x = v
    p.y = v * 2
    return p
}

function main() -> int {
    P q = make(4)
    println(int_to_str(q.x) + "," + int_to_str(q.y))   // 4,8
    return 0
}
```

:::

::: warning 语义有别，别混淆
`P b = a` 是拷贝（写 `b` 不动 `a`）；把 `P` 交给函数是引用（函数写 `p` 会动到 `a`）。
如果希望函数不改动调用方，请传标量字段，或在函数内先自行拷一份。
:::

## 固长数组字段

字段可以是固长数组，数组元素**值内嵌**在 struct 里（`_type_slots` 会按 `维度 × 元素槽数` 展开）。

```c
struct Student {
    int grades[5]         // 固长数组字段（值内嵌）
    float gpa
}

function main() -> int {
    Student s
    for (int i = 0; i < 5; i = i + 1) {
        s.grades[i] = 60 + i * 10
    }
    s.gpa = 88.5
    println(int_to_str(s.grades[0]) + " "
            + int_to_str(s.grades[4]) + " "
            + float_to_str(s.gpa))       // 60 100 88.5
    return 0
}
```

多维固长数组字段同样支持：

```c
struct Grid {
    int cell[3][4]        // 行主序
    int rows
}
```

## 字段限制

| 允许的字段 | 说明 |
|------------|------|
| 标量 `int` `float` `bool` `string` | `string` 本身只占一个指针槽 |
| 嵌套 struct | 值内嵌，占满内层全部槽 |
| 固长数组 `T[n]`、`T[n][m]` | 值内嵌，占 `n × 元素槽数` |
| struct 类型固长数组 `Point ps[3]` | 值内嵌 |

| 不支持的用法 | 说明 |
|--------------|------|
| 变长指针数组字段 `T[]` | 官方限制，**不要依赖**（见 `docs/CIN_GUIDE.md` §14 限制 5）；需要变长集合请用固长数组上限 + 长度字段 |
| 字段带初始化器 | 字段只声明类型与名字，没有默认初始化表达式 |
| 方法 / 构造函数 / 继承 | 语言不提供，用普通函数接收 struct 实现 |
| 取字段地址（`&s.x`） | 语言无取地址运算 |

::: danger 变长指针数组字段
`struct Bag { int[] items }` 这类写法不在受支持范围内。请改写为定长容量 + 计数字段：

```c
struct Bag {
    int items[64]        // 容量固定
    int count            // 实际使用数量
}
```

:::

## 实例化与默认全零

`StructName 变量名` 声明即分配存储，**所有字段默认为类型默认值**（`int` → `0`、`float` → `0.0`、`bool` → `false`、`string` → 空串、嵌套 struct 递归全零）。

```c
struct Point { int x
    int y
    string label
    bool visible }

function main() -> int {
    Point p
    println(int_to_str(p.x) + " " + int_to_str(p.y))   // 0 0
    println("[" + p.label + "]")                       // []
    println("visible=" + bool_to_str(p.visible))       // visible=false
    return 0
}
```

- 全局 struct 变量同样全零，且只能是全零（全局初始化器必须是常量）。
- 局部 struct 变量在进入所在块时分配，离开块后释放。

::: tabs

== 局部实例化

```c
struct Point { int x
    int y }

function main() -> int {
    Point p           // 栈上分配, 全零
    p.x = 1
    return p.x
}
```

== 全局实例化

```c
struct Point { int x
    int y }

Point origin          // 数据区, 全零

function main() -> int {
    return origin.x   // 0
}
```

== 数组实例化

```c
struct Point { int x
    int y }

function main() -> int {
    Point ps[4]           // 4 份完整 Point, 全零
    ps[2].x = 7
    return ps[0].x + ps[2].x   // 7
}
```

:::

## 完整示例：Point / Rectangle / Student

```c
// shapes.cin —— 运行: python cpu.py shapes.cin

struct Point {
    float x
    float y
}

struct Rectangle {
    Point top_left
    Point bottom_right
    float area
}

struct Student {
    string name
    int age
    int grades[5]
    float gpa
}

// 参数是 struct: 引用可见
function compute_area(Rectangle r) {
    float w = r.bottom_right.x - r.top_left.x
    float h = r.bottom_right.y - r.top_left.y
    r.area = w * h
}

// 返回值是 struct: 值拷贝
function make_student(string name, int age) -> Student {
    Student s
    s.name = name
    s.age = age
    for (int i = 0; i < 5; i = i + 1) {
        s.grades[i] = 60 + (i + 1) * 5
    }
    s.gpa = 88.5
    return s
}

function average(Student s) -> float {
    float sum = 0.0
    for (int i = 0; i < 5; i = i + 1) {
        sum = sum + s.grades[i]
    }
    return sum / 5.0
}

function main() -> int {
    Rectangle box
    box.top_left.x = 0.0
    box.top_left.y = 0.0
    box.bottom_right.x = 4.0
    box.bottom_right.y = 5.0
    compute_area(box)                       // 引用可见, box.area 被写入
    println("area = " + float_to_str(box.area))       // area = 20

    Rectangle copy = box                    // 值拷贝
    copy.top_left.x = 1.0
    println("orig.x = " + float_to_str(box.top_left.x)
            + " copy.x = " + float_to_str(copy.top_left.x))   // 0 1

    Student s = make_student("Ada", 18)
    println(s.name + " age=" + int_to_str(s.age)
            + " g0=" + int_to_str(s.grades[0])
            + " g4=" + int_to_str(s.grades[4]))
    println("avg = " + float_to_str(average(s)))      // avg = 75

    return int_to_str(box.area) == "20" ? 0 : 1
}
```

预期输出：

```text
area = 20
orig.x = 0 copy.x = 1
Ada age=18 g0=65 g4=85
avg = 75
```

## 常见 struct 错误

| 错误信息 | 原因 | 修正 |
|----------|------|------|
| `Struct P has no field y` | 访问了不存在的字段 | 检查字段名拼写 |
| `Member access on non-struct type: int` | 对 int/float 用 `.` | 只有 struct 变量能用 `.` |
| `Undefined variable: s` | struct 变量未声明 | 先 `Point s` 声明 |
| `Non-constant global initializer: ...` | 全局 struct 用了非常量初始化 | 全局留空、在函数内赋值 |
| `Expected RBRACE ... at line N` | 字段块括号不配对 | 检查 struct 定义 |

## 相关页面

| 主题 | 说明 | 页面 |
|------|------|------|
| 类型系统 | 各类型默认值与槽数 | [类型系统](/language/types) |
| 函数 | struct 传参与返回 | [函数](/language/functions) |
| 数组 | 固长数组与指针数组 | [数组](/language/arrays) |
| 字符串 | `string` 字段的指针语义 | [字符串](/language/strings) |
| 标准库 | `matrix.cin` / `queue.cin` 等库内 struct 用法 | [标准库总览](/stdlib/) |
| 限制与错误 | struct 字段限制原文 | [限制与常见错误](/language/errors) |
