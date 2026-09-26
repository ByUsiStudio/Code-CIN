---
description: "CIN 内嵌 CPU 指令语句：set / add / subtract / multiply / divide / increment / decrement 七条寄存器风格语句的等价语义。"
---

# 内嵌 CPU 指令语句

CIN 保留了 7 条 **CPU 风格语句**, 直接对变量做寄存器级操作 —— 它们以“当前语句所在变量的
栈槽”作为操作数, 等价于对应的赋值/复合赋值。

## 语句与等价含义

| 语句 | 等价含义 |
|------|----------|
| `set x 30` | `x = 30` |
| `add x y` | `x = x + y` |
| `subtract x 5` | `x = x - 5` |
| `multiply x 2` | `x = x * 2` |
| `divide x 4` | `x = x / 4` |
| `increment x` | `x = x + 1` |
| `decrement x` | `x = x - 1` |

第二操作数可以是**变量**或**立即数**; 第一操作数必须是已声明的变量。

```c
function cpu_ops() -> void {
    int x = 10
    set x 30
    add x 12          // x = 42
    multiply x 2      // x = 84
    subtract x 42     // x = 42
    divide x 6        // x = 7
    increment x       // x = 8
    println("x = " + int_to_str(x))    // x = 8
}
```

## 与普通表达式的关系

```c
int a = 5
int b = 3

// 这两种写法等价
set a 10              // 内嵌语句
a = 10                // 普通赋值

add a b               // 内嵌语句 (a = a + b)
a += b                // 复合赋值 (同样 a = a + b)
```

::: tip 什么时候用
这组语句是**低级特性**, 主要服务于从汇编视角理解 CIN 的寄存器模型、以及移植早期示例。
日常写业务逻辑用常规表达式 (`x = x + y`、`x += y`) 更清晰, 也更不容易踩到 `/` 的浮点语义 ——
`divide x 4` 与 `x = x / 4` 一样是**浮点除法**, 需要整数除法请用 `idiv(x, 4)`。
:::

## 与汇编的关系

内嵌语句编译出来的就是普通 UCBC 指令 (例如 `set` → `MOV`), 与 `.pl` / `.asm` 汇编
共享同一套 ISA。想进一步下潜可以看:

- [指令语义参考](/asm/instructions) — 112 条指令逐条语义
- [汇编语法参考](/asm/syntax) — `.pl` (关键字风格) 与 `.asm` (助记符风格)
- [指令集编码表](/reference/isa) — 自动生成的编码表

```c
// 用 --debug 可以看到内嵌语句编译出的真实指令
function demo() -> void {
    int x = 1
    increment x
    multiply x 3
}
```

```text
PC=0x0000 #00000000 CALL main->0x2  SP=0xfff8
...
PC=0x0004 #00000002 ADD ...
```

## 限制

- 只能作用于**已声明的变量**, 不支持 `set arr[0] 5` 这种复合左值;
- 第一操作数必须是变量名, 不能是表达式;
- 7 条语句都是语句, 不能出现在表达式中 (没有返回值);
- 浮点变量可以参与, 但 `divide` 是浮点除。

## 相关页面

- [运算符](/language/operators) — 复合赋值与自增自减
- [函数](/language/functions) — 变量声明与作用域
- [汇编语法参考](/asm/syntax) — 从内嵌语句过渡到完整汇编
