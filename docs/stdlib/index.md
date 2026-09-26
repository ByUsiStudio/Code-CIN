---
description: Code CIN 官方标准库总览：19 个内置库的用途、前缀约定、宿主能力依赖与快速上手。
---

# 标准库总览

Code CIN 官方标准库由 19 个 `.cin` 源文件组成，随 pip 包一起分发在 `codecin/lib/` 目录下。
它们本身是**用 CIN 语言写成的模块**，不是宿主内建函数：编译器在编译主文件时按需展开，
因此函数签名、边界行为和返回值都可以直接阅读源码核对。

标准库按职责分层：纯计算类库（数组、排序、数学、统计、哈希、位运算）只依赖语言内建，
在三条执行路径下行为完全一致；而文件、画布、Termux 这类库需要宿主提供能力，
只能运行在 Go 原生引擎上。

## 分发位置与查找规则

| 你写的 import | 编译器解析到 | 说明 |
| --- | --- | --- |
| `import "math.cin"` | `codecin/lib/math.cin` | 官方标准库的规范写法 |
| `import "lib/math.cin"` | `codecin/lib/math.cin` | 历史兼容别名，两者等价 |
| `import "./util.cin"` | 当前 `.cin` 文件同目录的 `util.cin` | 项目自有模块，必须带 `./` 前缀 |

要点：

- `import` 只能出现在**文件顶部（列首）**；
- 不带 `./` 前缀的名字一律当作**内置库名**，不会去当前目录找；
- 同一文件每个编译只包含一次（防重复），循环引用报错，缺失模块报 `Import file not found`；
- 标准库之间可以层层引用（例如 `codecin/lib/termux.cin` 内部 `import "json.cin"`）。

模块解析的完整规则见 [模块与标准库](/language/modules)。

## 命名前缀约定

标准库不位于独立的命名空间，所有函数都是**全局符号**。为避免冲突，每个库使用固定的前缀：

| 前缀 | 库 | 前缀含义 |
| --- | --- | --- |
| `f_` | `math.cin` | float 浮点辅助 |
| `i_` | `math.cin` | int 整数辅助 |
| `s_` | `str.cin` | string 字符串变换 |
| `a_` | `array.cin` | array 整数数组 |
| `sort_` | `sort.cin` | 排序 |
| `bin_` | `sort.cin` | 二分查找 |
| `c_` | `conv.cin` | convert 进制与格式化 |
| `v_` | `vec.cin` | vector 浮点向量与统计 |
| `r_` | `rand.cin` | random 随机 |
| `j_` | `json.cin` | json 扁平取值 |
| `t_` | `time.cin` / `test.cin` | time 时间；test 断言（两库共享 `t_`，勿同时导入） |
| `io_` | `io.cin` | 文件与路径 |
| `g_` | `gui.cin` | graphics 画布绘图 |
| `tx_` | `termux.cin` | Termux API |
| `bits_` | `bits.cin` | 位运算 |
| `stat_` | `stat.cin` | 顺序统计 |
| `hash_` | `hash.cin` | 哈希 |
| `val_` | `validate.cin` | 校验与安全解析 |
| `mat_` | `matrix.cin` | matrix 方阵 |
| `queue_` / `stack_` | `queue.cin` | 环形队列 / 栈 |

::: warning `t_` 前缀冲突
`codecin/lib/time.cin` 与 `codecin/lib/test.cin` 都使用 `t_` 前缀且符号不同名，同时导入不会报
重复定义，但两库混用后**函数名容易看错**（`t_report` 是测试库、`t_hms` 是时间库）。
建议在测试文件里只导入 `test.cin`，把时间格式化放到别的模块中。
:::

## 总览表

「函数个数」按 `codecin/lib/<库>.cin` 中 `function` 定义逐个数出，与源码一致。

| 库 | 前缀 | 函数个数 | 用途 | 依赖宿主能力 |
| --- | --- | --- | --- | --- |
| `math.cin` | `f_` `i_` | 9 | 取整、最值、夹取 | 否 |
| `str.cin` | `s_` | 7 | 大小写、包含/前后缀、计数、重复 | 否 |
| `array.cin` | `a_` | 14 | 整数数组求和/最值/查找/反转/填充/拷贝 | 否 |
| `sort.cin` | `sort_` `bin_` | 7 | 冒泡/选择/插入/快速排序、有序判定、二分查找 | 否 |
| `conv.cin` | `c_` | 11 | 十六进制/二进制转换、填充、字符与浮点解析 | 否 |
| `vec.cin` | `v_` | 12 | 浮点向量求和/均值/方差/点积/范数/归一化 | 否 |
| `rand.cin` | `r_` | 7 | 区间随机、随机浮点、洗牌、随机取元素 | 否（依赖内建 `rand`） |
| `json.cin` | `j_` | 6 | 扁平 JSON 字段取值（字符串/整数/浮点/布尔） | 否 |
| `time.cin` | `t_` | 6 | 时间戳、`HH:MM:SS`/`MM:SS`、时长拆解与人性化 | 否（依赖内建 `time`） |
| `bits.cin` | `bits_` | 16 | popcount/clz/ctz、位读写、循环移位、位序与字节序反转 | 否 |
| `stat.cin` | `stat_` | 15 | 整数顺序统计：中位数/众数/百分位/直方图/方差 | 否 |
| `hash.cin` | `hash_` | 7 | djb2 / FNV-1a / sdbm、整数混合、桶下标映射 | 否 |
| `validate.cin` | `val_` | 15 | 字符类别、整型/浮点/标识符/颜色校验、限幅、安全解析 | 否 |
| `matrix.cin` | `mat_` | 15 | 方阵加减乘、转置、迹、行列式、对称性判定 | 否 |
| `queue.cin` | `queue_` `stack_` | 16 | 定长环形队列（FIFO）与定长栈（LIFO） | 否 |
| `test.cin` | `t_` | 7 | 轻量断言与通过/失败计数汇总 | 否 |
| `io.cin` | `io_` | 15 | 文件读写/追加/删除/大小、目录、路径、按行与按分隔取值 | **是**（Go 原生） |
| `gui.cin` | `g_` | 9 | 画布建面/清屏/边框、柱状图与折线图、导出 PNG、弹窗查看 | **是**（Go 原生） |
| `termux.cin` | `tx_` | 18 | Termux 通知/Toast/剪贴板/振动/TTS/短信/电池/定位/WiFi | **是**（Go 原生） |

## 纯 CIN 库与宿主能力库

纯 CIN 库（上表「依赖宿主能力 = 否」的 16 个）内部只调用语言内建，例如 `codecin/lib/matrix.cin`
只用数组与循环，`codecin/lib/conv.cin` 只用 `substr`/`strlen`/`atoi`。它们的行为在
**Go 原生 VM / JIT / 纯 Python 解释器**三条路径下完全一致，可直接用 `--no-native` 验证：

```bash
python cpu.py prog.cin                # 自动选择原生/JIT/解释 (默认)
python cpu.py prog.cin --no-native    # 强制纯 Python 解释器
```

宿主能力库（`io.cin` / `gui.cin` / `termux.cin`）转调 `file_*`、`canvas`/`draw_line`、
`termux_*` 等内建，这些内建**全部由 Go 原生引擎实现**，在解释器下会给出明确错误。
另有两个库处于中间地带：

- `rand.cin` 依赖内建 `rand()`，`time.cin` 依赖内建 `time()`——它们由运行时的 `SYS` 指令实现，
  三条路径都可用，因此不算宿主能力库；
- `gui.cin` 与 `io.cin` 的正确性测试需要原生引擎（`tests/test_libs.py` 里标了 `needs_native`）。

宿主能力的完整清单与各平台可用性见 [宿主能力](/language/host-abilities)。

## 快速上手

新建 `demo.cin`，导入数学与数组两个库并在 `main` 中调用：

```c
import "math.cin"
import "array.cin"
import "sort.cin"

function main() -> int {
    int a[5] = {9, 2, 7, 1, 5}
    sort_quick_all(a, 5)                       // 原地升序
    println("sorted0=" + int_to_str(a[0]))     // 1
    println("sum=" + int_to_str(a_sum(a, 5)))  // 24
    println("clamp=" + int_to_str(i_clamp(99, 0, 10)))   // 10
    return 0
}
```

```bash
python cpu.py demo.cin
```

```text
sorted0=1
sum=24
clamp=10
```

更完整的演示（`array` / `sort` / `conv` / `vec` / `json` / `time` / `test` 七库联动）见仓库内
`examples/stdlib_demo.cin`，模块引用示例见 `examples/modules_demo.cin`。

## 从内建到标准库

标准库是内建的语义化包装，改写时理解这一点很重要——内建是单条指令的原子操作，
标准库函数通常带边界处理与默认约定。下面两者输出相同，但浮点路径不同：

::: tabs

== 调用标准库

```c
import "math.cin"

function main() -> int {
    println(float_to_str(f_round(2.5)))    // 3.000000
    return 0
}
```

== 调用内建

```c
function main() -> int {
    println(float_to_str(round(2.5)))      // 3.000000
    return 0
}
```

:::

内建函数的整体清单（`sqrt`/`pow`/`idiv`/`substr`/`strlen`/`rand`/`srand`/`time` 等）见
[内建函数](/language/builtins)。

## 下一步

- 逐库逐函数的签名、返回值与边界行为：[/stdlib/reference](/stdlib/reference)
- 自建模块与相对导入：[/language/modules](/language/modules)
- 数组与字符串的语义细节：[/language/arrays](/language/arrays)、[/language/strings](/language/strings)
- 函数与参数传递：[/language/functions](/language/functions)
