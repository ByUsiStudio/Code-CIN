---
description: Code CIN 示例程序集：高级语言、struct/数组/标准库、宿主能力以及 PL/ASM 汇编示例，全部经过实机运行验证。
---

# 示例程序集

下面的示例都可以直接运行, 输出为实机运行结果 (使用 `--log-level ERROR` 过滤日志行)。
把文件存成对应后缀, 用 `codecin <文件>` 或 `python cpu.py <文件>` 运行即可。

## 1. Hello World

```c
function main() -> int {
    println("Hello, Code CIN!")
    println("2 + 3 = " + (2 + 3))
    return 0
}
```

```text
Hello, Code CIN!
2 + 3 = 5
```

## 2. 函数与递归

```c
function fib(int n) -> int {
    if (n < 2) {
        return n
    }
    return fib(n - 1) + fib(n - 2)
}

function main() -> int {
    println("fib(10) = " + int_to_str(fib(10)))
    return 0
}
```

```text
fib(10) = 55
```

要点: 参数按值传递、返回值经 `X0` 传回、递归深度受栈区限制 (默认内存 64 KiB, 栈约 1024 槽,
可用 `--mem-size` 扩容, 见 [限制与常见错误](/language/errors))。

## 3. 数组、struct 与标准库

```c
import "sort.cin"
import "array.cin"

struct Point {
    int x
    int y
}

function main() -> int {
    int data[8] = { 5, 3, 8, 1, 9, 2, 7, 4 }
    sort_bubble(data, 8)
    println("sorted = " + int_to_str(data[0]) + " " + int_to_str(data[1]) + " "
            + int_to_str(data[2]) + " " + int_to_str(data[3]) + " "
            + int_to_str(data[4]) + " " + int_to_str(data[5]) + " "
            + int_to_str(data[6]) + " " + int_to_str(data[7]))
    println("sum = " + int_to_str(a_sum(data, 8)))
    println("max = " + int_to_str(a_max(data, 8)))

    Point p
    p.x = 3
    p.y = 4
    println("point = (" + int_to_str(p.x) + ", " + int_to_str(p.y) + ")")
    return 0
}
```

```text
sorted = 1 2 3 4 5 7 8 9
sum = 39
max = 9
point = (3, 4)
```

要点: `import "sort.cin"` 走内置标准库 (`codecin/lib/`); 固长数组以指针形式传参;
struct 实例默认全零、成员用 `.` 访问。参考 [模块与标准库](/language/modules)、
[数组](/language/arrays)、[struct](/language/structs) 与 [标准库参考](/stdlib/reference)。

## 4. 控制流

```c
function main() -> int {
    int sum = 0
    for (int i = 1; i <= 10; i++) {
        if (i % 2 == 1) continue      // 只累加偶数
        if (i > 8) break              // 提前终止
        sum += i
    }
    int d = 0
    do {
        d++
    } while (d < 4)

    int grade = 75
    int tier = grade / 25             // '/' 是浮点除, 赋给 int 时截断
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

要点: `switch` 分支默认贯穿 (与 C 一致, 用 `break` 跳出); 三目为短路表达式。
参考 [控制流](/language/control-flow)。

## 5. 位运算、整数除法与字符串

```c
function main() -> int {
    int a = 0b1100 & 0b1010        // 8
    int b = 0b1100 | 0b0010        // 14
    int c = 0b1100 ^ 0b1010        // 6
    int d = 1 << 5                 // 32
    int e = -16 >> 2               // -4 (算术右移, 保留符号)
    int f = ~0                     // -1

    int g = 7
    g &= 3                         // 3
    g |= 8                         // 11
    g ^= 1                         // 10
    g <<= 2                        // 40
    g >>= 3                        // 5

    int q = idiv(17, 5)            // 3   (整数除法, 向零截断)
    int r = idiv(-17, 5)           // -3

    string s = "Code CIN"
    int h = s[0]                   // 'C' = 67 (单字节只读访问)

    println("bit_and=" + int_to_str(a) + " bit_or=" + int_to_str(b)
            + " bit_xor=" + int_to_str(c))
    println("shl=" + int_to_str(d) + " asr=" + int_to_str(e)
            + " not=" + int_to_str(f))
    println("compound=" + int_to_str(g) + " idiv=" + int_to_str(q)
            + " idiv_neg=" + int_to_str(r))
    println("s[0]=" + int_to_str(h))
    return 0
}
```

```text
bit_and=8 bit_or=14 bit_xor=6
shl=32 asr=-4 not=-1
compound=5 idiv=3 idiv_neg=-3
s[0]=67
```

要点: `/` 恒为浮点除, 整数除法用 `idiv(a, b)`; 位运算仅接受整数, `>>` 为算术右移。
参考 [运算符](/language/operators) 与 [内建函数](/language/builtins)。

## 6. 模块与内置标准库

```c
import "math.cin"
import "str.cin"

function main() -> int {
    println("abs=" + float_to_str(f_abs(-3.25)))
    println("floor(2.7)=" + float_to_str(f_floor(2.7))
            + " ceil(2.1)=" + float_to_str(f_ceil(2.1)))
    println("clamp=" + int_to_str(i_clamp(99, 0, 10)))

    string up = s_upper("aBcDe")
    println("upper=" + up + " lower=" + s_lower("HeLLo"))
    println("indexof(world)=" + int_to_str(indexof("hello world", "world")))
    println("contains(bana)=" + int_to_str(s_contains("banana", "nan")))
    println("repeat(ab,3)=" + s_repeat("ab", 3))
    return 0
}
```

```text
abs=3.25
floor(2.7)=2 ceil(2.1)=3
clamp=10
upper=ABCDE lower=hello
indexof(world)=6
contains(bana)=1
repeat(ab,3)=ababab
```

要点: 不写 `./` 前缀的 `import` 一律解析到内置标准库 `codecin/lib/`; 自建模块要写
`import "./helpers.cin"`。参考 [模块与标准库](/language/modules)。

## 7. 宿主能力 (需要原生运行时)

```c
function main() -> int {
    string f = "cin_io.txt"
    file_write(f, "hello")
    file_append(f, " world")
    println(file_read(f))
    println("size = " + int_to_str(file_size(f)))
    println("exists = " + int_to_str(file_exists(f)))
    println("os = " + os_name())
    println("cwd = " + cwd())
    return 0
}
```

```text
hello world
size = 11
exists = 1
os = windows
cwd = D:\ByUsi\Projects\UCPU
```

::: warning 纯 Python 路径下会报错退出
宿主能力只有 Go 原生实现。`codecin prog.cin --no-native` 运行上面的程序会以退出码 1 结束:

```text
ERROR    Execution error: host builtins (GUI/audio/system/Termux) require the
         native Go runtime (run without --no-native)
```
:::

更多宿主能力 (画布导出 PNG、联网音频、Termux API) 见 [宿主能力](/language/host-abilities)。

## 8. 汇编示例

### 循环求和 + 字符串打印 (`test_asm.asm`)

```asm
.text
main:
    MOV x0, #msg
    SYS #24              ; PRINT_STR

    MOV x1, #0           ; sum
    MOV x2, #1           ; i
loop:
    ADD x1, x2
    INC x2
    CMP x2, #11
    B.NE loop

    MOV x0, x1
    SYS #22              ; ITOA -> x0 = 缓冲
    SYS #24              ; PRINT_STR
    OUT #10              ; 换行

    MOV x3, #nums
    SD x1, [x3]          ; 存回数据段
    LD x4, [x3]          ; 再读出来
    ADDI x4, x4, #100
    MOV x0, x4
    SYS #22
    SYS #24
    OUT #10

    HALT

.data
msg: ASCIZ "Sum 1..10 = "
nums: DQ 0
```

```text
Sum 1..10 = 55
155
```

### 递归斐波那契

```asm
.text
main:
    mov x0, 10
    call fib
    mov x1, x0          ; 保存结果
    mov x0, x1
    sys #22             ; ITOA
    sys #24             ; PRINT_STR
    out #10
    halt

fib:                    ; 入口 x0 = n, 返回 x0 = fib(n)
    cmp x0, 1
    jg recurse
    mov x0, 1           ; fib(0) = fib(1) = 1
    ret

recurse:
    push x0             ; 保存 n
    dec x0
    call fib            ; fib(n-1)
    pop x1              ; x1 = n
    push x0             ; 保存 fib(n-1)
    mov x0, x1
    dec x0
    dec x0
    call fib            ; fib(n-2)
    pop x1              ; x1 = fib(n-1)
    add x0, x1
    ret
```

```text
89
```

### PL 关键字风格

```asm
.text
main:
    set x0, 65
    output x0          ; OUT 输出数值; 值为 10 时输出换行
    output #10
    stop
```

```text
65
```

要点: `.asm` 用助记符 (`MOV`/`OUT`/`HALT`/`CMP`/`JG`), `.pl` 用关键字 (`set`/`output`/`stop`/
`compare`/`jump_greater`); 两者由同一汇编器处理。完整语法见 [汇编语法参考](/asm/syntax),
逐条语义见 [指令语义参考](/asm/instructions)。

## 9. 编译成独立可执行文件

```bash
codecin hello.cin --build-exe hello              # 本机平台
codecin hello.cin --build-exe app --build-target linux/arm64   # 交叉编译
./hello                                          # 运行产物 (无需 Python/Go)
```

详见 [AOT 独立可执行文件](/runtime/aot)。

## 仓库内其他示例

| 文件 | 主题 |
|------|------|
| `basic.cin` | 综合回归基准 (类型、控制流、数组、字符串、函数、struct) |
| `examples/control_flow.cin` | `break`/`continue`/`do-while`/`switch`/三目/复合赋值 |
| `examples/literals_types.cin` | 进制与字符字面量、类型别名、`++/--`、转换内建 |
| `examples/bitwise_builtins.cin` | 位运算、`idiv`、字符串下标、数值/字符串内建 |
| `examples/modules_demo.cin` | `import` 与内置标准库 |
| `examples/stdlib_demo.cin` | 多库联动 (断言式演示) |
| `examples/system_interaction.cin` | 文件、进程、环境变量、主机信息 |
| `examples/asm_constants.asm` | `.equ` 常量与表达式立即数 |
| `test_asm.asm` | 汇编端到端 (循环、`SYS`、数据段、间接寻址) |
