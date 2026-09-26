---
description: "CIN 控制流：if/else、while、for、do-while、break/continue、switch/case 贯穿语义与三目表达式。"
---

# 控制流

CIN 的控制流与 C 基本一致: 条件必须写在圆括号里, 代码块用 `{}`; 单语句体可以省略花括号。
语句以换行结尾, 因此**块内的换行不会被忽略**。

## if / else

```c
if (x > 0) {
    println("positive")
} else if (x == 0) {
    println("zero")
} else {
    println("negative")
}
```

单语句体可以省略花括号 (但建议始终加上):

```c
if (done) return
if (!ready) println("not ready")
```

条件表达式可以是任意能求值成 bool 的表达式; `&&` / `||` 短路求值, `!` 取反。

## while

```c
int n = 10
int sum = 0
while (n > 0) {
    sum = sum + n
    n = n - 1
}
println("sum = " + sum)      // sum = 55
```

`while (true) { ... }` 是常用的无限循环写法, 用 `break` 退出。

## for

```c
for (int i = 0; i < 10; i = i + 1) {
    println("i = " + i)
}
```

- 三段 (init / cond / update) 都可以省略: `for (;;) { break }`;
- init 段支持类型声明, 声明的变量作用域覆盖整个循环;
- update 段是赋值表达式, 也可以写 `i++` 或 `i += 2`;
- 条件为空视为恒真。

## do-while

```c
int n = 0
do {
    n++
} while (n < 3)
println("n = " + n)          // n = 3
```

循环体**至少执行一次**, 条件在体后求值; 结尾的 `while (...)` 之后不需要分号 (写了也可以)。

## break / continue

```c
int sum = 0
for (int i = 1; i <= 10; i++) {
    if (i % 2 == 1) continue     // 跳过奇数
    if (i > 8) break             // 超过 8 提前结束
    sum += i                     // 只累加偶数
}
println("sum = " + sum)          // sum = 20
```

- `break` 跳出**最近一层** `switch` 或循环;
- `continue` 跳到循环的条件求值处 (`for` 会先执行 update 段);
- 在 `switch` 内部的循环里, `continue` 作用于循环、`break` 作用于 `switch`。

## switch / case / default

```c
int grade = 75
int tier = grade / 25            // '/' 是浮点除, 赋给 int 时截断
switch (tier) {
    case 0: println("level: low");    break
    case 1: println("level: medium"); break
    case 2:
    case 3: println("level: high");   break   // case 贯穿 (fallthrough)
    default: println("level: top")
}
```

实测输出:

```text
level: top
```

规则:

- 选择表达式与 `case` 常量必须是**整数**; `case` 支持常量表达式 (`case 2+3`) 与字符字面量 (`case 'A'`);
- 分支体**默认贯穿**到下一个 `case` (与 C 一致), 用 `break` 跳出整个 `switch`;
- `break` 跳出的是最近一层 `switch`/循环; `switch` 内嵌套循环时 `continue` 仍作用于循环;
- `default` 可以出现在任意位置, 无匹配时执行; 没有 `default` 且无匹配则整段跳过;
- `switch` 不会自动为最后一个分支加 `break`。

::: tip 多分支也可以用 if 链
`case` 常量必须是编译期整型常量, 需要范围判断或字符串判断时用 `if / else if` 链
(字符串用 `strcmp(a, b) == 0`)。
:::

## 三目表达式

```c
string status = (score >= 60) ? "pass" : "fail"
int sign = (x < 0) ? -1 : 1
int abs_x = (x < 0) ? -x : x
```

- 三目是**短路表达式**: 只求值被选中的一侧, 另一侧的副作用不会发生;
- int/float 混用时按 float 提升; 两侧同为 `string` 才允许字符串结果;
- 可以嵌套, 但超过两层建议改写成 `if` 链。

## 完整示例

`examples/control_flow.cin` (仓库自带, 实测输出如下):

```c
function main() -> int {
    int sum = 0
    for (int i = 1; i <= 10; i++) {
        if (i % 2 == 1) continue
        if (i > 8) break
        sum += i
    }
    int d = 0
    do {
        d++
    } while (d < 4)

    int grade = 75
    int tier = grade / 25
    switch (tier) {
        case 0: println("level: low");    break
        case 1: println("level: medium"); break
        case 2: println("level: high");   break
        default: println("level: top")
    }

    string ok = (grade >= 60) ? "pass" : "fail"
    println("sum=" + int_to_str(sum) + " d=" + int_to_str(d) + " " + ok)
    return sum + d
}
```

```text
level: top
sum=20 d=4 pass
```

## 常见错误

| 现象 | 原因 | 处理 |
|----------|------|------|
| `Expected RBRACE ... at line N` | 花括号不配对, 或块内语句缺少换行 | 检查第 N 行附近 |
| `switch` 分支“串”到一起 | 忘了 `break`, 分支默认贯穿 | 每个分支末尾加 `break` |
| `case` 报错 | `case` 常量不是整数常量表达式 | 用整数或常量表达式 |
| 死循环 | `--max-instructions` 上限内未退出 | 检查循环条件; 上限默认 1 亿 |
| `do-while` 少执行一次 | 条件写在体前 (写成了 `while`) | 确认使用 `do { } while (...)` |

## 相关页面

- [运算符](/language/operators) — 比较、逻辑短路与三目优先级
- [函数](/language/functions) — 递归与调用栈
- [限制与常见错误](/language/errors) — 报错表与排错入口
- [示例程序集](/guide/examples) — 更多可运行例子
