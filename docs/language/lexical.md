---
description: CIN 词法规则：注释、标识符、各类字面量、转义序列、语句分隔与续行、BOM 处理
---

# 词法规则

CIN 的编译前端是**手写词法分析器**（`codecin/cin.py` 中的 `tokenize()`）：
先把源码切成记号（token），再做续行过滤，最后交给递归下降解析器。
本页说明写代码时真正会碰到的词法约定。

## 注释

| 形式 | 写法 | 说明 |
|------|------|------|
| 行注释 | `// 说明文字` | 到行尾结束；可用于语句尾部 |
| 块注释 | `/* 说明文字 */` | 可跨多行；块注释内不嵌套 |

```c
// 整行注释
int x = 1        // 行尾注释
/* 块注释
   可以跨行 */
int y = 2
```

::: warning 块注释的边界
块注释以遇到的下一个 `*/` 结束，**不支持嵌套**。写成 `/* a /* b */ c */` 时，
注释在第一个 `*/` 就结束了，剩下的 `c */` 会被当成代码而产生语法错误。
:::

## 标识符与关键字

| 元素 | 规则 |
|------|------|
| 标识符 | 首字符为字母或 `_`，后续为字母、数字或 `_`；**区分大小写** |
| 保留字 | `struct` `function` `if` `else` `while` `for` `do` `switch` `case` `default` `break` `continue` `return` `true` `false` `unsigned` |
| 内嵌指令名 | `set` `add` `subtract` `multiply` `divide` `increment` `decrement`（语句首出现时按内嵌语句解析） |
| 内建函数名 | `println` `print` `strlen` `sqrt` … 见 [内建函数](/language/builtins) |

```c
int Counter = 1      // 与 counter 是两个不同的变量
int _tmp = 2
int a1_b2 = 3
```

::: tip 关键字不是硬保留
CIN 的关键字识别发生在**语法层**而不是词法层：`function`、`if` 等只是普通标识符，
在特定位置才被当作关键字解释。因此把变量命名为 `set` 之类在语法上可能通过，
但会与内嵌指令语句撞车 —— 别这么写。
:::

## 字面量

### 整数字面量

```c
int dec = 42
int neg = -7              // 负号是一元运算符, 不是字面量的一部分
int hex = 0xFF            // 16 进制 (0x / 0X)
int bin = 0b1010          // 2 进制  (0b / 0B)
int oct = 0o17            // 8 进制  (0o / 0O)
int sep = 1_000_000       // 数字下划线可读性分隔
int hsep = 0x1_0          // 进制前缀字面量同样支持下划线
unsigned int big = 4000000000
```

| 规则 | 说明 |
|------|------|
| 进制前缀 | `0x`/`0X` = 16，`0b`/`0B` = 2，`0o`/`0O` = 8 |
| 下划线 | 允许出现在数字之间，任意位置（`1_0` 合法）；解析时被剔除 |
| 后缀 `u/U/l/L` | **被忽略**（值模型是 64 位槽，无宽度差异）：`0xFFu`、`100L`、`42u` 都合法 |
| 后缀 `f/F` | 把该字面量标记为 **float**：`100f` 等价于 `100.0` |
| 十进制上限 | 不能超过 `0x7FFFFFFFFFFFFFFF`（有符号 64 位最大值） |
| 十六进制上限 | 不能超过 `0xFFFFFFFFFFFFFFFF`（无符号 64 位最大值） |
| 非法写法 | 只有前缀没有数字（`0x`）、纯下划线（`0x_`）会报 `Malformed numeric literal` |

```c
int a = 0xFFu             // 255
int b = 0b1010U           // 10
int c = 0o17L             // 15
int d = 42u               // 42
float e = 1.5f            // 1.5
float f = 100f            // 100.0
```

::: danger 超出范围会编译失败
`int x = 9223372036854775808` 直接报错（不是静默截断）。
同理 `0xFFFFFFFFFFFFFFFF` 是合法的（恰好 64 位无符号上限），再加一位就报错。
:::

### 浮点字面量

```c
float pi = 3.14
float tiny = 1e-5             // 科学计数法 (e / E, 指数可带 +/-)
float big = 2.5E+10
float one = 1.0
float dot = .5                // 以 . 开头也可
float forced = 1f             // 整数字面量 + f 后缀 -> float
float under = 1_000.5         // 下划线同样可读
```

::: info 整数值的 float 打印出来没有小数点
`float_to_str(100.0)` 输出 `100`（去掉多余的 `.0`），`float_to_str(3.14)` 输出 `3.14`。
写日志时若需要固定的 `x.0` 形式，请自行拼接字符串。
:::

### 字符字面量

字符字面量是**整数字符编码**（没有任何独立的 char 值语义），可用转义：

```c
char letter = 'A'             // 65
char newline = '\n'           // 10
char tab = '\t'               // 9
char quote = '\''             // 39
char backslash = '\\'         // 92
char nul = '\0'               // 0
char bell = '\a'              // 7  (报警)
char backspace = '\b'         // 8
char formfeed = '\f'          // 12
char vtab = '\v'              // 11
char dquote = '\"'            // 34
```

### 字符串字面量

字符串用双引号，支持 `\n \t \r \" \\ \0` 转义；**不支持** `'` 包裹的字符串：

```c
string s = "hello"
string path = "C:\\temp\\file.txt"      // 反斜杠要转义
string multi = "line1\nline2"
string empty = ""
```

::: warning 字符串里没有的转义
遇到未定义的转义（如 `\q`）时，词法分析器会退化为「保留该字符本身」而不是报错，
`"\q"` 得到的就是 `q`。这类写法容易掩盖笔误，建议只用上表列出的转义。
:::

### 布尔字面量

```c
bool ok = true
bool no = false
println("ok = " + ok)          // ok = true
```

`bool` 打印为 `true` / `false`；参与数值运算时按 `1` / `0` 处理（见 [类型系统](/language/types)）。

## 语句分隔与续行

CIN 以**换行作为语句终止符**（`;` 可作显式分隔符）。词法层对换行的处理有三条规则：

| 场景 | 换行是否被忽略 | 说明 |
|------|----------------|------|
| 圆括号 `(...)` / 方括号 `[...]` 内 | **忽略**（自动连接） | 参数表、条件、下标可自由折行 |
| 行尾是运算符 | **忽略**（续行） | 长的表达式可以断在运算符后 |
| 花括号 `{...}` 块内 | **保留**（语句终止符） | 不能把一条语句折成两行 |

可触发续行的行尾运算符包括：

```text
+  -  *  /  %              算术
&  |  ^  ~  <<  >>        位运算
=  +=  -=  *=  /=  %=  &=  |=  ^=  <<=  >>=
==  !=  <  >  <=  >=      比较
&&  ||                    逻辑
++  --                    自增自减
,  .  ->                  逗号 / 成员 / 箭头
```

```c
// 可行: 行尾运算符续行
int long_result = value1 + value2 +
                  value3

// 可行: 括号内续行 (无需行尾运算符)
float x = (a + b) *
          (c + d)
println("total = " +
        int_to_str(long_result))

// 可行: 多行参数表
int m = idiv(1000 +
             234,
             7)
```

```c
// 错误: 花括号内不能把语句折行
function bad() -> int {
    int x = 1 +
        2            // 这里的换行是语句终止符 -> 解析报错
    return x
}
```

::: danger 最常见的语法错误来源
```
Expected RBRACE ... at line N
```
出现这个报错时，先回头看第 N 行附近：多数情况是**块内语句折行**、
**花括号不配对**，或者把表达式断在了行尾非运算符的位置。
:::

### 语句终止符对照

::: tabs

== 换行结尾（推荐）

```c
int a = 1
int b = 2
println("sum = " + (a + b))
```

== 分号结尾

```c
int a = 1; int b = 2;
println("sum = " + (a + b));
```

== 分号 + 同行多语句

```c
case 1: s = 10; break
case 2: s = 20; break
```

:::

## 大小写与编码

| 事项 | 约定 |
|------|------|
| 大小写 | **敏感**：`x` 与 `X` 是不同标识符，`if` 与 `If` 不同 |
| 源文件编码 | UTF-8；字符串字面量可直接包含中文等多字节字符 |
| BOM | 文件开头的 UTF-8 BOM（`\ufeff`）**被自动忽略**，含 BOM 的源文件与 `import` 均正常工作 |
| 行尾符 | `\r` 与 `\t`、空格一起被当作空白跳过，CRLF 源文件可用 |
| 制表符 | 仅作空白，不参与语法 |

```powershell
# Windows: 查看文件是否带 BOM (前 3 字节 EF BB BF)
Format-Hex -Path hello.cin | Select-Object -First 1
```

```bash
# Linux / macOS: 同样的检查
head -c 3 hello.cin | xxd
```

::: tip BOM 与 import 的关系
早期版本中带 BOM 的文件里 `import` 会静默失效；5.5.0 起 BOM 在词法层被丢弃，
带 BOM 的模块文件也能正常引用。参见 `tests/test_literals_and_bom.py`。
:::

## 词法错误速查

| 报错信息 | 触发原因 | 修正 |
|----------|----------|------|
| `Malformed numeric literal at ...` | `0x` 后无数字、只有下划线、非法进制数字 | 补全数字或改进制前缀 |
| `Numeric literal out of 64-bit range at ...` | 十进制 > `0x7FFF...FFFF`、十六进制 > `0xFFFF...FFFF` | 缩小字面量或用 `unsigned int` 接收合法范围内的值 |
| `Unexpected character 'x' at ...` | 出现了词法层不认识的字符（如 `@`、`#`、全角符号） | 改用合法运算符，检查是否混入中文标点 |
| `Unterminated char literal at ...` | 字符字面量缺右引号，或写了 `'ab'` | 字符字面量只能是一个字符或一个转义 |
| `Expected RBRACE ... at line N` | 块内语句折行 / 花括号不配对 | 用行尾运算符或括号续行 |

::: details 一个完整的词法探针
下面这段程序用到了本页的全部字面量形式，可直接运行验证：

```c
function main() -> int {
    int hex = 0xFFu
    int bin = 0b1010U
    int oct = 0o17L
    int sep = 1_000_000
    float forced = 100f
    char letter = 'A'
    string s = "a\tb" + "\n"
    bool ok = true
    println("hex=" + int_to_str(hex) + " bin=" + int_to_str(bin)
            + " oct=" + int_to_str(oct) + " sep=" + int_to_str(sep))
    println("forced=" + float_to_str(forced) + " letter=" + int_to_str(letter))
    println("len=" + int_to_str(strlen(s)) + " ok=" + bool_to_str(ok))
    return 0
}
```

```text
hex=255 bin=10 oct=15 sep=1000000
forced=100 letter=65
len=4 ok=true
```
:::

## 相关页面

- 类型与默认值：[类型系统](/language/types)
- 变量声明与作用域：[变量与作用域](/language/variables)
- 运算符与优先级：[运算符](/language/operators)
- 控制流语法：[控制流](/language/control-flow)
- 报错清单：[限制与常见错误](/language/errors)
