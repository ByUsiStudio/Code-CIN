# Code CIN - 高级语言与运行时

---

**Code CIN - ByUsi Studio**

**开发者: 北啊呢**

---

## 项目简介

Code CIN 是一门简洁的类 C 高级语言及其跨平台运行时（VM）。它从 CIN 源码出发，编译为 UCPU 字节码后由 **Go 原生 VM**（默认）/ JIT / Python 解释器三路径执行，行为一致。除完整语言工具链外，还内置 **2D 绘图画布（导出 PNG）** 与 **联网音频播放** 等宿主能力。

正在从 Python 优先**逐步切换为 Go 优先**：Go 原生库已接管字节码 VM、CROM 与 CIN 编译器（`codecin/native/compiler`），并提供独立的 Go CLI（`codecin`）；Python 保留为 CLI 壳与回退路径。支持 CIN/PL/ASM 三语言、ARM64 与 RISC-V 指令集扩展、JIT 编译、缓存系统、性能分析与调试。

模块化包结构（`codecin/`，Go 侧 `codecin/native/`），全线日志与错误输出基于 **rich**（彩色表格、面板、traceback），`--debug` 模式提供逐指令/寄存器/内存/栈/缓存的超详细追踪。

## 文档

| 文档 | 说明 |
|------|------|
| [开发者编译文档 (BUILDING)](docs/BUILDING.md) | 环境搭建、Go 原生库编译、构建产物、打包、日志系统、扩展指南 |
| [CIN 编程指南 (CIN_GUIDE)](docs/CIN_GUIDE.md) | CIN 高级语言完整语法：类型/函数/struct/数组/字符串/内建函数 |
| [远程调试协议 (REMOTE_DEBUG)](docs/REMOTE_DEBUG.md) | `--debug-server` 换行文本协议：命令/响应/状态机/示例会话 |

---

## 设计理念

Code CIN的设计围绕五个核心原则展开：

```mermaid
mindmap
  root((Code CIN设计哲学))
    完整性
      完整工具链
      高级语言到机器码
      多语言支持
    性能
      JIT编译
      缓存系统
      快速指令分发
    可用性
      美观终端界面
      交互式调试
      实时状态显示
    可分析性
      性能分析
      指令统计
      缓存监控
    可扩展性
      模块化设计
      丰富指令集
      插件架构
```

---

## 核心特性

### 多语言支持

```mermaid
graph LR
    subgraph 输入层
        A[CIN 高级语言]
        D[PL 汇编]
        G[ASM 汇编]
    end
    
    subgraph 编译层
        B[C++ 生成]
        E[指令编码]
    end
    
    subgraph 输出层
        C[可执行程序]
        F[CROM 镜像]
        H[CPU 执行]
    end
    
    A --> B --> C
    D --> E --> F --> H
    G --> E
    C --> H
```

### 功能矩阵

| 功能模块 | 状态 | 说明 |
|----------|------|------|
| 指令集 | 112条 | Base + ARM64 + RISC-V + FP + Vector + SYS |
| 寄存器 | 32+32 | 通用寄存器 + 向量寄存器 |
| 内存系统 | 可配置 | 保护机制 + 分页支持 |
| 缓存系统 | LRU | 可配置大小/关联度 |
| JIT编译 | 动态 | 热点基本块编译优化 (与debug互斥) |
| Go原生库 | c-shared | 整程序VM加速, 缺失时自动回退纯Python |
| 日志系统 | rich | 彩色日志/表格/面板/traceback, --debug超详细追踪 |
| 调试器 | 交互式 | 断点 + 单步 + 状态查看 |
| 性能分析 | 指令级 | CPI + 缓存统计 + 热指令 |
| CROM压缩 | zlib | 节省存储空间 (Go/Python双实现) |

---

## 系统架构

### 分层架构

```mermaid
flowchart TB
    subgraph APP[应用层]
        CLI[CLI 界面]
        DBG[交互式调试器]
        PRF[性能分析器]
    end
    
    subgraph COMP[编译层]
        CIN[CIN 编译器]
        PL[PL 汇编器]
        ASM[ASM 汇编器]
    end
    
    subgraph EXEC[执行层]
        CORE[CPU Core<br/>解释执行]
        JIT[JIT 引擎<br/>基本块编译]
        NATIVE[Go 原生库<br/>c-shared VM]
    end

    subgraph HW[硬件层]
        REG[寄存器文件<br/>X0-X31 / V0-V31]
        CACHE[缓存系统<br/>LRU淘汰]
        MEM[内存系统<br/>保护机制]
    end
    
    subgraph ISA[指令集层]
        BASE[Base ISA<br/>28条指令]
        ARM64[ARM64 Ext<br/>40条指令]
        RISCV[RISC-V Ext<br/>27条指令]
        FP[FP Ext<br/>10条指令]
        VEC[Vector Ext<br/>6条指令]
    end
    
    APP --> COMP --> EXEC --> HW --> ISA
```

### 数据流

```mermaid
flowchart LR
    SOURCE[源文件<br/>.cin/.pl/.asm] --> COMPILER[编译器/汇编器]
    COMPILER --> BINARY[二进制/CROM]
    BINARY --> CACHE[缓存]
    CACHE --> CPU[CPU核心]
    CPU --> STATS[统计信息]
    CPU --> DISPLAY[显示输出]
    STATS --> DISPLAY
```

### 执行时序

```mermaid
sequenceDiagram
    participant User
    participant CLI
    participant Compiler
    participant CPU
    participant Memory
    participant Cache
    
    User->>CLI: 执行程序
    CLI->>Compiler: 编译/汇编
    Compiler->>Memory: 加载代码
    Memory->>Cache: 缓存预热
    
    loop 执行循环
        Cache->>CPU: 取指
        CPU->>CPU: 解码
        CPU->>Cache: 读操作数
        Cache->>Memory: 缓存未命中
        Memory->>Cache: 加载数据
        Cache->>CPU: 返回数据
        CPU->>CPU: 执行
        CPU->>Cache: 写结果
        Cache->>Memory: 回写
        CPU->>CLI: 状态更新
        CLI->>User: 显示状态
    end
    
    CPU->>CLI: 执行完成
    CLI->>User: 显示统计
```

---

## 指令集架构

### 指令集总览

```mermaid
pie title Code CIN 指令集组成
    "Base ISA (28条)" : 28
    "ARM64 Ext (40条)" : 40
    "RISC-V Ext (27条)" : 27
    "FP Ext (10条)" : 10
    "Vector Ext (6条)" : 6
    "SYS 宿主调用 (1条)" : 1
```

> 指令总数与分组由 `python script/gen_isa_docs.py` 依据 `codecin/isa.py` 自动生成/校验;
> 完整的逐条指令表见 [docs/ISA.md](docs/ISA.md)。ARM64 的 WFE/WFI/SEV 无事件模型, 语义等同 NOP。

### 指令分类

```mermaid
graph TD
    ISA[Code CIN ISA<br/>112条指令]
    
    ISA --> BASE[Base ISA<br/>28条指令]
    ISA --> ARM[ARM64 Ext<br/>40条指令]
    ISA --> RISCV[RISC-V Ext<br/>27条指令]
    ISA --> FP[FP Ext<br/>10条指令]
    ISA --> VEC[Vector Ext<br/>6条指令]
    ISA --> SYS[SYS 宿主调用<br/>1条指令]
    
    BASE --> B1[数据传输: MOV, LOAD, STORE]
    BASE --> B2[算术: ADD, SUB, MUL, DIV]
    BASE --> B3[逻辑: AND, OR, XOR, SHL, SHR]
    BASE --> B4[控制: JMP, JZ, CALL, RET]
    
    ARM --> A1[条件: ADDS, SUBS]
    ARM --> A2[移位: LSL, LSR, ASR, ROR]
    ARM --> A3[加载存储: LDR, STR, LDP, STP]
    ARM --> A4[分支: CBZ, CBNZ, B, BL]
    
    RISCV --> R1[加载: LB, LH, LW, LD]
    RISCV --> R2[存储: SB, SH, SW, SD]
    RISCV --> R3[立即数: ADDI, XORI, ORI, ANDI]
    RISCV --> R4[分支: BEQ, BNE, BLT, BGE]
    
    FP --> F1[运算: FADD, FSUB, FMUL, FDIV]
    FP --> F2[比较: FCMP]
    FP --> F3[转换: FCVT]
    
    VEC --> V1[向量运算: VADD, VSUB, VMUL, VDIV]
    VEC --> V2[向量加载存储: VLD1, VST1]
```

### Base指令集 (28条)

| 分类 | 指令 | 说明 |
|------|------|------|
| 数据传输 | MOV, LOAD, STORE | 数据移动和内存访问 |
| 算术 | ADD, SUB, MUL, DIV | 基本算术运算 |
| 逻辑 | AND, OR, XOR, SHL, SHR | 位运算和移位 |
| 控制 | JMP, JZ, JNZ, JE, JL, JG, CALL, RET | 程序流程控制 |
| 堆栈 | PUSH, POP | 栈操作 |
| I/O | IN, OUT | 输入输出 |
| 其他 | CMP, INC, DEC, HALT | 比较、自增、自减、停机 |

### ARM64扩展 (40条)

ADDS, SUBS, ADDC, SUBC, LSL, LSR, ASR, ROR, MVN, EOR, BIC, ORN, LDR, STR, LDP, STP, CBZ, CBNZ, TBZ, TBNZ, B, BL, BR, NOP, WFE, WFI, SEV, CSEL, CSINC, CSINV, CSNEG, SXTB, SXTH, SXTW, UXTB, UXTH, CLZ, CLS, RBIT, REV

> WFE/WFI/SEV: 模拟器无中断/多核事件模型, 语义等同 NOP (见 `codecin/cpu.py` `_OP_ALIASES`)。

### RISC-V扩展 (27条)

LB, LH, LW, LD, SB, SH, SW, SD, ADDI, SLTI, SLTIU, XORI, ORI, ANDI, SLLI, SRLI, SRAI, BEQ, BNE, BLT, BGE, BLTU, BGEU, JALR, JAL, LUI, AUIPC

### 浮点和向量指令

FADD, FSUB, FMUL, FDIV, FCMP, FCVT, FABS, FNEG, LDRS, STRS, VADD, VSUB, VMUL, VDIV, VLD1, VST1

---

## CROM文件格式

### v3格式结构

```mermaid
block-beta
    columns 5
    
    block:header:5
        columns 5
        Magic["Magic<br/>'CROM'"] space1[" "]
        Version["Version<br/>0x03"] space2[" "]
        Size["Size<br/>Memory Size"] space3[" "]
        Flags["Flags<br/>压缩标志"] space4[" "]
        Checksum["Checksum<br/>CRC32"] space5[" "]
        Reserved["Reserved<br/>0x0000"] space6[" "]
    end
    
    block:data:5
        columns 5
        Data["Data<br/>压缩/未压缩"]
    end
    
    header --> data
```

| 偏移 | 大小 | 字段 | 说明 |
|------|------|------|------|
| 0x00 | 4 | Magic | 'CROM' 魔数 |
| 0x04 | 1 | Version | 0x03 版本号 |
| 0x05 | 4 | Memory Size | 内存大小 |
| 0x09 | 1 | Flags | bit0: 压缩标志 |
| 0x0A | 4 | Checksum | CRC32校验和 |
| 0x0E | 2 | Reserved | 保留字段 |
| 0x10 | N | Data | 压缩/未压缩数据 |

### 版本对比

```mermaid
xychart-beta
    title "CROM 版本特性对比"
    x-axis ["v1", "v2", "v3"]
    y-axis "特性支持" 0 --> 100
    line [40, 60, 100]
    line [30, 50, 95]
    line [0, 0, 80]
```

| 特性 | v1 | v2 | v3 |
|------|----|----|-----|
| 压缩支持 | 否 | 否 | 是 |
| 校验和 | 否 | 否 | 是 |
| 元数据 | 否 | 是 | 是 |
| 兼容性 | - | 是 | 是 |

---

## 使用指南

### 执行流程

```mermaid
flowchart TD
    START([开始]) --> INPUT{输入文件类型}
    
    INPUT -->|.cin| CIN[CIN编译器]
    INPUT -->|.pl| PL[PL汇编器]
    INPUT -->|.asm| ASM[ASM汇编器]
    INPUT -->|.bin| BIN[二进制加载器]
    INPUT -->|.crom| CROM[CROM加载器]
    
    CIN --> CPP[生成C++]
    CPP --> BUILD[编译构建]
    BUILD --> EXEC
    
    PL --> ASSEMBLE[指令编码]
    ASM --> ASSEMBLE
    ASSEMBLE --> BINLOAD[二进制加载]
    
    BIN --> BINLOAD
    CROM --> CROMLOAD[CROM加载]
    
    BINLOAD --> EXEC[CPU执行]
    CROMLOAD --> EXEC
    
    EXEC --> CHECK{检查状态}
    CHECK -->|运行中| STEP[执行指令]
    STEP --> STATS[更新统计]
    STATS --> DISPLAY[显示状态]
    DISPLAY --> CHECK
    
    CHECK -->|停止| DONE([完成])
```

### 快速开始

**方式一：一键安装（推荐）**

```bash
# Linux / macOS / Termux
bash install.sh
# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File install.ps1
# Termux 专用（克隆 + 装依赖 + 编译 + 启动器）
bash script/install_termux.sh
```

一键脚本会自动：检测/安装 Go → 编译 Go 原生库与 `codecin` CLI → 安装 Python 依赖 → 生成 `codecin` 启动器。

**方式二：手动**

1. 克隆项目:
   ```
   git clone https://github.com/ByUsiStudio/codecin.git
   cd codecin
   ```

2. 运行（Go CLI 或 Python 入口）:
   ```
   go run ./codecin/native/cmd/codecin basic.cin   # Go 独立 CLI
   python cpu.py basic.cin                          # Python 入口 (自动用 Go 原生库)
   python cpu.py --help
   ```

> 未编译原生库时, Python 入口自动回退纯 Python 解释执行; GUI/联网音频需 Go 原生库 (默认启用)。

### 开发与测试

```bash
python -m pytest                            # 指令黄金 / 三路径一致性 / 断点回归 / 内存保护 / CLI
python script/gen_isa_docs.py --check       # docs/ISA.md 与指令集同步
python script/gen_native_isa.py --check     # Go 原生常量与指令集同步
ruff check codecin cpu.py script tests
```

仓库内置 GitHub Actions CI (`.github/workflows/ci.yml`): 多 Python 版本测试、ruff、Go 原生库编译校验;
完整逐条指令表由 `script/gen_isa_docs.py` 从 `codecin/isa.py` 生成至 [docs/ISA.md](docs/ISA.md)。

### 执行路径

| 路径 | 启用方式 | 特点 |
|------|----------|------|
| Go 独立 CLI | `codecin program.cin` | **全 Go 链路**: Go 版 CIN 编译器 + Go VM, 不依赖 Python |
| Go 原生 | 默认优先 (需编译库) | Python 编译 + Go VM 整程序一次执行, 速度最快 |
| JIT | `--jit` | 基本块动态编译, 与 `--debug` 互斥 |
| 解释执行 | `--no-native` 或回退 | 支持全部 debug/step 功能 |

> Go 版编译器 (`codecin/native/compiler/`) 与 Python 编译器产物逐字节等价, 由
> `script/diff_go_python.py` 差分校验 (含 `basic.cin` 400 行综合示例)。

### 命令行选项

**基础执行**
- `python cpu.py program.cin` - 运行CIN程序
- `python cpu.py program.pl` - 运行PL程序
- `python cpu.py program.asm` - 运行ASM程序
- `python cpu.py program.bin` - 运行字节码

**执行路径**
- `--no-native` - 禁用 Go 原生库, 强制纯 Python
- `--jit` - 启用 Python JIT (基本块动态编译)

**日志与调试**
- `--debug` - 超详细 rich 调试 (逐指令/寄存器/内存/栈/缓存)
- `--step` - 交互式单步调试
- `--debug-server <port>` - 启动 TCP 远程调试服务 (驱动式: step/continue/break/regs/mem/history)
- `--log-level DEBUG|INFO|WARNING|ERROR` - 日志级别
- `--log-file <file>` - 日志输出到文件

**性能与行为**
- `--profile` - 性能统计
- `--cache-size 128` - 配置缓存大小
- `--mem-size <bytes>` - 内存大小
- `--max-instructions <n>` - 指令数上限
- `--seed <n>` - 随机种子 (确定性执行)
- `--bounds-check` - CIN 数组越界运行时检查
- `--mmu` - 启用 MMU 分页 (identity 页表, 未映射页缺页错误)

**编译选项**
- `--compile` / `--compile-only` - 编译为 .bin 字节码
- `--disasm` - 反汇编 .bin/UCBC 为文本清单后退出
- `-o, --output <file>` - 输出文件名
- `--no-io` - 禁止宿主 I/O
- `--strict` - 严格汇编模式

**CROM选项**
- `--save` - 保存CROM
- `--no-compress` - 禁用压缩
- `--crom <file>` - 加载指定 CROM 镜像

---

## 调试器

### 调试会话流程

```mermaid
stateDiagram-v2
    [*] --> 运行
    
    运行 --> 断点命中: 执行到断点
    断点命中 --> 调试命令: 用户交互
    
    调试命令 --> 单步: step
    调试命令 --> 继续: continue
    调试命令 --> 查看状态: print
    调试命令 --> 修改断点: break/delete
    调试命令 --> 退出: quit
    
    单步 --> 调试命令
    继续 --> 运行
    查看状态 --> 调试命令
    修改断点 --> 调试命令
    
    退出 --> [*]
    运行 --> [*]: 程序完成
```

### 调试命令树

```mermaid
flowchart TD
    DBG[调试命令]
    
    DBG --> CONTINUE[continue / c<br/>继续执行]
    DBG --> STEP[step / s<br/>单步执行]
    DBG --> BREAK[break / b<br/>设置断点]
    DBG --> DELETE[delete / d<br/>删除断点]
    DBG --> LIST[list / l<br/>列出断点]
    DBG --> PRINT[print / p<br/>打印信息]
    DBG --> QUIT[quit / q<br/>退出]
    
    PRINT --> REGS[regs<br/>所有寄存器]
    PRINT --> REG[X0-X31<br/>单个寄存器]
    PRINT --> MEM["mem [addr]<br/>内存内容"]
    PRINT --> CACHE[cache<br/>缓存统计]
```

### 交互式调试命令

| 命令 | 缩写 | 说明 |
|------|------|------|
| continue | c | 继续执行 |
| step | s | 单步执行 |
| break <addr> | b | 设置断点 |
| delete <addr> | d | 删除断点 |
| list | l | 列出断点 |
| print <target> | p | 打印信息 |
| quit | q | 退出 |

### 打印目标

- `X0-X31` - 寄存器值
- `regs` - 所有寄存器
- `mem [addr]` - 内存内容
- `cache` - 缓存统计

### 调试会话示例

```
dbg> break 0x10
Breakpoint set at 0x10

dbg> continue
Breakpoint hit at PC=0x10

dbg> p X0
X0 = 42

dbg> p regs
[寄存器显示]

dbg> step
Executing: ADD X2, X0, X1

dbg> continue
Program completed
```

---

## 日志与调试 (rich)

全线日志与错误输出基于 **rich**: 彩色表格、面板、进度与完整 traceback。模块不直接 `print`, 统一经 `codecin/console.py` 适配层输出。

### 日志级别

| 级别 | 内容 |
|------|------|
| `ERROR` | 仅错误面板 |
| `WARNING` | + 回退/降级告警 (如原生库缺失) |
| `INFO` (默认) | + 编译汇总、执行起止、统计表 |
| `DEBUG` | **超详细**: 全部埋点 + 逐指令追踪 |

### debug 超详细输出 (`--debug`)

- **CPU 初始化 dump**: 内存大小、缓存拓扑、SP 初值、堆基址、路径选择
- **逐指令追踪**: 每条指令输出 PC、全局序号、操作数值、SP 与执行后 NZCV 标志
  ```
  PC=0x0004 #00000002 ADD X1=0x0(0) X2=0x1(1)  SP=0xfff8
    => pc=0x0005 N=0 Z=0 C=0 V=0
  ```
- **内存读写追踪**: `MEM WR @0x000c w=1 value=0x0`, 覆盖全部加载/存储指令
- **栈操作 / 缓存命中缺失 / SYS 系统调用** (功能号+参数)
- **编译埋点**: CIN tokenize/parse 统计、JIT 块源码 dump、原生库调用参数

### 错误处理

- 加载/汇编/编译/运行错误统一红色 rich 面板, 带 `文件:行号` 定位
- 未预期异常输出 rich 彩色完整 traceback (`--debug` 下加载/运行错误也附带)

```
┌──────────────────────── Load Error ────────────────────────┐
│ prog.cin:12: Compiler error: Unknown function: printline   │
└────────────────────────────────────────────────────────────┘
```

详见 [开发者编译文档 · 日志系统](docs/BUILDING.md#7-日志系统-rich-与-debug-超详细输出)。

---

## 性能分析

### 性能指标流

```mermaid
flowchart LR
    subgraph INPUT[输入]
        I1[指令流]
    end
    
    subgraph MEASURE[测量]
        M1[指令计数]
        M2[周期计数]
        M3[缓存统计]
        M4[JIT统计]
    end
    
    subgraph CALC[计算]
        C1[CPI = 周期/指令]
        C2[IPC = 指令/周期]
        C3[命中率]
        C4[执行时间]
    end
    
    subgraph OUTPUT[输出]
        O1[性能报告]
        O2[热指令列表]
        O3[优化建议]
    end
    
    I1 --> M1 --> C1
    I1 --> M2 --> C2
    I1 --> M3 --> C3
    I1 --> M4
    
    C1 --> O1
    C2 --> O1
    C3 --> O1
    C4 --> O1
    M1 --> O2
    M4 --> O3
```

### 指令周期分布

```mermaid
pie title 指令周期分布示例
    "MOV (28%)" : 28
    "ADD (19%)" : 19
    "LOAD (10%)" : 10
    "STORE (8%)" : 8
    "MUL (7%)" : 7
    "CMP (6%)" : 6
    "JMP (5%)" : 5
    "其他 (17%)" : 17
```

### 性能对比

```mermaid
xychart-beta
    title "执行模式性能对比"
    x-axis ["解释执行", "JIT编译", "原生C++"]
    y-axis "相对性能" 0 --> 10
    bar [1, 4, 8]
    line [1, 4.2, 7.8]
```

### 统计指标

**执行统计**
- 总指令数
- 总周期数
- CPI (每指令周期数)
- 执行时间
- 指令/秒

**内存统计**
- 内存读取次数
- 内存写入次数

**缓存统计**
- 缓存命中次数
- 缓存缺失次数
- 缓存命中率

**JIT统计**
- JIT调用次数
- JIT缓存命中次数
- JIT命中率
- JIT编译块数

### 指令周期表

| 指令类型 | 延迟(周期) | 说明 |
|----------|------------|------|
| ADD/SUB | 1 | 整数运算 |
| MUL | 3 | 整数乘法 |
| DIV | 10 | 整数除法 |
| LOAD | 4 | 内存加载 |
| STORE | 4 | 内存存储 |
| FADD | 3 | 浮点加法 |
| FMUL | 5 | 浮点乘法 |
| FDIV | 10 | 浮点除法 |
| VADD | 2 | 向量加法 |
| VMUL | 4 | 向量乘法 |

---

## 示例程序

> CIN 语言完整语法见 [CIN 编程指南](docs/CIN_GUIDE.md)。
>
> **CIN 新语法示例**: `examples/control_flow.cin` (break/continue、do-while、switch/case、三目、复合赋值)、
> `examples/literals_types.cin` (char/short/long/unsigned、0x/0b/0o 与字符字面量、`++/--`、转换内建函数);
> 汇编器 `.equ`/表达式示例: `examples/asm_constants.asm`。
>
> **官方标准库 (`lib/`)**: `math` `str` `array` `sort` `conv` `vec` `rand` `json` `time` `io` `gui` `termux` `test`
> —— 示例 `examples/modules_demo.cin`、`examples/stdlib_demo.cin` (断言全部通过)。详见
> [CIN 编程指南 · 官方标准库清单](docs/CIN_GUIDE.md#官方标准库清单-lib)。
>
> **CIN 位运算/整除/字符串下标/更多内建**: `& | ^ ~ << >>` (及复合赋值)、`idiv()` 整数除法、
> `s[i]` 单字节读取、`floor/ceil/round/min/max/atoi/trim/ltrim/rtrim` 内建函数 (详见
> [CIN 编程指南 · 运算符/内建](docs/CIN_GUIDE.md))。
>
> **CIN 宿主能力 (GUI / 联网音频 / 系统交互)**: 2D 绘图画布 `canvas/set_color/fill_rect/fill_circle/draw_line/draw_text`
> + `save_png()` 导出 / `show_canvas()` 弹窗查看; 联网音频 `audio_play(url)` / `audio_stop` /
> `audio_volume` / `audio_wait` (http 下载 + WAV 播放); 系统原生交互 `file_read/file_write/file_append/
> file_exists/file_delete/file_size/mkdir/dir_list`、`exec/exec_output`、`getenv/setenv`、
> `os_name/hostname/username/cwd/home_dir` (Windows/Linux/macOS); Termux API `termux_notify/toast/
> clipboard_get/clipboard_set/battery/vibrate/tts/location/wifi_info/dialog/sms_send`。

### 程序执行流程图

```mermaid
flowchart TD
    subgraph CIN[Hello World - CIN]
        C1[function main] --> C2[println]
        C2 --> C3[return 0]
    end
    
    subgraph PL[斐波那契 - PL]
        P1[set x0, 10] --> P2[call fibonacci]
        P2 --> P3[out x0]
        P3 --> P4[halt]
    end
    
    subgraph ASM[快速排序 - ASM]
        A1[main] --> A2[ldr x0, =array]
        A2 --> A3[ldr x1, =size]
        A3 --> A4[bl quicksort]
        A4 --> A5[halt]
    end
```

### Hello World (CIN)

```c
function main() {
    println("Hello, World!")
    println("Welcome to Code CIN")
    return 0
}
```

### 斐波那契 (PL)

```
.text
    set x0, 10          // n = 10
    call fibonacci
    out x0
    halt

fibonacci:
    cmp x0, 1
    jle base_case
    push x0
    sub x0, 1
    call fibonacci
    pop x1
    push x0
    add x0, x1
    ret

base_case:
    set x0, 1
    ret
```

### 快速排序 (ASM)

```
; 快速排序实现
.text
main:
    ldr x0, =array
    ldr x1, =size
    bl quicksort
    halt

quicksort:
    cmp x0, x1
    bge done
    
    ; Partition
    ldr x2, [x0]        ; pivot
    mov x3, x0
    mov x4, x1
    
partition:
    cmp x3, x4
    bge swap_pivot
    
    ldr x5, [x3]
    cmp x5, x2
    ble swap_left
    
    ; ... 更多代码
    
done:
    ret

.data
array: .word 5, 3, 8, 1, 9, 2, 7, 4, 6
size: .word 9
```

---

## 技术栈

### 依赖关系图

```mermaid
graph TD
    Code CIN[Code CIN]

    Code CIN --> PY[Python 3.8+]
    Code CIN --> RICH[Rich Library]
    Code CIN --> GO[Go 1.21+ 原生库]
    Code CIN --> STDLIB[Standard Library]

    RICH --> COLOR[彩色输出]
    RICH --> TABLE[表格渲染]
    RICH --> PANEL[面板/Traceback]

    GO --> CSHARE[c-shared 动态库]
    CSHARE --> VM[原生字节码 VM]
    CSHARE --> CROMGO[CROM 加速]

    STDLIB --> STRUCT[struct]
    STDLIB --> ZLIB[zlib]
    STDLIB --> CTYPES[ctypes]
    STDLIB --> RE[正则表达式]
```

| 组件 | 技术 | 说明 |
|------|------|------|
| 语言 | Python 3.8+ | 核心实现语言 (模块化包 `codecin/`) |
| UI/日志 | Rich | 彩色输出、表格、面板、traceback |
| 原生加速 | Go 1.21+ (c-shared) | 原生 VM + CROM, 可选, 自动回退 |
| 压缩 | zlib | CROM压缩 |
| 序列化 | struct | 二进制格式 |
| FFI | ctypes | 加载 Go 共享库 |
| 调试 | 原生Python | 交互式调试 |

> 模块结构、原生库编译与扩展指南见 [开发者编译文档](docs/BUILDING.md)。

---

## 贡献指南

### 贡献流程

```mermaid
flowchart LR
    subgraph DEV[开发流程]
        FORK[Fork项目]
        BRANCH[创建特性分支]
        CODE[编写代码]
        TEST[运行测试]
        COMMIT[提交更改]
        PUSH[推送到分支]
        PR[Pull Request]
    end
    
    subgraph REVIEW[审查流程]
        CHECK[代码检查]
        REVIEW2[同行审查]
        MERGE[合并到主分支]
    end
    
    FORK --> BRANCH --> CODE --> TEST
    TEST --> COMMIT --> PUSH --> PR
    PR --> CHECK --> REVIEW2 --> MERGE
```

### 代码规范

- 遵循PEP 8编码规范
- 使用类型提示
- 编写文档字符串
- 添加单元测试

---

## 联系方式

| 角色 | 信息 |
|------|------|
| 开发组织 | ByUsi Studio |
| 主要开发者 | 北啊呢 |
| 邮箱 | admin@byusistudio.fun |
| GitHub | github.com/ByUsiStudio/codecin |

---

## 致谢

```mermaid
flowchart LR
    THANKS[致谢]
    
    THANKS --> RICH[Rich库<br/>终端美化]
    THANKS --> PY[Python社区<br/>强大生态]
    THANKS --> CONTRIB[贡献者<br/>代码贡献]
```

---

**Code CIN - 让编程与运行时变得简单而强大**

---

Made with love by ByUsi Studio