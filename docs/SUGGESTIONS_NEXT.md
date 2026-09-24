# Code CIN (UCPU) 更新建议 — 第二轮

> 审查对象：`D:\ByUsi\Projects\UCPU`  
> 审查日期：2026-09-24  
> 环境：Windows / Python 3.14.6 / Go 1.26.5 / ruff 0.16.3  
> 方式：4 路并行代码审查 + 本机实测复现。**除标注「未复核」的条目外，每条结论都有实测输出或代码位置支撑。**  
> 上一轮报告见 [`docs/SUGGESTIONS.md`](SUGGESTIONS.md)（面向 5.4.0，已全部落地）。  
> 本轮**不重复**上一轮已修条目，只报新问题。  
> 使用AI工具辅助审查，确保建议的准确性和完整性。

---

## ⚠️ 两点必读说明

**1. 审查期间仓库 HEAD 变动过。** 起点 `017ccb8`（版本 5.4.2）→ 审查中 `git pull` 到
`5424540`（改 `pyproject.toml` 的 `packages.find`、新增 `setup.py`、版本升 5.4.3）→
收尾时本地提交 `c8c5af6`（删除 `install.ps1` / `install.sh` / `codecin.spec` /
`codecin_linux.spec`，新增 `build.bat` / `build.sh`）。因此 §5 的打包与安装结论
以 **`c8c5af6`** 为准；其余各节在两个版本上都成立。

**2. 本机沙箱限制（影响 3 个测试用例，不是仓库缺陷）。** 本机沙箱用受限令牌运行子进程，
`tempfile.mkdtemp()` 与 `os.chmod(dir, 0o700)` 会造出当前用户也访问不了的目录，
导致 `go build` 写 GOCACHE/临时目录被拒。因此：
- `tests/test_aot.py` 的 3 个用例失败（`CODECIN_AOT_TESTS=1` 的交叉编译用例本就跳过）；
- `python -m build` / `pip wheel` 无法在本机完整跑通（我用「构造等价站点包 + 直接驱动
  `setuptools.build_meta`」的方式取得打包证据）。

---

## 0. 摘要：先做这 6 件

| # | 问题 | 影响 | 位置 | 工作量 |
|---|------|------|------|--------|
| 1 | `lib/` 标准库不进 wheel/sdist | **`pip install` 后所有 `import "lib/*.cin"` 直接编译失败**；而 `build.bat`/`build.sh` 就是 `twine upload dist/*` | `pyproject.toml`、`codecin/cin.py:423` | 半天 |
| 2 | `--sandbox` 是死开关 | 文档承诺「限制宿主访问」，实测 `exec()` 照样执行、`file_write()` 照样写盘 | `cli.py:62/134`、`config.py:17` | 实现 ~40 行 / 删除 1 小时 |
| 3 | 平台原生库被打进 `py3-none-any` wheel | pip 会把 Linux `.so` 装到 Windows/macOS，然后静默回退纯 Python | `pyproject.toml:43-45`、`setup.py` | 半天 |
| 4 | 非恒定全局初始化器：Go 静默算 0，Python 报错 | **同一份源码两条路径结果不同且不报错** | `codegen.go:191-196` | ~10 行 |
| 5 | 条件断点用 `eval()` 求值 | 一行表达式即可在宿主 Python 进程内任意代码执行；`--debug-server` 无鉴权 | `debugger.py:182` | ~45 行 |
| 6 | 非 GBK 字符在 Windows 上直接崩 | `println("😀")` → `UnicodeEncodeError`，Python 路径退出 1、Go 路径正常 | `cpu.py:391` | ~5 行 |

另有 4 条「文档承诺 ≠ 实际行为」的硬伤，见 §5。

---

## 1. P0 — 会静默产生错误结果 / 两端不一致

### 1.1 非恒定全局初始化器：Go 静默丢弃，Python 报编译错误 ⭐

- **位置**：`codecin/native/compiler/codegen.go:191-196`（单值）、`:202-205`、`:213-217`（数组元素）
- **代码**：

  ```go
  if gv.init != nil {
      _, raw, err := c.constValue(gv.init)
      if err == nil {          // ← err != nil 时既不 failf 也不 continue 之外的任何处理
          ...append DataWrites...
      }
  }
  ```

  对照 Python `codecin/cin.py:1249-1268` 是直接调用 `self._const_value(...)`，而它在
  `cin.py:1245/1247` 会 `raise CompilerError`。Go 的 `constValue`（`codegen.go:151-185`）
  同样会返回 error，只是**这个 error 被丢掉了**，且 `emitGlobalsInit` 从不调用 `failf`
  （`failf` 定义见 `codegen.go:78-82`），所以粘性错误通道也救不回来。
- **实测**（最小复现 `float g = ~1.5`）：

  | 路径 | 命令 | 结果 |
  |------|------|------|
  | Python 编译器 | `python cpu.py t_global.cin` | `Compiler error: Bitwise NOT requires integer, got: float`，exit 1 |
  | Go CLI / 原生库 / **AOT 产物** | `codecin t_global.cin` | `g=0`，**exit 0** |

- **影响**：这是本项目最危险的类别——「同一程序两条路径结果不同，且错的那条不报错」。
  AOT 尤其吃亏：`--build-exe` 时 Python 只做编译编排，这条错误路径**完全走不到 Python**。
- **建议**：三处改为 `if err != nil { c.failf("%v", err); continue }`，并补一条双端一致性用例。

### 1.2 非 ASCII 字符串：`lib/str.cin` 的 `s_ends_with` 恒返回 0 ⭐

- **位置**：`lib/str.cin:24-31`，根因在「`strlen` 数**字节**、`substr`/`indexof` 数**字符**」的语义错配
  （`codecin/cpu.py:1216-1220` vs `:1284-1296`；Go 侧 `vm.go:867-872` vs `:964-995`）。
  **两端在这里是一致的（都是字符语义），所以不是差分问题，而是标准库本身算错。**
- **代码**：

  ```cin
  function s_ends_with(string s, string suffix) -> int {
      int sl = strlen(s)        // 字节数: "héllo" -> 6
      int fl = strlen(suffix)   // 字节数: "lo"    -> 2
      string tail = substr(s, sl - fl, fl)   // substr 按字符索引 -> runes[4:6] = "o"
      if (strcmp(tail, suffix) == 0) { return 1 }
      return 0
  }
  ```
- **实测**（`import "lib/str.cin"`，`s = "héllo"`）：`s_ends_with(s, "lo")` → **0**（应为 1）；
  `s_ends_with(s, "llo")` → **0**（应为 1）。纯 ASCII 时正常。
- **同类隐患**：`s_count`（`lib/str.cin:33-45`）把字符偏移与字节长度相加；
  `lib/io.cin:56-75` 的 `io_basename`/`io_dirname` 用字节循环配字符 `substr`。
  非 ASCII 路径/后缀一律取错。
- **建议**：二选一并两端统一——① 全部改**字节**语义（与 `strlen`、`s[i]`、`io_*` 的字节循环一致，
  改动最小）；② 全部改字符语义（则 `STRLEN`、`s[i]` 都要动）。
  另需补非 ASCII 的差分/回归用例（现有 `script/diff_go_python.py` 只跑 `examples/*.cin`，全 ASCII）。

### 1.3 非 GBK 字符在中文 Windows 上直接崩溃 ⭐

- **位置**：`codecin/cpu.py:391` `sys.stdout.write(text)`（`_emit_text`），未做编码兜底
- **实测**：

  ```
  # t_utf8.cin: println("emoji: 😀 箭头: →")
  python cpu.py t_utf8.cin   →  UnicodeEncodeError: 'gbk' codec can't encode character '\U0001f600'，exit 1
  codecin.exe t_utf8.cin     →  emoji: 😀 箭头: →，exit 0
  ```
- **补充证据（字节级）**：同一个含 `é` 的程序，`python cpu.py x.cin > out.txt` 写出的是
  **GBK 字节**（`68 A8 A6`），`codecin.exe x.cin > out.txt` 写出的是 **UTF-8 字节**（`68 C3 A9`）。
  即：即使不崩溃，两端产物也不是同一份字节，`diff_go_python.py` 的「产物等价」在非 ASCII 下不成立
  （它现在能过只是因为示例全是 ASCII）。
- **建议**：CLI 启动时把标准输出切成 UTF-8（`sys.stdout.reconfigure(encoding='utf-8', errors='replace')`，
  或对写入做 `errors='replace'` 兜底）。约 5 行，能同时消掉「崩溃」和「字节不等价」。

### 1.4 `input()` 被编译成常量 0 —— 文档承诺的功能从未实现

- **位置**：`codecin/cin.py:2466-2468`

  ```python
  if name == 'input':
      self.emit('MOV', self.reg(0), self.imm(0))
      return 'int'
  ```
- **实测**：`"42" | python cpu.py t_input.cin` → `a=0`（原生与 `--no-native` 都是 0）。
- **文档**：`docs/CIN_GUIDE.md:405` 写 `| input() | int | 读入一行并解析为整数 (失败为 0) |`。
  另外 `cpu.py:1391` 把 `self.input_buffer` 传给原生 VM，而 `input_buffer` 在 `cpu.py:121`
  初始化为 `""` 且**全仓无写入点**，原生 VM 的输入通道永远是空的。
- **影响**：程序照跑、结果恒定错——三引擎「一致地错」，任何差分测试都抓不到。
- **建议**：短期改成 `CompilerError("input() not implemented")` 并同步文档；正确做法是
  在启动时预读 stdin 到 `input_buffer` 并传给原生 VM（约 8-12 行），否则会引入新的三引擎分歧。

### 1.5 `sqrt(负数)`：原生返回 NaN，解释器抛 CPython 内部错误

- **位置**：`codecin/cpu.py:1182-1183`（`Syscall.SQRT` 直接用 `math.sqrt`，无定义域处理与异常转换）；
  同类风险 `POW`（`:1184-1185`）、`FLOOR/CEIL/ROUND`（`:1305-1311`）
- **实测**：

  | 路径 | 输出 | 退出码 |
  |------|------|--------|
  | 原生（默认） | `sqrt(-1)=NaN` | 0 |
  | `--no-native` | `Execution Error: expected a nonnegative input, got -1.0` | 1 |

  报错文案是 CPython `math` 的原话，既不是 CIN 语义（应写「平方根定义域」），也没有源码行号。
- **建议**：解释器侧与 Go 对齐返回 `NaN`，并给 `_op_sys` 的数学分支统一包一层，
  把内建异常转成 `ExecutionError`。约 15 行。

### 1.6 大全局数据：普通运行报错，AOT 静默丢数据

- **位置**：`codecin/aot.py:216-220`（`if addr >= 0 and end <= len(mem)` 越界即静默跳过）；
  Go 侧 `cmd/codecin/main.go:191-199` 同样
- **实测**：`int big[9000] = {1,2,3}`（数据段 72 KB > 默认 64 KB）

  | 路径 | 结果 |
  |------|------|
  | `python cpu.py t_big.cin`（原生） | `Load Error: Address 0x11940 out of bounds (memory size 0x10000)`，exit 1 |
  | `python cpu.py t_big.cin --no-native` | 同上，exit 1 |
  | `--build-exe` | 构建仍会走同一条 `build_program`（`aot.py:216` 静默截断） |

- **相关**：AOT 完全忽略运行期配置——`aot.py:28` 硬编码 `DEFAULT_MEM_SIZE = 65536`，
  `--mem-size` / `--max-instructions` / `--bounds-check` / `--sandbox` 与 `--build-exe` 同用
  既不报错也不生效（`aot.go:33` 另硬编码 `maxSteps = 100000000`）。
- **建议**：越界改为报错；把 `Config` 的相关字段透传进 AOT，或对不适用的 flag 显式警告。

---

## 2. P0 — 安全承诺失效

### 2.1 `--sandbox` 完全没有实现 ⭐

- **位置**：`codecin/cli.py:62-63`（定义）、`cli.py:134`（`config.sandbox_mode = ns.sandbox`）、
  `codecin/config.py:17`（字段）；`docs/BUILDING.md:126` 文档写「沙箱模式 (限制宿主访问)」
- **证据**：全仓 `grep sandbox_mode` 只有上面 2 处命中（定义 + 赋值），**没有任何读取点**。
- **实测**：

  ```
  # t_sandbox.cin: exec("echo SANDBOX_ESCAPED") + file_write("zz_probe_sandbox.txt", ...)
  python cpu.py t_sandbox.cin --sandbox
  → r=0 fw=0            # exec 真的执行了，file_write 真的返回成功
  → 文件被创建? True
  ```
- **影响**：教学场景里「跑一下别人给的 CIN」是最常见的用法，`--sandbox` 给出的是**虚假安全感**。
  同源的 `--no-io`（`cpu.py:561-583`）也只关掉 IN/OUT，宿主 SYS（`exec`/`file_*`/`getenv`/Termux）
  全部照跑。
- **建议**：二选一，不要留中间态。
  (a) 真做：在 `_op_sys` 入口对宿主能力类 SYS 拦截（`>= Syscall.FILEREAD`，含 `EXEC*`、`FILE*`、
  `GETENV/SETENV`、`TERMUX*`、`AUDIO*`），原生路径需要给 `codecin_run` 传 flags（约 40 行 + 测试）；
  (b) 先删：`argparse` 直接报「未实现」，同时删 `BUILDING.md:126` 与 `tests/test_cli.py:19`。

### 2.2 条件断点用 `eval()` 求值 → 宿主任意代码执行

- **位置**：`codecin/debugger.py:172-190`，核心 `debugger.py:182`：

  ```python
  if eval(bp.condition, {"__builtins__": {}}, namespace):
  ```
- **现象**：`__builtins__` 置空只挡名字查找，挡不住属性链。条件字符串来自
  `DebugSession._set_breakpoint`（`debugger.py:509-513`）与远程 `DebugServer` 的
  `break <addr> <expr>`（`debugger.py:244-251`）；`--debug-server` 只 bind localhost、**无任何鉴权**
  （`debugger.py:373-377`）。
- **影响**：任何能连上该端口的本地进程都能在宿主 Python 进程里执行任意代码——而这恰好绕过
  `--sandbox` 想拦的一切（两者叠加时尤其讽刺）。
- **建议**：换成白名单求值器（`ast.parse(expr, mode='eval')` + 只允许
  `Compare/BoolOp/UnaryOp/Name/Constant/BinOp`，名字限定 `{x0..x31, sp, pc, N,Z,C,V}`，
  拒绝 `Attribute/Subscript/Call`）。约 45 行，本地与远程共用一份。

---

## 3. P1 — AOT 与错误路径

### 3.1 `--build-exe` 的文档调用式在 Windows 上产出「跑不了的文件」
- `README.md:440` 的注释写 `# Windows 自动加 .exe`，但 `cli.py:178` 是
  `out = ns.build_exe or (... + aot.exe_suffix(goos))` —— **显式给了路径就不加 `.exe`**。
- **实测**：`python cpu.py examples/control_flow.cin --build-exe cf_aot` → 产出 `cf_aot`（无扩展名，
  6.5 MB）；`powershell -Command "& .\cf_aot"` 报「无法在管道中间运行文档」，
  `cmd /c cf_aot` 报「is not recognized as an internal or external command」。
  只有**省略** `--build-exe` 的值时才会得到 `cf2.exe`（实测通过）。
- **建议**：Windows 目标且 `out` 无扩展名时补 `.exe`（或把 README 注释改成准确描述）。

### 3.2 `--build-exe` 的错误处理不闭合：程序文件不存在直接吐裸 traceback

- **位置**：`cli.py:166-188`。`_run_aot_build` 从不检查 `program_file` 是否存在，
  `aot.build_program` → `CINCompiler().compile()` → `open()` 抛 `FileNotFoundError`，
  而 `cli.py:186` 只捕获 `(aot.AotError, CPUSimulatorError)`。
- **实测**：

  | 命令 | 结果 |
  |------|------|
  | `python cpu.py z:\nope.cin` | `Load Error` 面板，exit 1 |
  | `python cpu.py z:\nope.cin --build-exe out` | **Python 裸 traceback**（`FileNotFoundError` 全栈），exit 1 |

- **建议**：AOT 分支前复用同一套存在性检查；`stub_source()`（`aot.py:82-85`）的 `open` 同样没有兜底。

### 3.3 AOT 临时目录：建在源码模块内、清不掉也不吭声

- **位置**：`codecin/aot.py:150`（`os.path.join(mod, '.aotbuild-' + token)`）、
  `aot.py:197`（`shutil.rmtree(tmp, ignore_errors=True)`）；Go 侧 `native/aot/build.go:86-88`
  （`defer func(){ _ = os.RemoveAll(tmp) }()`）
- **实测证据**：`codecin/native/` 下现存 **6 个残留目录**，其中 3 个 `.aotbuild-*` + 2 个
  `.aotprobe-*` 来自 2026-09-13，**当前用户连读都读不了、`Remove-Item -Force` 也删不掉**：

  ```
  icacls codecin\native\.aotbuild-90n5wyqb  → Access is denied
  Remove-Item ... -Force -Recurse           → FAILED: 访问被拒绝
  Get-ChildItem -Recurse                    → 5 个目录报 Access is denied
  ```

  后果是任何遍历仓库的工具都会失败（`git status --ignored`、编辑器索引、我自己的目录枚举都被卡过）。
- **说明**：`aot.py:148-149` 的注释显示作者已经踩过 `mkdtemp` 的 0700 权限坑并绕开了它；
  但 `ignore_errors=True` 让「删不掉」这件事**完全静默**，于是残留在受限环境里持续累积。
- **建议**：① 临时目录改到系统 temp（配合权限规避）；② 清理失败至少 `logger.warning`；
  ③ 加一条测试断言「构建后模块目录无 `.aotbuild-*` 残留」；④ 清理历史残留并把
  `.aotbuild-*`/`.aotprobe-*` 纳入守卫。

### 3.4 原生库 ABI 不匹配时不是回退，而是崩掉整个运行

- **位置**：`codecin/native.py:314-336`，只 `except OSError`（`:330`）；
  `NativeEngine._configure()`（`native.py:184-207`）缺符号时抛 `AttributeError`
- **实测**：`$env:CODECIN_NATIVE_LIB='C:\Windows\System32\msvcrt.dll'; python cpu.py t.cin`
  → `Unexpected Error` + 完整 traceback，`AttributeError: function 'codecin_run' not found`，exit 1。
- **影响**：`native.py` 文件头明确承诺「库不存在或加载失败时自动回退纯 Python」。现实是
  旁边放一个旧 `codecin_native.dll`（**本仓库历史上真的发生过**，见上一轮 §2.1）就会让整个程序不可用，
  而且报错完全不提原生库，用户只能看到 `Unexpected Error`。
- **建议**：`except (OSError, AttributeError)` + `logger.warning("原生库不可用，回退纯 Python: ...")`。
  约 5 行。

---

## 4. P1 — Python 核心

### 4.1 JIT 绕过栈溢出/堆碰撞保护（唯一会静默出错的执行路径）
- `codecin/jit.py:168-173` 的 `PUSH` 生成 `cpu.sp = (cpu.sp - 8) & MASK` + 写内存，
  **没有**调用 `cpu._check_stack()`；而解释器的 `_push`（`cpu.py:362-369`）有
  `if self.sp < self.heap_ptr + 4096: raise ExecutionError("Stack overflow (collides with heap)")`。
  `PUSH`/`POP` 又在 `_JIT_OPS`（`jit.py:14-19`）里。
- **影响**：`--jit` 下深递归/大栈帧不会在碰撞前报错，栈帧会静默覆盖堆对象（或反之）→ 错误结果。
  三引擎里只有它「资源耗尽还继续跑」。
- **建议**：生成代码里插一行守卫，或把 `PUSH/POP` 移出 `_JIT_OPS`。**2 行，性价比最高。**

### 4.2 `--profile` 在 JIT/原生路径下大面积为 0
- `jit.py` 的访存直接调 `mem.read_*/write_*`，不经过 `cpu.stats.record_memory_*` 与 `cpu.cache.*`
  （对照解释器 `cpu.py:419-421`、`430-432`）；`cpu.py:1438-1448` 的 `_apply_native_state`
  只累加 `instruction_count`，并把 `opcode_count`/`cycles` 一律记到占位符 `'?'`。
- **影响**：`--profile` 的访存计数、缓存命中率恒为 0，指令分布退化成一个 `?` 行。
  而这三张表正是教学演示的主要交付物。
- **注意**：`tests/test_three_paths.py:83-84` 把 `opcode_count == {}` / `hot_instructions['?'] == steps`
  写成了断言——即「当前行为被测试固化」了。改行为需同时改测试。
- **建议**：JIT 补统计（~10 行）；原生至少让 `cycles` 有意义、不再用 `'?'` 污染直方图。

### 4.3 整数除零报成 "Float division by zero (SYS)"
- CIN 的 `/` 一律走浮点（`cin.py:2033`：`float_mode = (...) or op == '/'`），
  所以 `int x = 5 / z;`（z=0）报的是 `cpu.py:1198-1202` 的浮点文案，把人引向完全错误的方向；
  而 `5 % z` 和 `idiv(5, z)` 报的是正确的 `Division by zero`。
- **建议**：两侧都是 int 时改用 `DIV`（去掉 `cin.py:2033` 的 `or op == '/'`，并让 `_expr_type`
  的 `/` 返回 `'int'`）。顺带修掉一个更实质的精度问题：`int q = 7/3` 现在要经过
  ITOF→FDIV→FTOI 三步，大整数会丢精度。约 5 行。

### 4.4 `_sys_buffer()` 借用堆区，`malloc` 后可被静默覆盖
- `cpu.py:1160-1165` 的 `_sys_buffer` 取 `heap_ptr + 2048 + idx*64` 且**不推进** `heap_ptr`；
  而 `MALLOC`（`:1244-1250`）与 `_heap_dup_string`（`:1150-1158`）都从 `heap_ptr` 起分配。
- **影响**：`itoa/ftoa/bool_to_str` 返回的指针在后续一次 `malloc(≥2048)` 或字符串拼接后指向被覆盖的字节，
  读到错误字符串而不崩溃——最难排查的一类。
- **建议**：把静态缓冲移到内存顶端独立区（或也走堆分配语义）。

### 4.5 装载不可信 `.bin` 时按头部声明重分配内存，无上限
- `codecin/crom.py:208-219`：`mem_size` 直接读自 `<I`，随即 `f.read(mem_size)` 并 `resize`，
  没有像 CROM 分支（`crom.py:139-159` 已有 `CROM_MAX_TRAILER` 上限）那样设限。
- **影响**：一个声明 4 GiB 的 `.bin` 就能触发巨大分配 → OOM 而非友好报错。
- **建议**：`mem_size <= config.mem_size`（允许 `--mem-size` 放宽），超出报 `CPUSimulatorError`。约 6 行。

### 4.6 其余小项（各 2-6 行）
- **汇编器**：`assembler.py:35-44` 的 `_repl_num` 在非法字面量时 `return '0'`，
  于是 `MOV X0, #0xZZ` 静默变成 `MOV X0, #0` —— 与上一轮修掉的 `#0x1F` 是同一处的另一条残余路径。
- **旧 CROM 分支**：`crom.py:108-120` 只要首 4 字节解释成「不太大的 mem_size」就当成内存镜像，
  传错文件会静默继续执行（内存全 0）。
- **损坏文件报错质量**：`crom.py:213-214`、`native.py:128-148`、`disasm.py:38-51`、
  `_restore_mmu_trailer`（`crom.py:41-58`）的长度字段都不校验，截断文件会抛裸 `struct.error` 并打印全栈。

---

## 5. P1 — 打包、发布与文档（当前 HEAD `c8c5af6`）

### 5.1 `lib/` 标准库不进 wheel/sdist ⭐
- **配置**：`pyproject.toml` 只有 `[tool.setuptools.packages.find] include = ["codecin*"]` 与
  `package-data: codecin = [...]; codecin.native = ["**/*"]`；无 `MANIFEST.in`，无任何 `lib` 条目。
- **运行时依赖**：`codecin/cin.py:423` 用 `_CODECIN_ROOT = dirname(dirname(__file__))` 定位标准库，
  即「包目录的上一级」——安装后就是 `site-packages/lib`，而那里什么都没有。
- **实测**（构造等价站点包：只复制 `codecin/` 下的 `.py` 与平台二进制）：

  ```
  sys.path.insert(0, site); CINCompiler().compile("t_str.cin")   # 内含 import "lib/str.cin"
  → CompilerError: Import file not found: 'lib/str.cin'
     (searched ...\ucpu-verify, ...\site\lib, ...\site)
  ```
- **影响**：`pip install codecin` 后，19 个官方标准库全部不可用；
  `examples/modules_demo.cin`、`stdlib_demo.cin`、`docs/CIN_GUIDE.md` 里的示例全部失效。
  而 `release.yml:129` 正是 `python -m build`。
- **建议**：把标准库放进包内（`codecin/lib/` + 调整 `_CODECIN_ROOT`/`_resolve_import`），
  或 `data_files`/`MANIFEST.in`；CI 加「装 wheel 后跑 `modules_demo.cin`」冒烟
  （现在 `release.yml:131-135` 只跑 `codecin --version`，抓不到）。

### 5.2 平台原生库被打进 `py3-none-any` 纯 Python wheel
- `setup.py:69-76` 在 `build_py.run()` 里必调 `build_native_lib()`（跑 `build.ps1`/`build.sh`），
  产出平台 `.so/.dll/.dylib`；但 wheel 元数据仍是 `Root-Is-Purelib: true` / `Tag: py3-none-any`。
- **影响**：pip 会把 Linux `.so` 装到 Windows/macOS，然后 `native.py` 静默回退纯 Python
  （性能骤降且无提示）。文件名一旦上传 PyPI 不可回收。
- **建议**：自定义 `bdist_wheel` 设置 `has_ext_modules`/平台 tag，或 wheel 不带原生库、
  原生库只走 Release 资产。

### 5.3 删除的脚本仍被文档当成主路径教给用户
- `c8c5af6` 删除了 `install.ps1` / `install.sh` / `codecin.spec` / `codecin_linux.spec`，
  但文档还在教：

  ```
  README.md:379    bash install.sh
  README.md:381    powershell -ExecutionPolicy Bypass -File install.ps1
  README.md:386    `install.sh` / `install.ps1` 会自动：检测/安装 Go → ...
  BUILDING.md:66   ├── codecin.spec               # PyInstaller 打包配置
  BUILDING.md:334  唯一入口为 `codecin.spec` (Windows 下直接运行 `build_win.bat`)
  BUILDING.md:340  pyinstaller --noconfirm --clean codecin.spec
  ```
  且 `build_win.bat` 仍在仓库里，内容就是 `pyinstaller --noconfirm --clean codecin.spec` → 必然失败。
- **建议**：恢复脚本，或同步删除/重写这些文档段落与 `build_win.bat`。

### 5.4 `build.bat` / `build.sh` 名为 build，实为 `twine upload dist/*`
- 两个文件全文：

  ```bat
  @echo off
  twine upload dist/*
  ```
- **问题**：没有 `python -m build`、不校验 `dist/` 内容与 `__version__` 一致、无 dry-run、无 `--repository`。
  配合 §5.1/§5.2，**一条命令就能把「缺标准库 + 带错平台 .so」的 wheel 永久发到 PyPI**；
  若 `dist/` 里还有历史产物（上一轮报告记载过 `dist/ucpu/...`）会一并上传。
- **建议**：改名为 `publish.*`，上传前先 `python -m build` 并校验文件名含当前版本；
  正式发布交给 CI（tag 门禁 + trusted publisher）。

### 5.5 `--help` 首行版本号停在 5.3；CHANGELOG 缺 5.4.3
- `codecin/cli.py:15` 的 `HELP_INTRO` 写死 `'Code CIN v5.3'`：

  ```
  python cpu.py --help    → Code CIN v5.3
  python cpu.py --version → Code CIN 5.4.3
  ```
  这是全仓**唯一**残留的版本串（README/BUILDING/CIN_GUIDE 已无版本号，Go 侧
  `engine/version_gen.go` = 5.4.3，`gen_native_isa.py --check` 通过 → 单一真源本身是成立的）。
  改成 `f'Code CIN v{__version__}'` 即可（1 行）。
- `CHANGELOG.md` 最新条目是 `[5.4.2]`，全文件 0 处提到 5.4.3；而 `release.yml` 只比对
  tag 与 `__init__.py`，可以发布一个没有变更日志的版本。建议补条目 + CI 加「tag 版本必须出现在 CHANGELOG」。

### 5.6 其余发布链路问题
- **`release.yml` 的 `workflow_dispatch` 不 checkout 输入 tag**（`:20` 的 `actions/checkout@v4`
  没有 `ref:`）→ 资产由默认分支构建却以任意 tag 发布。
- **原生库资产无架构维度**：`matrix.os` 用 `macos-latest`（现已是 arm64），产物名固定
  `libcodecin_native.dylib` → Intel Mac 用户拿到 arm64 dylib；而 CLI 作业却有
  `darwin-amd64/arm64` 两套，命名体系不一致。
- **发布的原生库没带静态链接参数**：`release.yml:97/101/106` 直接 `go build -buildmode=c-shared`，
  而 `codecin/native/build.sh:31`、`docs/BUILDING.md:208` 的约定是
  「优先 `-linkmode external -extldflags -static`，失败回退」。发布物比本地构建更弱
  （`BUILDING.md:477` 的 FAQ 自己承认 Windows 上会加载失败）。
- **CI 从不构建 wheel/sdist**，release 也没有测试门禁 → §5.1/§5.2 这类缺陷只能等发布后由用户发现。
- **CI 把整套测试跑两遍**：`ci.yml:75` 一次全量，`:80-81` 又 `pip install pytest-cov` + 再跑一次全量
  （且第二次没有 `CODECIN_AOT_TESTS=1`，覆盖率数字与第一次口径不同）；
  而 `pytest-cov>=5` 早已在 `requirements-dev.txt:3`。建议第一次就带
  `--cov=codecin --cov-report=term-missing --cov-fail-under=70`。
  另外**所有作业都没有 `timeout-minutes`**，`ci.yml` 也没有 `permissions: contents: read`。
- **「被 .gitignore 吞掉的源码」守卫对唯一真实案例自废武功**：`ci.yml:41` 用
  `grep -v '/tmpdump/'` 豁免，而 `.gitignore:36` 正好忽略 `codecin/native/tmpdump/`；
  更糟的是 `package-data "codecin.native" = ["**/*"]` 现在会**把 tmpdump 连同其余 Go 源码打进 wheel**。

---

## 6. P2 — 文档与体验

- **三条文档命令实测跑不通**：
  1. `README.md:399` `go run ./codecin/native/cmd/codecin basic.cin`（在仓库根执行）→
     `go: cannot find main module, but found .git/config`（根目录没有 `go.mod`；正确写法是先 `cd codecin/native`）。
  2. `docs/BUILDING.md:227` `native.load_native_library()` → `AttributeError`（真实 API 是 `get_engine`）。
  3. `README.md:393/946` 的 clone 地址 `github.com/ByUsiStudio/codecin` 与实际 remote
     `byusistudio/code-cin` 不一致（**未复核**：本机网络抓取失败，无法确认是否 404）。
- **测试规模陈述漂移**：`CHANGELOG.md:11` 写「413 项」，实测 `pytest --collect-only` = **447 项**
  （本机全量：441 passed / 3 failed / 3 skipped，失败全是上面的沙箱原因）。
- **AOT 共用模板只有「存在性」守卫**：`tests/test_aot.py:97-105` 只 grep 字符串，
  不校验 Python 侧（`aot.py:156-159`）写出的文件名与模板 `//go:embed` 目标一致，
  一旦模板改名只能在 `go build` 阶段才暴露。
- **两端缓存回退判断不等价**：Go 用 `os.Getenv("GOCACHE") == ""`（`native/aot/build.go:138`），
  Python 用 `'GOCACHE' not in os.environ`（`aot.py:113`）——显式设成空串时两端行为不同。1 行。
- **编译错误文案两端不一致**：`codegen.go:963` `"Cannot assign to string index"` vs
  `cin.py:1862-1863` `"Cannot assign to string element (strings are immutable)"`，
  而 `codegen.go:76-77` 的注释宣称「错误文案与 Python 编译器保持一致」。
- **`diff_go_python.py` 验证不了「解释器 vs 原生 VM」**：它的 Python 侧也是 `use_native=True`
  （`script/diff_go_python.py:30`），即两边跑的是**同一个 Go VM**，只能比编译器产物。
  而 `tests/test_three_paths.py` 用的是一段 9 条指令、无 SYS 的手写程序，
  且把 `opcode_count == {}` / `hot_instructions['?']` 写成断言。
  **「三路径一致」这个核心卖点目前几乎没有有效覆盖**——本轮实测出的 §1.5（sqrt）就是它漏掉的类型。

### 6.1 已检查、未发现新问题（供你跳过复查）

- **标准库空集合边界**：`a_sum/a_max/a_min/a_find/a_count/a_lower_bound`（`lib/array.cin`）、
  `stat_sum/min/max/median_sorted/mode/percentile_sorted/variance_x1000`（`lib/stat.cin`）、
  `bin_search`（`lib/sort.cin`）在 `n = 0` 与 `n = 1` 下**原生与解释器结果一致且合理**
  （全部返回 0 / -1，无越界、无崩溃），`bin_search`（`lib/sort.cin:80-93`）的边界写法正确。
- **ISA 门禁**：`script/gen_isa_docs.py --check`、`script/gen_native_isa.py --check` 均通过；
  版本单一真源（`codecin/__init__.py` → `engine/version_gen.go`）本身成立，唯一残留是 §5.5 的 `HELP_INTRO`。
- **产物级差分**：`python script/diff_go_python.py` → **6/6 passed**，
  6 个示例的 UCBC 产物 5637–211164 字节**逐字节一致**（ASCII 范围内）。

---

## 7. 三个「未决问题」的结论

上一轮 `SUGGESTIONS.md` 留了 6 个待你决定的问题，本轮给出可执行结论：

| 问题 | 结论 | 依据 |
|------|------|------|
| `requires-python >= 3.8` 下界 | **提到 `>= 3.9`** | `ast.parse(feature_version=(3,8))` 扫 57 个 `.py` 零语法错误（代码本身 3.8 干净），但 CI 矩阵是 3.9/3.11/3.13，且 3.8 已 EOL；3.8 上 `pytest>=7` 会解析到很旧的 8.3.x，与 3.13 腿差异过大。若坚持 3.8，必须加 `runs-on: ubuntu-22.04` 的 3.8 矩阵腿。 |
| `codecin/native/tmpdump/` | **删除** | 功能已被 `codecin --dump-bytecode`、`python cpu.py --disasm` 与 `compiler`/`engine` 两个 go test 包覆盖；无参数校验（`os.Args[1]` 越界 panic）；现在还会被打进 wheel/sdist；并让 CI 守卫永久豁免。删时同步删 `.gitignore:36` 与 `ci.yml:39/41`。 |
| 覆盖率门槛 | **`--cov-fail-under=70` 起步** | 实测基线 **75%**（6180 stmts / 1559 missed；最低：`debugger 40%`、`stats 49%`、`logger 54%`、`registers 56%`、`cpu 59%`）。留出 Linux/原生路径差异余量，稳定后提到 72-73%，不要直接设 75。 |
| `go.mod` 降到 1.21 | **可以降** | 把 `go.mod` 改成 `go 1.21` 后 `go vet ./...` = 0、`go test ./...` 全绿（compiler 3.7s / engine 10.1s）。建议降级并在脚本加 `go env GOVERSION` 校验（对 1.20 及更老），或保留 1.26 但明确说明 `GOTOOLCHAIN=auto` 会自动下载、需要联网。 |
| 解释器统计记账 | 见 §4.2 | 仍是解释执行的性能大头，但改口径要同步改 `tests/test_three_paths.py`。 |
| 原生调用边界拷贝 | 未处理 | 需改 ABI，风险高，本轮未涉及。 |

---

## 8. 建议的落地顺序

**第一批（半天内，全是「静默错误」或「安全承诺」）**

1. §1.1 全局初始化器 `failf`（~10 行）— 唯一「静默算错且两端不一致」的编译期缺陷
2. §2.1 `--sandbox` 删掉或实现（1 小时 / 半天）— 安全承诺
3. §2.2 断点 `eval` 白名单化（~45 行）— 安全承诺
4. §1.3 stdout 编码兜底（~5 行）— Windows 中文环境下直接崩
5. §3.4 原生库回退捕获 `AttributeError`（~5 行）
6. §4.1 JIT `PUSH` 补栈检查（**2 行**）

**第二批（1-2 天，「同一程序两条路径结果不同」+ 打包）**

7. §1.2 字符串字节/字符语义统一 + 非 ASCII 用例（~20 行 + 3 用例）
8. §1.5 `sqrt` 等数学域错误对齐（~15 行）
9. §5.1 `lib/` 进包 + CI 装 wheel 后跑 `modules_demo.cin`（半天）
10. §5.4 `build.*` 改为真正的构建+校验，或直接改由 CI 发布（1 小时）
11. §3.1/§3.2 `--build-exe` 的 `.exe` 与错误处理（~20 行）

**第三批（收尾与治理）**

12. §5.2 wheel 平台 tag、§5.6 release 的 tag/架构/静态链接、CI 超时与权限
13. §5.3 文档与已删脚本对齐、§5.5 版本串与 CHANGELOG、§6 的三条跑不通的命令
14. §4.2-§4.6 统计口径、除零文案、`_sys_buffer`、`.bin` 上限、汇编器残余静默
15. §7 的四个未决问题（`requires-python`、tmpdump、覆盖率门槛、go.mod）

---

## 9. 环境中未能核实的部分

- **GitHub 仓库地址是否 404**：本机 `web_fetch` 失败，只确认了与 remote `byusistudio/code-cin` 不一致。
- **`twine upload dist/*` 在 cmd.exe 下是否靠 twine 内部 glob 生效**：本机未装 twine；
  但「无构建、无版本校验」由脚本内容确定。
- **干净 CI 上 `python -m build` 能否稳定产出原生库**：本机沙箱禁止 `mkdtemp`/`chmod 0700`，
  cgo 构建路径跑不通，只能确认 wheel 的内容清单（§5.1/§5.2）。
- **覆盖率数字**来自 Windows/py3.14 且跳过 AOT 交叉编译用例，Linux CI 数值可能略有出入。
- **`namespaces = true` 的边界**：本次构建未发现 `script/`、`examples/` 被当成命名空间包收进 wheel，
  但本机目录布局不完整，未穷尽。
- **原生 VM 的输出总量上限**与**原生 VM 是否尊重「禁止 I/O」标志**：
  `codecin_run` 的签名（`native.py:186-192`）里没有任何 flags 参数，
  因此 `--no-io` 在原生路径下**结构上不可能生效**；其余需读 Go 侧才能定论。

---

## 10. 本轮实测命令（可复现）

```powershell
# 基线
python -m pytest -q                                   # 441 passed / 3 failed / 3 skipped（3 个失败是本机沙箱）
python script/diff_go_python.py                       # 6/6 passed，产物 5637–211164 字节逐字节一致
python script/gen_native_isa.py --check               # 通过
python script/gen_isa_docs.py --check                 # 通过

# §1.1 全局初始化器
python cpu.py t_global.cin                            # Compiler error: Bitwise NOT requires integer, got: float
codecin.exe t_global.cin                              # g=0   ← 静默错误

# §1.2 / §1.3 字符串与编码
python cpu.py t_str.cin                               # ends_with(s,lo)=0   ← 应为 1
codecin.exe t_str.cin                                 # ends_with(s,lo)=0
python cpu.py t_utf8.cin                              # UnicodeEncodeError 'gbk'，exit 1
codecin.exe t_utf8.cin                                # 正常输出，exit 0

# §1.5 sqrt
python cpu.py t_sqrt.cin                              # sqrt(-1)=NaN
python cpu.py t_sqrt.cin --no-native                  # Execution Error: expected a nonnegative input, got -1.0

# §2.1 sandbox
python cpu.py t_sandbox.cin --sandbox                 # r=0 fw=0；文件真的被创建

# §3.1 / §3.2 AOT
python cpu.py examples\control_flow.cin --build-exe cf_aot    # 产出 cf_aot（无 .exe），cmd/PowerShell 都跑不起来
python cpu.py z:\nope.cin --build-exe out                      # 裸 FileNotFoundError traceback

# §3.3 残留目录
Get-ChildItem codecin\native -Force -Directory -Filter '.aot*' # 6 个残留，5 个 Access is denied
icacls codecin\native\.aotbuild-90n5wyqb                       # Access is denied
Remove-Item codecin\native\.aotbuild-90n5wyqb -Force -Recurse  # 失败：访问被拒绝

# §3.4 原生库 ABI
$env:CODECIN_NATIVE_LIB='C:\Windows\System32\msvcrt.dll'; python cpu.py t.cin
                                                      # AttributeError: function 'codecin_run' not found

# §4.4 input()
"42" | python cpu.py t_input.cin                      # a=0

# §6.1 标准库空集合边界（原生与解释器各跑一遍，结果一致）
python cpu.py t_libedge.cin                           # n=0 全部返回 0 / -1，无异常
python cpu.py t_libedge.cin --no-native               # 同上

# §5.1 /packaging（等价站点包）
python -c "sys.path.insert(0,'site'); from codecin.cin import CINCompiler; CINCompiler().compile('t_str.cin')"
                                                      # Import file not found: 'lib/str.cin'

# §5.3 / §5.5 文档与版本
python cpu.py --help                                  # 首行 Code CIN v5.3
python cpu.py --version                               # Code CIN 5.4.3
Select-String CHANGELOG.md -Pattern '5\.4\.3'         # 0 命中
```

**产出**：本报告（`docs/SUGGESTIONS_NEXT.md`）。审查期间未修改任何被跟踪文件；
`git status` 全程干净。
