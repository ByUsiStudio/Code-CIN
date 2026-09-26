---
description: "CIN 字符串：NUL 结尾字节序列、指针语义、+ 拼接与自动字符串化、字符串内建、只读字节下标与常见陷阱。"
---

# 字符串

`string` 是 **NUL 结尾的字节序列**, 变量本身只保存指向它的**指针** (64 位槽)。
字符串字面量存放在数据区, 拼接、`strcpy`、`substr` 等操作会产生新的堆块。

## 字面量与拼接

```c
string s = "hello"
string t = s + " " + "world"    // 拼接产生新堆块
println(t)                      // hello world
println(strlen(t))              // 11
```

`+` 任意一侧为 `string` 时, 另一侧自动字符串化:

```c
println("n = " + 42)            // n = 42
println("pi = " + 3.14)         // pi = 3.14
println("ok = " + true)         // ok = true
println("c = " + 'A')           // c = 65  (字符字面量是整数编码)
```

::: tip 显式转换更可控
需要精确控制格式时用转换内建: `int_to_str` / `float_to_str` / `bool_to_str`
(别名 `itoa` / `ftoa`), 例如 `"x=" + int_to_str(x)`。
:::

## 比较与复制

```c
string a = "abc"
string b = "abc"

if (strcmp(a, b) == 0) {        // 内容比较: 正确做法
    println("equal")
}

string copy = strcpy(a)         // 复制为新堆块
```

::: danger 不要用 `==` 比较字符串内容
`==` 比较的是 64 位指针值而不是字符串内容。实测结果:

```c
string a = "ab" + "c"           // 堆块
string b = "abc"                // 数据区字面量
if (a == b) { println("true") } else { println("false") }   // false
if (strcmp(a, b) == 0) { println("strcmp equal") }          // strcmp equal
```

同一个字面量地址的两次比较可能“碰巧为真”, 因此永远不要依赖 `==` 判断内容。
:::

## 单字节访问

```c
string s = "Code CIN"
int h = s[0]                    // 67  ('C')
int i = s[5]                    // 67  ('C')
```

- `s[i]` 按字节下标, 返回 `0..255` 的整数编码;
- 字符串**不可原地修改**: `s[i] = 65` 不能作为赋值左值;
- 没有越界检查, `i` 必须落在 `0..strlen(s)` 之内。

## 字符串内建

| 函数 | 签名 | 说明 |
|------|------|------|
| `strlen(s)` | int | 字节长度 (不含结尾 NUL) |
| `strcmp(a, b)` | int | 字典序比较, `<0` / `0` / `>0` |
| `strcpy(s)` | string | 复制为新堆块 |
| `substr(s, start, len)` | string | 子串 (新堆块; 越界自动裁剪) |
| `indexof(hay, needle)` | int | 首次出现位置, 未找到为 `-1` |
| `upper(s)` / `lower(s)` | string | ASCII 大小写转换 (新堆块) |
| `trim(s)` / `ltrim(s)` / `rtrim(s)` | string | 去首尾 / 前导 / 尾部空白 (新堆块) |
| `atoi(s)` | int | 字符串 → 十进制整数 (忽略前导空白, 失败为 `0`) |
| `int_to_str(n)` / `itoa(n)` | string | 整数 → 十进制字符串 |
| `float_to_str(f)` / `ftoa(f)` | string | 浮点 → 字符串 |
| `bool_to_str(b)` | string | 布尔 → `"true"` / `"false"` |

完整列表与边界行为见 [内建函数](/language/builtins); 内置标准库 `str.cin`
(`s_upper` / `s_contains` / `s_starts_with` / `s_repeat` …) 见
[标准库参考](/stdlib/reference)。

## 完整示例

```c
import "str.cin"

function main() -> int {
    string raw = "  Hello, CIN  "
    string t = trim(raw)                       // 新堆块
    println("[" + t + "]")                     // [Hello, CIN]
    println("len = " + int_to_str(strlen(t)))  // len = 10
    println("upper = " + upper(t))             // upper = HELLO, CIN
    println("sub = " + substr(t, 0, 5))        // sub = Hello
    println("idx = " + int_to_str(indexof(t, "CIN")))    // idx = 7
    println("num = " + int_to_str(atoi(" 42 ")))          // num = 42
    println("rep = " + s_repeat("ab", 3))      // rep = ababab
    if (strcmp(s_lower("HeLLo"), "hello") == 0) {
        println("case-insensitive equal")      // case-insensitive equal
    }
    return 0
}
```

## 内存与性能注意

- 每次拼接、`strcpy`、`substr`、`upper` 等都会**分配新的堆块**;
- 在长循环里反复拼接会持续占用堆, 必要时改用标准库工具或预先算好长度;
- 堆与栈在默认 64 KiB 内存里是共享的 (堆基址 `0x8000`, 栈从 `0xFFF8` 往下),
  堆栈相撞会报 `Stack overflow (collides with heap)`, 可用 `--mem-size` 扩容;
- 内存布局细节见 [寄存器与内存模型](/reference/registers-memory)。

## 常见错误

| 现象 | 原因 | 处理 |
|------|------|------|
| 两个内容相同的字符串 `==` 为 false | `==` 比较指针 | 用 `strcmp(a, b) == 0` |
| `s[i] = 65` 编译/运行报错 | 字符串不可原地修改 | 生成新串 (`upper`/`substr`/拼接) |
| 打印出乱码或截断 | 字符串被越界写破坏 (`s[i]` 越界) | 检查下标范围; 打开 `--bounds-check` |
| `atoi("abc")` 返回 0 | 解析失败返回 0, 不报错 | 先用 `val_is_int` 校验 (见标准库 `validate`) |
| 循环里拼接后内存耗尽 | 每步都产生新堆块 | 减少拼接次数或扩容 `--mem-size` |

## 相关页面

- [内建函数](/language/builtins) — 字符串与转换内建全表
- [运算符](/language/operators) — `+` 拼接与自动字符串化规则
- [标准库参考](/stdlib/reference) — `str` / `conv` / `validate` 库
- [数组](/language/arrays) — `s[i]` 与数组下标的区别
