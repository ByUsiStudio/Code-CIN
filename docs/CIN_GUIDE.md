# CIN 编程指南

> CIN 是 Code CIN 的高级语言, 语法近似 C/Go: 函数、struct、数组 (含多维)、浮点、字符串。
> CIN 编译为 Code CIN 字节码后由解释器 / JIT / Go 原生 VM 三路径执行, 行为一致。
> 开发环境/构建相关见 [开发者编译文档](BUILDING.md)。

---

## 目录

- [1. 第一个程序](#1-第一个程序)
- [2. 词法规则](#2-词法规则)
- [3. 类型系统](#3-类型系统)
- [4. 变量与作用域](#4-变量与作用域)
- [5. 运算符](#5-运算符)
- [6. 控制流](#6-控制流)
- [7. 函数](#7-函数)
- [8. struct](#8-struct)
- [9. 数组](#9-数组)
- [10. 字符串](#10-字符串)
- [11. 内建函数](#11-内建函数)
- [12. 内嵌 CPU 指令语句](#12-内嵌-cpu-指令语句)
- [13. 编译与运行](#13-编译与运行)
- [14. 限制与注意事项](#14-限制与注意事项)
- [15. 常见错误](#15-常见错误)

---

## 1. 第一个程序

```cin
function main() -> int {
    println("Hello, Code CIN!")
    println("2 + 3 = " + (2 + 3))
    return 0
}
```

运行:

```bash
python cpu.py hello.cin
```

`main` 为入口 (不要求必须有 `main`, 程序从第一条指令开始执行, 按源码顺序先执行全局初始化)。

**语句以换行结尾** (分号可选)。字符串用 `+` 与任意类型拼接, `println` 自动追加换行。

---

## 2. 词法规则

| 元素 | 规则 |
|------|------|
| 注释 | `// 行注释` 与 `/* 块注释 */` |
| 标识符 | 字母/`_` 开头, 字母/数字/`_` 组成, 区分大小写 |
| 整数字面量 | `42`, `-7`, 前缀 `0xFF`(16) / `0b1010`(2) / `0o17`(8), 后缀 `u/U/l/L/f/F` 忽略, 数字下划线 `1_000` 允许 |
| 浮点字面量 | `3.14`, `1e-5`, `1.5f` (支持科学计数法与 `f` 后缀) |
| 字符字面量 | `'a'` `'\n'` `'\t'` `'\''` `'\\'` `'\0'` (值为整数字符编码) |
| 字符串字面量 | `"..."`, 支持转义 `\n \t \r \" \\ \0` |
| 布尔字面量 | `true` / `false` |
| 语句分隔 | 换行 (推荐) 或 `;` |

**续行规则**: 圆括号/方括号内换行会自动连接; 行尾以运算符 (`+ - * / % & | ^ ~ << >> = += -= *= /= %= &= |= ^= <<= >>= ++ -- < > <= >= == != && || , . ->`) 结尾也会连接。花括号 `{}` 块内换行必须保留 (语句终止符)。

> 源文件开头的 UTF-8 BOM (`\ufeff`) 会被自动忽略。

```cin
// 可行: 行尾运算符续行
int long_result = value1 + value2 +
                  value3

// 可行: 括号内续行
float x = (a + b) *
          (c + d)
```

---

## 3. 类型系统

| 类型 | 说明 | 默认值 |
|------|------|--------|
| `int` | 64 位有符号整数 | `0` |
| `char` / `short` / `long` | 整数语法别名 (存储仍为 64 位槽) | `0` |
| `unsigned int` (及 `unsigned char/short/long`) | 无符号修饰 (同 64 位槽, 主要用于大数值字面量) | `0` |
| `float` | 64 位 IEEE754 浮点 | `0.0` |
| `bool` | 布尔 | `false` |
| `string` | NUL 结尾字符串指针 | `""` |
| `void` | 仅函数返回类型 | - |
| `StructName` | 用户定义 struct | 全零 |
| `T[n]` / `T[n][m]` | 固长数组 (值语义) | 全零 |
| `T[]` / `int[][]` | 指针形式数组 (参数/返回) | 空指针 |

类型转换规则:

- `int` / `bool` 参与浮点运算时**自动提升**为 `float` ( `/` 恒为浮点除法; 整数取模用 `%` );
- `float -> int` 在赋值 (`int i = f`) 或显式内建 (`int_to_str`) 时截断转换;
- `bool` 打印为 `"true"` / `"false"`, 数值上下文中为 `1` / `0`。

---

## 4. 变量与作用域

```cin
// 全局变量 (文件顶层)
int counter = 0
float pi = 3.14159265
string greeting = "hello"
Person admin                 // struct 默认全零
int primes[20]               // 固长数组
float grid[7][24]            // 二维数组
int matrix[2][3] = { {1, 2, 3}, {4, 5, 6} }   // 数组字面量

function demo() -> void {
    int local = 10           // 局部变量
    int a = 1, b = 2         // 一行多声明
    string s = "x" + "y"
    local = local + a        // 赋值
}
```

- 全局变量支持数组字面量 `{...}` 初始化 (可嵌套表示多维)。
- 局部变量在进入所在块时分配, 离开释放; 同名内层变量遮蔽外层。
- 未初始化的变量为类型默认值。

---

## 5. 运算符

按优先级从低到高 (越靠后结合越紧):

| 优先级 | 运算符 | 说明 | 适用类型 |
|--------|--------|------|----------|
| 1 | `?:` | 三目 `cond ? a : b` (短路, 结果可赋值) | bool 条件 |
| 2 | `\|\|` | 逻辑或 (短路) | bool (任意类型可转 bool) |
| 3 | `&&` | 逻辑与 (短路) | bool |
| 4 | `\|` | 位或 | int/bool |
| 5 | `^` | 位异或 | int/bool |
| 6 | `&` | 位与 | int/bool |
| 7 | `==` `!=` | 相等比较 | 全部 |
| 8 | `<` `>` `<=` `>=` | 关系比较 | int/float |
| 9 | `<<` `>>` | 左移 / 算术右移 (保留符号) | int/bool |
| 10 | `+` `-` | 加减, `+` 兼作字符串拼接 | int/float/string |
| 11 | `*` `/` `%` | 乘除取模 (`/` 浮点除, `%` 仅整数) | int/float |
| 12 | `!` `~` `-x` `++x` `--x` | 逻辑非 / 位取反 / 负号 / 前缀自增自减 | - |
| 13 | `x++` `x--` `[]` `.` `f()` | 后缀自增自减 / 下标 / 成员 / 调用 | - |
| 14 | `=` `+=` `-=` `*=` `/=` `%=` `&=` `\|=` `^=` `<<=` `>>=` | 赋值 (右结合, 左值地址只求值一次) | int/float 左值 |

`int` / `float` 均可复合赋值与自增自减 (自增对 float 每次 ±1.0); `%=` 及位运算复合赋值仅整数。
赋值/复合赋值/自增自减同时也是**表达式** (值为结果), 例如 `int y = ++x * 2`。

**位运算** (`& | ^ ~ << >>`, 仅整数; 复合 `&= |= ^= <<= >>=`):

```cin
int a = 0b1100 & 0b1010     // 8   (1000)
int b = 0b1100 | 0b1010     // 14  (1110)
int c = 0b1100 ^ 0b1010     // 6   (0110)
int d = 1 << 4              // 16
int e = -16 >> 2            // -4  (算术右移, 保留符号)
int f = ~0                  // -1  (位取反)
int m = 0xFF
m &= 0x0F                   // 15
m <<= 4                     // 240
```

> `<<` 左移在低位补 0; `>>` 为**算术右移** (符号位扩展), 对负数保持符号。移位量按低 6 位 (`& 63`) 取模。

字符串拼接: `+` 任意一侧为 `string` 时, 另一侧自动字符串化 (int/float/bool)。

```cin
println("Age: " + 18)            // Age: 18
println("PI: " + 3.14)           // PI: 3.14
println("OK: " + true)           // OK: true
```

---

## 6. 控制流

### if / else

```cin
if (x > 0) {
    println("positive")
} else if (x == 0) {
    println("zero")
} else {
    println("negative")
}

// 单语句体可省略花括号
if (done) return
```

### while

```cin
while (n > 0) {
    sum = sum + n
    n = n - 1
}
```

### for

```cin
for (int i = 0; i < 10; i = i + 1) {
    println("i = " + i)
}
```

三段均可省略: `for (;;) { break }`。init 段支持类型声明; update 段为赋值表达式。

### break / continue

```cin
while (true) {
    x = x + 1
    if (x >= 10) {
        break
    }
    if (x % 2 == 0) {
        continue
    }
    println("odd: " + x)
}
```

条件短路求值: `&&` 左侧为假时右侧不求值; `||` 左侧为真时右侧不求值。

### do-while

```cin
int n = 0
do {
    n++
} while (n < 3)          // 至少执行一次, n 最后为 3
```

`continue` 跳转到条件求值处; `break` 直接退出。

### switch / case / default

```cin
int tier = grade / 25     // 除法为 float, 赋值给 int 即截断
switch (tier) {
    case 0: println("low");    break
    case 1: println("medium"); break
    case 2:
    case 3: println("high");   break   // case 贯穿 (fallthrough)
    default: println("top")            // 无 break 将贯穿到后续语句
}
```

- 选择表达式与 `case` 常量必须是整数 (支持 `case 2+3` 常量表达式与 `'A'` 等字面量);
- 分支体 **默认贯穿**到下一个 case (与 C 一致), 用 `break` 跳出整个 switch;
- `break` 跳出的是最近一层 switch/loop; switch 内嵌套循环时 `continue` 仍作用于循环;
- `default` 可出现在任意位置, 无匹配时执行; 无 `default` 且无匹配则整段跳过。

### 三目表达式

```cin
string status = (score >= 60) ? "pass" : "fail"
int sign = (x < 0) ? -1 : 1
```

三目为短路表达式: 只求值被选中一侧; int/float 混用时按 float 提升, 两侧同为 string 才允许字符串结果。

---

## 7. 函数

```cin
// 定义: function 名(参数表) -> 返回类型 { ... }
function add(int a, int b) -> int {
    return a + b
}

function swap(int[] arr, int i, int j) -> void {
    int t = arr[i]
    arr[i] = arr[j]
    arr[j] = t
}

// 无返回值时 -> 可省略 (默认 void)
function greet() {
    println("hi")
}
```

- 参数按值传递; 数组参数 (`int[]` / `T[]`) 与 struct 退化为引用传递 (修改可见)。
- 固长数组作参数写 `int[5]`, 传入后衰减为指针形式。
- 递归支持 (调用栈由 VM 栈承载, 默认栈深有限, 见 [限制](#14-限制与注意事项))。
- 返回值通过 `X0` 传递, `void` 函数 `return` 可省略。

---

## 8. struct

```cin
struct Point {
    float x
    float y
}

struct Rectangle {
    Point top_left        // 嵌套 struct (值内嵌)
    Point bottom_right
    float area
}

function area(Rectangle r) -> float {
    float w = r.bottom_right.x - r.top_left.x
    float h = r.bottom_right.y - r.top_left.y
    return w * h
}
```

- 成员访问用 `.`, 支持链式: `r.bottom_right.x`。
- struct 可整体赋值/传参/返回 (值语义拷贝); 数组字段为值内嵌。

```cin
struct Student {
    Person info
    int grades[5]         // 固长数组字段
    float gpa
}

function make(string name, int age) -> Student {
    Student s
    s.info.name = name
    s.info.age = age
    s.grades[0] = 95
    s.gpa = 88.5
    return s
}
```

> struct 字段不能是变长指针数组 (`T[]`); 只允许标量、嵌套 struct 与固长数组。

---

## 9. 数组

```cin
int a[5]                        // 一维, 全零
float m[3][4]                   // 二维 (行主序)
int init[4] = {1, 2, 3, 4}      // 字面量初始化
int ident[2][2] = { {1, 0}, {0, 1} }

function sum(int[] arr, int n) -> int {
    int s = 0
    for (int i = 0; i < n; i = i + 1) {
        s = s + arr[i]
    }
    return s
}

function zeros(int n) -> int[] {   // 指针数组作返回值
    int[] r
    for (int i = 0; i < n; i = i + 1) {
        r[i] = 0
    }
    return r
}
```

- 下标从 0 开始; 多维逐维下标: `m[day][hour]`。
- 数组名作表达式使用时是指针 (基址); `arr[i]` 即基址偏移取值。
- 局部声明中 `int[] r` 为指针形式, 指向堆/数据区由运行时分配。

---

## 10. 字符串

字符串是 NUL 结尾的字节序列, `string` 变量持有指针。

```cin
string s = "hello"
string t = s + " " + "world"    // 拼接产生新堆块
println(t)                      // hello world
println(strlen(t))              // 11

if (strcmp(s, "hello") == 0) {
    println("equal")
}

string copy = strcpy(s)         // 拷贝为新堆块
```

- `+` 拼接右侧任意类型: `"n = " + 42`、`"pi = " + 3.14`、`"ok = " + true`。
- 支持 `s[i]` 读取单字节 (按字节下标, 返回 0–255 整数编码, 无越界检查);
  字符串不可原地修改, `s[i]` 不能作为赋值左值。
- 字符串内建: `strlen` / `strcmp` / `strcpy` / `substr` / `indexof` / `trim` / `atoi` 等 (见下表)。

---

## 11. 内建函数

| 函数 | 签名 | 说明 |
|------|------|------|
| `print(x)` | void | 输出不换行 (自动字符串化) |
| `println(x)` | void | 输出并换行; 无参输出空行 |
| `input()` | int | 读入一行并解析为整数 (失败为 0) |
| `abs(x)` | int | 整数绝对值 |
| `sqrt(x)` | float | 平方根 |
| `pow(x, y)` | float | x 的 y 次幂 |
| `sin(x)` / `cos(x)` / `tan(x)` | float | 三角函数 (弧度) |
| `rand()` | int | 非负随机整数 |
| `srand(n)` | void | 设置随机种子 |
| `time()` | int | Unix 时间戳 (秒) |
| `strlen(s)` | int | 字符串长度 |
| `strcmp(a, b)` | int | 字典序比较 (<0 / 0 / >0) |
| `strcpy(s)` | string | 复制为新堆块 |
| `int_to_str(n)` (别名 `itoa`) | string | 整数 → 十进制字符串 |
| `float_to_str(f)` (别名 `ftoa`) | float | 浮点 → 字符串 (自动提升 int/bool) |
| `bool_to_str(b)` | string | 布尔 → `"true"` / `"false"` |
| `substr(s, start, len)` | string | 子串 (新堆块, 越界自动裁剪) |
| `indexof(hay, needle)` | int | 首次出现位置, 未找到为 `-1` |
| `upper(s)` / `lower(s)` | string | ASCII 大小写转换 (新堆块) |
| `trim(s)` / `ltrim(s)` / `rtrim(s)` | string | 去首尾 / 前导 / 尾部空白 (新堆块) |
| `atoi(s)` | int | 字符串 → 十进制整数 (前导空白忽略, 失败为 `0`) |
| `floor(x)` / `ceil(x)` | float | 向下 / 向上取整 (结果仍为 float) |
| `round(x)` | float | 四舍五入 (`floor(x + 0.5)`, 半值向 +∞) |
| `min(a, b)` / `max(a, b)` | int/float | 数值最小 / 最大值 (int/float 混用按 float 提升) |
| `idiv(a, b)` | int | 整数除法 (向零截断; `/` 恒为浮点除) |

内建在表达式任意位置可用; 数值参数按需自动提升为 float。

示例:

```cin
function math_demo() -> void {
    float angle = pi / 4
    println("sin(45°): " + sin(angle))
    println("sqrt(16): " + sqrt(16))
    println("pow(2, 8): " + pow(2, 8))
    println("abs(-42): " + abs(-42))
}
```

### 宿主能力: 2D 绘图画布 (导出 PNG)

CIN 内置一个跨平台的 2D 绘图画布（Go 标准库实现，导出 PNG）。同一时刻存在一个「当前画布」与「当前画笔颜色」（默认黑色，背景白色）。

| 函数 | 签名 | 说明 |
|------|------|------|
| `canvas(w, h)` | void | 新建 w×h 画布 (白底) |
| `set_color(rgb)` | void | 设置画笔颜色 `0xRRGGBB` |
| `fill_rect(x, y, w, h)` | void | 填充矩形 |
| `fill_circle(cx, cy, r)` | void | 填充圆 |
| `draw_line(x0, y0, x1, y1)` | void | 画线 (Bresenham) |
| `draw_text(x, y, s)` | void | 绘制文本 (内置 5×7 点阵字库; 小写自动转大写) |
| `save_png(path)` | int | 导出 PNG, 返回 `0` 成功 / `-1` 失败 |
| `show_canvas()` | int | 保存当前画布到临时 PNG 并用系统查看器打开 (跨平台「窗口」) |

```cin
function draw() -> int {
    canvas(200, 100)
    set_color(0xFF0000)            // 红
    fill_rect(0, 0, 80, 100)
    set_color(0x0000FF)            // 蓝
    fill_circle(140, 50, 40)
    set_color(0x00FF00)            // 绿
    draw_line(0, 0, 199, 99)
    set_color(0x000000)            // 黑
    draw_text(4, 4, "HELLO")
    return save_png("out.png")     // 0 成功
}
```

也可以直接用系统查看器弹出窗口查看: `show_canvas()`。

> 交互式**控件**版 (按钮/输入框) 依赖桌面图形库（如 Fyne），当前沙箱无网络无法引入外部依赖，
> 故先提供画布 + PNG 导出 + 系统查看器窗口；控件版为后续项。

### 宿主能力: 联网音频 (下载 + 播放 + 控制)

| 函数 | 签名 | 说明 |
|------|------|------|
| `audio_play(url)` | int | 下载 `http/https` URL 或读取本地文件并播放 (WAV/PCM)，返回 `0` 成功 / `-1` 失败 |
| `audio_stop()` | void | 停止当前播放 |
| `audio_volume(v)` | void | 设置音量 `0..100` (支持则生效, 否则忽略) |
| `audio_wait()` | void | 阻塞到当前播放结束 (按 WAV 头时长估算) |

```cin
function music() -> int {
    int ok = audio_play("https://example.com/tone.wav")
    if (ok == 0) {
        audio_volume(80)
        audio_wait()
    }
    return ok
}
```

> MP3/OGG 解码依赖外部库，当前仅支持 WAV(PCM)；音频与 GUI 为 **Go 原生能力**，`--no-native` 下会给出明确错误。

### 宿主能力: 系统原生交互 (Windows / Linux / macOS)

| 函数 | 签名 | 说明 |
|------|------|------|
| `file_read(path)` | string | 读文件内容 (失败为空串) |
| `file_write(path, s)` | int | 覆盖写; `0` 成功 / `-1` 失败 |
| `file_append(path, s)` | int | 追加写 |
| `file_exists(path)` | int | `1` 存在 / `0` 不存在 |
| `file_delete(path)` | int | 删除文件或空目录 |
| `file_size(path)` | int | 字节数 / `-1` |
| `mkdir(path)` | int | 递归创建目录 |
| `dir_list(path)` | string | 换行分隔条目 (目录名带 `/`) |
| `exec(cmd)` | int | 执行 shell 命令, 返回退出码 |
| `exec_output(cmd)` | string | 执行并返回 stdout |
| `getenv(name)` | string | 读环境变量 (未设置为空串) |
| `setenv(name, value)` | int | 设置环境变量 |
| `os_name()` | string | `"windows"` / `"darwin"` / `"linux"` |
| `hostname()` | string | 主机名 |
| `username()` | string | 用户名 |
| `cwd()` | string | 当前工作目录 |
| `home_dir()` | string | 用户主目录 |

```cin
string f = "data.txt"
file_write(f, "hello")
file_append(f, " world")
println(file_read(f))                       // hello world
println("size=" + int_to_str(file_size(f)))
println("os=" + os_name())
println("cwd=" + cwd())
println(exec_output("echo hi"))             // hi
setenv("MY_VAR", "42")
println(getenv("MY_VAR"))                   // 42
```

> `exec` / `exec_output` 经平台默认 shell 执行 (Windows: `cmd /c`, 其他: `sh -c`)。
> 系统交互为 **Go 原生能力**, 具备真实文件/进程权限, 请谨慎使用。

### 宿主能力: Termux API (Android)

在 Termux 中执行 `pkg install termux-api` 并安装 **Termux:API** 应用后可用; 非 Termux 环境所有调用优雅失败 (返回 `-1` 或空串)。

| 函数 | 签名 | 说明 |
|------|------|------|
| `termux_available()` | int | `1` 可用 / `0` 不可用 |
| `termux_notify(title, content)` | int | 系统通知 |
| `termux_toast(msg)` | int | Toast 提示 |
| `termux_clipboard_get()` | string | 读剪贴板 |
| `termux_clipboard_set(s)` | int | 写剪贴板 |
| `termux_battery()` | string | 电池状态 (JSON) |
| `termux_vibrate(ms)` | int | 振动指定毫秒 |
| `termux_tts(text)` | int | 文字转语音 |
| `termux_location()` | string | 定位信息 (JSON) |
| `termux_wifi_info()` | string | WiFi 连接信息 (JSON) |
| `termux_dialog(title)` | string | 弹出输入对话框 (JSON) |
| `termux_sms_send(number, text)` | int | 发送短信 |

```cin
if (termux_available() == 1) {
    termux_notify("Code CIN", "任务完成")
    termux_toast("hello from CIN")
    termux_vibrate(200)
    termux_tts("done")
    println(termux_battery())
    println(termux_clipboard_get())
}
```

> 示例: `examples/system_interaction.cin`; Termux 一键安装见 `script/install_termux.sh`。

---

## 12. 内嵌 CPU 指令语句

CIN 保留了 7 条 CPU 风格语句, 直接对变量/立即数做寄存器级操作 (以当前语句所在变量的栈槽为操作数):

| 语句 | 等价含义 |
|------|----------|
| `set x 30` | `x = 30` |
| `add x y` | `x = x + y` |
| `subtract x 5` | `x = x - 5` |
| `multiply x 2` | `x = x * 2` |
| `divide x 4` | `x = x / 4` |
| `increment x` | `x = x + 1` |
| `decrement x` | `x = x - 1` |

```cin
function cpu_ops() -> void {
    int x = 10
    set x 30
    add x 12          // x = 42
    multiply x 2      // x = 84
    subtract x 42     // x = 42
    divide x 6        // x = 7
    increment x       // x = 8
    println("x = " + x)
}
```

第二操作数可为变量或立即数。这组语句是低级特性, 一般场景用常规表达式即可。

---

## 13. 编译与运行

```bash
python cpu.py prog.cin                    # 编译并运行 (自动选择原生/JIT/解释)
python cpu.py prog.cin --no-native        # 强制纯 Python
python cpu.py prog.cin --debug            # 超详细逐指令追踪 (rich)
python cpu.py prog.cin --profile          # 性能统计
python cpu.py prog.cin --compile-only     # 仅编译为 prog.bin (UCBC 字节码)
python cpu.py prog.bin                    # 运行字节码
python cpu.py prog.cin --save             # 运行后保存 prog.crom 内存镜像
```

编译错误输出红色 rich 面板, 带 `文件:行号` 定位:

```
┌──────────────────────── Load Error ────────────────────────┐
│ prog.cin:12: Compiler error: Unknown function: printline   │
└────────────────────────────────────────────────────────────┘
```

---

## 模块与标准库 (import)

`import "path.cin"` 只能出现在**文件顶部 (列首)**。解析顺序: 源文件目录 →
仓库 `lib/` 目录 → 仓库根; 同一文件每个编译仅包含一次 (防重复), 循环引用报错。

```cin
// main.cin
import "lib/math.cin"     // f_abs/f_floor/f_ceil/f_round/f_min/f_max/i_min/i_max/i_clamp
import "lib/str.cin"      // s_upper/s_lower/s_contains/s_starts_with/s_ends_with/
                          // s_count/s_repeat
import "helpers.cin"      // 自建模块 (放同目录)

function main() -> int {
    int v = f_floor(3.9)
    string up = s_upper("hi")
    println(float_to_str(f_round(2.5)) + " " + up + " " + int_to_str(v))
    return v
}
```

- 模块文件本身可用 `import` 层层引用 (DAG); 编译主文件时按需展开, 不做独立编译单元;
- 库函数名建议 `f_*` (浮点) / `s_*` (字符串) / `i_*` (整数) 前缀避免冲突;
- 模块示例: `examples/modules_demo.cin`、`examples/stdlib_demo.cin`。

### 官方标准库清单 (`lib/`)

| 库 | 前缀 | 主要函数 |
|----|------|----------|
| `lib/math.cin` | `f_` `i_` | `f_abs` `f_floor` `f_ceil` `f_round` `f_min` `f_max` `i_min` `i_max` `i_clamp` |
| `lib/str.cin` | `s_` | `s_upper` `s_lower` `s_contains` `s_starts_with` `s_ends_with` `s_count` `s_repeat` |
| `lib/array.cin` | `a_` | `a_sum` `a_max` `a_min` `a_avg` `a_find` `a_contains` `a_count` `a_reverse` `a_fill` `a_copy` `a_index_of_max` `a_index_of_min` `a_sum_range` `a_lower_bound` |
| `lib/sort.cin` | `sort_` `bin_` | `sort_bubble` `sort_selection` `sort_insertion` `sort_quick` `sort_quick_all` `sort_is_sorted` `bin_search` |
| `lib/conv.cin` | `c_` | `c_to_hex` `c_parse_hex` `c_to_bin` `c_parse_bin` `c_pad_left` `c_pad_right` `c_pad_int` `c_repeat` `c_chr` `c_to_int` `c_parse_float` |
| `lib/vec.cin` | `v_` | `v_sum` `v_mean` `v_var` `v_std` `v_dot` `v_min` `v_max` `v_add` `v_scale` `v_normalize` `v_norm` `v_lerp` |
| `lib/rand.cin` | `r_` | `r_range` `r_bool` `r_float` `r_float_range` `r_shuffle` `r_choice` `r_chance` |
| `lib/json.cin` | `j_` | `j_raw` `j_str` `j_int` `j_float` `j_bool` `j_has` (扁平 JSON 取值) |
| `lib/time.cin` | `t_` | `t_now` `t_hms` `t_ms` `t_breakdown` `t_human` `t_two` |
| `lib/io.cin` | `io_` | `io_read` `io_write` `io_append` `io_exists` `io_size` `io_remove` `io_mkdir` `io_list` `io_join` `io_basename` `io_dirname` `io_line_count` `io_get_line` `io_split_get` `io_split_count` |
| `lib/gui.cin` | `g_` | `g_rgb` `g_new` `g_clear` `g_rect_outline` `g_bar_chart` `g_line_chart` `g_grid` `g_save` `g_show` |
| `lib/termux.cin` | `tx_` | `tx_ok` `tx_notify` `tx_toast` `tx_copy` `tx_paste` `tx_vibrate` `tx_say` `tx_sms` `tx_battery_level` `tx_battery_temp` `tx_battery_plugged` `tx_latitude` `tx_longitude` `tx_wifi_ssid` `tx_prompt` `tx_alert` |
| `lib/test.cin` | `t_` | `t_eq_int` `t_eq_str` `t_near` `t_true` `t_false` `t_reset` `t_report` |

> `lib/io.cin` / `lib/gui.cin` / `lib/termux.cin` 依赖宿主能力 (需 Go 原生运行时);
> 其余库为纯 CIN, 三执行路径一致。示例见 `examples/stdlib_demo.cin`。

---

## 14. 限制与注意事项

1. **无指针/取地址运算**: `*` 仅是乘法、`&` 仅是位与 (不是解引用/取地址); "引用" 仅通过数组/struct 传参隐式实现。
2. **`/` 恒为浮点除**: 整数除法用内建 `idiv(a, b)` (向零截断); `%` 仅支持整数取模, 浮点取模报错。
3. **位运算仅整数**: `&` `|` `^` `<<` `>>` `~` 不接受 float/string 操作数; `>>` 为算术右移 (符号位扩展)。
4. **递归深度**: 每层调用消耗栈槽 (默认内存 64KB, 栈区约 1024 槽); 过深递归触发栈溢出错误, 可用 `--mem-size` 加大内存。
5. **struct 字段**: 不支持变长指针数组字段; 字符串字段是指针, 拼接/复制会产生新堆块。
6. **全局初始化顺序**: 按声明顺序写入数据区; 数组字面量长度超过声明维度会报错。
7. **函数先定义后使用不强制**: 同文件内的函数可互相调用 (两遍编译); 但变量必须先声明后使用。
8. **字符串不可原位修改**: `strcpy` 返回新堆块; 没有可变的原地字符替换。

---

## 15. 常见错误

| 错误信息 | 原因 | 修正 |
|----------|------|------|
| `Unknown function: xxx` | 调用了未定义/拼错的函数 | 检查函数名或自定义该函数 |
| `Unsupported int operator: xx` | 对整数使用了不支持的运算 | 使用 `+ - * / % & \| ^ << >>` |
| `Float modulo not supported` | 浮点使用 `%` | 先取整或改用整数 |
| `Bitwise operator ... requires integer operands` | 对 float/string 使用位运算 | 位运算仅支持 int/bool |
| `Expected RBRACE ... at line N` | 花括号不配对 / 块内缺换行 | 检查第 N 行附近括号 |
| `Undefined variable: xxx` | 使用未声明变量 | 先声明 |
| `Type mismatch ...` | 赋值/传参类型不匹配 | 显式转换或修改类型 |
| `Stack overflow` | 递归过深 / 栈耗尽 | 减少深度或 `--mem-size` 扩容 |

**调试技巧**:

```bash
python cpu.py prog.cin --debug           # 逐指令 rich 追踪, 定位崩溃点 PC
python cpu.py prog.cin --step            # 交互式单步, print regs / mem / cache
python cpu.py prog.cin --log-level DEBUG --log-file codecin.log   # 全量日志落盘
```
