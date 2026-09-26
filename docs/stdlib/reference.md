---
description: Code CIN 19 个官方标准库的逐库逐函数参考：签名、返回值、边界行为与可运行示例。
---

# 逐库函数参考

本页覆盖 `codecin/lib/` 下全部 19 个官方标准库，共 218 个函数。每个库一节，先说明用途与
`import` 语句，再以表格列出**该库的全部函数**，最后给出一个可直接运行的 CIN 示例。

约定与阅读提示：

- 签名（参数个数、顺序、类型）与返回值**直接取自 `codecin/lib/<库>.cin` 中的真实定义**；
- CIN 数组**不携带长度**，因此所有数组接口都要求显式传入元素个数 `n`；
- `void` 返回值表示该函数只产生副作用（原地修改数组、写输出数组、打印、写文件），无返回值；
- `_sorted` 结尾的函数要求输入**已升序**，否则结果无意义；矩阵类 `_to` 风格函数把结果写入调用方提供的输出数组；
- 三条执行路径（Go 原生 VM / JIT / 纯 Python 解释器）对纯 CIN 库的行为一致；
  `io` / `gui` / `termux` 三库依赖宿主能力，需 Go 原生运行时。

## array

整数数组工具库。CIN 数组不带长度，所有接口都要显式传元素个数 `n`。就地修改的接口返回值
是 `void`，调用后直接读原数组即可。

```c
import "array.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `a_sum` | `a_sum(int[] a, int n)` | `int` | 前 `n` 个元素求和；`n <= 0` 时为 0 |
| `a_max` | `a_max(int[] a, int n)` | `int` | 最大值；`n <= 0` 返回 0（不是错误码） |
| `a_min` | `a_min(int[] a, int n)` | `int` | 最小值；`n <= 0` 返回 0 |
| `a_avg` | `a_avg(int[] a, int n)` | `float` | 平均值，走 `a_sum(a,n) / n` 的浮点除法；`n <= 0` 返回 `0.0` |
| `a_find` | `a_find(int[] a, int n, int v)` | `int` | 线性查找首个等于 `v` 的下标；未找到返回 `-1` |
| `a_contains` | `a_contains(int[] a, int n, int v)` | `int` | 是否包含 `v`，返回 `1`/`0` |
| `a_count` | `a_count(int[] a, int n, int v)` | `int` | `v` 出现次数 |
| `a_reverse` | `a_reverse(int[] a, int n)` | `void` | 原地反转前 `n` 个元素 |
| `a_fill` | `a_fill(int[] a, int n, int v)` | `void` | 把前 `n` 个元素全部置为 `v` |
| `a_copy` | `a_copy(int[] src, int[] dst, int n)` | `void` | 把 `src` 前 `n` 个元素拷入 `dst`；长度不足会越界 |
| `a_index_of_max` | `a_index_of_max(int[] a, int n)` | `int` | 最大值下标（并列取最靠前）；`n <= 0` 返回 `-1` |
| `a_index_of_min` | `a_index_of_min(int[] a, int n)` | `int` | 最小值下标（并列取最靠前）；`n <= 0` 返回 `-1` |
| `a_sum_range` | `a_sum_range(int[] a, int lo, int hi)` | `int` | 左闭右开区间 `[lo, hi)` 的和 |
| `a_lower_bound` | `a_lower_bound(int[] a, int n, int v)` | `int` | 首个 `>= v` 的下标（数组需有序）；无则 `-1`。实现是线性扫描，不是二分 |

```c
import "array.cin"

function main() -> int {
    int a[6] = {4, 8, 1, 8, 3, 6}
    println("sum=" + int_to_str(a_sum(a, 6)))              // 30
    println("avg=" + float_to_str(a_avg(a, 6)))            // 5.000000
    println("find3=" + int_to_str(a_find(a, 6, 3)))        // 4
    a_reverse(a, 6)
    println("first=" + int_to_str(a[0]))                   // 6
    a_fill(a, 3, 0)
    println("count0=" + int_to_str(a_count(a, 6, 0)))      // 3
    return 0
}
```

## bits

64 位位运算库。内建 `>>` 是**算术右移**（负数补 1），因此本库所有涉及负数的位移都先做
符号位/掩码处理，保证结果与 64 位无符号语义一致。位下标合法范围是 `0..63`。

```c
import "bits.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `bits_mask` | `bits_mask()` | `int` | 返回 64 位全 1 掩码 `0xFFFFFFFFFFFFFFFF` |
| `bits_popcount` | `bits_popcount(int x)` | `int` | 二进制中 1 的个数，**含符号位**；`bits_popcount(-1)` 为 64 |
| `bits_clz` | `bits_clz(int x)` | `int` | 前导零个数（按 64 位）；`x < 0` 返回 0，`x == 0` 返回 64 |
| `bits_ctz` | `bits_ctz(int x)` | `int` | 末尾零个数；`x == 0` 返回 64 |
| `bits_is_pow2` | `bits_is_pow2(int x)` | `int` | 是否为 2 的幂，仅正数成立；`x <= 0` 返回 0 |
| `bits_next_pow2` | `bits_next_pow2(int x)` | `int` | 不小于 `x` 的最小 2 的幂；`x <= 1` 返回 1；溢出时循环提前结束 |
| `bits_test` | `bits_test(int x, int i)` | `int` | 读取第 `i` 位，返回 `0`/`1`；`i` 越界返回 0 |
| `bits_set` | `bits_set(int x, int i)` | `int` | 置位；`i` 越界时原样返回 `x` |
| `bits_clear` | `bits_clear(int x, int i)` | `int` | 清位；`i` 越界时原样返回 `x` |
| `bits_toggle` | `bits_toggle(int x, int i)` | `int` | 取反某一位；`i` 越界时原样返回 `x` |
| `bits_rotl` | `bits_rotl(int x, int n)` | `int` | 循环左移，`n` 自动对 64 取模并归一为非负数；`k == 0` 返回 `x` |
| `bits_rotr` | `bits_rotr(int x, int n)` | `int` | 循环右移，等价于 `bits_rotl(x, 64 - k)` |
| `bits_reverse` | `bits_reverse(int x)` | `int` | 64 位位序反转（低位变高位） |
| `bits_range_mask` | `bits_range_mask(int lo, int hi)` | `int` | 闭区间 `[lo, hi]` 掩码；`lo < 0`、`hi > 63` 或 `hi < lo` 返回 0 |
| `bits_extract` | `bits_extract(int x, int lo, int hi)` | `int` | 取出 `[lo, hi]` 并右移到最低位；区间非法返回 0 |
| `bits_insert` | `bits_insert(int x, int lo, int hi, int v)` | `int` | 用 `v` 的低位替换 `x` 的 `[lo, hi]`；区间非法原样返回 `x` |
| `bits_bswap` | `bits_bswap(int x)` | `int` | 字节序翻转（bswap64）；`bits_bswap(0x0102)` 为 `0x0201000000000000` |

```c
import "bits.cin"

function main() -> int {
    println("pc=" + int_to_str(bits_popcount(0xFF)))        // 8
    println("clz=" + int_to_str(bits_clz(1)))               // 63
    println("ctz=" + int_to_str(bits_ctz(8)))               // 3
    println("pow2=" + int_to_str(bits_is_pow2(64)))         // 1
    println("next=" + int_to_str(bits_next_pow2(5)))        // 8
    println("mask=" + int_to_str(bits_range_mask(4, 7)))    // 240 (0xF0)
    println("ext=" + int_to_str(bits_extract(0xABCD, 4, 7)))// 12 (0xC)
    println("ins=" + int_to_str(bits_insert(0, 4, 7, 0xC))) // 192 (0xC0)
    return 0
}
```

## conv

进制转换与字符串格式化库。整数一律按 **64 位无符号**处理，负数的十六进制输出是 16 位补码形式。

```c
import "conv.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `c_hex_digit` | `c_hex_digit(int ch)` | `int` | 十六进制字符 → 数值，支持 `0-9` `A-F` `a-f`；非法返回 `-1`（参数是字符字节值） |
| `c_to_hex` | `c_to_hex(int v)` | `string` | 整数 → 大写十六进制字符串；`0` 返回 `"0"`，无 `0x` 前缀，负数给出 16 位补码 |
| `c_parse_hex` | `c_parse_hex(string s)` | `int` | 十六进制字符串 → 整数；可带 `0x`/`0X` 前缀，**遇非法字符停止**而非报错 |
| `c_to_bin` | `c_to_bin(int v)` | `string` | 整数 → 二进制字符串；`0` 返回 `"0"`，负数按 64 位无符号展开 |
| `c_parse_bin` | `c_parse_bin(string s)` | `int` | 二进制字符串 → 整数；可带 `0b`/`0B` 前缀，遇非法字符停止 |
| `c_pad_left` | `c_pad_left(string s, int width, string fill)` | `string` | 左填充到宽度 `width`；`fill` 可多字符（长度不整除时结果可超过 `width`），`fill` 为空串时原样返回 |
| `c_pad_right` | `c_pad_right(string s, int width, string fill)` | `string` | 右填充到宽度 `width`，规则同上 |
| `c_pad_int` | `c_pad_int(int v, int width)` | `string` | 用 `0` 左填充整数到 `width`；负数保留 `-` 号且只填充数字部分 |
| `c_repeat` | `c_repeat(string s, int n)` | `string` | 字符串重复 `n` 次；`n <= 0` 返回空串 |
| `c_chr` | `c_chr(int code)` | `string` | 字符编码 → 单字符字符串；特殊处理 `10`/`9`/`13`；覆盖常用可见 ASCII，未知返回空串 |
| `c_to_int` | `c_to_int(string s)` | `int` | 十进制字符串 → 整数，是内建 `atoi` 的别名 |
| `c_parse_float` | `c_parse_float(string s)` | `float` | 字符串 → 浮点；支持可选负号与小数部分，遇非法字符停止，不解析指数形式 |

```c
import "conv.cin"

function main() -> int {
    println(c_to_hex(255))                  // FF
    println(int_to_str(c_parse_hex("0x1F")))// 31
    println(c_to_bin(10))                   // 1010
    println(int_to_str(c_parse_bin("0b1010")))  // 10
    println(c_pad_int(7, 3))                // 007
    println(c_pad_left("ab", 4, "-"))       // --ab
    println(c_repeat("xy", 3))              // xyxyxy
    println(float_to_str(c_parse_float("-3.25")))  // -3.250000
    return 0
}
```

## gui

画布绘图助手库。内部调用 `canvas` / `set_color` / `fill_rect` / `fill_circle` / `draw_line` /
`draw_text` / `save_png` / `show_canvas` 等宿主能力内建，因此**必须运行在 Go 原生引擎上**。

```c
import "gui.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `g_rgb` | `g_rgb(int r, int g, int b)` | `int` | 打包 RGB 为 `0xRRGGBB`；每个分量只取低 8 位（`& 255`） |
| `g_new` | `g_new(int w, int h)` | `void` | 新建白色画布 `w × h`，等价于内建 `canvas(w, h)` |
| `g_clear` | `g_clear(int w, int h, int rgb)` | `void` | 新建画布并整体填充为 `rgb` |
| `g_rect_outline` | `g_rect_outline(int x, int y, int w, int h, int rgb)` | `void` | 画线宽 1 的矩形边框（四条 `draw_line`，不填充） |
| `g_bar_chart` | `g_bar_chart(int[] values, int n, int w, int h)` | `void` | 柱状图：先清成白底，按最大值自动缩放，柱子深蓝 `(60,120,200)`，最后补黑边框；`n <= 0` 时只留白底 |
| `g_grid` | `g_grid(int w, int h, int step)` | `void` | 浅灰 `(200,200,200)` 网格；`step <= 0` 会死循环，务必传正数 |
| `g_line_chart` | `g_line_chart(int[] values, int n, int w, int h)` | `void` | 折线图：白底 + 深红 `(200,40,40)` 折线；`n <= 1` 时只留白底 |
| `g_save` | `g_save(string path)` | `int` | 导出 PNG，`0` 成功 / `-1` 失败，转调内建 `save_png` |
| `g_show` | `g_show()` | `int` | 调系统查看器弹出窗口，转调内建 `show_canvas` |

```c
import "gui.cin"

function main() -> int {
    int a[5] = {3, 7, 2, 9, 5}
    g_bar_chart(a, 5, 50, 30)
    if (g_save("bar.png") != 0) { return 1 }
    println(int_to_str(g_rgb(255, 0, 0)))   // 16711680 (0xFF0000)
    g_line_chart(a, 5, 40, 20)
    g_save("line.png")
    return 0
}
```

## hash

哈希函数库。全部为 64 位整数运算（自然溢出即取模 2^64），适合与 `rand.cin` 配合做简易哈希表
或布隆过滤器的散列函数。

```c
import "hash.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `hash_djb2` | `hash_djb2(string s)` | `int` | djb2：`h = h*33 + c`，初值 5381；空串返回 5381 |
| `hash_fnv1a` | `hash_fnv1a(string s)` | `int` | FNV-1a（64 位）；空串返回偏移基 `0xCBF29CE484222325` |
| `hash_sdbm` | `hash_sdbm(string s)` | `int` | sdbm：`h = c + (h<<6) + (h<<16) - h`，初值 0 |
| `hash_int` | `hash_int(int x)` | `int` | splitmix64 风格的整数位混合，用于打散规律性输入；`hash_int(0)` 为 0 |
| `hash_combine` | `hash_combine(int h1, int h2)` | `int` | 组合两个哈希值，**不可交换**；用于把多个字段揉成一个哈希 |
| `hash_bucket` | `hash_bucket(int h, int buckets)` | `int` | 映射到 `[0, buckets)`；`buckets <= 0` 返回 0，负数取模结果也归一到非负 |
| `hash_string_bucket` | `hash_string_bucket(string s, int buckets)` | `int` | 字符串 → 桶下标，即 `hash_bucket(hash_fnv1a(s), buckets)` |

```c
import "hash.cin"

function main() -> int {
    println(int_to_str(hash_djb2("")))              // 5381
    println(int_to_str(hash_string_bucket("hello", 16)))  // 0..15
    println(int_to_str(hash_bucket(-3, 8)))         // 5
    int h = hash_combine(hash_djb2("user"), hash_fnv1a("42"))
    println(int_to_str(hash_bucket(h, 64)))
    if (hash_djb2("abc") == hash_djb2("abd")) { return 1 }
    return 0
}
```

## io

文件与路径工具库。内部转调 `file_read` / `file_write` / `file_append` / `file_exists` /
`file_size` / `file_delete` / `mkdir` / `dir_list` 等**宿主能力内建**，必须运行在 Go 原生引擎上；
文本处理部分（`io_line_count` / `io_get_line` / `io_split_get` / `io_split_count`）是纯 CIN 逻辑。

```c
import "io.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `io_read` | `io_read(string path)` | `string` | 读取整个文本；失败返回空串（不抛错，需自行判断） |
| `io_write` | `io_write(string path, string text)` | `int` | 覆盖写入，`0` 成功 / `-1` 失败 |
| `io_append` | `io_append(string path, string text)` | `int` | 追加写入，`0` 成功 / `-1` 失败 |
| `io_exists` | `io_exists(string path)` | `int` | 是否存在，`1`/`0` |
| `io_size` | `io_size(string path)` | `int` | 文件字节数；失败返回 `-1` |
| `io_remove` | `io_remove(string path)` | `int` | 删除文件，`0` 成功 / `-1` 失败 |
| `io_mkdir` | `io_mkdir(string path)` | `int` | **递归创建目录**，`0` 成功 / `-1` 失败 |
| `io_list` | `io_list(string path)` | `string` | 目录条目，换行分隔；**目录名带 `/` 后缀** |
| `io_join` | `io_join(string a, string b)` | `string` | 路径拼接：任一侧为空则返回另一侧；已以 `/` 或 `\` 结尾则直接相连，否则补 `/`。**不做 `..` 归一化** |
| `io_basename` | `io_basename(string path)` | `string` | 取最后一段（同时识别 `/` 与 `\`）；无分隔符则原样返回 |
| `io_dirname` | `io_dirname(string path)` | `string` | 取目录部分；无分隔符返回空串，`"/a"` 返回空串 |
| `io_line_count` | `io_line_count(string text)` | `int` | 按 `\n` 统计行数；空串返回 0，`"a\nb"` 返回 2 |
| `io_get_line` | `io_get_line(string text, int idx)` | `string` | 取第 `idx` 行（0 起，不含换行符）；越界返回空串 |
| `io_split_get` | `io_split_get(string text, string sep, int idx)` | `string` | 按分隔串切分，取第 `idx` 段；`sep` 为空串时 `idx == 0` 返回原文，否则空串；越界返回空串 |
| `io_split_count` | `io_split_count(string text, string sep)` | `int` | 按分隔串切分的段数；`sep` 为空串时返回 1 |

```c
import "io.cin"

function main() -> int {
    if (io_write("note.txt", "a\nb\nc") != 0) { return 1 }
    println(int_to_str(io_size("note.txt")))                 // 5
    println(int_to_str(io_line_count(io_read("note.txt"))))  // 3
    println(io_get_line(io_read("note.txt"), 1))             // b
    println(io_basename("dir/note.txt"))                     // note.txt
    println(io_join("x", "y"))                               // x/y
    println(int_to_str(io_split_count("a,b,c", ",")))        // 3
    println(int_to_str(io_remove("note.txt")))
    return 0
}
```

## json

极简 JSON 取值库。面向**扁平对象**（典型场景是 Termux API 返回的 JSON），不做完整语法解析，
只支持 `"key": value` 形式的字符串/数字/布尔/空值提取；嵌套对象内层字段不会被正确解析。

```c
import "json.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `j_raw` | `j_raw(string json, string key)` | `string` | 取 key 对应的原始片段：字符串值去引号，数字/`true`/`false`/`null` 原样返回（两端 `trim`）；找不到返回空串 |
| `j_str` | `j_str(string json, string key)` | `string` | 取字符串字段，直接转调 `j_raw` |
| `j_int` | `j_int(string json, string key)` | `int` | 取整数字段，走 `atoi(j_raw(...))`；无法解析为 0 |
| `j_float` | `j_float(string json, string key)` | `float` | 取浮点字段，支持可选负号与小数；无法解析为 `0.0`，不解析指数形式 |
| `j_has` | `j_has(string json, string key)` | `int` | 是否包含 `"key"` 字面量，`1`/`0`；只看引号包裹的键名，不做语法校验 |
| `j_bool` | `j_bool(string json, string key)` | `int` | 取布尔字段：值为 `true` 或 `1` 时返回 1，其余（含 `false`、缺失）返回 0 |

```c
import "json.cin"

function main() -> int {
    string j = "{\"name\":\"cin\",\"level\":42,\"on\":true,\"temp\":30.5}"
    println(j_str(j, "name"))                       // cin
    println(int_to_str(j_int(j, "level")))          // 42
    println(int_to_str(j_bool(j, "on")))            // 1
    println(float_to_str(j_float(j, "temp")))       // 30.500000
    println(int_to_str(j_has(j, "missing")))        // 0
    return 0
}
```

## math

数学扩展库。注意 CIN 的 `/` 恒为浮点除法，本库提供的是显式的取整/最值/夹取辅助函数。
同一文件同时给出 `f_`（浮点）与 `i_`（整数）两组函数。

```c
import "math.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `f_abs` | `f_abs(float x)` | `float` | 绝对值 |
| `f_floor` | `f_floor(float x)` | `float` | 向下取整，负数向 `-inf`（`f_floor(-2.5)` 为 `-3.0`） |
| `f_ceil` | `f_ceil(float x)` | `float` | 向上取整，实现为 `-f_floor(-x)` |
| `f_round` | `f_round(float x)` | `float` | 四舍五入，实现为 `f_floor(x + 0.5)`；`.5` 一律向上 |
| `f_min` | `f_min(float a, float b)` | `float` | 两浮点较小者 |
| `f_max` | `f_max(float a, float b)` | `float` | 两浮点较大者 |
| `i_min` | `i_min(int a, int b)` | `int` | 两整数较小者 |
| `i_max` | `i_max(int a, int b)` | `int` | 两整数较大者 |
| `i_clamp` | `i_clamp(int v, int lo, int hi)` | `int` | 夹取到 `[lo, hi]`；`lo > hi` 时先命中 `v < lo` 分支 |
| `f_round`↔内建 | 见下方 tabs | — | `f_round` 与内建 `round` 语义一致，后者单条指令更快 |

::: tabs

== 调用标准库

```c
import "math.cin"

function main() -> int {
    println(float_to_str(f_round(2.5)))    // 3.000000
    println(float_to_str(f_floor(-2.5)))   // -3.000000
    println(int_to_str(i_clamp(99, 0, 10)))// 10
    return 0
}
```

== 等价内建

```c
function main() -> int {
    println(float_to_str(round(2.5)))      // 3.000000
    println(float_to_str(floor(-2.5)))     // -3.000000
    println(int_to_str(max(min(99, 10), 0)))  // 10
    return 0
}
```

:::

## matrix

方阵运算库。矩阵以**一维数组行主序**存放：`m[i*n + j]` 表示第 `i` 行第 `j` 列，因此可直接用
CIN 的 `int[]` 与固定数组，无需动态内存。所有 `_to` 形式把结果写入调用方提供的输出数组
`out`（`out` 与输入数组共用时结果不确定，`mat_mul` 明确要求 `out` 不得与 `a`/`b` 为同一数组）。

```c
import "matrix.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `mat_zero` | `mat_zero(int[] m, int n)` | `void` | 把 `n*n` 个元素全部置 0 |
| `mat_identity` | `mat_identity(int[] m, int n)` | `void` | 置为单位阵（对角线 1，其余 0） |
| `mat_get` | `mat_get(int[] m, int n, int i, int j)` | `int` | 逐元素取值；下标越界返回 0（不报错） |
| `mat_set` | `mat_set(int[] m, int n, int i, int j, int v)` | `void` | 逐元素赋值；下标越界**静默不做任何事** |
| `mat_add` | `mat_add(int[] a, int[] b, int[] out, int n)` | `void` | `out = a + b` |
| `mat_sub` | `mat_sub(int[] a, int[] b, int[] out, int n)` | `void` | `out = a - b` |
| `mat_scale` | `mat_scale(int[] a, int k, int[] out, int n)` | `void` | `out = a * k`（逐元素乘标量） |
| `mat_mul` | `mat_mul(int[] a, int[] b, int[] out, int n)` | `void` | `out = a × b`，经典三重循环；`out` 不得与 `a`/`b` 为同一数组 |
| `mat_transpose` | `mat_transpose(int[] a, int[] out, int n)` | `void` | `out = a^T` |
| `mat_trace` | `mat_trace(int[] a, int n)` | `int` | 迹（对角线之和） |
| `mat_sum` | `mat_sum(int[] a, int n)` | `int` | 所有 `n*n` 个元素之和 |
| `mat_equals` | `mat_equals(int[] a, int[] b, int n)` | `int` | 是否逐元素相等，`1`/`0` |
| `mat_is_symmetric` | `mat_is_symmetric(int[] a, int n)` | `int` | 是否为对称矩阵（只比较上三角），`1`/`0` |
| `mat_det` | `mat_det(int[] a, int n)` | `int` | 行列式，拉普拉斯递归展开；**适合 `n <= 6`**（内部 `minor[36]` 只够 `(n-1)^2 <= 36` 的余子式）。`n <= 0` 返回 0，`n == 1` 返回 `a[0]`，`n == 2` 走闭式公式 |
| `mat_print` | `mat_print(int[] a, int n)` | `void` | 调试打印，每行一个方括号，元素以空格分隔 |

```c
import "matrix.cin"

function main() -> int {
    int n = 2
    int a[4] = {1, 2, 3, 4}
    int b[4] = {5, 6, 7, 8}
    int out[4]
    mat_mul(a, b, out, n)
    println(int_to_str(out[0]))            // 19
    mat_transpose(a, out, n)
    println(int_to_str(out[1]))            // 3
    println(int_to_str(mat_trace(a, n)))   // 5
    println(int_to_str(mat_det(a, n)))     // -2
    int m3[9] = {2, 0, 0, 0, 3, 0, 0, 0, 4}
    println(int_to_str(mat_det(m3, 3)))    // 24
    mat_print(a, n)
    return 0
}
```

## queue

队列与栈库。提供**定长环形队列（FIFO）**与**定长栈（LIFO）**，使用库内全局状态
（`queue_ring[64]`、`queue_head`、`queue_tail`、`queue_len`、`stack_data[64]`、`stack_len`），
因此**同一程序内各只有一份实例**；需要多实例时请用 `struct` 自行封装。
容量上限 `QUEUE_CAP` / `STACK_CAP` 均为 **64**。

```c
import "queue.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `queue_capacity` | `queue_capacity()` | `int` | 固定返回 64 |
| `queue_clear` | `queue_clear()` | `void` | 清空队列（头尾指针与长度归零） |
| `queue_size` | `queue_size()` | `int` | 当前元素个数 |
| `queue_is_empty` | `queue_is_empty()` | `int` | `1` 为空 |
| `queue_is_full` | `queue_is_full()` | `int` | `1` 为满（长度等于 64） |
| `queue_push` | `queue_push(int v)` | `int` | 入队；成功返回 1，**满时返回 0 且丢弃该值** |
| `queue_pop` | `queue_pop()` | `int` | 出队并返回；**空时返回 0**（无法区分“空”与“队首就是 0”，需先查 `queue_is_empty`） |
| `queue_front` | `queue_front()` | `int` | 查看队首（不出队）；空时返回 0 |
| `queue_back` | `queue_back()` | `int` | 查看队尾；空时返回 0。实现为 `tail-1`，`tail == 0` 时回绕到下标 63 |
| `stack_capacity` | `stack_capacity()` | `int` | 固定返回 64 |
| `stack_clear` | `stack_clear()` | `void` | 清空栈 |
| `stack_size` | `stack_size()` | `int` | 当前元素个数 |
| `stack_is_empty` | `stack_is_empty()` | `int` | `1` 为空 |
| `stack_push` | `stack_push(int v)` | `int` | 入栈；成功返回 1，**满时返回 0** |
| `stack_pop` | `stack_pop()` | `int` | 出栈并返回；空时返回 0 |
| `stack_peek` | `stack_peek()` | `int` | 查看栈顶（不出栈）；空时返回 0 |

```c
import "queue.cin"

function main() -> int {
    queue_clear()
    queue_push(1)
    queue_push(2)
    println(int_to_str(queue_size()))     // 2
    println(int_to_str(queue_front()))    // 1
    println(int_to_str(queue_back()))     // 2
    println(int_to_str(queue_pop()))      // 1

    stack_clear()
    stack_push(7)
    stack_push(8)
    println(int_to_str(stack_peek()))     // 8
    println(int_to_str(stack_pop()))      // 8
    println(int_to_str(stack_pop()))      // 7
    println(int_to_str(stack_pop()))      // 0 (已空)
    return 0
}
```

## rand

随机工具库。基于内建 `rand()`；需要可复现序列时先调用内建 `srand(种子)`（只种一次，别在循环里种）。

```c
import "rand.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `r_range` | `r_range(int lo, int hi)` | `int` | 闭区间 `[lo, hi]` 随机整数；`hi <= lo` 时直接返回 `lo` |
| `r_bool` | `r_bool()` | `bool` | 随机布尔，等价于 `r_range(0, 1) == 1` |
| `r_float` | `r_float()` | `float` | `[0.0, 1.0)` 随机浮点，实现为 `rand() / 2147483648.0` |
| `r_float_range` | `r_float_range(float lo, float hi)` | `float` | `[lo, hi)` 随机浮点；`hi < lo` 时区间反向，结果落在 `(hi, lo]` |
| `r_shuffle` | `r_shuffle(int[] a, int n)` | `void` | 原地 Fisher-Yates 洗牌，元素多重集不变 |
| `r_choice` | `r_choice(int[] a, int n)` | `int` | 随机取一个元素；`n <= 0` 返回 0，不做错误上报 |
| `r_chance` | `r_chance(int p)` | `int` | 以 `p`（0..100 的百分比）概率返回 1；`p <= 0` 恒 0，`p >= 100` 恒 1 |

```c
import "rand.cin"

function main() -> int {
    srand(12345)                                  // 固定种子 -> 可复现
    for (int i = 0; i < 3; i = i + 1) {
        println(int_to_str(r_range(10, 20)))      // 10..20
    }
    int a[3] = {1, 2, 3}
    r_shuffle(a, 3)
    println(int_to_str(r_choice(a, 3)))           // 1 / 2 / 3
    println(int_to_str(r_chance(50)))             // 0 或 1
    return 0
}
```

## sort

排序与查找库。三个基础排序 + 快速排序，全部原地升序；二分查找要求数组已升序。

```c
import "sort.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `sort_is_sorted` | `sort_is_sorted(int[] a, int n)` | `int` | 是否已升序（相等视为有序），`1`/`0`；`n <= 1` 返回 1 |
| `sort_bubble` | `sort_bubble(int[] a, int n)` | `void` | 冒泡排序，原地升序 |
| `sort_selection` | `sort_selection(int[] a, int n)` | `void` | 选择排序，原地升序 |
| `sort_insertion` | `sort_insertion(int[] a, int n)` | `void` | 插入排序，原地升序；小数组通常最快 |
| `sort_quick` | `sort_quick(int[] a, int lo, int hi)` | `void` | 快速排序**闭区间** `[lo, hi]`；取中点值为枢轴，`lo >= hi` 直接返回 |
| `sort_quick_all` | `sort_quick_all(int[] a, int n)` | `void` | 整体入口，等价于 `n > 1` 时调用 `sort_quick(a, 0, n - 1)` |
| `bin_search` | `bin_search(int[] a, int n, int v)` | `int` | 二分查找，返回命中下标或 `-1`；**数组必须已升序**，重复元素返回其中某个位置（不保证是第一个） |

```c
import "sort.cin"

function main() -> int {
    int a[7] = {9, 2, 7, 1, 8, 3, 5}
    sort_quick_all(a, 7)
    println(int_to_str(sort_is_sorted(a, 7)))   // 1
    println(int_to_str(a[0]))                   // 1
    println(int_to_str(bin_search(a, 7, 7)))    // 4
    println(int_to_str(bin_search(a, 7, 100)))  // -1
    int b[5] = {5, 4, 3, 2, 1}
    sort_insertion(b, 5)
    println(int_to_str(b[0]))                   // 1
    return 0
}
```

## stat

面向 `int` 数组的**顺序统计量**库（中位数/众数/百分位/直方图）与免排序的聚合量。
与 `codecin/lib/vec.cin`（浮点统计）互补。需要升序输入的接口以 `_sorted` 结尾。

```c
import "stat.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `stat_sum` | `stat_sum(int[] a, int n)` | `int` | 求和 |
| `stat_min` | `stat_min(int[] a, int n)` | `int` | 最小值；`n <= 0` 返回 0 |
| `stat_max` | `stat_max(int[] a, int n)` | `int` | 最大值；`n <= 0` 返回 0 |
| `stat_range` | `stat_range(int[] a, int n)` | `int` | 极差 `max - min`；`n <= 0` 返回 0 |
| `stat_mean` | `stat_mean(int[] a, int n)` | `int` | 整数均值，`idiv` 向零截断；`n <= 0` 返回 0 |
| `stat_count` | `stat_count(int[] a, int n, int v)` | `int` | 等于 `v` 的元素个数 |
| `stat_median_sorted` | `stat_median_sorted(int[] a, int n)` | `int` | 中位数，**要求已升序**；偶数个取中间两个的截断平均；`n <= 0` 返回 0 |
| `stat_mode` | `stat_mode(int[] a, int n)` | `int` | 众数（出现次数最多的值）；**并列时取较小值**；`O(n^2)`，无需排序；`n <= 0` 返回 0 |
| `stat_percentile_sorted` | `stat_percentile_sorted(int[] a, int n, int p)` | `int` | 最近秩百分位，**要求已升序**，`p` 属于 `0..100`；`p <= 0` 返回 `a[0]`，`p >= 100` 返回 `a[n-1]`；内部秩为 `ceil(n*p/100)` 且至少为 1 |
| `stat_q1_sorted` | `stat_q1_sorted(int[] a, int n)` | `int` | 下四分位，即 `stat_percentile_sorted(a, n, 25)`；要求已升序 |
| `stat_q3_sorted` | `stat_q3_sorted(int[] a, int n)` | `int` | 上四分位，即 `stat_percentile_sorted(a, n, 75)`；要求已升序 |
| `stat_histogram` | `stat_histogram(int[] a, int n, int[] hist, int bins)` | `void` | 直方图：先把 `hist[0..bins-1]` 清零，再把落在 `[0, bins-1]` 的值计数；**负值与越界值被忽略** |
| `stat_variance_x1000` | `stat_variance_x1000(int[] a, int n)` | `int` | 方差 ×1000（整数化，便于断言）；公式 `(n*sum(x^2) - sum(x)^2) / n^2` 再乘 1000；`n <= 0` 返回 0 |
| `stat_stdev_x100` | `stat_stdev_x100(int[] a, int n)` | `int` | 标准差 ×100（整数化），实现为 `stat_variance_x1000(a, n) / 10` |
| `stat_is_sorted` | `stat_is_sorted(int[] a, int n)` | `int` | 升序判定，`1`/`0`（与 `sort_is_sorted` 同语义） |

```c
import "stat.cin"

function main() -> int {
    int a[6] = {4, 8, 1, 8, 3, 6}
    println(int_to_str(stat_sum(a, 6)))        // 30
    println(int_to_str(stat_mean(a, 6)))       // 5
    println(int_to_str(stat_mode(a, 6)))       // 8
    println(int_to_str(stat_variance_x1000(a, 6)))  // 6666
    int s[6] = {1, 3, 4, 6, 8, 8}
    println(int_to_str(stat_median_sorted(s, 6)))   // 5
    println(int_to_str(stat_q1_sorted(s, 6)))       // 3
    int h[10]
    stat_histogram(a, 6, h, 10)
    println(int_to_str(h[8]))                  // 2
    return 0
}
```

## str

字符串变换库。依赖内建 `upper` / `lower` / `substr` / `indexof` / `strlen` / `strcmp`。
这些函数**分配新堆块**，大量循环拼接会消耗堆（64 位槽，不回收），长循环里请尽量减少拼接次数。

```c
import "str.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `s_upper` | `s_upper(string s)` | `string` | 转大写，转调内建 `upper` |
| `s_lower` | `s_lower(string s)` | `string` | 转小写，转调内建 `lower` |
| `s_contains` | `s_contains(string hay, string needle)` | `int` | 是否包含子串，`1`/`0`；空 `needle` 恒为 1 |
| `s_starts_with` | `s_starts_with(string s, string prefix)` | `int` | 是否以 `prefix` 开头（`indexof == 0`）；空 `prefix` 为 1 |
| `s_ends_with` | `s_ends_with(string s, string suffix)` | `int` | 是否以 `suffix` 结尾；`strlen(suffix) > strlen(s)` 返回 0，空 `suffix` 为 1 |
| `s_count` | `s_count(string hay, string needle)` | `int` | 不重叠出现次数（如 `s_count("aaa","aa")` 为 1）；`needle` 为空串返回 0 |
| `s_repeat` | `s_repeat(string ch, int n)` | `string` | 把 `ch` 重复 `n` 次；`n <= 0` 返回空串。参数名是 `ch` 但可传任意字符串 |

```c
import "str.cin"

function main() -> int {
    println(s_upper("aBcDe"))                      // ABCDE
    println(s_lower("HeLLo"))                      // hello
    println(int_to_str(s_contains("banana", "nan")))    // 1
    println(int_to_str(s_starts_with("readme.txt", "read")))  // 1
    println(int_to_str(s_ends_with("readme.txt", ".txt")))    // 1
    println(int_to_str(s_count("aaa", "aa")))      // 1
    println(s_repeat("ab", 3))                     // ababab
    return 0
}
```

## termux

Termux API 便捷封装库。内部 `import "json.cin"`，并转调 `termux_available` / `termux_notify` /
`termux_clipboard_*` / `termux_battery` / `termux_location` / `termux_wifi_info` 等宿主能力内建。
需要 **termux-api 命令 + Termux:API 应用**；非 Termux 环境下调用返回 `-1` 或空串。

```c
import "termux.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `tx_ok` | `tx_ok()` | `int` | Termux API 是否可用，`1`/`0`；非 Termux 环境返回 0 |
| `tx_notify` | `tx_notify(string title, string content)` | `int` | 发送系统通知 |
| `tx_toast` | `tx_toast(string msg)` | `int` | 弹出 Toast |
| `tx_copy` | `tx_copy(string s)` | `int` | 写入剪贴板 |
| `tx_paste` | `tx_paste()` | `string` | 读取剪贴板；不可用时为空串 |
| `tx_vibrate` | `tx_vibrate(int ms)` | `int` | 振动 `ms` 毫秒 |
| `tx_say` | `tx_say(string text)` | `int` | 文字转语音 |
| `tx_sms` | `tx_sms(string number, string text)` | `int` | 发送短信 |
| `tx_battery_json` | `tx_battery_json()` | `string` | 电池状态原始 JSON；不可用时为空串 |
| `tx_battery_level` | `tx_battery_level()` | `int` | 电池百分比；JSON 为空返回 `-1`，解析失败经 `j_int` 得 0 |
| `tx_battery_temp` | `tx_battery_temp()` | `float` | 电池温度；不可用返回 `-1.0` |
| `tx_battery_plugged` | `tx_battery_plugged()` | `int` | 是否充电中，`1`/`0`；`plugged` 为 `UNPLUGGED` 或字段为空时 0 |
| `tx_location_json` | `tx_location_json()` | `string` | 定位原始 JSON |
| `tx_latitude` | `tx_latitude()` | `float` | 定位纬度；不可用为 `0.0` |
| `tx_longitude` | `tx_longitude()` | `float` | 定位经度；不可用为 `0.0` |
| `tx_wifi_json` | `tx_wifi_json()` | `string` | WiFi 连接信息原始 JSON |
| `tx_wifi_ssid` | `tx_wifi_ssid()` | `string` | 当前 WiFi SSID；不可用为空串 |
| `tx_prompt` | `tx_prompt(string title)` | `string` | 弹出输入对话框并返回用户输入文本；不可用为空串 |
| `tx_alert` | `tx_alert(string title, string content)` | `int` | 组合动作：通知 + 振动 200ms，返回通知的返回值 |

```c
import "termux.cin"

function main() -> int {
    if (tx_ok() == 0) {
        println("not termux")              // 非 Termux 环境
        return 0
    }
    tx_alert("Code CIN", "构建完成")        // 通知 + 振动
    println(int_to_str(tx_battery_level())) // 0..100
    println(float_to_str(tx_battery_temp()))
    println(int_to_str(tx_battery_plugged()))  // 1/0
    tx_toast("hello")
    return 0
}
```

## test

轻量测试断言库。提供计数器（全局 `T_PASS` / `T_FAIL`）与断言；结束时调用 `t_report()`
输出汇总并**返回失败数**，因此可直接 `return t_report()` 作为 `main` 的退出码。

```c
import "test.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `t_eq_int` | `t_eq_int(int got, int want, string label)` | `int` | 断言两整数相等；通过返回 1，失败打印 `FAIL label: got=.. want=..` 并返回 0 |
| `t_eq_str` | `t_eq_str(string got, string want, string label)` | `int` | 断言两字符串相等（`strcmp`）；失败打印 `got=[..] want=[..]` |
| `t_near` | `t_near(float got, float want, float eps, string label)` | `int` | 断言浮点近似相等，判据是绝对差 `d <= eps`（闭区间） |
| `t_true` | `t_true(int cond, string label)` | `int` | 断言 `cond != 0`；失败打印 `condition false` |
| `t_false` | `t_false(int cond, string label)` | `int` | 断言 `cond == 0`，实现为 `t_true(cond == 0, label)` |
| `t_reset` | `t_reset()` | `void` | 把 `T_PASS` / `T_FAIL` 归零 |
| `t_report` | `t_report()` | `int` | 输出汇总：全通过打印 `OK: N assertions passed`，否则 `FAILED: F of T`；返回失败数 `T_FAIL` |

```c
import "test.cin"
import "array.cin"

function main() -> int {
    t_reset()
    int a[4] = {4, 8, 1, 8}
    t_eq_int(a_sum(a, 4), 21, "a_sum")
    t_eq_str("a", "a", "strcmp")
    t_near(1.0, 1.0001, 0.01, "near")
    t_true(a_count(a, 4, 8) == 2, "count8")
    t_false(a_contains(a, 4, 99), "no99")
    return t_report()          // 全通过时返回 0
}
```

## time

时间工具库。依赖内建 `time()` 取当前 Unix 时间戳（秒）。字符串化依赖 `int_to_str` / `idiv`。

```c
import "time.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `t_now` | `t_now()` | `int` | 当前 Unix 时间戳（秒），转调内建 `time()` |
| `t_two` | `t_two(int v)` | `string` | 两位左补零；负数先取绝对值，`>= 100` 时原样输出（不截断） |
| `t_hms` | `t_hms(int secs)` | `string` | 秒 → `"HH:MM:SS"`；负数按绝对值处理，小时不封顶（`>= 100` 时输出三位） |
| `t_ms` | `t_ms(int secs)` | `string` | 秒 → `"MM:SS"`；负数按绝对值处理，分钟不封顶 |
| `t_breakdown` | `t_breakdown(int secs, int[] out)` | `void` | 拆解为天/时/分/秒写入 4 元素数组 `out[0..3]`；负秒按绝对值处理 |
| `t_human` | `t_human(int secs)` | `string` | 人性化时长 `"1d 2h 3m 4s"`，**省略为 0 的高位单位**（`t_human(5)` 为 `"5s"`，`t_human(90061)` 为 `"1d 1h 1m 1s"`） |

```c
import "time.cin"

function main() -> int {
    println(t_hms(3661))              // 01:01:01
    println(t_ms(125))                // 02:05
    println(t_human(90061))           // 1d 1h 1m 1s
    println(t_human(5))               // 5s
    int parts[4]
    t_breakdown(90061, parts)
    println(int_to_str(parts[0]))     // 1 (天)
    println(t_two(7))                 // 07
    if (t_now() <= 0) { return 1 }    // 时间戳应为正
    return 0
}
```

## validate

字符/字符串校验库。与 `codecin/lib/str.cin` 互补：`str.cin` 做变换（大小写/包含/重复），
本库做**判定与安全解析**（字符类别、整数字符串、标识符、限幅、带 fallback 的解析）。

```c
import "validate.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `val_is_digit` | `val_is_digit(int c)` | `int` | 是否 `0-9`；参数是**字符的字节值**，如 `'7'` 或 `s[i]` |
| `val_is_upper` | `val_is_upper(int c)` | `int` | 是否 `A-Z` |
| `val_is_lower` | `val_is_lower(int c)` | `int` | 是否 `a-z` |
| `val_is_alpha` | `val_is_alpha(int c)` | `int` | 是否字母（大小写任一） |
| `val_is_alnum` | `val_is_alnum(int c)` | `int` | 是否字母或数字；下划线 `_` **不算** |
| `val_is_hex` | `val_is_hex(int c)` | `int` | 是否十六进制数字符（`0-9` `a-f` `A-F`） |
| `val_is_space` | `val_is_space(int c)` | `int` | 是否空白：空格、`\t`、`\n`、`\r` |
| `val_is_int` | `val_is_int(string s)` | `int` | 整数字符串：可选 `+`/`-`（后面必须至少有一位数字），其余全是数字；空串、`"+"`、`"4a"` 均为 0 |
| `val_is_float` | `val_is_float(string s)` | `int` | 浮点字符串：可选符号 + 数字 + 至多一个小数点，**至少一位数字**；`"."`、`"1.2.3"` 为 0；不校验指数形式 |
| `val_is_ident` | `val_is_ident(string s)` | `int` | 标识符：`[A-Za-z_][A-Za-z0-9_]*` |
| `val_count_char` | `val_count_char(string s, int c)` | `int` | 字符 `c` 在 `s` 中出现的次数（同样传字节值） |
| `val_is_blank` | `val_is_blank(string s)` | `int` | 整个串是否全为空白（含空串为 1） |
| `val_clamp_int` | `val_clamp_int(int v, int lo, int hi)` | `int` | 限幅到 `[lo, hi]`；`lo > hi` 时原样返回 `v`（与 `i_clamp` 的取舍不同） |
| `val_parse_int` | `val_parse_int(string s, int fallback)` | `int` | 安全解析：`val_is_int` 不通过时返回 `fallback`，否则 `atoi(s)` |
| `val_is_hex_color` | `val_is_hex_color(string s)` | `int` | 是否 3 位或 6 位十六进制颜色，可带 `#`：`"#ABC"` / `"A1B2C3"` 通过，`"#AB"` / `"#XYZ"` 不通过 |

```c
import "validate.cin"

function main() -> int {
    println(int_to_str(val_is_digit('7')))          // 1
    println(int_to_str(val_is_int("-42")))          // 1
    println(int_to_str(val_is_int("4a")))           // 0
    println(int_to_str(val_is_float("3.14")))       // 1
    println(int_to_str(val_is_float("1.2.3")))      // 0
    println(int_to_str(val_is_ident("_x1")))        // 1
    println(int_to_str(val_parse_int("x", -1)))     // -1
    println(int_to_str(val_clamp_int(15, 0, 10)))   // 10
    println(int_to_str(val_is_hex_color("#A1B2C3")))// 1
    return 0
}
```

## vec

浮点向量与统计库。与 `codecin/lib/stat.cin`（整数顺序统计）互补；`v_var` / `v_std` 是
**样本**方差与样本标准差（除以 `n-1`），`v_normalize` 是原地操作。

```c
import "vec.cin"
```

| 函数名 | 签名 | 返回值 | 说明与边界行为 |
| --- | --- | --- | --- |
| `v_sum` | `v_sum(float[] a, int n)` | `float` | 求和；`n <= 0` 返回 `0.0` |
| `v_mean` | `v_mean(float[] a, int n)` | `float` | 均值；`n <= 0` 返回 `0.0` |
| `v_var` | `v_var(float[] a, int n)` | `float` | **样本**方差（除以 `n-1`）；`n <= 1` 返回 `0.0` |
| `v_std` | `v_std(float[] a, int n)` | `float` | 样本标准差 `sqrt(v_var(a, n))` |
| `v_dot` | `v_dot(float[] a, float[] b, int n)` | `float` | 点积；两个数组都按前 `n` 个元素访问 |
| `v_min` | `v_min(float[] a, int n)` | `float` | 最小值；`n <= 0` 返回 `0.0` |
| `v_max` | `v_max(float[] a, int n)` | `float` | 最大值；`n <= 0` 返回 `0.0` |
| `v_add` | `v_add(float[] a, float[] b, float[] dst, int n)` | `void` | 逐元素相加写入 `dst` |
| `v_scale` | `v_scale(float[] a, float k, float[] dst, int n)` | `void` | 逐元素乘标量写入 `dst`；标量参数是 `float` |
| `v_normalize` | `v_normalize(float[] a, int n)` | `void` | **原地**按 min/max 归一化到 `[0, 1]`；`span == 0` 时全置 `0.0`；`n <= 0` 直接返回 |
| `v_norm` | `v_norm(float[] a, int n)` | `float` | 欧几里得范数 `sqrt(v_dot(a, a, n))` |
| `v_lerp` | `v_lerp(float a, float b, float t)` | `float` | 线性插值 `a + (b - a) * t`；`t` 不限制在 `[0,1]`（可外插） |

```c
import "vec.cin"

function main() -> int {
    float v[5] = {2.0, 4.0, 4.0, 4.0, 6.0}
    println(float_to_str(v_sum(v, 5)))    // 20.000000
    println(float_to_str(v_mean(v, 5)))   // 4.000000
    println(float_to_str(v_dot(v, v, 5))) // 88.000000
    println(float_to_str(v_norm(v, 5)))   // sqrt(88)
    println(float_to_str(v_lerp(0.0, 10.0, 0.25)))  // 2.500000
    v_normalize(v, 5)
    println(float_to_str(v[0]))           // 0.000000
    println(float_to_str(v[4]))           // 1.000000
    return 0
}
```

## 相关页面

- 标准库总览、前缀约定与宿主能力依赖：[/stdlib/](/stdlib/)
- 模块解析规则与自建模块：[/language/modules](/language/modules)
- 内建函数完整清单：[/language/builtins](/language/builtins)
- 宿主能力与平台可用性：[/language/host-abilities](/language/host-abilities)
- 数组与字符串语义：[/language/arrays](/language/arrays)、[/language/strings](/language/strings)
