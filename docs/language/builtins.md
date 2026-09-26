---
description: "CIN 内建函数完整参考：I/O、数学、随机与时间、字符串/转换内建，含签名、返回值与边界行为。"
---

# 内建函数

内建函数由编译器/运行时直接提供 (不需要 `import`), 可以在**表达式任意位置**使用。
数值参数按需自动提升为 `float`; 整数与浮点混用时结果按浮点计算。

## 输入输出

| 函数 | 签名 | 说明 |
|------|------|------|
| `print(x)` | void | 输出不换行 (自动字符串化) |
| `println(x)` | void | 输出并换行; **无参调用输出空行** |
| `input()` | int | 读入一行并解析为整数 (解析失败为 `0`, EOF 也是 `0`) |

```c
function main() -> int {
    print("请输入一个整数: ")
    int n = input()
    println("你输入的是 " + int_to_str(n))
    return 0
}
```

## 数学

| 函数 | 签名 | 说明 |
|------|------|------|
| `abs(x)` | int | 整数绝对值 |
| `sqrt(x)` | float | 平方根 |
| `pow(x, y)` | float | `x` 的 `y` 次幂 |
| `sin(x)` / `cos(x)` / `tan(x)` | float | 三角函数 (弧度) |
| `floor(x)` / `ceil(x)` | float | 向下 / 向上取整 (**结果仍是 float**) |
| `round(x)` | float | 四舍五入 (`floor(x + 0.5)`, 半值向 +∞) |
| `min(a, b)` / `max(a, b)` | int/float | 最小 / 最大值 (混用按 float 提升) |
| `idiv(a, b)` | int | 整数除法 (向零截断) |

```c
function math_demo() -> void {
    float angle = 3.14159265 / 4
    println("sin(45°) = " + sin(angle))
    println("sqrt(16) = " + sqrt(16))       // 4
    println("pow(2, 8) = " + pow(2, 8))     // 256
    println("abs(-42) = " + abs(-42))       // 42
    println("floor(3.7) = " + floor(3.7))   // 3 (float)
    println("ceil(3.2) = " + ceil(3.2))     // 4 (float)
    println("round(2.5) = " + round(2.5))   // 3 (float)
    println("idiv(17, 5) = " + idiv(17, 5)) // 3
}
```

::: warning 取整函数返回 float
`floor` / `ceil` / `round` 返回的是 `float`, 赋给 `int` 时会截断转换:

```c
int n = floor(3.7)      // 3
float f = floor(3.7)    // 3.0
```
:::

## 随机与时间

| 函数 | 签名 | 说明 |
|------|------|------|
| `rand()` | int | 非负随机整数 |
| `srand(n)` | void | 设置随机种子 |
| `time()` | int | Unix 时间戳 (秒) |

```c
function roll() -> int {
    srand(42)                    // 固定种子 -> 序列可复现
    return rand() % 6 + 1
}
```

> 命令行 `--seed <n>` 也能让整个程序的随机序列确定 (见 [命令行参考](/guide/cli))。

## 字符串

| 函数 | 签名 | 说明 |
|------|------|------|
| `strlen(s)` | int | 字节长度 (不含 NUL) |
| `strcmp(a, b)` | int | 字典序比较 (`<0` / `0` / `>0`) |
| `strcpy(s)` | string | 复制为新堆块 |
| `substr(s, start, len)` | string | 子串 (新堆块, 越界自动裁剪) |
| `indexof(hay, needle)` | int | 首次出现位置, 未找到 `-1` |
| `upper(s)` / `lower(s)` | string | ASCII 大小写转换 (新堆块) |
| `trim(s)` / `ltrim(s)` / `rtrim(s)` | string | 去首尾 / 前导 / 尾部空白 (新堆块) |
| `atoi(s)` | int | 字符串 → 十进制整数 (前导空白忽略, 失败为 `0`) |

## 类型转换

| 函数 | 签名 | 说明 |
|------|------|------|
| `int_to_str(n)` / `itoa(n)` | string | 整数 → 十进制字符串 |
| `float_to_str(f)` / `ftoa(f)` | string | 浮点 → 字符串 (int/bool 自动提升) |
| `bool_to_str(b)` | string | 布尔 → `"true"` / `"false"` |

```c
println(int_to_str(-7))          // -7
println(float_to_str(3.5))       // 3.5
println(bool_to_str(1 > 2))      // false
```

::: tip 拼接会自动转换
`"n = " + 42` 与 `"n = " + int_to_str(42)` 结果相同; 需要固定格式时用显式转换。
:::

## 内嵌 CPU 指令语句

除了函数调用, CIN 还保留了 7 条寄存器风格的语句 (`set x 30`、`add x y` …),
它们直接作用于当前变量, 见 [内嵌 CPU 指令语句](/language/inline-cpu)。

## 宿主能力内建

下列内建**只在 Go 原生运行时可用** (`--no-native` 下会报
`host builtins ... require the native Go runtime`), 完整签名与示例见
[宿主能力](/language/host-abilities):

| 分类 | 函数 |
|------|------|
| 2D 画布 | `canvas` `set_color` `fill_rect` `fill_circle` `draw_line` `draw_text` `save_png` `show_canvas` |
| 联网音频 | `audio_play` `audio_stop` `audio_volume` `audio_wait` |
| 文件系统 | `file_read` `file_write` `file_append` `file_exists` `file_delete` `file_size` `mkdir` `dir_list` |
| 进程/环境 | `exec` `exec_output` `getenv` `setenv` |
| 系统信息 | `os_name` `hostname` `username` `cwd` `home_dir` |
| Termux (Android) | `termux_available` `termux_notify` `termux_toast` `termux_clipboard_get` `termux_clipboard_set` `termux_battery` `termux_vibrate` `termux_tts` `termux_location` `termux_wifi_info` `termux_dialog` `termux_sms_send` |

## 内建速查示例

```c
function builtins_demo() -> int {
    println("len = " + int_to_str(strlen("hello")))          // 5
    println("cmp = " + int_to_str(strcmp("a", "b")))         // -1
    println("idx = " + int_to_str(indexof("hello", "ll")))   // 2
    println("up  = " + upper("abc"))                          // ABC
    println("num = " + int_to_str(atoi(" 42 ")))              // 42
    println("min = " + int_to_str(min(5, 3)))                 // 3
    println("max = " + int_to_str(max(5, 3)))                 // 5
    println("div = " + int_to_str(idiv(17, 5)))               // 3
    return 0
}
```

## 常见错误

| 现象 | 原因 | 处理 |
|------|------|------|
| 调用未定义的“函数” | 拼错名字或忘记 `import` 标准库 | 检查拼写; 库函数需 `import "xxx.cin"` |
| `atoi` / `input` 返回 0 | 解析失败返回 0 (不抛错) | 先校验输入 (见标准库 `validate`) |
| `floor` 结果被截断 | 取整函数返回 float | 需要 int 时显式赋值截断 |
| 宿主内建报原生运行时错误 | 走了纯 Python 路径 | 去掉 `--no-native` 并确保原生库可用 |
| 浮点打印出现多余位 | `float_to_str` 按实现格式化 | 需要固定格式时自行取整/拼接 |

## 相关页面

- [字符串](/language/strings) — 字符串语义与陷阱
- [宿主能力](/language/host-abilities) — 画布 / 音频 / 文件 / 进程 / Termux
- [标准库概览](/stdlib/) — 19 个内置库 (数组、排序、统计、哈希、矩阵…)
- [标准库参考](/stdlib/reference) — 逐库逐函数签名
