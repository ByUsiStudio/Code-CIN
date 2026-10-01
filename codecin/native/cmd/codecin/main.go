//go:build ignore

// 本文件**故意不参与构建** (//go:build ignore)。
//
// 项目约定: 命令行 CLI **只能由 Python 实现** (`cpu.py` / `codecin/cli.py`)。
// Go 侧只允许提供「语言实现」本身 —— 词法/语法/编译/VM/宿主能力 ——
// 以 c-shared 库形式被 Python 调用, 不得自带 CLI 入口。
//
// 这份纯 Go CLI 草稿曾在迁移评估期被写出, 现已按该约定停用; 保留文件是为了
// 记录 "为什么不允许 Go CLI": 一旦 Go 也提供 CLI, 就会出现两套参数解析、
// 两套退出码约定、两套日志/错误面板, 与"三路径语义一致"的目标直接冲突。
//
// 原草稿的能力 (编译 → 编码 → Go VM 执行) 已并入 Python CLI 的调用链:
// Python 负责参数/日志/退出码, Go 负责语言语义。
package main

func main() {}
