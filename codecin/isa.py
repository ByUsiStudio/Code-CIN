from enum import Enum, IntEnum
from typing import Dict, Set


class Opcode(Enum):
    MOV = 0
    LOAD = 1
    STORE = 2
    ADD = 3
    SUB = 4
    MUL = 5
    DIV = 6
    AND = 7
    OR = 8
    XOR = 9
    SHL = 10
    SHR = 11
    INC = 12
    DEC = 13
    CMP = 14
    JMP = 15
    JZ = 16
    JNZ = 17
    JE = 18
    JL = 19
    JG = 20
    PUSH = 21
    POP = 22
    CALL = 23
    RET = 24
    IN = 25
    OUT = 26
    HALT = 27
    ADDS = 28
    SUBS = 29
    ADDC = 30
    SUBC = 31
    LSL = 32
    LSR = 33
    ASR = 34
    ROR = 35
    MVN = 36
    EOR = 37
    BIC = 38
    ORN = 39
    LDR = 40
    STR = 41
    LDP = 42
    STP = 43
    CBZ = 44
    CBNZ = 45
    TBZ = 46
    TBNZ = 47
    B = 48
    BL = 49
    BR = 50
    NOP = 51
    WFE = 52
    WFI = 53
    SEV = 54
    CSEL = 55
    CSINC = 56
    CSINV = 57
    CSNEG = 58
    SXTB = 59
    SXTH = 60
    SXTW = 61
    UXTB = 62
    UXTH = 63
    CLZ = 64
    CLS = 65
    RBIT = 66
    REV = 67
    FADD = 68
    FSUB = 69
    FMUL = 70
    FDIV = 71
    FCMP = 72
    FCVT = 73
    FABS = 74
    FNEG = 75
    LDRS = 76
    STRS = 77
    VADD = 78
    VSUB = 79
    VMUL = 80
    VDIV = 81
    VLD1 = 82
    VST1 = 83
    LB = 84
    LH = 85
    LW = 86
    LD = 87
    SB = 88
    SH = 89
    SW = 90
    SD = 91
    ADDI = 92
    SLTI = 93
    SLTIU = 94
    XORI = 95
    ORI = 96
    ANDI = 97
    SLLI = 98
    SRLI = 99
    SRAI = 100
    BEQ = 101
    BNE = 102
    BLT = 103
    BGE = 104
    BLTU = 105
    BGEU = 106
    JALR = 107
    JAL = 108
    LUI = 109
    AUIPC = 110
    # UCPU 扩展: 宿主系统调用 (CIN 内建函数 / 浮点运算支撑)
    SYS = 111


class Syscall(IntEnum):
    """SYS 指令的功能号。参数通过 X0-X2 (整数) 传递,
    浮点参数/结果以 float64 位模式存于 X0/X1。返回值写入 X0。"""
    SQRT = 0        # float sqrt(x0 bits) -> x0 bits
    POW = 1         # float pow(x0, x1) -> x0 bits
    ABS = 2         # int abs(x0)
    SIN = 3
    COS = 4
    TAN = 5
    FADD = 6        # float x0 + x1
    FSUB = 7
    FMUL = 8
    FDIV = 9
    FCMP = 10       # float cmp(x0,x1) -> -1/0/1
    FTOI = 11       # float -> int
    ITOF = 12       # int -> float bits
    RAND = 13       # -> 非负随机整数
    SRAND = 14      # srand(x0)
    TIME = 15       # -> unix 时间戳
    STRLEN = 16     # strlen(x0=ptr)
    STRCMP = 17     # strcmp(x0, x1)
    STRCPY = 18     # strcpy(x0=dst, x1=src) -> dst
    STRCAT = 19     # strcat(x0=dst, x1=src) -> dst
    MALLOC = 20     # malloc(x0=bytes) -> ptr
    PRINT_FLOAT = 21  # 打印 float(x0 bits)
    ITOA = 22       # itoa(x0) -> 静态缓冲 ptr
    FTOA = 23       # ftoa(x0 bits) -> 静态缓冲 ptr
    PRINT_STR = 24  # 打印 x0 指向的字符串 (OUT str 的系统调用形式)
    STR_CONCAT = 25  # strcat_alloc(x0=a, x1=b) -> 新堆块 ptr (NUL 结尾)
    BOOL_STR = 26   # bool_str(x0) -> "true"/"false" 静态缓冲 ptr
    ABORT = 27      # abort(x0=消息指针): 抛 ExecutionError (assert/边界检查)
    SUBSTR = 28     # substr(x0=s, x1=start, x2=len) -> 新堆字符串 (越界裁剪)
    INDEXOF = 29    # indexof(x0=hay, x1=needle) -> 首位置或 -1
    TOUPPER = 30    # upper(x0=s) -> 新堆字符串 (ASCII 大写)
    TOLOWER = 31    # lower(x0=s) -> 新堆字符串 (ASCII 小写)
    FLOOR = 32      # float floor(x0 bits) -> float bits (向 -inf 取整)
    CEIL = 33       # float ceil(x0 bits) -> float bits (向 +inf 取整)
    ROUND = 34      # float round(x0 bits) -> float bits (floor(x+0.5), 半值向 +inf)
    ATOI = 35       # int atoi(x0=string ptr) -> 十进制整数 (失败为 0)
    TRIM = 36       # string trim(x0=s) -> 去首尾空白的新堆字符串
    LTRIM = 37      # string ltrim(x0=s) -> 去前导空白的新堆字符串
    RTRIM = 38      # string rtrim(x0=s) -> 去尾部空白的新堆字符串


class Cond:
    """条件码编号 (与 Go 端一致)。"""
    EQ = 0
    NE = 1
    CS = 2
    CC = 3
    MI = 4
    PL = 5
    VS = 6
    VC = 7
    HI = 8
    LS = 9
    GE = 10
    LT = 11
    GT = 12
    LE = 13
    AL = 14
    NV = 15

    NAMES = ('EQ', 'NE', 'CS', 'CC', 'MI', 'PL', 'VS', 'VC',
             'HI', 'LS', 'GE', 'LT', 'GT', 'LE', 'AL', 'NV')

    @classmethod
    def code(cls, name: str) -> int:
        return cls.NAMES.index(name.upper())


# 字节码操作数类型码 (与 Go native 库共享)
KIND_REG = 0
KIND_IMM = 1
KIND_VEC = 2
KIND_VECLANE = 3
KIND_MEM = 4
KIND_COND = 5
KIND_FLOAT = 6
KIND_STR = 7

BC_MAGIC = b'UCBC'
BC_VERSION = 1


class Constants:
    NUM_REGISTERS = 32
    SP_REG = 32          # 伪寄存器: 栈指针
    NUM_REGS_TOTAL = 33  # 0-31 通用 + 32 SP
    NUM_VECTOR_REGISTERS = 32
    VECTOR_LANES = 4
    INSTR_SIZE = 16
    DEFAULT_MEM_SIZE = 1024 * 64
    MAGIC_NUMBER = b'CPUSA'
    CROM_MAGIC = b'CROM'
    CROM_VERSION = 3
    BIN_VERSION = 2
    MAX_INSTRUCTIONS = 100_000_000
    STACK_SLOT = 8       # 栈槽位大小 (qword)

    OPCODE_NAMES: Dict['Opcode', str] = {
        Opcode.MOV: 'MOV', Opcode.LOAD: 'LOAD', Opcode.STORE: 'STORE',
        Opcode.ADD: 'ADD', Opcode.SUB: 'SUB', Opcode.MUL: 'MUL',
        Opcode.DIV: 'DIV', Opcode.AND: 'AND', Opcode.OR: 'OR',
        Opcode.XOR: 'XOR', Opcode.SHL: 'SHL', Opcode.SHR: 'SHR',
        Opcode.INC: 'INC', Opcode.DEC: 'DEC', Opcode.CMP: 'CMP',
        Opcode.JMP: 'JMP', Opcode.JZ: 'JZ', Opcode.JNZ: 'JNZ',
        Opcode.JE: 'JE', Opcode.JL: 'JL', Opcode.JG: 'JG',
        Opcode.PUSH: 'PUSH', Opcode.POP: 'POP', Opcode.CALL: 'CALL',
        Opcode.RET: 'RET', Opcode.IN: 'IN', Opcode.OUT: 'OUT',
        Opcode.HALT: 'HALT',
        Opcode.ADDS: 'ADDS', Opcode.SUBS: 'SUBS',
        Opcode.ADDC: 'ADDC', Opcode.SUBC: 'SUBC',
        Opcode.LSL: 'LSL', Opcode.LSR: 'LSR',
        Opcode.ASR: 'ASR', Opcode.ROR: 'ROR',
        Opcode.MVN: 'MVN', Opcode.EOR: 'EOR',
        Opcode.BIC: 'BIC', Opcode.ORN: 'ORN',
        Opcode.LDR: 'LDR', Opcode.STR: 'STR',
        Opcode.LDP: 'LDP', Opcode.STP: 'STP',
        Opcode.CBZ: 'CBZ', Opcode.CBNZ: 'CBNZ',
        Opcode.TBZ: 'TBZ', Opcode.TBNZ: 'TBNZ',
        Opcode.B: 'B', Opcode.BL: 'BL', Opcode.BR: 'BR',
        Opcode.NOP: 'NOP', Opcode.WFE: 'WFE',
        Opcode.WFI: 'WFI', Opcode.SEV: 'SEV',
        Opcode.CSEL: 'CSEL', Opcode.CSINC: 'CSINC',
        Opcode.CSINV: 'CSINV', Opcode.CSNEG: 'CSNEG',
        Opcode.SXTB: 'SXTB', Opcode.SXTH: 'SXTH',
        Opcode.SXTW: 'SXTW', Opcode.UXTB: 'UXTB',
        Opcode.UXTH: 'UXTH',
        Opcode.CLZ: 'CLZ', Opcode.CLS: 'CLS',
        Opcode.RBIT: 'RBIT', Opcode.REV: 'REV',
        Opcode.FADD: 'FADD', Opcode.FSUB: 'FSUB',
        Opcode.FMUL: 'FMUL', Opcode.FDIV: 'FDIV',
        Opcode.FCMP: 'FCMP', Opcode.FCVT: 'FCVT',
        Opcode.FABS: 'FABS', Opcode.FNEG: 'FNEG',
        Opcode.LDRS: 'LDRS', Opcode.STRS: 'STRS',
        Opcode.VADD: 'VADD', Opcode.VSUB: 'VSUB',
        Opcode.VMUL: 'VMUL', Opcode.VDIV: 'VDIV',
        Opcode.VLD1: 'VLD1', Opcode.VST1: 'VST1',
        Opcode.LB: 'LB', Opcode.LH: 'LH', Opcode.LW: 'LW',
        Opcode.LD: 'LD', Opcode.SB: 'SB', Opcode.SH: 'SH',
        Opcode.SW: 'SW', Opcode.SD: 'SD',
        Opcode.ADDI: 'ADDI', Opcode.SLTI: 'SLTI',
        Opcode.SLTIU: 'SLTIU', Opcode.XORI: 'XORI',
        Opcode.ORI: 'ORI', Opcode.ANDI: 'ANDI',
        Opcode.SLLI: 'SLLI', Opcode.SRLI: 'SRLI',
        Opcode.SRAI: 'SRAI',
        Opcode.BEQ: 'BEQ', Opcode.BNE: 'BNE',
        Opcode.BLT: 'BLT', Opcode.BGE: 'BGE',
        Opcode.BLTU: 'BLTU', Opcode.BGEU: 'BGEU',
        Opcode.JALR: 'JALR', Opcode.JAL: 'JAL',
        Opcode.LUI: 'LUI', Opcode.AUIPC: 'AUIPC',
        Opcode.SYS: 'SYS',
    }

    OPCODE_NAME_TO_ENUM: Dict[str, 'Opcode'] = {v: k for k, v in OPCODE_NAMES.items()}

    # 参数个数; -1 表示不检查 (变长)
    ARG_COUNTS: Dict['Opcode', int] = {
        Opcode.MOV: 2, Opcode.LOAD: 2, Opcode.STORE: 2,
        Opcode.ADD: 2, Opcode.SUB: 2, Opcode.MUL: 2,
        Opcode.DIV: 2, Opcode.AND: 2, Opcode.OR: 2,
        Opcode.XOR: 2, Opcode.SHL: 2, Opcode.SHR: 2,
        Opcode.INC: 1, Opcode.DEC: 1, Opcode.CMP: 2,
        Opcode.JMP: 1, Opcode.JZ: 1, Opcode.JNZ: 1,
        Opcode.JE: 1, Opcode.JL: 1, Opcode.JG: 1,
        Opcode.PUSH: 1, Opcode.POP: 1, Opcode.CALL: 1,
        Opcode.RET: 0, Opcode.IN: 1, Opcode.OUT: 1,
        Opcode.HALT: 0,
        Opcode.ADDS: 3, Opcode.SUBS: 3,
        Opcode.ADDC: 3, Opcode.SUBC: 3,
        Opcode.LSL: 3, Opcode.LSR: 3,
        Opcode.ASR: 3, Opcode.ROR: 3,
        Opcode.MVN: 2, Opcode.EOR: 3,
        Opcode.BIC: 3, Opcode.ORN: 3,
        Opcode.LDR: 2, Opcode.STR: 2,
        Opcode.LDP: 3, Opcode.STP: 3,
        Opcode.CBZ: 2, Opcode.CBNZ: 2,
        Opcode.TBZ: 3, Opcode.TBNZ: 3,
        Opcode.B: -1, Opcode.BL: 1, Opcode.BR: 1,
        Opcode.NOP: 0, Opcode.WFE: 0,
        Opcode.WFI: 0, Opcode.SEV: 0,
        Opcode.CSEL: 4, Opcode.CSINC: 4,
        Opcode.CSINV: 4, Opcode.CSNEG: 4,
        Opcode.SXTB: 2, Opcode.SXTH: 2,
        Opcode.SXTW: 2,
        Opcode.UXTB: 2, Opcode.UXTH: 2,
        Opcode.CLZ: 2, Opcode.CLS: 2,
        Opcode.RBIT: 2, Opcode.REV: 2,
        Opcode.FADD: 3, Opcode.FSUB: 3,
        Opcode.FMUL: 3, Opcode.FDIV: 3,
        Opcode.FCMP: 2, Opcode.FCVT: 2,
        Opcode.FABS: 2, Opcode.FNEG: 2,
        Opcode.LDRS: 2, Opcode.STRS: 2,
        Opcode.VADD: 3, Opcode.VSUB: 3,
        Opcode.VMUL: 3, Opcode.VDIV: 3,
        Opcode.VLD1: 2, Opcode.VST1: 2,
        Opcode.LB: 2, Opcode.LH: 2, Opcode.LW: 2,
        Opcode.LD: 2, Opcode.SB: 2, Opcode.SH: 2,
        Opcode.SW: 2, Opcode.SD: 2,
        Opcode.ADDI: 3, Opcode.SLTI: 3,
        Opcode.SLTIU: 3, Opcode.XORI: 3,
        Opcode.ORI: 3, Opcode.ANDI: 3,
        Opcode.SLLI: 3, Opcode.SRLI: 3,
        Opcode.SRAI: 3,
        Opcode.BEQ: 3, Opcode.BNE: 3,
        Opcode.BLT: 3, Opcode.BGE: 3,
        Opcode.BLTU: 3, Opcode.BGEU: 3,
        Opcode.JALR: -1, Opcode.JAL: 2,
        Opcode.LUI: 2, Opcode.AUIPC: 2,
        Opcode.SYS: -1,
    }

    CONDITIONS: Set[str] = {
        'EQ', 'NE', 'CS', 'CC', 'MI', 'PL', 'VS', 'VC',
        'HI', 'LS', 'GE', 'LT', 'GT', 'LE', 'AL', 'NV'
    }

    DATA_DIRECTIVES: Set[str] = {
        'DB', 'DW', 'DD', 'DQ', 'BYTE', 'WORD', 'DWORD', 'QWORD',
        'ASCII', 'ASCIZ', 'STRING'
    }

    PL_KEYWORDS: Dict[str, str] = {
        'set': 'MOV', 'load': 'LOAD', 'store': 'STORE',
        'add': 'ADD', 'subtract': 'SUB', 'multiply': 'MUL', 'divide': 'DIV',
        'and': 'AND', 'or': 'OR', 'xor': 'XOR',
        'shift_left': 'SHL', 'shift_right': 'SHR',
        'increment': 'INC', 'decrement': 'DEC',
        'compare': 'CMP', 'jump': 'JMP', 'jump_zero': 'JZ',
        'jump_not_zero': 'JNZ', 'jump_equal': 'JE',
        'jump_less': 'JL', 'jump_greater': 'JG',
        'push': 'PUSH', 'pop': 'POP', 'call': 'CALL',
        'return': 'RET', 'input': 'IN', 'output': 'OUT', 'stop': 'HALT',
        'add_set': 'ADDS', 'subtract_set': 'SUBS',
        'add_carry': 'ADDC', 'subtract_carry': 'SUBC',
        'logical_shift_left': 'LSL', 'logical_shift_right': 'LSR',
        'arithmetic_shift_right': 'ASR', 'rotate_right': 'ROR',
        'move_not': 'MVN', 'exclusive_or': 'EOR',
        'bit_clear': 'BIC', 'or_not': 'ORN',
        'load_register': 'LDR', 'store_register': 'STR',
        'load_pair': 'LDP', 'store_pair': 'STP',
        'compare_branch_zero': 'CBZ', 'compare_branch_not_zero': 'CBNZ',
        'test_branch_zero': 'TBZ', 'test_branch_not_zero': 'TBNZ',
        'branch': 'B', 'branch_link': 'BL', 'branch_register': 'BR',
        'nop': 'NOP', 'wait_event': 'WFE', 'wait_interrupt': 'WFI',
        'send_event': 'SEV',
        'select': 'CSEL', 'select_increment': 'CSINC',
        'select_invert': 'CSINV', 'select_negate': 'CSNEG',
        'sign_extend_byte': 'SXTB', 'sign_extend_half': 'SXTH',
        'sign_extend_word': 'SXTW', 'zero_extend_byte': 'UXTB',
        'zero_extend_half': 'UXTH', 'count_leading_zeros': 'CLZ',
        'count_leading_sign': 'CLS', 'reverse_bits': 'RBIT',
        'reverse_bytes': 'REV',
        'float_add': 'FADD', 'float_subtract': 'FSUB',
        'float_multiply': 'FMUL', 'float_divide': 'FDIV',
        'float_compare': 'FCMP', 'float_convert': 'FCVT',
        'float_abs': 'FABS', 'float_negate': 'FNEG',
        'load_float': 'LDRS', 'store_float': 'STRS',
        'vec_add': 'VADD', 'vec_subtract': 'VSUB',
        'vec_multiply': 'VMUL', 'vec_divide': 'VDIV',
        'vec_load': 'VLD1', 'vec_store': 'VST1',
        'load_byte': 'LB', 'load_half': 'LH', 'load_word': 'LW',
        'load_double': 'LD',
        'store_byte': 'SB', 'store_half': 'SH', 'store_word': 'SW',
        'store_double': 'SD',
        'add_imm': 'ADDI', 'set_less_than_imm': 'SLTI',
        'set_less_than_imm_unsigned': 'SLTIU',
        'xor_imm': 'XORI', 'or_imm': 'ORI', 'and_imm': 'ANDI',
        'shift_left_logical_imm': 'SLLI',
        'shift_right_logical_imm': 'SRLI',
        'shift_right_arithmetic_imm': 'SRAI',
        'branch_equal': 'BEQ', 'branch_not_equal': 'BNE',
        'branch_less_than': 'BLT', 'branch_greater_equal': 'BGE',
        'branch_less_than_unsigned': 'BLTU',
        'branch_greater_equal_unsigned': 'BGEU',
        'jump_and_link_register': 'JALR',
        'jump_and_link': 'JAL',
        'load_upper_imm': 'LUI', 'add_upper_imm_pc': 'AUIPC',
        'syscall': 'SYS',
    }

    # 分支类指令 (统计用)
    BRANCH_OPS = frozenset({
        'JMP', 'JZ', 'JNZ', 'JE', 'JL', 'JG', 'B', 'BL', 'BR',
        'BEQ', 'BNE', 'BLT', 'BGE', 'BLTU', 'BGEU',
        'CBZ', 'CBNZ', 'TBZ', 'TBNZ', 'JAL', 'JALR', 'CALL', 'RET',
    })

    FP_OPS = frozenset({
        'FADD', 'FSUB', 'FMUL', 'FDIV', 'VADD', 'VSUB', 'VMUL', 'VDIV',
    })
