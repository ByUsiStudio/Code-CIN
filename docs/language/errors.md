---
description: "CIN 语言限制、常见编译/运行错误对照表与排错流程：从报错信息定位到修复方式。"
---

# 限制与常见错误

这一页汇总 CIN 与 C/其它语言**最不一样的地方**, 以及编译器/运行时的真实报错与修复方式。

## 语言限制

1. **没有指针与取地址运算**: `*` 只是乘法, `&` 只是位与 (不是解引用/取地址);
   “引用语义”仅通过数组与 struct 传参隐式获得。
2. **`/` 恒为浮点除法**: 整数除法用内建 `idiv(a, b)` (向零截断); `%` 仅支持整数,
   浮点取模报错。
3. **位运算只接受整数**: `& | ^ << >> ~` 不接受 float/string; `>>` 是算术右移 (符号扩展),
   移位量按低 6 位取模。
4. **递归深度受栈区限制**: 默认内存 64 KiB, 栈约 1024 个 8 字节槽; 过深递归报
   `Stack overflow`。用 `--mem-size` 扩容 (例如 `--mem-size 262144`)。
5. **struct 字段限制**: 字段可以是标量、嵌套 struct、固长数组, 但**不能是变长指针数组
   (`T[]`)**; 字符串字段是指针, 拼接/复制会产生新堆块。
6. **全局初始化顺序**: 按声明顺序写入数据区; 数组字面量长度超过声明维度会报错。
7. **函数可先用后定义**: 同文件内的函数互相调用不受顺序限制 (两遍编译);
   但**变量必须先声明后使用**。
8. **字符串不可原地修改**: `strcpy` / 拼接 / `upper` 都返回新堆块; 没有可变的原地字符替换。
9. **字符串不能用 `==` 比内容**: `==` 比较的是 64 位指针值, 请用 `strcmp(a, b) == 0`
   (见 [字符串](/language/strings))。
10. **数组不携带长度**: 所有数组接口都要显式传入元素个数 `n`; 默认不做越界检查
    (需要时用 `--bounds-check`)。

## 常见错误对照表

| 错误信息 | 原因 | 修正 |
|----------|------|------|
| `Unknown function: xxx` | 调用了未定义或拼错的函数 | 检查函数名; 库函数需要先 `import "xxx.cin"` |
| `Undefined variable: xxx` | 使用了未声明 (或声明在使用之后) 的变量 | 先声明再使用 |
| `Unsupported int operator: xx` | 对整数用了不支持的运算 | 使用 `+ - * / % & \| ^ << >>` |
| `Float modulo not supported` | 对浮点用了 `%` | 先取整或改用 `idiv` |
| `Bitwise operator ... requires integer operands` | 对 float/string 使用位运算 | 位运算仅支持 int/bool |
| `Type mismatch ...` | 赋值/传参类型不匹配 | 显式转换或修正类型 |
| `Expected RBRACE ... at line N` | 花括号不配对, 或块内语句缺少换行 | 检查第 N 行附近 |
| `Import file not found` | 模块名写错, 或自建模块漏了 `"./"` 前缀 | 见 [模块与标准库](/language/modules) |
| `Stack overflow (collides with heap)` | 递归过深 / 局部数组过大 / 堆栈相撞 | `--mem-size` 扩容, 或减少局部大对象 |
| `Memory access out of bounds` 类错误 | 越界读写或保护违例 | 打开 `--bounds-check` 定位 |
| `host builtins ... require the native Go runtime` | 纯 Python 路径下调用宿主能力 | 去掉 `--no-native` (见 [宿主能力](/language/host-abilities)) |
| `Unknown instruction: xxx` (汇编) | 用了本 ISA 没有的指令 (例如 `jle`) | 见 [指令语义参考](/asm/instructions); 比较用 `JG`/`JL`/`JE` |

错误输出统一为 rich 红色面板, 带 `文件:行号` 定位:

```text
┌──────────────────────── Load Error ────────────────────────┐
│ prog.cin:12: Compiler error: Unknown function: printline   │
└────────────────────────────────────────────────────────────┘
```

## 排错流程

::: tabs

== 1. 先看定位

```bash
codecin prog.cin                # 面板里的 文件:行号 就是第一现场
codecin prog.cin --log-level DEBUG --log-file codecin.log
```

== 2. 语义对照

```bash
codecin prog.cin --no-native              # 纯解释执行 (语义基准)
codecin prog.cin --no-native --debug      # 逐指令追踪, 看最后一条 PC/寄存器
```

== 3. 交互定位

```bash
codecin prog.cin --no-native --step       # step> 提示符: b <地址> 设断点, p regs, p mem <地址>
codecin prog.cin --debug-server 9999      # 供 IDE/脚本驱动 (换行文本协议)
```

== 4. 边界与资源

```bash
codecin prog.cin --no-native --bounds-check     # 数组越界检查
codecin prog.cin --mem-size 262144              # 栈/堆扩容
codecin prog.cin --max-instructions 1000000     # 死循环保护 (默认 1 亿)
```

:::

更多排错入口:

- [日志与错误输出](/tools/logging) — 日志级别、`--debug` 详细内容、退出码
- [交互式调试器](/tools/debugger) — `step>` 命令集与打印目标
- [远程调试协议](/tools/remote-debug) — `--debug-server` 命令/响应
- [常见问题 (FAQ)](/guide/faq) — 安装、性能、产物、宿主能力等问答

## 相关页面

- [类型系统](/language/types) — 提升与截断
- [运算符](/language/operators) — `/` 与 `idiv`、位运算规则
- [字符串](/language/strings) — 指针语义与 `strcmp`
- [内存与缓存](/tools/memory-cache) — 内存布局、`--mem-size`、越界与保护
