# Code CIN 更新日志

本文件记录 Code CIN 项目的所有重要变更。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 规范，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [5.3.0] - 2026-09-11

项目正式重命名为 **Code CIN**，从 Python 优先架构切换为 **Go 优先** 架构。新增完整的 Go 原生 CIN 编译器与独立 CLI，
扩展 CIN 高级语言语法、官方标准库、跨平台宿主能力（2D 画布 / 联网音频 / 系统交互 / Termux API），
并引入 CROM v3 压缩格式、MMU 分页与远程调试服务。

### 新增 (Added)

#### Go 原生工具链
- **Go 版 CIN 编译器** (`codecin/native/compiler/`)：完整的词法分析、类型系统、语法分析器与代码生成，
  与 Python 编译器在 `examples/*.cin` 上输出等价，由 `script/diff_go_python.py` 差分校验
  （比较程序 stdout；两侧编译产物的字节级比对尚未覆盖）。
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
