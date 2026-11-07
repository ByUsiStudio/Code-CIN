package mc

import (
	"encoding/binary"
	"fmt"

	"codecin-native/ir"
)

const (
	baseAddr = 0x400000
	dataAddr = 0x402000
	regCount = 33
	flagOff  = 0x400 // zf at +0x400, lf at +0x401 (相对 dataAddr)
	itoaBuf  = 0x4F0 // ITOA 缓冲区相对 dataBase 的偏移 (向前增长)
	// IR 里的数据地址 (如 0x1000) 会被搬到 dataBase+addr 虚地址。
	dataBase = 0x410000
)

// dataLen 返回静态字符串 (以 0 结尾) 的长度; 找不到返回 0。
func dataLen(p *ir.Program, addr int64) int {
	var last int64 = addr
	for _, w := range p.DataWrites {
		if int64(w.Addr) <= addr && int64(w.Addr)+int64(len(w.Data)) > addr {
			for i := addr - int64(w.Addr); i < int64(len(w.Data)); i++ {
				if w.Data[i] == 0 {
					return int(i - (addr - int64(w.Addr)))
				}
			}
		}
		if int64(w.Addr)+int64(len(w.Data)) > last {
			last = int64(w.Addr) + int64(len(w.Data))
		}
	}
	return 0
}


// emitITOA 把 R0 的十进制表示写到 dataBase+itoaBuf, 结果指针回填 R0。
// 使用 r8=10 做除法, r9b 记录负号; 缓冲区自 itoaBuf 向前增长。
func (e *Encoder) emitITOA() {
	const buf = dataBase + itoaBuf
	e.loadReg(0)                    // rax = R0
	e.raw(0x48, 0xBE); e.imm64(buf) // rsi = buf
	e.raw(0xC6, 0x06, 0x00)         // mov byte [rsi], 0
	e.raw(0x45, 0x31, 0xC9)         // xor r9d, r9d
	e.raw(0x48, 0x85, 0xC0)         // test rax, rax
	e.raw(0x0F, 0x89)
	jnsPos := len(e.code)
	e.imm32(0) // jns after neg
	e.raw(0x48, 0xF7, 0xD8)         // neg rax
	e.raw(0x41, 0xB1, 0x01)         // mov r9b, 1
	afterNeg := len(e.code)
	e.patchRel(jnsPos, afterNeg)
	e.raw(0x41, 0xB8); e.imm32(10)  // mov r8d, 10
	loopStart := len(e.code)
	e.raw(0x48, 0x31, 0xD2)         // xor rdx, rdx
	e.raw(0x49, 0xF7, 0xF0)         // div r8
	e.raw(0x48, 0xFF, 0xCE)         // dec rsi
	e.raw(0x80, 0xC2, 0x30)         // add dl, '0'
	e.raw(0x88, 0x16)               // mov [rsi], dl
	e.raw(0x48, 0x85, 0xC0)         // test rax, rax
	e.raw(0x0F, 0x85)
	jnzPos := len(e.code)
	e.imm32(0) // jnz loopStart
	e.patchRel(jnzPos, loopStart)
	e.raw(0x45, 0x85, 0xC9)         // test r9d, r9d
	e.raw(0x0F, 0x84)
	jzPos := len(e.code)
	e.imm32(0) // jz done
	e.raw(0x48, 0xFF, 0xCE)         // dec rsi
	e.raw(0xC6, 0x06, 0x2D)         // mov byte [rsi], '-'
	done := len(e.code)
	e.patchRel(jzPos, done)
	e.raw(0x48, 0x89, 0xF0)               // mov rax, rsi
	e.raw(0x48, 0x2D); e.imm32(dataBase)  // sub rax, dataBase -> VM 地址
	e.raw(0x48, 0x89, 0x04, 0x25); e.imm32(regAddr(0)) // [R0] = rax
}


const memSize = 1 << 30 // VM 地址空间 (mmap 惰性分配)

// prologue mmap 一块 VM 内存, 基址存入 r15, 并初始化 R32 (SP)。
func (e *Encoder) prologue() {
	e.raw(0x31, 0xFF)             // xor edi, edi   (addr = NULL)
	e.raw(0xBE); e.imm32(memSize) // mov esi, memSize
	e.raw(0xBA); e.imm32(3)       // mov edx, PROT_READ|PROT_WRITE
	e.raw(0x41, 0xBA); e.imm32(0x22) // mov r10d, MAP_PRIVATE|MAP_ANONYMOUS
	e.raw(0x41, 0xB8); e.imm32(-1)   // mov r8d, -1 (fd)
	e.raw(0x45, 0x31, 0xC9)       // xor r9d, r9d (offset)
	e.raw(0xB8); e.imm32(9)       // mov eax, SYS_mmap
	e.raw(0x0F, 0x05)
	e.raw(0x49, 0x89, 0xC7)       // mov r15, rax
	// R32 = memSize - 8
	e.raw(0x48, 0xB8); e.imm64(memSize - 8)
	e.raw(0x48, 0x89, 0x04, 0x25); e.imm32(regAddr(32))
}

// vmAddr 计算 rax = r15 + R[base] + off。
func (e *Encoder) vmAddr(base int, off int32) {
	e.raw(0x4C, 0x89, 0xF8)             // mov rax, r15
	e.raw(0x48, 0x03, 0x04, 0x25); e.imm32(regAddr(base)) // add rax, [Rbase]
	e.raw(0x48, 0x05); e.imm32(off)     // add rax, off
}

// loadMem 读 8 字节到 rd。
func (e *Encoder) loadMem(rd, base int, off int32) {
	e.vmAddr(base, off)
	e.raw(0x48, 0x8B, 0x00)             // mov rax, [rax]
	e.storeReg(rd)
}

// storeMem 把 rs 写 8 字节到内存。
func (e *Encoder) storeMem(rs, base int, off int32) {
	e.vmAddr(base, off)
	e.raw(0x48, 0x8B, 0x14, 0x25); e.imm32(regAddr(rs)) // mov rdx, [Rs]
	e.raw(0x48, 0x89, 0x10)             // mov [rax], rdx
}

// outStr 打印静态字符串 (write(1, dataBase+addr, len))。
func (e *Encoder) outStr(addr int64, n int) {
	e.raw(0xB8)
	e.imm32(1) // rax = SYS_write
	e.raw(0x48, 0xC7, 0xC7)
	e.imm32(1) // rdi = fd 1
	e.raw(0x48, 0xBE)
	e.imm64(int64(dataBase) + addr) // rsi = ptr
	e.raw(0x48, 0xC7, 0xC2)
	e.imm32(int32(n)) // rdx = len
	e.raw(0x0F, 0x05)
}

// Encoder 生成 x86-64 代码字节。
type Encoder struct {
	code []byte
}

// ErrUnsupported 表示 IR 里出现当前后端尚未实现的指令。
type ErrUnsupported struct{ Op string }

func (e ErrUnsupported) Error() string { return "x86-64 后端暂不支持指令: " + e.Op }

func (e *Encoder) raw(bs ...byte) { e.code = append(e.code, bs...) }
func (e *Encoder) imm32(v int32) {
	var b [4]byte
	binary.LittleEndian.PutUint32(b[:], uint32(v))
	e.raw(b[:]...)
}
func (e *Encoder) imm64(v int64) {
	var b [8]byte
	binary.LittleEndian.PutUint64(b[:], uint64(v))
	e.raw(b[:]...)
}
func regAddr(r int) int32 { return int32(dataAddr + r*8) }
func flagAddr(off int) int32 { return int32(dataAddr + flagOff + off) }

func (e *Encoder) movImm(r int, v int64) {
	e.raw(0x48, 0xB8)
	e.imm64(v)
	e.raw(0x48, 0x89, 0x04, 0x25)
	e.imm32(regAddr(r))
}

func (e *Encoder) loadReg(r int) {
	e.raw(0x48, 0x8B, 0x04, 0x25)
	e.imm32(regAddr(r))
}

func (e *Encoder) storeReg(r int) {
	e.raw(0x48, 0x89, 0x04, 0x25)
	e.imm32(regAddr(r))
}

func (e *Encoder) addImm(r int, v int32) {
	e.loadReg(r)
	e.raw(0x48, 0x05)
	e.imm32(v)
	e.storeReg(r)
}

func (e *Encoder) subImm(r int, v int32) {
	e.loadReg(r)
	e.raw(0x48, 0x2D)
	e.imm32(v)
	e.storeReg(r)
}

// cmpImm 比较 r 与立即数, 把 ZF/LF 写入标志字节。
func (e *Encoder) cmpImm(r int, v int32) {
	e.loadReg(r)
	e.raw(0x48, 0x3D) // cmp rax, imm32
	e.imm32(v)
	e.raw(0x0F, 0x94, 0x04, 0x25) // sete byte [zf]
	e.imm32(flagAddr(0))
	e.raw(0x0F, 0x9C, 0x04, 0x25) // setl byte [lf]
	e.imm32(flagAddr(1))
}

// cmpFlag 生成 "读取标志字节并条件跳转" 的短序列。
func (e *Encoder) cmpFlag(off int) {
	e.raw(0x80, 0x3C, 0x25) // cmp byte [abs], 0
	e.imm32(flagAddr(off))
	e.raw(0x00)
}

func (e *Encoder) jmpPlaceholder() int {
	e.raw(0xE9)
	pos := len(e.code)
	e.imm32(0)
	return pos
}

func (e *Encoder) jccPlaceholder(op []byte) int {
	e.raw(op...)
	pos := len(e.code)
	e.imm32(0)
	return pos
}

// imm32Pos 写入一个占位 rel32 并返回它的位置 (用于 CALL)。
func (e *Encoder) imm32Pos() int {
	pos := len(e.code)
	e.imm32(0)
	return pos
}

// patchRel 把 rel32 填成目标偏移。
func (e *Encoder) patchRel(pos, target int) {
	rel := int32(target - (pos + 4))
	binary.LittleEndian.PutUint32(e.code[pos:], uint32(rel))
}

func (e *Encoder) exitWithReg(r int) {
	e.loadReg(r)
	e.raw(0x48, 0x89, 0xC7) // mov rdi, rax
	e.raw(0xB8)
	e.imm32(60)       // SYS_exit
	e.raw(0x0F, 0x05) // syscall
}

func regImm(ins ir.Instr) (int, int64, error) {
	if len(ins.Args) != 2 || ins.Args[0].Kind != "reg" || ins.Args[1].Kind != "imm" {
		return 0, 0, fmt.Errorf("%s: 目前只支持 reg, imm 形式", ins.Op)
	}
	return int(ins.Args[0].V), ins.Args[1].V, nil
}

func labelOf(ins ir.Instr) (string, error) {
	if len(ins.Args) != 1 || ins.Args[0].Kind != "label" {
		return "", fmt.Errorf("%s: 需要 label 操作数", ins.Op)
	}
	return ins.Args[0].Name, nil
}

// Encode 把 IR 编译为机器码 (入口为偏移 0)。
func Encode(p *ir.Program) ([]byte, error) {
	e := &Encoder{}
	e.prologue()
	offs := make([]int, len(p.Instructions))
	type fix struct {
		pos   int
		label string
	}
	var fixups []fix
	sawHalt := false

	for i, ins := range p.Instructions {
		offs[i] = len(e.code)
		switch ins.Op {
		case "MOV":
			if len(ins.Args) == 2 && ins.Args[0].Kind == "reg" && ins.Args[1].Kind == "reg" {
				e.loadReg(int(ins.Args[1].V))
				e.storeReg(int(ins.Args[0].V))
				continue
			}
			r, v, err := regImm(ins)
			if err != nil {
				return nil, err
			}
			e.movImm(r, v)
		case "INC":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("INC: 需要 reg 操作数")
			}
			e.addImm(int(ins.Args[0].V), 1)
		case "DEC":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("DEC: 需要 reg 操作数")
			}
			e.subImm(int(ins.Args[0].V), 1)
		case "CMP":
			r, v, err := regImm(ins)
			if err != nil {
				return nil, err
			}
			e.cmpImm(r, int32(v))
		case "JMP":
			name, err := labelOf(ins)
			if err != nil {
				return nil, err
			}
			fixups = append(fixups, fix{e.jmpPlaceholder(), name})
		case "JZ", "JE":
			name, err := labelOf(ins)
			if err != nil {
				return nil, err
			}
			e.cmpFlag(0)
			// 标志字节为 1 表示 ZF=1, 因此用 JNE 跳转
			fixups = append(fixups, fix{e.jccPlaceholder([]byte{0x0F, 0x85}), name})
		case "JNZ":
			name, err := labelOf(ins)
			if err != nil {
				return nil, err
			}
			e.cmpFlag(0)
			// 标志字节为 0 表示 ZF=0, 因此用 JE 跳转
			fixups = append(fixups, fix{e.jccPlaceholder([]byte{0x0F, 0x84}), name})
		case "OUT":
			if len(ins.Args) == 1 && ins.Args[0].Kind == "imm" && ins.Args[0].V == 10 {
				e.raw(0x48, 0x83, 0xEC, 0x08)       // sub rsp, 8
				e.raw(0xC6, 0x04, 0x24, 0x0A)       // mov byte [rsp], 10
				e.raw(0x48, 0x89, 0xE6)             // mov rsi, rsp
				e.raw(0xB8); e.imm32(1)             // rax = write
				e.raw(0x48, 0xC7, 0xC7); e.imm32(1) // rdi = 1
				e.raw(0x48, 0xC7, 0xC2); e.imm32(1) // rdx = 1
				e.raw(0x0F, 0x05)
				e.raw(0x48, 0x83, 0xC4, 0x08)       // add rsp, 8
				continue
			}
			if len(ins.Args) != 1 || ins.Args[0].Kind != "str" {
				return nil, ErrUnsupported{Op: "OUT(非字符串)"}
			}
			addr := ins.Args[0].V
			e.outStr(addr, dataLen(p, addr))
		case "ADD", "SUB", "AND", "OR", "XOR":
			if len(ins.Args) == 2 && ins.Args[0].Kind == "reg" && ins.Args[1].Kind == "reg" {
				rd, rs := int(ins.Args[0].V), int(ins.Args[1].V)
				e.loadReg(rd)
				switch ins.Op {
				case "ADD":
					e.raw(0x48, 0x03, 0x04, 0x25)
				case "SUB":
					e.raw(0x48, 0x2B, 0x04, 0x25)
				case "AND":
					e.raw(0x48, 0x23, 0x04, 0x25)
				case "OR":
					e.raw(0x48, 0x0B, 0x04, 0x25)
				default:
					e.raw(0x48, 0x33, 0x04, 0x25)
				}
				e.imm32(regAddr(rs))
				e.storeReg(rd)
				continue
			}
			switch ins.Op {
			case "ADD":
				r, v, err := regImm(ins)
				if err != nil {
					return nil, err
				}
				e.addImm(r, int32(v))
			case "SUB":
				r, v, err := regImm(ins)
				if err != nil {
					return nil, err
				}
				e.subImm(r, int32(v))
			default:
				return nil, ErrUnsupported{Op: ins.Op}
			}
		case "ADDI":
			if len(ins.Args) != 3 || ins.Args[0].Kind != "reg" || ins.Args[1].Kind != "reg" || ins.Args[2].Kind != "imm" {
				return nil, fmt.Errorf("ADDI: 需要 reg, reg, imm")
			}
			e.loadReg(int(ins.Args[1].V))
			e.raw(0x48, 0x05); e.imm32(int32(ins.Args[2].V))
			e.storeReg(int(ins.Args[0].V))
		case "LD", "SD":
			if len(ins.Args) != 2 || ins.Args[1].Kind != "mem" {
				return nil, fmt.Errorf("%s: 需要 reg, mem", ins.Op)
			}
			base := int(ins.Args[1].V)
			off := int32(ins.Args[1].Off)
			if ins.Op == "LD" {
				e.loadMem(int(ins.Args[0].V), base, off)
			} else {
				e.storeMem(int(ins.Args[0].V), base, off)
			}
		case "MUL":
			if len(ins.Args) != 2 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("MUL: 需要 reg, reg|imm")
			}
			rd := int(ins.Args[0].V)
			if ins.Args[1].Kind == "reg" {
				e.loadReg(rd)
				e.raw(0x48, 0x0F, 0xAF, 0x04, 0x25)
				e.imm32(regAddr(int(ins.Args[1].V)))
				e.storeReg(rd)
			} else if ins.Args[1].Kind == "imm" {
				e.loadReg(rd)
				e.raw(0x48, 0x69, 0xC0)
				e.imm32(int32(ins.Args[1].V))
				e.storeReg(rd)
			} else {
				return nil, fmt.Errorf("MUL: 操作数不支持")
			}
		case "DIV", "MOD", "REM":
			if len(ins.Args) != 2 || ins.Args[0].Kind != "reg" || ins.Args[1].Kind != "reg" {
				return nil, fmt.Errorf("%s: 需要 reg, reg", ins.Op)
			}
			rd, rs := int(ins.Args[0].V), int(ins.Args[1].V)
			e.loadReg(rd)
			e.raw(0x48, 0x99)                        // cqo
			e.raw(0x48, 0xF7, 0x3C, 0x25)            // idiv qword [Rs]
			e.imm32(regAddr(rs))
			if ins.Op == "DIV" {
				e.storeReg(rd)
			} else {
				e.raw(0x48, 0x89, 0x14, 0x25)        // [Rd] = rdx
				e.imm32(regAddr(rd))
			}
		case "CALL":
			name, err := labelOf(ins)
			if err != nil {
				return nil, err
			}
			e.raw(0xE8)
			fixups = append(fixups, fix{e.imm32Pos(), name})
		case "RET":
			e.raw(0xC3)
		case "PUSH":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("PUSH: 需要 reg 操作数")
			}
			e.raw(0x48, 0x83, 0xEC, 0x08) // sub rsp, 8
			e.loadReg(int(ins.Args[0].V))
			e.raw(0x48, 0x89, 0x04, 0x24) // mov [rsp], rax
		case "POP":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("POP: 需要 reg 操作数")
			}
			e.raw(0x48, 0x8B, 0x04, 0x24) // mov rax, [rsp]
			e.raw(0x48, 0x83, 0xC4, 0x08) // add rsp, 8
			e.storeReg(int(ins.Args[0].V))
		case "SYS":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "imm" {
				return nil, fmt.Errorf("SYS: 需要立即数功能号")
			}
			switch ins.Args[0].V {
			case 24: // PRINT_STR: x0 指向程序内存里的 NUL 结尾字符串
				// rsi = dataBase + R0
				e.raw(0x48, 0x8B, 0x34, 0x25); e.imm32(regAddr(0))
				e.raw(0x48, 0x81, 0xC6); e.imm32(dataBase)
				// rdx = strlen(rsi)
				e.raw(0x31, 0xD2)             // xor edx, edx
				strLoop := len(e.code)
				e.raw(0x80, 0x3C, 0x16, 0x00) // cmp byte [rsi+rdx], 0
				e.raw(0x74, 0x05)             // je +5 (跳过 inc+jmp)
				e.raw(0x48, 0xFF, 0xC2)       // inc rdx
				rel := int32(strLoop - (len(e.code) + 2))
				e.raw(0xEB); e.raw(byte(int8(rel))) // jmp strLoop
				// write(1, rsi, rdx)
				e.raw(0xB8); e.imm32(1)
				e.raw(0x48, 0xC7, 0xC7); e.imm32(1)
				e.raw(0x0F, 0x05)
			case 12: // ITOF: R0 = double(R0) 的位模式
				e.loadReg(0)
				e.raw(0xF2, 0x48, 0x0F, 0x2A, 0xC0) // cvtsi2sd xmm0, rax
				e.raw(0x66, 0x48, 0x0F, 0x7E, 0xC0) // movq rax, xmm0
				e.storeReg(0)
			case 11: // FTOI: R0 = (int64)double(R0)
				e.loadReg(0)
				e.raw(0x66, 0x48, 0x0F, 0x6E, 0xC0) // movq xmm0, rax
				e.raw(0xF2, 0x48, 0x0F, 0x2C, 0xC0) // cvttsd2si rax, xmm0
				e.storeReg(0)
			case 6, 7, 8, 9: // FADD/FSUB/FMUL/FDIV (R0 op= R1)
				e.raw(0xF2, 0x0F, 0x10, 0x04, 0x25); e.imm32(regAddr(0))
				op := byte(0x58)
				switch ins.Args[0].V {
				case 7:
					op = 0x5C
				case 8:
					op = 0x59
				case 9:
					op = 0x5E
				}
				e.raw(0xF2, 0x0F, op, 0x04, 0x25); e.imm32(regAddr(1))
				e.raw(0xF2, 0x0F, 0x11, 0x04, 0x25); e.imm32(regAddr(0))
			case 23: // FTOA: 先截断为整数再按十进制输出 (整数场景等价)
				e.loadReg(0)
				e.raw(0x66, 0x48, 0x0F, 0x6E, 0xC0) // movq xmm0, rax
				e.raw(0xF2, 0x48, 0x0F, 0x2C, 0xC0) // cvttsd2si rax, xmm0
				e.storeReg(0)
				e.emitITOA()
			case 22: // ITOA
				e.emitITOA()
			case 137: // ALLOCFRAME: R32 -= n; R0 = R32
				e.loadReg(0)                                        // rax = n
				e.raw(0x48, 0x8B, 0x14, 0x25); e.imm32(regAddr(32)) // rdx = SP
				e.raw(0x48, 0x29, 0xC2)                             // sub rdx, rax
				e.raw(0x48, 0x89, 0x14, 0x25); e.imm32(regAddr(32))
				e.raw(0x48, 0x89, 0x14, 0x25); e.imm32(regAddr(0))
			default:
				return nil, ErrUnsupported{Op: fmt.Sprintf("SYS %d", ins.Args[0].V)}
			}
		case "HALT":
			e.exitWithReg(0)
			sawHalt = true
			continue
		default:
			return nil, ErrUnsupported{Op: ins.Op}
		}
	}
	if !sawHalt {
		return nil, fmt.Errorf("程序缺少 HALT")
	}
	for _, f := range fixups {
		idx, ok := p.Labels[f.label]
		if !ok {
			return nil, fmt.Errorf("未定义标签: %s", f.label)
		}
		e.patchRel(f.pos, offs[idx])
	}
	return e.code, nil
}
