---
description: 五分钟上手 Code CIN：第一个 CIN 程序、三种输入语言、三条执行路径、编译为字节码与 AOT 单文件。
---

# 快速开始

本节假设你已经完成 [安装](/guide/installation) (`pip install codecin==5.5.0`)。
下面所有命令既可以用安装后的 `codecin`, 也可以用源码树里的 `python cpu.py`,
两者完全等价。

## 1. 第一个程序

新建 `hello.cin`:

```c
function main() -> int {
    println("Hello, Code CIN!")
    println("2 + 3 = " + (2 + 3))
    return 0
}
```

运行:

```bash
codecin hello.cin
```

实际输出 (日志行会与程序输出混在同一终端, 见下方提示):

```text
08:37:15 INFO     CIN compiled: 30 instructions
         INFO     Starting program execution
Hello, Code CIN!
2 + 3 = 5
         INFO     HALT instruction executed
         INFO     Program execution finished
```

::: tip 想要“干净”的输出
日志默认是 `INFO` 级别。脚本或对比输出时可加 `--log-level ERROR`:

```bash
codecin hello.cin --log-level ERROR
```

```text
Hello, Code CIN!
2 + 3 = 5
```
:::

这段程序的要点:

- `main` 是入口函数 (不是必须叫 `main`, 程序按源码顺序先执行全局初始化, 再到第一条语句);
- **语句以换行结尾**, 分号可选;
- 字符串用 `+` 与任意类型拼接, `println` 自动追加换行;
- `-> int` 是返回类型, 省略时默认 `void`。

## 2. 同一件事, 三种写法

Code CIN 的编译器/汇编器接受三种输入, 最终都产出 UCBC 字节码:

::: tabs

== CIN (`.cin`)

```c
function main() -> int {
    println("Hello, Code CIN!")
    return 0
}
```

== PL (`.pl`, 关键字风格)

```asm
.text
main:
    set x0, #msg
    sys #24            ; PRINT_STR: 打印 x0 指向的字符串
    output #10         ; 输出换行
    stop

.data
msg: ASCIZ "Hello, Code CIN!"
```

== ASM (`.asm`, 助记符风格)

```asm
.text
main:
    mov x0, #msg
    sys #24            ; PRINT_STR
    out #10
    halt

.data
msg: ASCIZ "Hello, Code CIN!"
```

:::

```bash
codecin hello.cin        # 高级语言
codecin hello.pl         # PL 关键字风格汇编
codecin hello.asm        # 汇编
```

- PL 风格使用关键字助记符 (`set` / `output` / `stop` / `compare` / `jump_zero` / `branch_link` …),
  ASM 风格使用标准助记符 (`MOV` / `OUT` / `HALT` / `CMP` / `JZ` / `BL` …); 两种风格都由同一个
  `codecin/assembler.py` 汇编, 语法细节见 [汇编语法参考](/asm/syntax)。
- `sys #24` 是宿主系统调用 `PRINT_STR` (打印 `x0` 指向的 NUL 结尾字符串), `out` 输出数值,
  值为 `10` 时输出换行 —— 详见 [指令语义参考](/asm/instructions)。

## 3. 三条执行路径

同一个程序可以用三种方式执行, 结果一致 (详见 [执行路径](/guide/execution-paths)):

::: tabs

== 解释执行 (纯 Python)

```bash
codecin hello.cin --no-native
```

支持全部 `--debug` / `--step` 功能, 速度最慢。

== JIT 基本块编译

```bash
codecin hello.cin --jit --no-native
```

把热点基本块编译成 Python 代码, 与 `--debug` 互斥。

== Go 原生 VM (默认优先)

```bash
codecin hello.cin
```

整程序一次性交给 Go 原生 VM, 速度最快; 原生库缺失时自动回退。

:::

## 4. 编译产物与内存镜像

```bash
# 编译为 UCBC 字节码 (不执行)
codecin hello.cin --compile-only -o hello.bin
# INFO  Binary saved to hello.bin (753 bytecode bytes)

# 直接运行字节码
codecin hello.bin

# 反汇编查看字节码清单
codecin hello.bin --disasm
```

```text
; CPUSA binary: 30 instructions, mem=65536 bytes, entry=0x0, sp=0xfff8
;
0000: CALL #2
0001: HALT
0002: PUSH X29
0003: MOV X29 X32
0004: MOV X0 #0
0005: SYS #24
```

运行后保存内存镜像 (CROM v3, 默认 zlib 压缩):

```bash
codecin hello.cin --save             # 生成 hello.crom (固定为 <程序名>.crom, 不受 -o 影响)
codecin prog.asm --crom prog.crom    # 汇编路径: 先恢复内存镜像再汇编并运行
```

::: warning `--crom` 只对 `.pl` / `.asm` 生效
`CPU.load_program` 在 `.cin` 与 `.bin` 分支会直接返回, 因此这两个入口**不会读取** `--crom`;
`.pl` / `.asm` 还会自动探测同目录同名的 `<程序名>.crom`。CROM 是内存镜像而不是可执行文件
(直接 `codecin x.crom` 会被当作汇编源码而报错), 详见 [二进制格式](/runtime/formats)。
:::

## 5. 生成独立可执行文件 (AOT)

```bash
# 本机平台: 生成 hello.exe (Windows) / hello (Linux/macOS)
codecin hello.cin --build-exe hello

# 交叉编译 (只需本机装好 Go 工具链)
codecin hello.cin --build-exe app-linux --build-target linux/amd64
codecin hello.cin --build-exe app-mac   --build-target darwin/arm64
```

产物内嵌字节码与初始内存镜像, 由内置 Go VM 执行, **运行时不需要 Python、Go 工具链或任何动态库**。
详见 [AOT 独立可执行文件](/runtime/aot)。

## 6. 看看内置示例

仓库 `examples/` 目录下的程序都可以直接跑, 输出如下 (用 `--log-level ERROR` 过滤日志):

::: tabs

== 控制流 (`control_flow.cin`)

```bash
codecin examples/control_flow.cin --log-level ERROR
```

```text
level: top
sum=20 d=4 pass
```

覆盖 `break` / `continue` / `do-while` / `switch` / 三目 / 复合赋值。

== 字面量与类型 (`literals_types.cin`)

```bash
codecin examples/literals_types.cin --log-level ERROR
```

```text
hex=31 bin=13 oct=15 letter=65
pre=6 post=6 x=4 q=2
pi=3.14159 ok=true
```

覆盖 `0x/0b/0o` 字面量、字符字面量、`++/--`、转换内建。

== 位运算与内建 (`bitwise_builtins.cin`)

```bash
codecin examples/bitwise_builtins.cin --log-level ERROR
```

```text
bit_and=8 bit_or=14 bit_xor=6
shl=32 asr=-4 not=-1
compound=5 idiv=3 idiv_neg=-3
s[0]=67 s[5]=67
min=3 max=5
floor=3 ceil=4 round=3
atoi=42 trim_len=2
total=256
```

== 模块与标准库 (`modules_demo.cin`)

```bash
codecin examples/modules_demo.cin --log-level ERROR
```

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

演示 `import "math.cin"` / `import "str.cin"` 等内置标准库。

== 汇编端到端 (`test_asm.asm`)

```bash
codecin test_asm.asm --log-level ERROR
```

```text
Sum 1..10 = 55
155
```

演示循环、`SYS` 字符串打印、`.data` 段与间接寻址。

:::

::: details 用 Go 原生路径运行同一批示例
把上面命令里的 `--log-level ERROR` 换成不加 `--no-native` 即可 (例如
`codecin examples/control_flow.cin`), 输出应当完全一致 —— 这正是
[三路径一致性](/guide/execution-paths)要保证的约束。
:::

## 7. 编辑器支持

仓库 `misc/vim/` 提供 Vim 语法文件:

| 文件 | 作用 |
|------|------|
| `misc/vim/ftdetect/codecin.vim` | 识别 `.cin` / `.pl` / `.asm` 文件类型 |
| `misc/vim/syntax/cin.vim` | CIN 语言语法高亮 |
| `misc/vim/syntax/codecinasm.vim` | 汇编语法高亮 |

把 `misc/vim/*` 拷进 `~/.vim/` (或 `%USERPROFILE%\vimfiles\`) 即可生效。

## 下一步

- [命令行参考](/guide/cli) — 全部选项、默认值与退出码
- [CIN 语言总览](/language/) — 类型、控制流、函数、struct、数组、字符串
- [标准库总览](/stdlib/) — 19 个内置库与逐函数参考
- [交互式调试器](/tools/debugger) — 单步、断点、寄存器/内存查看
- [示例程序集](/guide/examples) — 按主题整理的完整示例
