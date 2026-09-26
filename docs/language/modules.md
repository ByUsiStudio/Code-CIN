---
description: "CIN 模块系统：import 解析规则、内置标准库 codecin/lib、重复与循环引用处理，以及 19 个官方库清单。"
---

# 模块与标准库

CIN 用 `import` 引入其它 `.cin` 文件。解析规则只有两条, 且 `import` 必须出现在
**文件顶部 (列首, 缩进前)**。

## 解析规则

| 写法 | 解析到 |
|------|--------|
| `import "./util.cin"`、`import "../shared/x.cin"` | **相对当前 `.cin` 文件**所在目录 (可带子目录) |
| `import "math.cin"`、`import "lib/math.cin"` | **codecin 内置标准库** `codecin/lib/` (随 pip 包分发) |

::: tip 一句话记住
想引用自己项目里的文件就写 `"./"` 前缀; 不写前缀一律当作内置库名。
`lib/` 前缀是历史写法的兼容别名, `import "lib/math.cin"` 等价于 `import "math.cin"`。
:::

```c
// main.cin
import "math.cin"         // f_abs / f_floor / f_ceil / f_round / f_min / f_max / i_clamp ...
import "str.cin"          // s_upper / s_lower / s_contains / s_starts_with / s_ends_with ...
import "./helpers.cin"    // 自建模块 (与 main.cin 同目录)

function main() -> int {
    int v = f_floor(3.9)
    string up = s_upper("hi")
    println(float_to_str(f_round(2.5)) + " " + up + " " + int_to_str(v))
    return v
}
```

实测输出 (`examples/modules_demo.cin`, 见 [示例程序集](/guide/examples)):

```text
abs=3.25
floor(2.7)=2 ceil(2.1)=3
round(2.5)=3 max=4.25
clamp=10
upper=ABCDE lower=hello
indexof(world)=6
contains(bana)=1
ends_with(.txt)=1 count(aa in aaa)=1
repeat(ab,3)=ababab
```

## 包含规则

- 同一文件在一次编译中**只包含一次** (自动去重, 不会重复定义);
- 模块可以层层 `import` (构成 DAG); 主文件编译时**按需展开**, 没有独立的编译单元与符号表;
- **循环引用会报错**;
- 找不到文件时报 `Import file not found`, 并指出查了哪里;
- 模块里的函数与全局变量对外可见, 因此命名建议加前缀避免冲突:
  `f_*` (浮点) / `i_*` (整数) / `s_*` (字符串) / `a_*` (数组) …

::: warning `import` 不能在函数里
`import` 只允许出现在文件顶部。写在函数体内会当作语法错误处理。
:::

## 自建模块示例

```c
// helpers.cin (与 main.cin 同目录)
function helper_double(int x) -> int {
    return x * 2
}

function helper_greet(string name) -> void {
    println("hello, " + name)
}
```

```c
// main.cin
import "./helpers.cin"

function main() -> int {
    helper_greet("CIN")                        // hello, CIN
    println(int_to_str(helper_double(21)))     // 42
    return 0
}
```

## 官方标准库清单

内置库位于 `codecin/lib/`, 随 pip 包一起分发 (安装后即可 `import`, 不需要额外下载):

| 库 | 前缀 | 主要函数 |
|----|------|----------|
| `math.cin` | `f_` `i_` | `f_abs` `f_floor` `f_ceil` `f_round` `f_min` `f_max` `i_min` `i_max` `i_clamp` |
| `str.cin` | `s_` | `s_upper` `s_lower` `s_contains` `s_starts_with` `s_ends_with` `s_count` `s_repeat` |
| `array.cin` | `a_` | `a_sum` `a_max` `a_min` `a_avg` `a_find` `a_contains` `a_count` `a_reverse` `a_fill` `a_copy` `a_index_of_max` `a_index_of_min` `a_sum_range` `a_lower_bound` |
| `sort.cin` | `sort_` `bin_` | `sort_bubble` `sort_selection` `sort_insertion` `sort_quick` `sort_quick_all` `sort_is_sorted` `bin_search` |
| `conv.cin` | `c_` | `c_to_hex` `c_parse_hex` `c_to_bin` `c_parse_bin` `c_pad_left` `c_pad_right` `c_pad_int` `c_repeat` `c_chr` `c_to_int` `c_parse_float` |
| `vec.cin` | `v_` | `v_sum` `v_mean` `v_var` `v_std` `v_dot` `v_min` `v_max` `v_add` `v_scale` `v_normalize` `v_norm` `v_lerp` |
| `rand.cin` | `r_` | `r_range` `r_bool` `r_float` `r_float_range` `r_shuffle` `r_choice` `r_chance` |
| `json.cin` | `j_` | `j_raw` `j_str` `j_int` `j_float` `j_bool` `j_has` (扁平 JSON 取值) |
| `time.cin` | `t_` | `t_now` `t_hms` `t_ms` `t_breakdown` `t_human` `t_two` |
| `io.cin` | `io_` | `io_read` `io_write` `io_append` `io_exists` `io_size` `io_remove` `io_mkdir` `io_list` `io_join` `io_basename` `io_dirname` `io_line_count` `io_get_line` `io_split_get` `io_split_count` |
| `gui.cin` | `g_` | `g_rgb` `g_new` `g_clear` `g_rect_outline` `g_bar_chart` `g_line_chart` `g_grid` `g_save` `g_show` |
| `termux.cin` | `tx_` | `tx_ok` `tx_notify` `tx_toast` `tx_copy` `tx_paste` `tx_vibrate` `tx_say` `tx_sms` `tx_battery_level` `tx_battery_temp` `tx_battery_plugged` `tx_latitude` `tx_longitude` `tx_wifi_ssid` `tx_prompt` `tx_alert` |
| `test.cin` | `t_` | `t_eq_int` `t_eq_str` `t_near` `t_true` `t_false` `t_reset` `t_report` |
| `bits.cin` | `bits_` | `bits_popcount` `bits_clz` `bits_ctz` `bits_is_pow2` `bits_next_pow2` `bits_test` `bits_set` `bits_clear` `bits_toggle` `bits_rotl` `bits_rotr` `bits_reverse` `bits_range_mask` `bits_extract` `bits_insert` `bits_bswap` |
| `stat.cin` | `stat_` | `stat_sum` `stat_min` `stat_max` `stat_range` `stat_mean` `stat_count` `stat_mode` `stat_median_sorted` `stat_percentile_sorted` `stat_q1_sorted` `stat_q3_sorted` `stat_histogram` `stat_variance_x1000` `stat_stdev_x100` `stat_is_sorted` |
| `hash.cin` | `hash_` | `hash_djb2` `hash_fnv1a` `hash_sdbm` `hash_int` `hash_combine` `hash_bucket` `hash_string_bucket` |
| `validate.cin` | `val_` | `val_is_digit` `val_is_alpha` `val_is_alnum` `val_is_hex` `val_is_space` `val_is_upper` `val_is_lower` `val_is_int` `val_is_float` `val_is_ident` `val_is_blank` `val_is_hex_color` `val_count_char` `val_clamp_int` `val_parse_int` |
| `matrix.cin` | `mat_` | `mat_zero` `mat_identity` `mat_get` `mat_set` `mat_add` `mat_sub` `mat_scale` `mat_mul` `mat_transpose` `mat_trace` `mat_sum` `mat_equals` `mat_is_symmetric` `mat_det` `mat_print` |
| `queue.cin` | `queue_` `stack_` | `queue_clear` `queue_push` `queue_pop` `queue_front` `queue_back` `queue_size` `queue_is_empty` `queue_is_full` `queue_capacity` + `stack_clear` `stack_push` `stack_pop` `stack_peek` `stack_size` `stack_is_empty` `stack_capacity` |

::: warning 依赖宿主能力的库
`io.cin` / `gui.cin` / `termux.cin` 封装的是宿主能力 (文件、画布、Termux API), 因此
**需要 Go 原生运行时**; 其余库为纯 CIN, 三条执行路径一致。
:::

逐库逐函数的签名、返回值与边界行为见 [标准库参考](/stdlib/reference);
总览与命名约定见 [标准库概览](/stdlib/)。

## 常见错误

| 错误信息 | 原因 | 修正 |
|----------|------|------|
| `Import file not found` | 文件名写错, 或自建模块忘了 `"./"` 前缀 | 自建模块写 `import "./x.cin"`; 内置库写裸名 |
| 循环引用报错 | A → B → A | 抽出公共模块, 或合并文件 |
| `import` 写在函数里报错 | 位置不合法 | 移到文件顶部 |
| 库函数未定义 | 忘记 `import "xxx.cin"` | 补上 import 语句 |
| 函数名冲突 | 模块间同名函数 | 给自己的函数加前缀 |

## 相关页面

- [标准库概览](/stdlib/) — 19 个库的定位与依赖
- [标准库参考](/stdlib/reference) — 全部函数签名
- [宿主能力](/language/host-abilities) — `io` / `gui` / `termux` 的底层内建
- [示例程序集](/guide/examples) — `modules_demo.cin`、`stdlib_demo.cin`
