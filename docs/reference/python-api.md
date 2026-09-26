---
description: 把 Code CIN 当作 Python 库使用: 包导出、Config 全部字段、CPU 构造与运行、异常层次、原生/JIT/AOT/CROM/反汇编/统计入口。
---

# Python 嵌入 API

Code CIN 除了命令行工具, 也可以直接当作 Python 库嵌入到自己的程序里: 构造一个
`Config`, 交给 `CPU`, 然后调用 `run()` 或逐条 `step()`。整条工具链 (汇编器、CIN
编译器、Go 原生 VM 桥接、CROM 读写、反汇编、统计) 都是公开可调用的。

本页所有签名均以 5.5.0 源码为准: 包导出见 `codecin/__init__.py`, 配置字段见
`codecin/config.py`, 运行时见 `codecin/cpu.py`。

## 顶层导出

`from codecin import ...` 可以拿到下面这些名字 (即 `codecin.__all__`, 见
`codecin/__init__.py`):

| 名称 | 类型 | 说明 |
|------|------|------|
| `CPU` | class | CPU 核心: 加载程序、执行、调试、状态查看 |
| `Config` | dataclass | 运行配置, 全部字段见下表 |
| `Opcode` | `enum.Enum` | 112 条指令的枚举, 值即 UCBC 编码 |
| `Constants` | class | 常量集合: 寄存器数/内存默认值/`OPCODE_NAMES`/`ARG_COUNTS` 等 |
| `Syscall` | `enum.IntEnum` | SYS 功能号 0–79 |
| `CPUSimulatorError` | exception | 所有 Code CIN 错误的基类 |
| `AssemblerError` | exception | 汇编阶段 (`.pl` / `.asm`) 错误 |
| `CompilerError` | exception | CIN 编译阶段错误 |
| `ExecutionError` | exception | 指令执行阶段错误 |
| `MemoryAccessError` | exception | 内存越界 / 保护违例 |
| `__version__` | str | 版本号单一真源 (`"5.5.0"`) |

```python
import codecin
from codecin import CPU, Config, Opcode, Constants, Syscall

print(codecin.__version__)            # 5.5.0
print(len(list(Opcode)))              # 112
print(Constants.OPCODE_NAMES[Opcode.SYS])   # 'SYS'
print(Syscall.PRINT_STR)              # 24
```

::: warning `PageFaultError` 不在顶层导出
`codecin/errors.py` 里还有一个 `PageFaultError` (MMU 缺页, `MemoryAccessError`
的子类), 但它**没有**出现在 `codecin/__init__.py` 的导出表里。要捕获它请显式
`from codecin.errors import PageFaultError`。
:::

其余子系统需要按模块导入 (它们都是包的公开模块, 只是不在顶层短名单里):

```python
from codecin import native, aot, crom, disasm, stats, jit, memory, registers
from codecin.cin import CINCompiler
from codecin.assembler import Assembler
from codecin.errors import PageFaultError
```

## `Config` 全字段

`Config` 是一个 `@dataclass` (`codecin/config.py`), 所有字段都有默认值, 因此
`Config()` 就是"命令行什么都不传"的行为。`CPU` **不会**替你调用
`Config.validate()` —— 需要夹取取值范围时自己调一次。

| 字段 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `mem_size` | `int` | `64 * 1024` | 内存字节数 (默认 65536) |
| `stack_size` | `int` | `1024` | 栈槽数 (配置项, 默认 1024 槽) |
| `step_mode` | `bool` | `False` | 交互式单步模式 (与原生/JIT 互斥) |
| `debug_mode` | `bool` | `False` | 超详细调试: 强制 `log_level='DEBUG'`, 关闭原生与 JIT |
| `auto_save_crom` | `bool` | `False` | 运行结束后保存 `.crom` 镜像 |
| `execution_interval` | `float` | `0.0` | 每条指令之间的休眠秒数 (演示减速) |
| `max_execution_time` | `float` | `60.0` | 墙钟执行上限 (配置项) |
| `interactive_mode` | `bool` | `True` | 是否允许交互式会话 (关掉才可能走紧凑解释循环) |
| `sandbox_mode` | `bool` | `False` | 沙箱模式 |
| `max_instructions` | `int` | `100_000_000` | 指令数上限, 超出按失败处理 |
| `allow_io` | `bool` | `True` | 是否允许 `IN` / `OUT` 宿主 I/O |
| `log_level` | `str` | `'INFO'` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `log_file` | `Optional[str]` | `None` | 日志文件路径 (非空则同时写入文件) |
| `show_memory_bytes` | `int` | `32` | 状态显示时转储的内存字节数 |
| `show_vector_regs` | `bool` | `True` | 状态显示是否包含向量寄存器 |
| `show_timings` | `bool` | `True` | 是否显示耗时统计 |
| `strict_mode` | `bool` | `False` | 汇编器严格模式 |
| `output_file` | `Optional[str]` | `None` | `.bin` / `.crom` 输出路径 |
| `optimize` | `int` | `0` | 优化级别 0–3 |
| `enable_jit` | `bool` | `False` | 启用 Python JIT (基本块编译) |
| `use_native` | `bool` | `True` | 允许使用 Go 原生库加速 |
| `cache_size` | `int` | `64` | 缓存行数 |
| `cache_assoc` | `int` | `4` | 缓存组相联度 |
| `profile` | `bool` | `False` | 结束后输出性能统计表 |
| `compress_crom` | `bool` | `True` | `.crom` 是否 zlib 压缩 |
| `compile_to_bin` | `bool` | `False` | 编译为 `.bin` 后继续执行 |
| `compile_only` | `bool` | `False` | 只编译不执行 |
| `seed` | `Optional[int]` | `None` | 确定性随机种子 (`None` = 随机) |
| `bounds_check` | `bool` | `False` | CIN 数组越界/断言检查 (关闭原生路径) |
| `mmu` | `bool` | `False` | 启用 MMU 分页 (关闭原生路径) |
| `debug_server_port` | `Optional[int]` | `None` | 远程调试端口 (`None` = 不启动) |

`Config.validate()` 会把越界值夹回合法区间: `mem_size < 256 → 256`,
`max_instructions < 1 → 1`, `optimize` 夹到 `0..3`, `cache_size < 8 → 8`。

```python
from codecin import CPU, Config

cfg = Config(
    mem_size=128 * 1024,
    max_instructions=5_000_000,
    seed=1234,                 # 确定性 rand()
    log_level='ERROR',
    interactive_mode=False,    # 嵌入式场景必须关掉交互
)
cfg.validate()
cpu = CPU(cfg, 'program.cin')
cpu.run()
print('failed =', cpu.execution_failed)
```

::: tip 嵌入式场景的两个必设项
`interactive_mode=False` 与 `log_level='ERROR'`。前者避免程序命中断点/单步时阻塞在
`input()` 上, 后者避免 rich 表格污染你自己的 stdout。需要捕获程序输出时, 见下面的
`output_buffer`。
:::

## `CPU` 构造与方法

```python
CPU(config: Config,
    filename: Optional[str] = None,
    crom_file: Optional[str] = None,
    from_bin: bool = False,
    console: Optional[Console] = None)
```

| 参数 | 说明 |
|------|------|
| `config` | 运行配置, 必填 |
| `filename` | 程序路径。按扩展名分派: `.cin` → CIN 编译器, `.bin` / `from_bin=True` → UCBC 字节码, `.pl` / `.asm` → 汇编器 |
| `crom_file` | 汇编路径下要预加载的 `.crom` 内存镜像; 传 `None` 时会尝试同名 `.crom` |
| `from_bin` | 强制按 `.bin` 解释 `filename` (扩展名不是 `.bin` 时也有用) |
| `console` | 自定义 `codecin.console.Console`; 默认新建一个 |

构造时如果 `filename` 非空, 会立即调用 `load_program()` 完成加载。也可以在
`filename=None` 构造空 CPU, 然后手工灌入指令 (测试辅助 `tests/helpers.py: new_cpu()`
就是这么做的)。

### 加载与执行

| 方法 / 属性 | 说明 |
|-------------|------|
| `load_program(filename, crom_file=None, from_bin=False)` | 重新加载程序; 文件不存在抛 `CPUSimulatorError` |
| `run()` | 按 `Config` 决定路径执行, 内部捕获异常并置 `execution_failed` |
| `execute(opcode: str, args: list) -> bool` | 执行一条指令; 返回 `False` 表示 `HALT` |
| `step() -> bool` | 执行 `pc` 处的一条指令; `pc` 越界返回 `False` |
| `display_state(title='CPU State', opcode=None, args=None)` | 渲染寄存器/内存/栈状态面板 |
| `add_breakpoint(addr)` / `remove_breakpoint(addr)` | 增删断点 |
| `debug_command_loop()` | 进入交互式调试会话 (断点命中时由 `run()` 调用) |

### 实例状态 (嵌入式最常读的几个)

| 属性 | 说明 |
|------|------|
| `pc` / `sp` / `heap_ptr` | 程序计数器 / 栈指针 / 堆指针 |
| `regs` | `RegisterFile`, 读 `cpu.regs.read(n)`, 全量 `cpu.regs.get_all()` |
| `vec_regs` | `VectorRegisterFile`, `read_vector(n)` / `get_all()` |
| `pstate` | `{'N': bool, 'Z': bool, 'C': bool, 'V': bool}` |
| `memory` | `FastMemory` 实例 (读写/保护/MMU) |
| `cache` | `Cache` 实例, `get_stats()` 给出命中率 |
| `stats` | `Statistics` 实例, 见下文 |
| `instructions` / `labels` / `data_labels` / `entry_pc` | 已加载的程序与符号表 |
| `native_used` | 本次执行是否真的走了 Go 原生 VM |
| `execution_failed` | 执行期是否发生过错误 (CLI 用它决定退出码) |
| `output_buffer` / `_capture_output` | 输出重定向缓冲 (见下) |

```python
from codecin import CPU, Config

cpu = CPU(Config(use_native=False, interactive_mode=False, log_level='ERROR'),
          'examples/control_flow.cin')
cpu._capture_output = True          # 把 OUT / SYS 打印收进 output_buffer
cpu.run()
text = ''.join(cpu.output_buffer)
print(text)
print('pc=0x%x sp=0x%x failed=%s native=%s'
      % (cpu.pc, cpu.sp, cpu.execution_failed, cpu.native_used))
```

::: warning `run()` 不抛异常
`CPU.run()` 内部 `try/except` 掉所有 `Exception`, 写日志并置
`execution_failed = True`。**判断成功要看 `cpu.execution_failed`**, 而不是等异常。
真的需要异常请用 `execute()` / `step()` 手工驱动, 或直接调用底层模块。
:::

## 异常层次

```
Exception
└── CPUSimulatorError                 # 基类: message + detail
    ├── AssemblerError                # 附加 line / line_num / filename
    ├── CompilerError                 # 附加 line_num / filename
    ├── ExecutionError                # 除零、未实现指令、栈溢出、SYS 未知号
    └── MemoryAccessError             # 越界 / 保护违例
        └── PageFaultError            # MMU 缺页 / 物理越界
```

| 异常 | 触发条件 |
|------|----------|
| `CPUSimulatorError` | 文件不存在、`.crom` 头非法、版本或校验和不符、字节码 magic 错误 |
| `AssemblerError` | 汇编语法错误、未定义标签/符号、操作数个数不符; 消息形如 `file:line: Assembler error: <原文> -- <详情>` |
| `CompilerError` | CIN 词法/语法/语义错误 (如 `Unknown function`、参数过少、非恒定 `case`) |
| `ExecutionError` | `Division by zero`、`Unimplemented instruction: X`、`Stack overflow (collides with heap)`、`instruction limit reached (N steps)`、`Runtime abort: <msg>` |
| `MemoryAccessError` | 地址越界、对只读页写入、保护位不允许该访问 |
| `PageFaultError` | 访问 `unmap` 过的页, 或映射到的物理地址超出内存大小 |

```python
from codecin import CPU, Config
from codecin.errors import (
    CPUSimulatorError, ExecutionError, MemoryAccessError, PageFaultError,
)

try:
    cpu = CPU(Config(mmu=True), 'prog.cin')
    cpu.memory.mmu.unmap(0x1000)        # 手工制造缺页
    cpu.memory.read_qword(0x1000)
except PageFaultError as e:
    print('缺页:', e)
except MemoryAccessError as e:
    print('访问违例:', e)
except CPUSimulatorError as e:
    print('其它 Code CIN 错误:', e.message, e.detail)
```

## Go 原生库: `codecin.native`

`codecin/native.py` 是 ctypes 桥接层。库文件按
`codecin/native/` → 包目录 的顺序查找, 先试架构专属名
(`libcodecin_native-linux-x64.so`), 再试通用名 (`libcodecin_native.so`); 也支持
环境变量 `CODECIN_NATIVE_LIB` 强制指定。

| 接口 | 说明 |
|------|------|
| `get_engine(logger=None) -> Optional[NativeEngine]` | 查找并加载原生库; 失败返回 `None` (结果被缓存, 只尝试一次) |
| `NativeEngine.version()` | 库自报版本字符串, 如 `codecin-native 5.5.0 (Go)` |
| `NativeEngine.run(...)` | 整程序字节码执行, 返回结果字典 |
| `NativeEngine.crom_pack(mem, compress)` / `crom_unpack(data)` | CROM 打包/解包 |
| `encode_program(instructions, entry=0, labels=None) -> bytes` | 指令元组列表 → UCBC 字节码 |
| `decode_program(data) -> (instructions, entry)` | UCBC 字节码 → 指令元组列表 |

```python
from codecin import native

engine = native.get_engine()
print(engine)                       # None 表示回退纯 Python
if engine is not None:
    print('native version:', engine.version())

# 手工编码并执行一个 9 条指令的小程序
prog = [
    ('MOV', [('reg', 0), ('imm', 21)]),
    ('MUL', [('reg', 0), ('imm', 2)]),
    ('HALT', []),
]
bc = native.encode_program(prog, entry=0)
res = engine.run(bytecode=bc, mem=bytes(64 * 1024), entry=0,
                 sp=0xFFF8, heap_base=0x8000, input_data=b'', max_steps=10_000)
print(res['status'], res['halted'], res['regs'][0])   # 0 True 42
```

::: info 路径一致性对嵌入者的含义
"三路径一致" 不是文档口号, 而是可执行的验收条件 (`script/check_paths.py`、
`tests/test_three_paths.py`): 同一份 UCBC 字节码在**解释器 / JIT / Go 原生 VM**
下必须给出相同的寄存器、内存、输出与退出状态。对嵌入者的实际含义是:

- 你可以放心用 `Config(use_native=True)` 默认走原生 VM 拿性能, 只在需要
  `--debug` / `--step` / `bounds_check` / `mmu` 时才退回解释器, **语义不变**;
- 允许的差异只有两类: 与时间/环境相关的输出 (`time()`、`cwd()`、主机名), 以及
  Python `math` 与 Go `math` 在超越函数上的末位舍入 (通常 ≤ 1 ulp);
- 因此**不要**用"换条路径"来解释行为差异 —— 那是 bug。回归时先跑
  `python script/check_paths.py`, 它会对 `examples/*.cin` 逐字节比较三条路径的 stdout。
:::

## AOT: `codecin.aot`

`codecin/aot.py` 把 CIN 程序编译成**独立静态可执行文件** (内嵌 UCBC 字节码与初始
内存镜像, 由内置 Go VM 执行, 运行时不需要 Python / Go / 动态库)。它只负责编排,
真正的入口 shell 是与 Go 侧共用的 `codecin/native/aot/stub_main.go.txt`。

| 接口 | 签名要点 |
|------|----------|
| `build_program(program_file, out=None, target=None, keep_temp=False, mem_size=None, logger=None) -> str` | 由 `.cin` 源文件一键构建, 返回产物绝对路径 |
| `build(bytecode, mem_image, out, target=None, keep_temp=False, logger=None) -> str` | 底层入口, 直接吃字节码与内存镜像 |
| `program_dependencies(program_file) -> List[str]` | 依赖闭包 (含自身, 去重保序) |
| `host_target() -> str` | 当前平台的 `os/arch` |
| `supported_targets() -> List[str]` | 常用交叉编译目标列表 |
| `AotError` | 构建失败 (非 `CPUSimulatorError` 子类) |

```python
from codecin import aot

print(aot.host_target())                  # 例如 'windows/amd64'
print(aot.supported_targets())            # ['windows/amd64', 'linux/arm64', ...]

exe = aot.build_program('examples/control_flow.cin',
                        out='demo', target='linux/amd64')
print('产物:', exe)                        # 绝对路径; Windows 目标自动补 .exe
```

`build_program` 会先做依赖完整性检查 (缺失或循环 → `AotError`), 再把全部依赖在编译期
展开嵌入产物, 所以产物运行时不读取任何 `.cin`。交叉编译需要本机 Go 工具链。
详见 [AOT 独立可执行文件](/runtime/aot)。

## CROM 与 `.bin`: `codecin.crom`

| 接口 | 说明 |
|------|------|
| `save_crom(memory, path, compress=True, logger=None)` | 把 `FastMemory` 写成 CROM v3 镜像 (有原生库时走 Go 打包, 否则 zlib) |
| `load_crom(memory, path, logger=None, enable_mmu=False)` | 读回镜像; `enable_mmu=True` 时同时恢复 MMU 页表尾部元数据 |
| `save_bin(cpu, path, logger=None)` | 把 CPU 的指令 + 内存镜像写成 CPUSA `.bin` 容器 |
| `load_bin(cpu, path)` | 读回 `.bin`, 重建 `instructions` / `pc` / `sp` (内存不足时自动 `resize`) |

```python
from codecin import CPU, Config, crom

cfg = Config(interactive_mode=False, log_level='ERROR')
cpu = CPU(cfg, 'examples/control_flow.cin')
cpu.run()

crom.save_crom(cpu.memory, 'snapshot.crom', compress=True)
crom.save_bin(cpu, 'snapshot.bin')

# 读回 CROM 到一个新 CPU
cpu2 = CPU(Config(interactive_mode=False, log_level='ERROR'))
crom.load_crom(cpu2.memory, 'snapshot.crom')

# 读回 .bin (直接当作程序加载也可以: CPU(cfg, 'snapshot.bin'))
cpu3 = CPU(Config(interactive_mode=False, log_level='ERROR'))
crom.load_bin(cpu3, 'snapshot.bin')
print(len(cpu3.instructions), hex(cpu3.pc))
```

格式细节 (头布局、flags、CRC32、MMU 尾部) 见 [二进制格式](/runtime/formats)。

## 反汇编: `codecin.disasm`

`disassemble_file()` 接受两种输入: CPUSA 容器 (`.bin`, `--compile-only` 的产物) 或
裸 UCBC 段, 返回按行拆分的文本清单 (`List[str]`)。

```python
from codecin import crom, disasm
from codecin import CPU, Config

cpu = CPU(Config(), 'examples/control_flow.cin')
crom.save_bin(cpu, 'prog.bin')

for line in disasm.disassemble_file('prog.bin')[:12]:
    print(line)
```

同模块还导出 `disassemble_bytes(data)` (直接吃 `bytes`) 与 `_extract_from_cpusa(data)`
(解析容器头, 返回 `(bytecode, mem_size, entry, sp)` 或 `None`)。命令行等价物是
`codecin prog.bin --disasm`, 见 [命令行参考](/guide/cli)。

## 统计: `codecin.stats`

`CPU.stats` 是一个 `Statistics` 实例。注意原生 VM 路径**不按指令逐个记账**
(一次性批量写入), 所以原生执行后 `opcode_count` 是空的, 只有总数可靠。

| 成员 | 说明 |
|------|------|
| `instruction_count` | 已执行指令总数 |
| `opcode_count` | `defaultdict(int)`, 各助记符执行次数 (解释/JIT 路径) |
| `execution_time` | `start()` / `stop()` 之间的墙钟秒数 |
| `memory_reads` / `memory_writes` | 内存读写次数 |
| `get_hot_instructions(top_n=10)` | 最热的指令列表 |
| `inst_profiler` | `InstructionProfiler`: `cycles` 与 `latency` 表, `get_total_cycles()` |
| `performance_counters` | `PerformanceCounters`: `get_stats()` 给出 IPC / 分支准确率 / 缓存命中率 |
| `display_summary(console, cache_stats=None, jit_stats=None, native_used=False)` | 渲染统计表 |

```python
from codecin import CPU, Config

cpu = CPU(Config(use_native=False, interactive_mode=False, log_level='ERROR',
                 profile=True))
cpu.run()
print('instructions =', cpu.stats.instruction_count)
print('exec time    = %.4fs' % cpu.stats.execution_time)
print('top hot      =', cpu.stats.get_hot_instructions(5))
print('cache        =', cpu.cache.get_stats())
print('perf         =', cpu.stats.performance_counters.get_stats())
```

## JIT: `codecin.jit`

JIT 把**无分支基本块**动态编译成 Python 函数并缓存, 适合没有 Go 工具链、又想比纯解释
快一些的场景。它由 `Config(enable_jit=True)` 经 `CPU.run()` 自动启用, 也可以直接调用。

| 接口 | 说明 |
|------|------|
| `JITCompiler(logger=None)` | 构造; `blocks` / `block_ranges` 为内部缓存 |
| `compile_block(cpu) -> Optional[int]` | 编译 `cpu.pc` 起的基本块, 返回块结束 PC (`None` 表示不可编译) |
| `try_step(cpu) -> Optional[bool]` | 尝试按块执行; `None` = 该指令不适合 JIT 应回退解释 |
| `get_stats() -> dict` | `blocks_compiled` / `cached_blocks` / `total_calls` / `cache_hits` / `hit_rate` |

```python
from codecin import CPU, Config, jit

cpu = CPU(Config(enable_jit=True, use_native=False,
                 interactive_mode=False, log_level='ERROR'),
          'examples/control_flow.cin')
cpu.run()
print(cpu.jit.get_stats())

# 或者手工编排: 编译 pc 处的基本块, 再用 try_step 执行
j = jit.JITCompiler()
end_pc = j.compile_block(cpu)        # 返回块结束 PC, None 表示该处不可编译
print('block end =', end_pc, j.get_stats())
```

::: tip JIT 的边界
`_JIT_OPS` 只包含直线指令 (数据传输/算术/逻辑/加载存储/栈); 控制流、`SYS`、`IN`/`OUT`
一律结束当前块。`--debug` 或 `--step` 与 JIT 互斥 —— `CPU.run()` 会优先保证追踪完整性。
本页与 [JIT 编译](/runtime/jit) 描述的是同一套实现。
:::

## 相关页面

- [寄存器与内存模型](/reference/registers-memory) — 寄存器/内存/栈帧约定
- [指令集编码表](/reference/isa) — `Opcode` 枚举的完整编码
- [执行路径](/guide/execution-paths) — 三条路径的选择规则与回退顺序
- [Go 原生运行时](/runtime/native) — 原生库加载与查找顺序
- [二进制格式 (.bin/.crom)](/runtime/formats) — UCBC 与 CROM v3 布局
- [命令行参考](/guide/cli) — 与 `Config` 字段一一对应的 CLI 选项
