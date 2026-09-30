# Code CIN 更新日志

本文件记录 Code CIN 项目的所有重要变更。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 规范，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [5.6.0] - 2026-09-27

一次以「静默错误清零 + 安全加固」为主的修复发布，落地 `docs/SUGGESTIONS_NEXT.md`
第一轮评审的全部 P0/P1 项。所有语义修复均同步 Python 解释器与 Go 原生 VM 两条路径，
并新增 25 个回归测试（`tests/test_p0_fixes.py`）；同时新增**键盘输入监听**宿主能力
（非阻塞轮询）与键盘场景的**顺序实时输出**、终端输入缓存兼容。

### 修复 (Fixed)

- **`enum` 末成员无尾逗号解析失败**：`expect(RBRACE)` 前未跳过换行导致
  `enum Color { RED, GREEN = 5 }` 报 `Expected RBRACE but got NL`。
  Python 编译器与 Go 编译器同步修复。
- **`input()` 真实现**：此前恒编译为 `MOV x0, 0`，任何输入都返回 0。现编译为
  `IN` 指令：解释器从 `input_buffer` / 标准输入逐行读取（非法行返回 0），
  Go 原生 VM 在缓冲耗尽后回退标准输入；CLI 启动时预读管道 stdin，
  管道喂入（`echo 42 | ...`）与交互输入均可用。
- **`substr` / `indexof` 统一字节语义**：此前按字符索引，与 `strlen` / `s[i]`
  的字节语义不一致，非 ASCII 字符串算错下标。现与 Go 侧一致按 UTF-8 字节切片/查找。
- **`sqrt` / `pow` 数学域错误对齐 NaN**：解释器此前对 `sqrt(-1.0)` 抛 CPython
  `ValueError`，与 Go 路径（返回 NaN）不一致。现统一返回 NaN
  （IEEE-754 允许同一 NaN 有多个位模式，不做精确位断言）。
- **CLI 编码兜底**：Windows GBK 控制台下输出非 ASCII 即崩，`main()` 入口对
  stdout/stderr 强制 `reconfigure(encoding='utf-8', errors='replace')`。
- **Go 编译器无法构建**（既有）：`genRangeFor` 的 `elemType` 参数误声明为
  `*Node` 导致整个 compiler 包编译失败，改回 `*Type`；同步修正两个从未跑通的
  Go 测试（range-for 标签前缀、enum 全局初始化器用例的期望值）。

### 安全 (Security)

- **`--sandbox` 真实现**：此前为空壳。现拦截全部宿主能力 SYS 调用
  （`AUDIOPLAY=39` 起的文件/网络/桌面/进程类），并强制放弃原生 DLL 路径
  回退解释器执行，保证拦截无旁路。
- **条件断点白名单求值**：`debugger` 的断点条件此前直接 `eval`，远程调试端口
  可执行任意代码。现以 AST 白名单（字面量/寄存器名/算术比较/布尔逻辑）
  校验后受限求值，拒绝 Call/Attribute/Subscript/推导式等一切可执行语法，
  条件长度上限 512 字符。

### 加固 (Hardened)

- **JIT 栈保护补齐**：PUSH 生成代码补栈溢出检查（栈顶与堆区相撞即报错），
  POP 补栈下溢检查，与解释器 / 原生 VM 对齐。
- **汇编器非法数字字面量报错**：`0x_`、`0b__` 等残缺字面量此前静默求值为 0，
  现编译报错。

### 新增 (Added)

#### CIN 语法

- **`enum` 枚举**：`enum Color { RED, GREEN = 5, BLUE }`。成员是**编译期整数常量**，
  未显式赋值时自动递增（首个成员从 `0` 起）；`= <整数常量表达式>` 支持
  `+ - * / % << >> & | ^` 与一元 `-` / `~`，可引用**先前已定义**的成员。
  枚举类型名等价于 `int`（`Color c = BLUE`），成员可用于表达式、全局初始化与 `case` 标签；
  成员只读，赋值报 `Cannot assign to enum member: X (constants are read-only)`。
- **范围 for**：`for (int v : arr)` 遍历定长数组元素（元素可为标量 / `string` / struct），
  支持 `break` / `continue`。被遍历对象必须是定长数组：`int[]` 指针形式报
  `range-for requires a fixed-size array`，多维数组报
  `range-for over multi-dimensional arrays is not supported`（改用下标循环）。
- **`for` 初始化子句支持赋值**：`for (i = 0; i < n; i = i + 1)` 现在与类型声明一样合法。
- **`switch` 增强**：`case 1, 2, 7..9:` —— 逗号分隔的单值与**闭区间**范围可混用（含负数），
  仍是 C 的贯穿语义；范围要求 `lo <= hi`，否则报 `Empty case range: lo..hi`。
- **转义扩展**：`\xH` / `\xHH` 写入**原始字节**（`"\xE4\xB8\xAD"` 即 `"中"` 的 UTF-8 三字节）、
  `\uHHHH` / `\UHHHHHHHH` 按 **Unicode 码点**解析并以 UTF-8 编码写入；新增 `\a \b \f \v`
  （字符字面量同样支持这四个）。`\u` / `\U` 位数不足或码点越界都有明确报错；未知转义保持原样。
- **多参数 `print` / `println`**：`println("a=", a, " b=", b)` 依次输出、**不加分隔符**；
  `println()` 输出空行；单参数行为不变。

#### 宿主 API（36 个，全部 Go 原生实现）

- **路径与文件系统**：`path_join` `path_basename` `path_dirname` `path_abs`（string，路径分隔符随平台）、
  `file_copy` `file_move` `dir_remove`（递归删除）`chdir`（int，`0`/`-1`）、
  `is_dir`（`1`/`0`）、`file_mtime`（Unix 秒 / `-1`）、`temp_dir`（string）。
- **时间与系统**：`time_ms`、`sleep_ms`（单次上限 10 分钟 / 600000 ms）、`cpu_count`、
  `arch_name`、`mem_info`（JSON `{"total_kb":N,"free_kb":M}`，未知平台为 0）、`is_android`。
- **网络**：`http_get` / `http_post`（返回响应体，失败空串；15 秒超时、8 MiB 上限）、
  `download`（`0`/`-1`；非 2xx 算失败，落盘上限 256 MiB）。
- **编码与哈希**：`sha256`（十六进制小写）、`base64_encode`、`base64_decode`（非法输入空串）。
- **桌面集成**（Termux 优先，再按平台分发，命令缺失即优雅失败）：`clipboard_get` / `clipboard_set`、
  `notify`、`open_url`。Windows 用 PowerShell `Get-Clipboard` / `cmd /c clip`、`Wscript.Shell.Popup`
  （10 秒自动消失）、`cmd /c start`；Linux 用 `wl-paste` / `xclip` / `xsel`、`notify-send`、`xdg-open`；
  macOS 用 `pbpaste` / `pbcopy`、`osascript`、`open`；Android/Termux 用 `termux-clipboard-*`、
  `termux-notification`、`termux-open-url`。
- **Android / Termux 扩展**（非 Android 环境一律优雅失败）：`android_intent`（`am start`，Termux 下回退
  `termux-am`）、`termux_call`、`termux_share`、`termux_torch`、`termux_volume`、`termux_brightness`、
  `termux_camera_photo`、`termux_fingerprint`、`termux_sensor`。
- **`os_name()` 说明更新**：除 `"windows"` / `"darwin"` / `"linux"` 外，Android 原生构建返回 `"android"`。

#### 键盘输入监听（SYS 116..118，非阻塞轮询）

- **`key_hit()` / `get_key()` / `key_flush()`**：面向游戏循环 / TUI 的非阻塞键盘轮询，
  核心实现全部由 Go 原生引擎承担（纯标准库，零第三方依赖）。
  - `key_hit()` -> `1` 有待读按键 / `0` 无；
  - `get_key()` -> 取出一个键码，无按键返回 `-1`；`0..255` 为原始字节
    （Ctrl+字母 = 字母 & 0x1F，监听期间 Ctrl+C 不再终止程序），
    方向键 / Home / End / PgUp / PgDn / Ins / Del 映射为 `1001..1010`，
    F1..F10 映射为 `1021..1030`（Unix 转义序列与 Windows 扫描码在引擎内统一解码）；
  - `key_flush()` -> 清空键盘输入缓冲。
- **平台实现**：Windows 用 msvcrt `_kbhit`/`_getch`；Linux / macOS / Termux 用
  termios 原始输入（只关行缓冲 / 回显 / Ctrl+C 信号，保留输出处理，
  `println` 不受影响）；程序退出自动恢复终端设置（幂等，覆盖所有执行出口）。
- **终端缓存兼容**：激活监听时清空控制台输入残留（Windows `FlushConsoleInputBuffer` /
  Unix 非阻塞排空）；Windows 启用前先以 `GetConsoleMode` 校验 stdin 为控制台，
  管道 / 重定向的判定与非 Windows 一致。
- **顺序输出（禁止一股脑输出）**：真实终端下激活监听时，原生引擎先把已缓冲输出
  落到终端，之后逐条直写 stdout 实时可见 —— "提示 → 等按键 → 反馈"顺序正确；
  非终端环境（管道 / 重定向 / 测试捕获）保持"缓冲 + 结束回传"不变。
- **非终端环境优雅失败**：`key_hit` 恒 `0`，`get_key` 恒 `-1`，不阻塞、不报错。
- **`codecin/lib/key.cin` 标准库**：`enum Key` 键码常量（`K_UP`..`K_F10`、
  `K_ESC` `K_ENTER` `K_TAB` `K_BACKSPACE`）+ `k_ctrl`（Ctrl 组合键码）+
  `k_is_special`（扩展键码判定）+ `key_wait`（10ms 轮询阻塞等一键）。
- 三个内建同时注册进 Python 编译器 `HOST_BUILTINS` 表；纯解释路径
  （`--no-native`）调用报 `require the native Go runtime`（与全部宿主 API 一致），
  `--sandbox` 同样拦截；`cpu.py` 宿主能力判定由编号区间改为
  `>= AUDIOPLAY` 下界，未来新增宿主 SYS 不再漏判。

### 文档 (Docs)

- `docs/CIN_GUIDE.md`：新增「enum 枚举」「范围 for」「转义序列」小节，`switch` 增加多值/范围 case，
  `for` 说明 init 赋值，`print`/`println` 改为多参数签名；宿主能力新增 6 个分组的 API 表
  （路径与文件系统 / 时间与系统 / 网络 / 编码与哈希 / 桌面集成，含各平台机制对照表 / Android-Termux 扩展）、
  限制与常见错误同步。
- `docs/language/`：`types.md`（新增 enum 类型）、`control-flow.md`（范围 for、case 范围）、
  `lexical.md`（转义表、关键字、续行运算符 `..`）、`strings.md`、`builtins.md`、`host-abilities.md`
  （网络 / 编码 / 桌面 / Android 分组与能力矩阵）、`errors.md`、`arrays.md`、`variables.md`、`index.md` 同步。
- `docs/beginner/`：`cheatsheet.md`、`ch11-io-host.md`（新增网络、编码与哈希、桌面集成、路径与文件管理、
  时间与系统、Android/Termux 扩展小节）、`ch04-conditions.md`、`ch05-loops.md`、`index.md` 同步。
- `misc/vim/syntax/cin.vim`：`enum` 加入类型高亮，36 个新宿主内建加入 `cinBuiltin`。
- `docs/CIN_GUIDE.md`：新增「宿主能力: 键盘输入监听（非阻塞轮询）」分节
  （键码约定表 + 游戏循环示例 + 平台实现与输出行为说明），官方标准库清单收录 `key.cin`。
- 修正 `input()` 的过时描述（`docs/language/builtins.md`、`beginner/ch02-variables.md`、
  `ch11-io-host.md`、`ch12-debug.md`、`cheatsheet.md`、`projects.md`）：
  自本版起 `input()` 已真实现（读一行标准输入，非法行 / EOF 返回 `0`），
  相关章节与避坑清单同步为实际行为；`cheatsheet.md` 的 `sqrt(-1)` 条目
  同步为"两条路径一致返回 NaN"。
- 本版宿主 API 均为 **Go 原生引擎实现**：纯解释路径（`--no-native`）调用会报
  `host builtins (GUI/audio/system/Termux) require the native Go runtime`；
  它们具备真实文件与网络权限，请只运行可信脚本。

---

## [5.5.3] - 2026-09-26

以「消除静默算错地址」为主线的一次修复发布：两个会**读错/写错内存且不报错**的
代码生成缺陷，配套文档站独立与 CI 适配。

### 修复 (Fixed)

#### 嵌套下标算错基址（静默读写错误内存）
- **`A[B[i]]` 一类的嵌套下标把外层基址弄丢**。编译器约定是"任何跨子表达式存活
  的临时值都存栈"，但 `_gen_index` 把数组/字符串基址暂存在 `x3` 才去求下标；
  内层下标自己也要用 `x3` 存基址，于是外层 `ADD` 用的是**内层数组的基址**：
  `A[B[i]]` 被算成 `&B[0]`，读取静默得到错值、赋值静默写到别的变量内存上
  （不越界、不报错）。实测 11 个用例里 10 个算错。
  现在基址改为入栈（`PUSH`/`POP`），Python 与 Go 两个编译器同步修复。
- **同类缺陷一并修掉**：`_gen_assign` 把待写入的值放在 `x2` 之后才求左值地址
  （`A[f(i)] = v` 会存错值）；`gen_init_2d_literal` 把行指针数组 `x4`、当前行
  `x5` 跨元素表达式存活（`int[][] m = {{g(1), g(2)}, ...}` 元素含函数调用时写错行）。
  两处均改为求值前入栈。

#### 浮点下标被当成字节偏移
- **`G_C[(lo + hi) / 2]` 抛 `address 379800000000000 out of bounds`**
  （issue #1）。`/` 恒为浮点除法，所以下标类型是 float，而 `_gen_index` 是唯一
  漏掉"int 上下文隐式截断"的地方：float 的 IEEE-754 位模式被直接当作字节偏移
  乘 8（249.5 的位模式 × 8 = `0x0379_8000_0000_0000`，正是报告里那个恒定高位
  `0x3798`）。标准 Hoare 快排因此完全无法运行。现在数组与字符串下标都会先按
  与 `int x = 1.9` 一致的规则向零截断，且截断发生在边界检查之前。

### 变更 (Changed)

- **文档站迁至独立仓库** [Code-CIN-Docs](https://github.com/ByUsiStudio/Code-CIN-Docs)：
  本仓库不再内嵌 `docs/` 子模块（`.gitmodules` 不再使用），README 相应改为单独克隆；
  ISA 指令表文档一致性由文档仓库自己保证（`script/gen_isa_docs.py` 保留为生成器）。
- **CI 适配 Node 20 下线**（GitHub 于 2026-09-23 移除 Node 20 运行时）：工作流里
  全部动作升级到 Node 24 版本 —— `checkout@v6`、`setup-python@v7`、`setup-go@v7`、
  `upload-artifact@v7`、`download-artifact@v7`、`softprops/action-gh-release@v3`。
- **修复 `release.yml` 的非法工作流**：4 个 checkout 步骤各写了两个 `with:` 键，
  GitHub 会判 `Invalid workflow file`（发布流程此前根本跑不起来）。同时移除所有
  `submodules: recursive` 与已失效的文档一致性检查步骤。
- **`requirements-dev.txt` 补 `PyYAML`**：`tests/test_workflows.py` 用
  `pytest.importorskip('yaml')`，缺依赖时整块门禁被静默跳过 —— 上面那个重复键
  错误就是这样漏过 CI 的。该文件现在还会检测**同层重复键**（PyYAML 默认静默
  后者覆盖前者）。
- **`.gitignore`/leak-check 排除表**补 `.gotmp`、`.gocache`、`pytest-cache-*`
  （都是记录在案的临时/构建目录）；`MANIFEST.in` 去掉已不存在的 `docs/` 条目。

### 测试 (Tests)
- 新增 `tests/test_nested_subscript_codegen.py`：17 个嵌套下标用例 × 解释器/JIT/
  原生三条路径，另有"写 `A[B[i]]` 不得触碰 `B` 的内存"用例；回退修复后 52 条中
  46 条失败。
- `codecin/native/compiler/compile_test.go` 新增同类 Go 侧用例（编译后直接在
  Go VM 中执行）。

---

## [5.5.0] - 2026-09-24

以「Python 只做 CLI、Go 是唯一实现」为主线的架构收敛，配套标准库打包修复与 AOT 依赖加固。
详见 [第二轮更新建议](docs/SUGGESTIONS_NEXT.md)。

### 新增 (Added)

- **原生库改为"安装时编译"**：PyPI 包**不再携带任何预编译库**（`package-data` 与
  `MANIFEST.in` 都移除了 `*.dll/*.so/*.dylib`）。`setup.py: BuildPyWithNative`
  在构建时调用 `codecin/native/build.*` 用**用户机器的 Go 工具链**现场编译，
  并把产物单独拷进安装目录 —— 所以 `pip install codecin`（sdist）装完即带原生加速。
  `build.sh`/`build.bat` 相应改为**只发布 sdist**：若同时发布 wheel，pip 会优先装
  wheel 而不执行构建，用户就拿不到原生加速。没有 Go 的用户可从 Release 下载预编译库。
- **原生库新增 ARM64 目标**：Release 现在为 **五种平台组合**构建 c-shared 库 ——
  linux/amd64、**linux/arm64**（`ubuntu-24.04-arm`）、darwin/amd64、darwin/arm64、
  windows/amd64。
  资产名带 `平台-架构` 后缀（如 `libcodecin_native-linux-arm64.so`）：
  此前各平台产物同名，`release` 汇总时会互相覆盖，只剩最后一个。
- **产物架构校验**：每个原生库构建后都用 `go version -m` 读出真实的 `GOOS`/`GOARCH`
  并断言与资产名一致，杜绝把错架构的库以 arm64/x64 的名义发出去。
- **原生库查找支持架构专属名**：`codecin/native.py: _lib_candidates` 现在按
  **架构专属名 → 通用名** 的顺序查找，同目录下同时存在两种架构的库时会优先选本机的那个。

### 变更 (Changed)

- **`get_engine` 不再因原生库 ABI/符号不符而中断运行**：此前只捕获 `OSError`，
  旁边放一个旧版或架构不符的库会抛 `AttributeError` 并让整个运行崩掉（哪怕
  `native.py` 承诺过"加载失败自动回退纯 Python"）。现在同时捕获 `AttributeError`，
  逐候选继续尝试，全部失败时记一条 warning 再回退。
- **Go 侧不再提供 CLI**：删除 `codecin/native/cmd/codecin/`（独立 Go CLI）、`codecin/native/tmpdump/`
  与仅供 CLI 调用的 `codecin/native/aot/build.go`。语言实现仍在 Go 侧
  （CIN 编译器、字节码 VM、CROM、AOT stub），但**只以库的形式存在**；
  CI 新增门禁断言 `codecin/native/` 下不存在 `package main`。
- **唯一 CLI 入口是 Python**：`python cpu.py` / 安装后的 `codecin` console script。
- **内置标准库迁入包内**：`lib/` → `codecin/lib/`，并通过
  `[tool.setuptools.package-data]` 与新增的 `MANIFEST.in` 进入 wheel/sdist —— 修复
  「`pip install codecin` 之后所有 `import "lib/*.cin"` 直接编译失败」的问题。
- **CIN import 解析规则 (B3)**：
  - `import "./x.cin"` / `import "../x.cin"` —— 相对**当前 .cin 文件**所在目录；
  - 其余任何形式（`import "x.cin"`、`import "lib/x.cin"`）—— 直接解析到
    **codecin 内置标准库** `codecin/lib/`（`lib/` 前缀保留为兼容写法）。
  仓库内示例与测试已统一为裸名字形式。
- **AOT 依赖检查与嵌入**：`--build-exe` 现在会先解析 import 闭包做依赖完整性检查
  （缺失/循环引用报 `AotError` 而不是等到 `go build` 失败），把依赖清单打印出来，
  并在编译期把依赖库全部展开嵌入产物；数据段越界不再静默丢弃，而是报错并提示
  `--mem-size`。Windows 目标未带 `.exe` 的输出路径会自动补上后缀。
- **发布与 CI**：移除 `release.yml` 的 Go CLI 构建矩阵；原生库资产补上架构维度
  （macOS x64/arm64）并使用与本地一致的静态链接参数；`workflow_dispatch` 现在
  会 checkout 输入 tag；新增 `dist` 作业断言 wheel/sdist 内含 19 个内置标准库模块
  并实际安装后跑一个使用标准库的程序；CI 补 `timeout-minutes` 与 `permissions`，
  覆盖率合并为一次运行并设 `--cov-fail-under=70`。
- **移除旧的安装/打包脚本**：`install.sh` / `install.ps1` / `codecin.spec` /
  `codecin_linux.spec` / `build_win.bat` 已删除，相关文档同步更新为 pip 安装路径。

### 修复 (Fixed)

- `python cpu.py --help` 的首行版本号长期停留在 `Code CIN v5.3`，现直接取
  `codecin.__version__`（此前是全仓唯一残留的版本串）。
- `--build-exe` 在程序文件不存在时抛裸 `FileNotFoundError` traceback，现在与普通路径
  一样给出 `Build Error` 面板。
- `--build-exe` 传给 `build_program` 的显式输出路径在 Windows 上不再产生无法执行的
  无扩展名文件。

---

## [5.4.2] - 2026-09-13

以「消除静默错误 + 工程可信度」为主线的一次修复与加固，详见
[优化建议报告](docs/SUGGESTIONS.md)。测试从 158 项增至 413 项（Python）+ 2 个 Go 测试包。

### 修复 (Fixed)

#### 会静默产生错误结果的缺陷
- **汇编器把十六进制立即数的尾字母 `F` 当类型后缀吃掉**：`#0x1F` 曾被解析成 `1`、
  `#0xABCDEF` 成 `703710`、`#0xFF` 直接抛异常。改为先判进制再剥后缀（`assembler.py`）。
- **Go `genSwitch` 负 case 永远匹配不上**：旧实现用 `-1` 当 default 哨兵并跳过 `raw < 0`，
  于是 `switch(x){case -1:...}` 走 default（Python 侧正确）。改为按无符号位模式比较。
- **Go `genSwitch` 非常量 case 造成标签错位**：旧实现遇到求值失败的 case 只 `continue`
  不补标签，导致后续 case 的标签贴到别人的语句体上（静默错误分派）；现在与 Python 一致报编译错误。
- **`switch` 内 `continue` 泄漏选择器栈槽**：两端都绕过 `ADDI SP,SP,8`，每轮迭代漏 8 字节、
  长循环必然栈堆碰撞。现在 continue 先弹出选择器再跳转。
- **Go 代码生成大面积静默吞错**：`println(foo())` 输出 `0`、`~1.5` 输出 float 位模式、
  `s += "b"` 静默无操作、未定义变量/成员访问/越界下标/浮点取模等都产出静默错误的字节码。
  引入粘性错误通道 (`compiler.err` + `failf`)，在 `Compile` 末尾统一返回编译错误，文案对齐 Python。
- **内置函数缺参数导致崩溃**：`sqrt()`、`substr("a",1)` 在 Go 侧 panic、Python 侧抛裸 `IndexError`；
  两端统一为 `CompilerError`，并新增参数个数表。
- **步数用尽伪装成正常结束**：原生 VM 返回 `StatusDone`、解释器只 warning 后 break，进程仍以 0 退出。
  现在两端都报 `instruction limit reached`，CLI 退出码为 1。
- **数值字面量**：`0xFFu`/`42u`/`1.5f` 在 Python 侧抛裸 `ValueError`（Go 侧十六进制正确、十进制报错）；
  十进制超出 64 位范围时 Python 静默截断、Go 报错。两端现在行为一致，越界一律报错。
- **带 UTF-8 BOM 的源文件 `import` 静默失效**（Windows 编辑器常见），两端均已修复。
- **CROM 解压无上限（zip bomb）**：65KB 的合法文件可解出 64MB+；Python 侧还无条件下按旧版
  裸格式加载任意文件（如 `NOTACROMFILE`）。两端统一上限为 `mem_size + 4MiB` 并校验头部自洽。
- **原生库信任不可信字节码头部**：`count` 直接用作 `make` 容量（13 字节输入可触发 137GB 预分配）、
  版本字节与参数个数从不校验（越界 panic）。现在解码阶段一次性拒绝。
- **VM 输出无上限**：新增 16 MiB 输出上限，超限报错；HTTP 下载与本地资源读取同样加上限。

### 性能 (Performance)

- **原生路径不再逐条记账**：`_apply_native_state` 曾按指令数在 Python 里循环调用
  `record_instruction`（且算出的直方图随即被 `clear()` 丢弃）。改为 O(1) 批量写入：
  62 万条指令的基准从 ~838ms 降到 ~14ms（约 60 倍），吞吐 0.4M → 45M instr/s。
- **解释器快路径**：无断点/单步/JIT/追踪/节流时走紧凑循环，解释执行提速约 1.15x。

### 新增 (Added)

- **AOT 静态编译: 编译成独立可执行文件 (Windows / Linux / macOS)**：
  `python cpu.py program.cin --build-exe app` 或 `codecin build program.cin -o app`，
  支持 `--target OS/ARCH` 交叉编译（windows/amd64|arm64、linux/amd64|arm64、darwin/amd64|arm64）。
  产物内嵌 UCBC 字节码与初始内存镜像，由内置 Go VM 执行，**不依赖 Python、Go 工具链、
  libc 或任何动态库**（`CGO_ENABLED=0`，Linux 产物无 `PT_INTERP`）；入口 shell 模板由
  Go 与 Python 两侧共用（`codecin/native/aot/stub_main.go.txt`）。
- **官方标准库新增 6 个模块**（`lib/`，共 19 个）：
  `bits.cin`（位运算/popcount/clz/ctz/循环移位/位域）、`stat.cin`（顺序统计量：中位数/
  众数/百分位/直方图/方差）、`hash.cin`（djb2/FNV-1a/sdbm/整数混合/桶映射）、
  `validate.cin`（字符类别判定与安全解析：`val_is_int`/`val_is_ident`/`val_parse_int`）、
  `matrix.cin`（行主序方阵：加减乘/转置/迹/对称判定/行列式）、
  `queue.cin`（定长环形队列与栈）。
- **编译器产物级差分**：Go CLI 新增 `--dump-bytecode`；`script/diff_go_python.py` 现在先比对
  编译出的 UCBC 字节、再比对 stdout —— `examples/*.cin` 6 个示例（5.6KB~211KB 字节码）**逐字节一致**。
- **Go 侧测试**：新增 `compiler` 与 `engine` 两个测试包（switch 语义、错误通道、字节码校验、
  CROM 往返与 zip bomb、步数上限），CI 以 `-race` 运行。
- **CI `integration` 作业**：真实编译 Go CLI 与原生库后运行全量测试与差分测试，并断言原生库已加载
  （否则红灯，不再静默 skip 约 20 个原生用例）；另加"被 `.gitignore` 吞掉的源码"守卫与 gofmt 门禁。
- **`release.yml`**：打 tag 时校验 tag 与 `codecin.__version__` 一致，构建 5 平台 Go CLI、
  三平台原生库与 sdist/wheel 并发布到 Release。
- **版本单一真源**：`pyproject.toml` 改为 `dynamic = ["version"]`（源自 `codecin.__version__`），
  Go 侧版本由 `script/gen_native_isa.py` 生成并由 CI `--check` 校验；新增 `codecin --version`；
  原生库不再自报与包版本无关的 `1.0`。
- **打包配置**：新增 `[build-system]`、`[project.scripts]`、`[tool.setuptools]`；
  PyInstaller 两个 spec 补上 `lib/`、`misc/vim` 数据文件与平台原生库。
- **`.gitattributes` / `.editorconfig`**：统一 LF（避免 Windows 下生成物被写成 CRLF 导致
  `gofmt -l` 误报与整文件 diff）。
- **ISA 单一真源守卫**：新增测试校验 `ARG_COUNTS`、`stats.latency`、`jit._JIT_OPS`
  与 `Opcode` 表一致（防拼写/漏项漂移）。

### 变更 (Changed)

- **`.gitignore` 重写**：删除 `*cache*` / `*tmp*` 两个会吞掉真实源码的通配
  （`tests/test_cache.py` 曾因此不在仓库中，现已补回）；补齐 `*.so` / `*.dylib` / `*.dll`
  与各类缓存目录。
- **构建产物不再入库**：`git rm --cached codecin/codecin_native.dll` —— 旧产物由
  `9a3087c`（脏工作树）构建却随源码长期提交，用户拿到的二进制与源码不对应。
- **ruff 规则集扩充**：从 `["E9","F63","F7","F82"]`（基本等价于"能否 import"）
  扩到 `E4/E5/E7/E9/F/I/UP/B/SIM/C4`，并清理全部违规。
- **文档修正**：`go.mod` 改为 `go 1.26` 并与 README/BUILDING/安装脚本统一为「Go 1.26+」；
  删除架构图中的 "C++ 生成"；补 `docs/ISA.md` 到文档索引；纠正 `basic.cin 400 行` 等错误陈述；
  Termux 安装脚本不再编译 Go CLI 的真实情况同步到 README/CHANGELOG。

---

## [5.3.0] - 2026-09-11

项目正式重命名为 **Code CIN**，从 Python 优先架构切换为 **Go 优先** 架构。新增完整的 Go 原生 CIN 编译器与独立 CLI，
扩展 CIN 高级语言语法、官方标准库、跨平台宿主能力（2D 画布 / 联网音频 / 系统交互 / Termux API），
并引入 CROM v3 压缩格式、MMU 分页与远程调试服务。

### 新增 (Added)

#### Go 原生工具链
- **Go 版 CIN 编译器** (`codecin/native/compiler/`)：完整的词法分析、类型系统、语法分析器与代码生成，
  与 Python 编译器产物**逐字节等价**：`script/diff_go_python.py` 先比对 `--dump-bytecode` 导出的
  UCBC 字节，再比对程序 stdout（覆盖 `examples/*.cin` 6 个示例）。
- **独立 Go CLI** (`codecin` 命令)：全 Go 链路执行，无需 Python 依赖。
- **字节码中间表示 IR** (`codecin/native/ir/`)：Go 侧 CIN 编译的中间表示定义。
- **一键安装脚本**：`install.sh`（Linux/macOS/Termux）、`install.ps1`（Windows PowerShell）、
  `script/install_termux.sh`（Termux 克隆 + 依赖 + 编译原生库 + 启动器；Termux 上暂不编译 Go CLI）。

#### CIN 高级语言
- **位运算与复合赋值**：`& | ^ ~ << >>` 及 `&= |= ^= <<= >>=`；`idiv()` 整数除法；`s[i]` 字符串单字节读取。
- **内建函数扩展**：`floor / ceil / round / min / max / atoi / trim / ltrim / rtrim`。
- **类型与字面量**：`char / short / long / unsigned` 类型；`0x / 0b / 0o` 与字符字面量；`++ / --` 自增自减；
  类型转换内建函数。
- **控制流**：`break / continue`、`do-while`、`switch / case`、三目运算符。
- **比较运算符作为值表达式**：比较结果可直接用于赋值与运算。
- **模块化 `import`**：行级 `file:line` 定位，支持 DAG 层层引用。

#### 官方标准库 (`lib/`)
新增 13 个标准库模块，覆盖纯 CIN 与宿主能力两类：

| 库 | 主要能力 |
|----|----------|
| `math.cin` | 浮点/整数绝对值、地板、天花板、四舍五入、最值、clamp |
| `str.cin` | 大小写、包含、前缀/后缀、计数、重复 |
| `array.cin` | 求和、最值、查找、计数、反转、填充、复制、下界查找 |
| `sort.cin` | 冒泡/选择/插入/快速排序、有序判定、二分查找 |
| `conv.cin` | 进制转换、填充、字符/整数/浮点解析 |
| `vec.cin` | 向量求和、均值、方差、标准差、点积、归一化、线性插值 |
| `rand.cin` | 范围随机、布尔、浮点、洗牌、选择、概率 |
| `json.cin` | 扁平 JSON 取值（字符串/整数/浮点/布尔/存在判定） |
| `time.cin` | 时间戳、时分秒、毫秒、时长格式化 |
| `io.cin` | 文件读写追加、存在/大小/删除、目录创建/列表、路径处理 |
| `gui.cin` | 2D 画布：颜色、清屏、矩形、柱状图、折线图、网格、保存、显示 |
| `termux.cin` | Termux API：通知、吐司、剪贴板、震动、TTS、短信、电池、定位、WiFi |
| `test.cin` | 断言框架：整数/字符串/近值/真假判定与汇总报告 |

#### 宿主能力 (Host Capabilities)
- **2D 绘图画布**：`canvas / set_color / fill_rect / fill_circle / draw_line / draw_text`，
  `save_png()` 导出 PNG，`show_canvas()` 弹窗预览（新增 `CANVASSHOW` 系统调用）。
- **联网音频**：`audio_play(url) / audio_stop / audio_volume / audio_wait`（HTTP 下载 + WAV 播放）。
- **跨平台系统交互**：`file_read / file_write / file_append / file_exists / file_delete / file_size`、
  `mkdir / dir_list`、`exec / exec_output`、`getenv / setenv`、
  `os_name / hostname / username / cwd / home_dir`（Windows / Linux / macOS）。
- **Termux API 支持**：通知、吐司、剪贴板、电池、震动、TTS、定位、WiFi、对话框、短信。

#### 运行时与基础设施
- **CROM v3 格式**：zlib 压缩 + CRC32 校验 + 元数据头，Go/Python 双实现。
- **MMU 分页** (`--mmu`)：identity 页表，未映射页触发缺页错误；页表可持久化到 CROM v3。
- **远程调试服务** (`--debug-server <port>`)：TCP 换行文本协议，支持 step / continue / break / regs / mem / history。
- **确定性执行** (`--seed`)：随机种子固定输出。
- **反汇编** (`--disasm`)：.bin / UCBC 反汇编为文本清单。
- **数组越界检查** (`--bounds-check`)：CIN 数组运行时越界检测。

#### 示例与测试
- 新增示例：`control_flow.cin`、`literals_types.cin`、`modules_demo.cin`、`bitwise_builtins.cin`、
  `stdlib_demo.cin`、`system_interaction.cin`、`asm_constants.asm`。
- 新增测试：`tests/test_libs.py`（标准库全覆盖）、CIN 宿主能力测试、Go CLI 集成测试、三路径一致性测试。
- Vim 语法高亮：`misc/vim/` 下 `.cin` / `.asm` 语法与文件类型检测。

### 变更 (Changed)

- **架构切换**：从 Python 优先改为 Go 优先，Go 原生库接管字节码 VM、CROM 与 CIN 编译器；
  Python 保留为 CLI 壳与回退路径（原生库缺失时自动降级纯 Python）。
- **项目重命名**：UCPU → Code CIN，包结构 `codecin/`，版本号升至 5.3.0。
- **原生 VM 指令集扩展**：新增 `ASR`（算术右移，保留符号）等指令的原生实现支持。
- **日志系统统一**：全线日志与错误输出基于 `rich`（彩色表格、面板、traceback），`--debug` 模式提供
  逐指令 / 寄存器 / 内存 / 栈 / 缓存的超详细追踪。
- **CLI 参数管理**：改用 `argparse` 统一管理命令行参数。
- **原生引擎重构**：`codecin/native/engine/` 代码结构与导出接口优化。

### 修复 (Fixed)

- 原生 VM `LW` 指令符号扩展与地址检查。
- 原生 VM 缺失 `ASR` 指令导致算术右移输出截断的问题。
- Go 编译器数值处理逻辑修复。
- `stdlib_demo.cin` 二分查找测试用例预期结果修正。
- 原生运行时错误处理：同步状态并抛出异常。
- 汇编器数据标签在程序编码期间的传递问题。

---

## [5.2.0]

### 新增 (Added)

- 远程驱动式调试功能与 `--debug-server` 服务。
- MMU 分页支持 (`--mmu`) 与 CROM v3 MMU 页表持久化。
- `import` 模块化与行级 `file:line` 定位。
- 字符串原语与 `lib/` 标准库初版。
- `--seed` 确定性执行、`--disasm` 反汇编、CIN `assert` 与越界检查。
- CIN 控制流（break/continue/do-while/switch/case/三目）、运算符、类型系统与内建函数扩展。
- 汇编器 `.equ` 与表达式支持，示例与编辑器语法文件。
- GitHub Actions CI 工作流（多 Python 版本、ruff、Go 原生库编译校验）。
- 指令集文档自动生成脚本 `script/gen_isa_docs.py` 与 `script/gen_native_isa.py`。

### 变更 (Changed)

- 测试/CI/argparse/打包/断点/调试器拆分/自动注册/内存保护/ISA 同步/原生单源等十项审查建议落地。
- 日志与控制台模块迁移至 `rich` 库，新增全链路调试日志与富控制台美化。
- Windows 打包脚本重构，使用 spec 文件并瘦身。

### 修复 (Fixed)

- CIN 标签操作数编码问题。
- Go 结果缓冲区偏移 panic。
- FCMP 指令 mask64 使用问题。
- 未知 SYS 号处理。

---

## [5.1.0] 及更早

- UCPU 模拟器完整工具链初始化（解释执行 / JIT / Go 原生三路径）。
- 模块化 Python 包结构（`codecin/`：硬件层、核心组件、加速工具）。
- Go 原生库（`codecin/native/`）：字节码 VM、CROM 打包解压。
- CROM 压缩格式（zlib）与 .bin 字节码编译。
- CIN / PL / ASM 三语言支持，Base / ARM64 / RISC-V / FP / Vector 指令集。
- LRU 缓存系统、性能分析、交互式调试器。
