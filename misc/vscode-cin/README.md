# Code CIN Language Support

UCPU 项目 Code CIN 语言的 VSCode 语法高亮扩展。纯声明式实现（TextMate 语法），无任何 JavaScript 代码。

## 功能

- `.cin` 文件语法高亮：关键字、类型、内建函数、注释、字符串、字符字面量、各进制数字、运算符
- 行注释切换（`Ctrl+/`）、块注释、括号匹配与自动闭合
- 代码片段：`main`、`function`、`for`、`foreach`、`switch`、`struct`、`enum`、`import` 等

## 安装

- 方式一：从 VSIX 安装

```powershell
npx @vscode/vsce package
```

在 VSCode 命令面板执行 `Extensions: Install from VSIX...`，选择生成的 `.vsix` 文件。

- 方式二：开发调试

用 VSCode 打开本目录，按 `F5` 启动 Extension Development Host，打开任意 `.cin` 文件验证。

- 方式三：从VSIX市场安装

在 VSCode 市场搜索 `Code CIN Language Support`，安装即可。

## 语言要素参考

- 关键字清单：`codecin/native/compiler/tokenizer.go`
- 词法规则（字面量、转义、运算符、续行）：`docs/language/lexical.md`
- 内建函数与宿主能力：`docs/language/builtins.md`、`docs/language/host-abilities.md`
- 标准库：`docs/language/modules.md`
