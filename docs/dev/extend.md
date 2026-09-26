---
description: "扩展 Code CIN：新增 ISA 指令或 SYS 系统调用需要同步改动的全部位置、常量生成与回归验证流程。"
---

# 扩展指令 / 系统调用

新增一条指令或一个系统调用会牵动多条执行路径。**按顺序改完下面每一处**, 再跑常量生成与
回归测试, 否则三路径一致性会被打破。

## 新增一条 ISA 指令

以新增指令 `MINUS` 为例, 需要改动的 6 处:

### 1. `codecin/isa.py` — 指令集单一真源

```python
class Opcode(IntEnum):
    ...
    MINUS = 112              # 新编号 (必须紧接在最后一条之后)

class Constants:
    OPCODE_NAMES = {..., Opcode.MINUS: 'MINUS'}
    OPCODE_NAME_TO_ENUM = {..., 'MINUS': Opcode.MINUS}
    ARG_COUNTS = {..., Opcode.MINUS: 2}      # 参数个数; -1 表示变长
    BRANCH_OPS = {...}     # 若是分支类, 加入统计集合
    FP_OPS = {...}         # 若是浮点类
    PL_KEYWORDS = {...}    # 需要 PL 关键字风格时加 'minus': 'MINUS'
```

### 2. `codecin/cpu.py` — 解释执行

```python
def _op_minus(self, args):
    dst = args[0][1]
    self._set_reg(dst, self._val(args[0]) - self._val(args[1]))
    return True
```

dispatch 表按 `_op_` 前缀**自动注册** (见 `_init_dispatch`), 不需要手工登记;
若新指令没有独立语义 (例如等同于 `NOP` 的别名), 在 `CPU._OP_ALIASES` 里声明即可。

### 3. `codecin/jit.py` — JIT 代码生成

在 JIT 支持的指令白名单/生成分支里加上新指令, 否则包含它的基本块会整块回退解释执行
(功能仍正确, 只是没有加速)。

### 4. `codecin/native/engine/vm.go` — Go 原生 VM

在字节码 `switch` 里加实现; **未实现时返回 `StatusUnsupported`**, Python 侧会自动回退解释
执行, 不会给出错误结果。操作码常量来自生成的 `engine/isa_gen.go`, 该文件**不要手工修改**。

### 5. `codecin/assembler.py` — 汇编器

常规 `reg / imm / label / mem` 操作数会被自动支持; 只有**特殊语法** (如条件后缀、成对访存)
才需要在这里适配。PL 关键字风格走 `Constants.PL_KEYWORDS`。

### 6. CIN 侧 (可选) — 让高级语言能用到

若新指令需要暴露给 CIN, 走 `Syscall` 功能号 + SYS 处理器 (见下一节);
宿主能力内建采用**表驱动**:

| 位置 | 内容 |
|------|------|
| `codecin/cin.py: HOST_BUILTINS` | 名称 → (SYS 号, 参数个数, 返回类型) |
| `codecin/native/compiler/codegen.go: hostBuiltins` | 同上 (Go 编译器) |

Python 解释器会对宿主内建统一给出“需原生运行时”的错误提示, 两处表必须一致。

## 新增一个 SYS 系统调用

四处编号必须一致:

```text
codecin/isa.py: Syscall 枚举编号
        ↓
codecin/cpu.py: SYS 分支 (解释执行)
        ↓
codecin/native/engine/vm.go: SYS 分支 (Go 原生 VM)
        ↓
codecin/cin.py: _gen_call / HOST_BUILTINS 内建映射 (以及 Go 侧 codegen 表)
```

Go 编译器使用的 SYS 常量在 `codecin/native/compiler/syscalls.go`, 由脚本自动生成。

## 常量生成与同步

改完源码后**必须**重新生成派生常量, 防止文档与 Go 侧漂移:

```bash
python script/gen_isa_docs.py      # 重写 docs/ISA.md 与 docs/reference/isa.md
python script/gen_native_isa.py    # 重写 codecin/native/engine/isa_gen.go 与 compiler/syscalls.go

# 校验 (CI 使用, 防止漂移)
python script/gen_isa_docs.py --check
python script/gen_native_isa.py --check
```

::: warning 不要把生成物当成手写文件
`isa_gen.go` / `syscalls.go` / `docs/ISA.md` / `docs/reference/isa.md` 都是生成结果,
手工改动会在下一次生成时被覆盖, CI 也会因为不匹配而失败。
:::

## 重新编译与回归

```bash
# 1. 重新编译原生库 (见 编译 Go 原生库)
cd codecin/native
sh build.sh            # Windows: .\build.ps1
cd ../..

# 2. 原生库确实加载
python -c "from codecin import native; print(native.get_engine())"

# 3. 三路径一致性 + 全量测试
python script/check_paths.py
python -m pytest

# 4. 文档站构建 (ISA 页面已重新生成)
cd docs && npm run docs:build
```

验收点:

- [ ] `python -m pytest` 全绿 (含 `test_isa_dispatch.py` 的“每个 opcode 都有处理器”断言)
- [ ] 两个 `--check` 都通过
- [ ] `--no-native` / `--jit --no-native` / 原生 三条路径输出一致 (`script/check_paths.py`)
- [ ] `--debug` 下能看到新指令的逐指令追踪行
- [ ] 文档站 ISA 页面已更新 (`docs/reference/isa.md`)

## 相关页面

- [指令集编码表](/reference/isa) — 自动生成的编码表
- [指令语义参考](/asm/instructions) — 每条指令的语义与操作数
- [项目结构](/dev/structure) — 模块职责与“改一处同步哪些文件”
- [测试与 CI](/dev/testing) — 回归与门禁
- [编译 Go 原生库](/dev/build-native) — 重新构建 c-shared 库
