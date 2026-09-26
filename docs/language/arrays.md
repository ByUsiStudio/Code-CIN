---
description: "CIN 数组：一维/多维固长数组、行主序、字面量初始化、指针形式数组、传参衰减与越界检查。"
---

# 数组

CIN 的数组分两种写法, 语义不同, 必须分清:

| 写法 | 名称 | 语义 |
|------|------|------|
| `int a[5]`、`float m[3][4]` | **固长数组** | 值语义, 在数据区/栈上就地分配, 生命周期与声明它的作用域一致 |
| `int[] arr`、`int[][] g` | **指针形式数组** | 只保存基址 (指针), 用于参数、返回值和需要运行时决定长度的场合 |

数组下标从 **0** 开始, 多维数组按**行主序**存放。

## 声明与默认值

```c
int a[5]                        // 一维, 全部为 0
float m[3][4]                   // 二维 (3 行 4 列, 行主序)
int init[4] = {1, 2, 3, 4}      // 字面量初始化
int ident[2][2] = { {1, 0}, {0, 1} }
string names[3]                 // 字符串数组 (三个空串指针)
```

- 未初始化的固长数组全部为类型默认值 (int → 0, float → 0.0, string → `""`);
- 字面量可以嵌套表示多维; 元素个数**超过声明维度会报错**;
- 固长数组可以出现在全局、局部以及 **struct 字段**位置。

## 下标访问

```c
int a[5] = {10, 20, 30, 40, 50}
println(int_to_str(a[0]))          // 10
a[4] = 99
println(int_to_str(a[4]))          // 99

float m[2][3] = { {1, 2, 3}, {4, 5, 6} }
println(int_to_str(m[1][2]))       // 6   (第 2 行第 3 列)
```

逐维下标: `m[day][hour]`; 数组名当作表达式使用时就是**基址指针**。

::: warning 默认不做越界检查
与 C 一样, CIN 默认不检查数组越界, 越界读写会踩到相邻数据。需要检查时加
`--bounds-check` (会强制走解释执行):

```bash
codecin prog.cin --no-native --bounds-check
```
:::

## 指针形式数组

参数与返回值用 `T[]` 表示“不知道长度的数组”:

```c
function sum(int[] arr, int n) -> int {
    int s = 0
    for (int i = 0; i < n; i = i + 1) {
        s = s + arr[i]
    }
    return s
}

function main() -> int {
    int data[5] = {1, 2, 3, 4, 5}
    println(int_to_str(sum(data, 5)))     // 15
    return 0
}
```

- 固长数组作参数时写 `int[5]` 也可以, 传入后**衰减为指针形式**;
- 数组按引用传递: 函数内修改元素对调用方可见 (struct 参数同理, 见 [struct](/language/structs));
- 数组**不携带长度**, 因此所有库函数都要求额外传入 `n` (见 [标准库参考](/stdlib/reference))。

::: danger 不要照抄“返回指针数组”的写法
`int[]` 作为函数返回类型在语法上存在, 但指向的堆块没有生命周期保证, 极易踩到脏数据。
需要返回多个值时, 更稳妥的做法是把结果写入调用方传入的数组:

```c
function divmod(int a, int b, int[] out2) -> void {
    out2[0] = idiv(a, b)
    out2[1] = a - idiv(a, b) * b
}

function main() -> int {
    int r[2]
    divmod(38, 7, r)
    println(int_to_str(r[0]) + " " + int_to_str(r[1]))   // 5 3
    return 0
}
```
:::

## 与标准库配合

内置库对数组的接口都是“数组 + 长度”的形式:

```c
import "array.cin"
import "sort.cin"

function main() -> int {
    int a[8] = {5, 3, 8, 1, 9, 2, 7, 4}
    sort_bubble(a, 8)                       // 原地升序
    println(int_to_str(a[0]) + " ... " + int_to_str(a[7]))
    println("sum = " + int_to_str(a_sum(a, 8)))
    println("max = " + int_to_str(a_max(a, 8)))
    return 0
}
```

```text
1 ... 9
sum = 39
max = 9
```

更多函数 (查找、反转、复制、切片求和、二分) 见 [标准库参考](/stdlib/reference)。

## 与字符串

`string` 不是数组, 但可以用 `s[i]` **只读**访问单个字节 (返回 `0..255` 的整数编码):

```c
string s = "Code CIN"
int c = s[0]              // 67 ('C')
```

字符串不可原地修改, `s[i] = 65` 是非法左值 —— 详见 [字符串](/language/strings)。

## 常见错误

| 现象 | 原因 | 处理 |
|------|------|------|
| 结果被莫名其妙覆盖 | 越界写踩到相邻变量 | 打开 `--bounds-check` 定位 |
| 函数里改了数组但外面没变 | 把数组当值传递的直觉 | 数组是引用语义, 改动本来就可见; 若不可见说明传的是副本字段 |
| 字面量初始化报错 | 元素个数超过声明维度 | 对齐长度 |
| 遍历读到垃圾 | `n` 传错 (超出实际长度) | CIN 不带长度, `n` 由调用方保证 |
| `Stack overflow` | 局部大数组占满栈 | 减小数组或 `--mem-size` 扩容 |

## 相关页面

- [类型系统](/language/types) — 值语义与指针形式
- [变量与作用域](/language/variables) — 数组字面量与全局数组
- [struct](/language/structs) — 数组字段与嵌套
- [标准库参考](/stdlib/reference) — `array` / `sort` / `matrix` 等库的全部函数
- [内存与缓存](/tools/memory-cache) — 内存布局与 `--bounds-check`
