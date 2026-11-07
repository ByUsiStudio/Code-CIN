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
	e.loadReg(0)                              // rax = R0
	e.raw(0x4C, 0x89, 0xFE)                   // mov rsi, r15
	e.raw(0x48, 0x81, 0xC6); e.imm32(itoaBuf) // rsi += itoaBuf
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
	e.raw(0x4C, 0x29, 0xF8)               // sub rax, r15 -> VM 地址
	e.raw(0x48, 0x89, 0x04, 0x25); e.imm32(regAddr(0)) // [R0] = rax
}


const memSize = 1 << 30 // VM 地址空间 (mmap 惰性分配)

// prologue mmap 一块 VM 内存, 基址存入 r15, 并初始化 R32 (SP)。
func (e *Encoder) prologue(writes []ir.DataWrite) {
	e.raw(0x31, 0xFF)             // xor edi, edi   (addr = NULL)
	e.raw(0xBE); e.imm32(memSize) // mov esi, memSize
	e.raw(0xBA); e.imm32(3)       // mov edx, PROT_READ|PROT_WRITE
	e.raw(0x41, 0xBA); e.imm32(0x22) // mov r10d, MAP_PRIVATE|MAP_ANONYMOUS
	e.raw(0x41, 0xB8); e.imm32(-1)   // mov r8d, -1 (fd)
	e.raw(0x45, 0x31, 0xC9)       // xor r9d, r9d (offset)
	e.raw(0xB8); e.imm32(9)       // mov eax, SYS_mmap
	e.raw(0x0F, 0x05)
	e.raw(0x49, 0x89, 0xC7)       // mov r15, rax
	// 把静态数据搬进 VM 内存 (源: ELF 数据段, 目标: r15 + addr)
	for _, w := range writes {
		e.raw(0x48, 0xBE); e.imm64(int64(dataBase + w.Addr)) // rsi = 源
		e.raw(0x4C, 0x89, 0xFF)                              // rdi = r15
		e.raw(0x48, 0x81, 0xC7); e.imm32(int32(w.Addr))      // rdi += addr
		e.raw(0x48, 0xC7, 0xC1); e.imm32(int32(len(w.Data))) // rcx = len
		e.raw(0xF3, 0xA4)                                    // rep movsb
	}
	// heap = VM 0x100000
	e.raw(0x48, 0xB8); e.imm64(0x100000)
	e.raw(0x48, 0x89, 0x04, 0x25); e.imm32(int32(dataBase+heapPtrOff))
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


// heapPtrOff: VM 堆顶 (VM 地址) 保存在 dataBase+heapPtrOff。
const heapPtrOff = 0x480

// randState: xorshift 状态所在地址 (dataBase + 0x488)
const randState = dataBase + 0x488

// emitMalloc: R0 = 分配 size 字节 (bump allocator), 返回 VM 地址。
func (e *Encoder) emitMalloc() {
	e.raw(0x48, 0x8B, 0x04, 0x25); e.imm32(regAddr(0))          // rax = size
	e.raw(0x48, 0x83, 0xC0, 0x07)                                // add rax, 7
	e.raw(0x48, 0x83, 0xE0, 0xF8)                                // and rax, -8
	e.raw(0x48, 0x8B, 0x14, 0x25); e.imm32(int32(dataBase+heapPtrOff)) // rdx = heap
	e.raw(0x48, 0x89, 0xD1)                                      // rcx = old
	e.raw(0x48, 0x01, 0xC2)                                      // rdx += rax
	e.raw(0x48, 0x89, 0x14, 0x25); e.imm32(int32(dataBase+heapPtrOff))
	e.raw(0x48, 0x89, 0x0C, 0x25); e.imm32(regAddr(0))           // R0 = old
}

// emitStrCopy: rax = 源 VM 地址, rdi = 目标宿主指针; 复制到 NUL 之后, rdi 指向结尾。
func (e *Encoder) emitStrCopy() {
	e.raw(0x4C, 0x89, 0xF9)             // mov rcx, r15
	e.raw(0x48, 0x01, 0xC1)             // add rcx, rax
	loop := len(e.code)
	e.raw(0x8A, 0x11)                   // mov dl, [rcx]
	e.raw(0x88, 0x17)                   // mov [rdi], dl
	e.raw(0x84, 0xD2)                   // test dl, dl
	e.raw(0x0F, 0x84)
	jzPos := len(e.code)
	e.imm32(0)
	e.raw(0x48, 0xFF, 0xC1)             // inc rcx
	e.raw(0x48, 0xFF, 0xC7)             // inc rdi
	e.raw(0xE9)
	e.imm32(int32(loop - (len(e.code) + 4)))
	e.patchRel(jzPos, len(e.code))
}

// emitStrlen: rax = VM 地址 -> rax = 长度
func (e *Encoder) emitStrlen() {
	e.raw(0x4C, 0x89, 0xF9)             // mov rcx, r15
	e.raw(0x48, 0x01, 0xC1)             // add rcx, rax
	e.raw(0x48, 0x31, 0xD2)             // xor rdx, rdx
	loop := len(e.code)
	e.raw(0x80, 0x3C, 0x11, 0x00)       // cmp byte [rcx+rdx], 0
	e.raw(0x0F, 0x84)
	jzPos := len(e.code)
	e.imm32(0)
	e.raw(0x48, 0xFF, 0xC2)             // inc rdx
	e.raw(0xE9)
	e.imm32(int32(loop - (len(e.code) + 4)))
	e.patchRel(jzPos, len(e.code))
	e.raw(0x48, 0x89, 0xD0)             // mov rax, rdx
}


// emitCaseConv: R0 = 字符串 VM 地址; upper 为 true 转大写, 否则小写。
func (e *Encoder) emitCaseConv(upper bool) {
	e.loadReg(0); e.storeReg(7)
	e.emitStrlen(); e.storeReg(9)
	e.raw(0x48, 0xFF, 0xC0)
	e.storeReg(0)
	e.emitMalloc()
	e.loadReg(0)
	e.raw(0x4C, 0x89, 0xFF); e.raw(0x48, 0x01, 0xC7) // rdi = r15 + dst
	e.loadReg(7)
	e.raw(0x4C, 0x89, 0xFE); e.raw(0x48, 0x01, 0xC6) // rsi = r15 + src
	e.loadReg(9)
	e.raw(0x48, 0x89, 0xC1) // rcx = len
	lo, hi, delta := byte(0x61), byte(0x7A), byte(0x20)
	if !upper {
		lo, hi = 0x41, 0x5A
	}
	loop := len(e.code)
	e.raw(0x8A, 0x06)       // mov al, [rsi]
	e.raw(0x3C); e.raw(lo)  // cmp al, lo
	e.raw(0x0F, 0x82)
	j1 := len(e.code); e.imm32(0)
	e.raw(0x3C); e.raw(hi)
	e.raw(0x0F, 0x87)
	j2 := len(e.code); e.imm32(0)
	if upper {
		e.raw(0x2C); e.raw(delta)
	} else {
		e.raw(0x04); e.raw(delta)
	}
	skip := len(e.code)
	e.patchRel(j1, skip)
	e.patchRel(j2, skip)
	e.raw(0x88, 0x07)
	e.raw(0x48, 0xFF, 0xC6)
	e.raw(0x48, 0xFF, 0xC7)
	e.raw(0x48, 0xFF, 0xC9)
	e.raw(0x0F, 0x85)
	j3 := len(e.code); e.imm32(0)
	e.patchRel(j3, loop)
	e.raw(0xC6, 0x07, 0x00)
}

// outStr 打印静态字符串 (write(1, dataBase+addr, len))。
func (e *Encoder) outStr(addr int64, n int) {
	e.raw(0xB8)
	e.imm32(1) // rax = SYS_write
	e.raw(0x48, 0xC7, 0xC7)
	e.imm32(1) // rdi = fd 1
	e.raw(0x4C, 0x89, 0xFE)                          // mov rsi, r15
	e.raw(0x48, 0x81, 0xC6); e.imm32(int32(addr))    // rsi += addr
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
	e.prologue(p.DataWrites)
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
			if len(ins.Args) == 2 && ins.Args[0].Kind == "reg" && ins.Args[1].Kind == "reg" {
				e.loadReg(int(ins.Args[0].V))
				e.raw(0x48, 0x3B, 0x04, 0x25)
				e.imm32(regAddr(int(ins.Args[1].V)))
				e.raw(0x0F, 0x94, 0x04, 0x25); e.imm32(flagAddr(0))
				e.raw(0x0F, 0x9C, 0x04, 0x25); e.imm32(flagAddr(1))
				continue
			}
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
		case "B":
			if len(ins.Args) != 2 || ins.Args[0].Kind != "label" || ins.Args[1].Kind != "cond" {
				return nil, fmt.Errorf("B: 需要 label, cond")
			}
			name := ins.Args[0].Name
			cond := ins.Args[1].Name
			jcc := func(op byte) {
				e.cmpFlag(0)
				if op == 0x85 {
					fixups = append(fixups, fix{e.jccPlaceholder([]byte{0x0F, 0x85}), name})
				} else {
					fixups = append(fixups, fix{e.jccPlaceholder([]byte{0x0F, 0x84}), name})
				}
			}
			flagJcc := func(off int, je bool) {
				e.cmpFlag(off)
				op := byte(0x85)
				if je {
					op = 0x84
				}
				fixups = append(fixups, fix{e.jccPlaceholder([]byte{0x0F, op}), name})
			}
			switch cond {
			case "EQ":
				jcc(0x85) // zf == 1
			case "NE":
				jcc(0x84) // zf == 0
			case "LT":
				flagJcc(1, false) // lf == 1
			case "GE":
				flagJcc(1, true) // lf == 0
			case "LE":
				flagJcc(1, false)
				flagJcc(0, false)
			case "GT":
				// lf == 0 且 zf == 0: 先跳过, 再判 zf
				e.cmpFlag(1)
				skipPos := e.jccPlaceholder([]byte{0x0F, 0x85})
				e.cmpFlag(0)
				fixups = append(fixups, fix{e.jccPlaceholder([]byte{0x0F, 0x84}), name})
				e.patchRel(skipPos, len(e.code))
			default:
				return nil, fmt.Errorf("B: 未知条件 %q", cond)
			}
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
			e.raw(0x48, 0x83, 0x2C, 0x25) // sub qword [R32], 8 (VM 返回槽)
			e.imm32(regAddr(32))
			e.raw(0x08)
			e.raw(0xE8)
			fixups = append(fixups, fix{e.imm32Pos(), name})
		case "RET":
			e.raw(0x48, 0x83, 0x04, 0x25) // add qword [R32], 8
			e.imm32(regAddr(32))
			e.raw(0x08)
			e.raw(0xC3)
		case "PUSH":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("PUSH: 需要 reg 操作数")
			}
			e.raw(0x48, 0x83, 0x2C, 0x25) // sub qword [R32], 8
			e.imm32(regAddr(32))
			e.raw(0x08)
			e.raw(0x48, 0x8B, 0x14, 0x25); e.imm32(regAddr(int(ins.Args[0].V)))
			e.raw(0x4C, 0x89, 0xF8)                        // mov rax, r15
			e.raw(0x48, 0x03, 0x04, 0x25); e.imm32(regAddr(32))
			e.raw(0x48, 0x89, 0x10)                        // mov [rax], rdx
		case "POP":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("POP: 需要 reg 操作数")
			}
			e.raw(0x4C, 0x89, 0xF8)
			e.raw(0x48, 0x03, 0x04, 0x25); e.imm32(regAddr(32))
			e.raw(0x48, 0x8B, 0x10)                        // mov rdx, [rax]
			e.raw(0x48, 0x89, 0x14, 0x25); e.imm32(regAddr(int(ins.Args[0].V)))
			e.raw(0x48, 0x83, 0x04, 0x25); e.imm32(regAddr(32))
			e.raw(0x08)                                    // add qword [R32], 8
		case "SYS":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "imm" {
				return nil, fmt.Errorf("SYS: 需要立即数功能号")
			}
			switch ins.Args[0].V {
			case 24: // PRINT_STR: x0 指向 VM 内存里的 NUL 结尾字符串
				e.raw(0x48, 0x8B, 0x34, 0x25); e.imm32(regAddr(0))
				e.raw(0x4C, 0x01, 0xFE)             // add rsi, r15
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
			case 16: // STRLEN: R0 = len(R0)
				e.loadReg(0)
				e.emitStrlen()
				e.storeReg(0)
			case 20: // MALLOC
				e.emitMalloc()
			case 17: // STRCMP: R0 = strcmp(R0, R1) 的符号结果
				e.raw(0x4C, 0x89, 0xFE)                        // rsi = r15
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(0))
				e.raw(0x4C, 0x89, 0xFF)                        // rdi = r15
				e.raw(0x48, 0x03, 0x3C, 0x25); e.imm32(regAddr(1))
				cmpLoop := len(e.code)
				e.raw(0x8A, 0x06)                              // mov al, [rsi]
				e.raw(0x8A, 0x1F)                              // mov bl, [rdi]
				e.raw(0x38, 0xD8)                              // cmp al, bl
				e.raw(0x0F, 0x85)
				nePos := len(e.code)
				e.imm32(0)
				e.raw(0x84, 0xC0)                              // test al, al
				e.raw(0x0F, 0x84)
				eqPos := len(e.code)
				e.imm32(0)
				e.raw(0x48, 0xFF, 0xC6)                        // inc rsi
				e.raw(0x48, 0xFF, 0xC7)                        // inc rdi
				e.raw(0xE9); e.imm32(int32(cmpLoop - (len(e.code) + 4)))
				neAddr := len(e.code)
				e.raw(0x0F, 0xB6, 0xC0)                        // movzx eax, al
				e.raw(0x0F, 0xB6, 0xDB)                        // movzx ebx, bl
				e.raw(0x29, 0xD8)                              // sub eax, ebx
				e.raw(0x48, 0x63, 0xC0)                        // movsxd rax, eax
				e.storeReg(0)
				e.raw(0xE9)
				endPos := len(e.code)
				e.imm32(0)
				eqAddr := len(e.code)
				e.raw(0x48, 0x31, 0xC0)                        // xor rax, rax
				e.storeReg(0)
				e.patchRel(nePos, neAddr)
				e.patchRel(eqPos, eqAddr)
				e.patchRel(endPos, len(e.code))
			case 28: // SUBSTR: R0=str, R1=start, R2=len
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(0))
				e.raw(0x48, 0x8B, 0x3C, 0x25); e.imm32(regAddr(1))
				e.raw(0x48, 0x01, 0xFE)                        // rsi += start
				e.raw(0x48, 0x8B, 0x0C, 0x25); e.imm32(regAddr(2))
				e.raw(0x48, 0x89, 0xF0); e.storeReg(7)         // src
				e.raw(0x48, 0x89, 0xC8); e.storeReg(8)         // len
				e.raw(0x48, 0xFF, 0xC0)                        // len+1
				e.storeReg(0)
				e.emitMalloc()
				e.loadReg(0)
				e.raw(0x4C, 0x89, 0xFF)                        // rdi = r15
				e.raw(0x48, 0x01, 0xC7)                        // rdi += dst
				e.loadReg(7)
				e.raw(0x48, 0x89, 0xC6)                        // rsi = src
				e.loadReg(8)
				e.raw(0x48, 0x89, 0xC1)                        // rcx = len
				e.raw(0xF3, 0xA4)                              // rep movsb
				e.raw(0xC6, 0x07, 0x00)                        // [rdi] = 0
			case 30: // TOUPPER
				e.emitCaseConv(true)
			case 31: // TOLOWER
				e.emitCaseConv(false)
			case 35: // ATOI: R0 = 十进制整数
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(0)) // rsi = str
				e.raw(0x45, 0x31, 0xC0)                            // xor r8d, r8d (sign)
				e.raw(0x0F, 0xB6, 0x0E)                            // movzx ecx, [rsi]
				e.raw(0x80, 0xF9, 0x2D)                            // cmp cl, '-'
				e.raw(0x0F, 0x85)
				notNeg := len(e.code); e.imm32(0)
				e.raw(0x41, 0xB0, 0x01)                            // mov r8b, 1
				e.raw(0x48, 0xFF, 0xC6)                            // inc rsi
				posNeg := len(e.code); e.patchRel(notNeg, posNeg)
				e.raw(0x48, 0x31, 0xC0)                            // xor rax, rax
				digits := len(e.code)
				e.raw(0x0F, 0xB6, 0x0E)                            // movzx ecx, [rsi]
				e.raw(0x83, 0xE9, 0x30)                            // sub ecx, '0'
				e.raw(0x83, 0xF9, 0x09)                            // cmp ecx, 9
				e.raw(0x0F, 0x87)
				donePos := len(e.code); e.imm32(0)
				e.raw(0x48, 0x6B, 0xC0, 0x0A)                      // imul rax, rax, 10
				e.raw(0x48, 0x01, 0xC8)                            // add rax, rcx
				e.raw(0x48, 0xFF, 0xC6)                            // inc rsi
				e.raw(0xE9); e.imm32(int32(digits - (len(e.code) + 4)))
				donePos2 := len(e.code); e.patchRel(donePos, donePos2)
				e.raw(0x45, 0x84, 0xC0)                            // test r8b, r8b
				e.raw(0x0F, 0x84)
				noNeg := len(e.code); e.imm32(0)
				e.raw(0x48, 0xF7, 0xD8)                            // neg rax
				noNeg2 := len(e.code); e.patchRel(noNeg, noNeg2)
				e.storeReg(0)
			case 26: // BOOLSTR: R0 = "true"/"false"
				e.raw(0x48, 0xC7, 0xC0); e.imm32(6) // size 6
				e.storeReg(0)
				e.emitMalloc()
				e.loadReg(0)
				e.raw(0x4C, 0x89, 0xFF); e.raw(0x48, 0x01, 0xC7) // rdi = r15 + dst
				e.raw(0x48, 0x8B, 0x04, 0x25); e.imm32(regAddr(0)) // rax = R0 (dst)
				e.raw(0x48, 0x85, 0xC0)           // test rax, rax
				e.raw(0x0F, 0x85)
				truePos := len(e.code); e.imm32(0)
				e.raw(0xC7, 0x07); e.imm32(0x736C6166) // "fals"
				e.raw(0xC6, 0x47, 0x04, 0x65)          // 'e'
				e.raw(0xC6, 0x47, 0x05, 0x00)
				e.raw(0xE9)
				endPos := len(e.code); e.imm32(0)
				trueAddr := len(e.code); e.patchRel(truePos, trueAddr)
				e.raw(0xC7, 0x07); e.imm32(0x65757274) // "true"
				e.raw(0xC6, 0x47, 0x04, 0x00)
				endAddr := len(e.code); e.patchRel(endPos, endAddr)
			case 29: // INDEXOF: R0 = 子串下标, 找不到为 -1
				e.raw(0x4C, 0x89, 0xFE)                              // rsi = r15
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(0))   // + hay
				e.raw(0x4C, 0x89, 0xFF)                              // rdi = r15
				e.raw(0x48, 0x03, 0x3C, 0x25); e.imm32(regAddr(1))   // + needle
				e.raw(0x4D, 0x31, 0xC0)                              // xor r8, r8
				outer := len(e.code)
				e.raw(0x42, 0x8A, 0x04, 0x06)                        // mov al, [rsi+r8]
				e.raw(0x84, 0xC0)                                    // test al, al
				e.raw(0x0F, 0x84)
				notFound := len(e.code); e.imm32(0)
				e.raw(0x4D, 0x31, 0xC9)                              // xor r9, r9
				inner := len(e.code)
				e.raw(0x42, 0x8A, 0x1C, 0x0F)                        // mov bl, [rdi+r9]
				e.raw(0x84, 0xDB)                                    // test bl, bl
				e.raw(0x0F, 0x84)
				found := len(e.code); e.imm32(0)
				e.raw(0x4D, 0x89, 0xC2)                              // mov r10, r8
				e.raw(0x4D, 0x01, 0xCA)                              // add r10, r9
				e.raw(0x42, 0x8A, 0x04, 0x16)                        // mov al, [rsi+r10]
				e.raw(0x38, 0xD8)                                    // cmp al, bl
				e.raw(0x0F, 0x85)
				mismatch := len(e.code); e.imm32(0)
				e.raw(0x49, 0xFF, 0xC1)                              // inc r9
				e.raw(0xE9); e.imm32(int32(inner - (len(e.code) + 4)))
				mismatchAddr := len(e.code); e.patchRel(mismatch, mismatchAddr)
				e.raw(0x49, 0xFF, 0xC0)                              // inc r8
				e.raw(0xE9); e.imm32(int32(outer - (len(e.code) + 4)))
				foundAddr := len(e.code); e.patchRel(found, foundAddr)
				e.raw(0x4C, 0x89, 0x04, 0x25); e.imm32(regAddr(0))   // [R0] = r8
				e.raw(0xE9)
				endPos := len(e.code); e.imm32(0)
				nfAddr := len(e.code); e.patchRel(notFound, nfAddr)
				e.raw(0x48, 0xC7, 0xC0); e.imm32(-1)                 // rax = -1
				e.storeReg(0)
				endAddr := len(e.code); e.patchRel(endPos, endAddr)
			case 15: // TIME: R0 = unix 秒
				e.raw(0xB8); e.imm32(201) // SYS_time
				e.raw(0x31, 0xFF)         // xor edi, edi
				e.raw(0x0F, 0x05)
				e.storeReg(0)
			case 13: // RAND: xorshift64
				e.raw(0x48, 0x8B, 0x04, 0x25); e.imm32(randState) // rax = s
				e.raw(0x48, 0x89, 0xC1)                            // rcx = s
				e.raw(0x48, 0xC1, 0xE1, 0x0D)                      // shl rcx, 13
				e.raw(0x48, 0x31, 0xC8)                            // xor rax, rcx
				e.raw(0x48, 0x89, 0xC1)
				e.raw(0x48, 0xC1, 0xE9, 0x07)                      // shr rcx, 7
				e.raw(0x48, 0x31, 0xC8)
				e.raw(0x48, 0x89, 0xC1)
				e.raw(0x48, 0xC1, 0xE1, 0x11)                      // shl rcx, 17
				e.raw(0x48, 0x31, 0xC8)
				e.raw(0x48, 0x89, 0x04, 0x25); e.imm32(randState)
				e.raw(0x48, 0x25); e.imm32(0x7FFFFFFF)             // 非负
				e.storeReg(0)
			case 14: // SRAND
				e.loadReg(0)
				e.raw(0x48, 0x89, 0x04, 0x25); e.imm32(randState)
			case 36, 37, 38: // TRIM / LTRIM / RTRIM
				doLeft := ins.Args[0].V != 38
				doRight := ins.Args[0].V != 37
				e.loadReg(0)
				e.emitStrlen()
				e.storeReg(9)                                  // len
				e.raw(0x4C, 0x89, 0xFE)                        // rsi = r15
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(0))
				e.loadReg(9)
				e.raw(0x49, 0x89, 0xC0)                        // r8 = len
				e.raw(0x49, 0x01, 0xF0)                        // r8 += rsi (end)
				if doLeft {
					leftLoop := len(e.code)
					e.raw(0x8A, 0x06)                          // mov al,[rsi]
					e.raw(0x3C, 0x20)                          // cmp al,32
					e.raw(0x0F, 0x84)
					l1 := len(e.code); e.imm32(0)
					e.raw(0x2C, 0x09)                          // sub al,9
					e.raw(0x3C, 0x04)                          // cmp al,4
					e.raw(0x0F, 0x86)
					l2 := len(e.code); e.imm32(0)
					e.raw(0xE9)
					l3 := len(e.code); e.imm32(0)
					wsAddr := len(e.code); e.patchRel(l1, wsAddr); e.patchRel(l2, wsAddr)
					e.raw(0x48, 0xFF, 0xC6)                    // inc rsi
					e.raw(0xE9); e.imm32(int32(leftLoop - (len(e.code) + 4)))
					notWs := len(e.code); e.patchRel(l3, notWs)
				}
				if doRight {
					rightLoop := len(e.code)
					e.raw(0x4C, 0x39, 0xC6)                    // cmp rsi, r8
					e.raw(0x0F, 0x84)
					r1 := len(e.code); e.imm32(0)
					e.raw(0x41, 0x8A, 0x40, 0xFF)              // mov al,[r8-1]
					e.raw(0x3C, 0x20)
					e.raw(0x0F, 0x84)
					r2 := len(e.code); e.imm32(0)
					e.raw(0x2C, 0x09)
					e.raw(0x3C, 0x04)
					e.raw(0x0F, 0x86)
					r3 := len(e.code); e.imm32(0)
					e.raw(0xE9)
					r4 := len(e.code); e.imm32(0)
					rws := len(e.code); e.patchRel(r2, rws); e.patchRel(r3, rws)
					e.raw(0x49, 0xFF, 0xC8)                    // dec r8
					e.raw(0xE9); e.imm32(int32(rightLoop - (len(e.code) + 4)))
					rdone := len(e.code); e.patchRel(r1, rdone); e.patchRel(r4, rdone)
				}
				e.raw(0x4C, 0x89, 0xC1)                        // rcx = r8
				e.raw(0x48, 0x29, 0xF1)                        // rcx -= rsi (len)
				e.raw(0x48, 0x89, 0xC8)                        // rax = rcx
				e.storeReg(9)                                  // 保存 len (malloc 会破坏 rcx)
				e.raw(0x48, 0xFF, 0xC0)                        // +1
				e.storeReg(0)
				e.emitMalloc()
				e.loadReg(0)
				e.raw(0x4C, 0x89, 0xFF)                        // rdi = r15
				e.raw(0x48, 0x01, 0xC7)                        // rdi += dst
				e.loadReg(9)
				e.raw(0x48, 0x89, 0xC1)                        // rcx = len
				e.raw(0xF3, 0xA4)                              // rep movsb
				e.raw(0xC6, 0x07, 0x00)                        // NUL
			case 52: // FILEWRITE(path=R0, data=R1)
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(0)) // rsi = path
				e.raw(0xB8); e.imm32(2)                            // open
				e.raw(0x48, 0x89, 0xF7)                            // rdi = path
				e.raw(0xBE); e.imm32(0x241)                        // O_WRONLY|CREAT|TRUNC
				e.raw(0xBA); e.imm32(0644)
				e.raw(0x0F, 0x05)
				e.raw(0x49, 0x89, 0xC0)                            // r8 = fd
				e.loadReg(1); e.emitStrlen()
				e.raw(0x48, 0x89, 0xC2)                            // rdx = len
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(1)) // rsi = data
				e.raw(0x44, 0x89, 0xC7)                            // edi = fd
				e.raw(0xB8); e.imm32(1)                            // write
				e.raw(0x0F, 0x05)
				e.raw(0x44, 0x89, 0xC7)
				e.raw(0xB8); e.imm32(3)                            // close
				e.raw(0x0F, 0x05)
				e.raw(0x48, 0x31, 0xC0)
				e.storeReg(0)
			case 51: // FILEREAD(path=R0) -> R0 = 内容
				e.loadReg(0); e.storeReg(7)
				e.raw(0xB8); e.imm32(65536)
				e.storeReg(0)
				e.emitMalloc()
				e.loadReg(0); e.storeReg(8)                        // dst vm
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(7)) // rsi = path
				e.raw(0xB8); e.imm32(2)
				e.raw(0x48, 0x89, 0xF7)
				e.raw(0x31, 0xF6)                                  // xor esi, esi (O_RDONLY)
				e.raw(0x0F, 0x05)
				e.raw(0x49, 0x89, 0xC0)                            // fd
				e.raw(0x44, 0x89, 0xC7)                            // edi = fd
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(8)) // rsi = buf
				e.raw(0xBA); e.imm32(65535)                        // rdx = size-1
				e.raw(0x31, 0xC0)                                  // eax = read
				e.raw(0x0F, 0x05)
				e.raw(0x48, 0x89, 0xF1)                            // rcx = buf
				e.raw(0x48, 0x01, 0xC1)                            // rcx += n
				e.raw(0xC6, 0x01, 0x00)                            // NUL
				e.raw(0x44, 0x89, 0xC7)
				e.raw(0xB8); e.imm32(3)                            // close
				e.raw(0x0F, 0x05)
				e.loadReg(8); e.storeReg(0)
			case 54: // FILEEXISTS(path=R0) -> 0/1
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(0))
				e.raw(0xB8); e.imm32(21)  // access
				e.raw(0x48, 0x89, 0xF7)   // rdi = path
				e.raw(0x31, 0xF6)         // esi = F_OK
				e.raw(0x0F, 0x05)
				e.raw(0x48, 0x85, 0xC0)   // test rax,rax
				e.raw(0x0F, 0x94, 0xC0)   // sete al
				e.raw(0x0F, 0xB6, 0xC0)   // movzx eax,al
				e.storeReg(0)
			case 56: // FILESIZE(path=R0) -> 字节数
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(0))
				e.raw(0xB8); e.imm32(2)
				e.raw(0x48, 0x89, 0xF7)
				e.raw(0x31, 0xF6)
				e.raw(0x0F, 0x05)
				e.raw(0x49, 0x89, 0xC0)   // fd
				e.raw(0x44, 0x89, 0xC7)   // edi = fd
				e.raw(0xB8); e.imm32(8)   // lseek
				e.raw(0x31, 0xF6)         // offset 0
				e.raw(0xBA); e.imm32(2)   // SEEK_END
				e.raw(0x0F, 0x05)
				e.raw(0x48, 0x89, 0xC3)   // rbx = size
				e.raw(0x44, 0x89, 0xC7)
				e.raw(0xB8); e.imm32(3)
				e.raw(0x0F, 0x05)         // close
				e.raw(0x48, 0x89, 0xD8)   // rax = size
				e.storeReg(0)
			case 55: // FILEDELETE(path=R0)
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(0))
				e.raw(0xB8); e.imm32(87)  // unlink
				e.raw(0x48, 0x89, 0xF7)
				e.raw(0x0F, 0x05)
				e.storeReg(0)
			case 57: // MKDIR(path=R0)
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(0))
				e.raw(0xB8); e.imm32(83)  // mkdir
				e.raw(0x48, 0x89, 0xF7)
				e.raw(0xBE); e.imm32(0755)
				e.raw(0x0F, 0x05)
				e.storeReg(0)
			case 147: // TCPDIAL(host=R0, port=R1) -> fd; 仅支持点分 IPv4
				e.loadReg(0); e.storeReg(7)   // host vm
				e.loadReg(1); e.storeReg(8)   // port
				e.raw(0x48, 0xC7, 0xC0); e.imm32(16)
				e.storeReg(0)
				e.emitMalloc()                // R0 = sockaddr vm
				e.loadReg(0); e.storeReg(9)
				e.raw(0x4C, 0x89, 0xF8)       // rax = r15
				e.raw(0x48, 0x03, 0x04, 0x25); e.imm32(regAddr(9)) // + sockaddr
				e.raw(0x48, 0x89, 0xC7)       // rdi = sockaddr host
				e.raw(0xC7, 0x07); e.imm32(2) // sin_family = AF_INET
				e.raw(0x4C, 0x89, 0xFE); e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(8))
				e.raw(0x66, 0xC1, 0xC6, 0x08) // rol si, 8  (host -> network)
				e.raw(0x66, 0x89, 0x77, 0x02) // mov [rdi+2], si
				// 解析点分 IPv4
				e.raw(0x4C, 0x89, 0xFE); e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(7))
				e.raw(0x45, 0x31, 0xC9)       // xor r9d, r9d (octet 索引)
				e.raw(0x31, 0xC0)             // xor eax, eax (累加)
				ipLoop := len(e.code)
				e.raw(0x0F, 0xB6, 0x0E)       // movzx ecx, [rsi]
				e.raw(0x84, 0xC9)             // test cl, cl
				e.raw(0x0F, 0x84)
				ipEnd := len(e.code); e.imm32(0)
				e.raw(0x80, 0xF9, 0x2E)       // cmp cl, '.'
				e.raw(0x0F, 0x84)
				ipDot := len(e.code); e.imm32(0)
				e.raw(0x6B, 0xC0, 0x0A)       // imul eax, eax, 10
				e.raw(0x83, 0xE9, 0x30)       // sub ecx, '0'
				e.raw(0x01, 0xC8)             // add eax, ecx
				e.raw(0x48, 0xFF, 0xC6)       // inc rsi
				e.raw(0xE9); e.imm32(int32(ipLoop - (len(e.code) + 4)))
				ipDotAddr := len(e.code); e.patchRel(ipDot, ipDotAddr)
				e.raw(0x42, 0x88, 0x44, 0x0F, 0x04) // mov [rdi+r9+4], al
				e.raw(0x49, 0xFF, 0xC1)       // inc r9
				e.raw(0x31, 0xC0)             // xor eax, eax
				e.raw(0x48, 0xFF, 0xC6)
				e.raw(0xE9); e.imm32(int32(ipLoop - (len(e.code) + 4)))
				ipEndAddr := len(e.code); e.patchRel(ipEnd, ipEndAddr)
				e.raw(0x42, 0x88, 0x44, 0x0F, 0x04) // 最后一节
				// socket(AF_INET, SOCK_STREAM, 0)
				e.raw(0xB8); e.imm32(41)
				e.raw(0xBF); e.imm32(2)
				e.raw(0xBE); e.imm32(1)
				e.raw(0x31, 0xD2)
				e.raw(0x0F, 0x05)
				e.raw(0x49, 0x89, 0xC0)       // r8 = fd
				// connect(fd, sockaddr, 16)
				e.raw(0x44, 0x89, 0xC7)       // edi = fd
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(9))
				e.raw(0xBA); e.imm32(16)
				e.raw(0xB8); e.imm32(42)
				e.raw(0x0F, 0x05)
				e.raw(0x4C, 0x89, 0xC0)       // rax = fd
				e.storeReg(0)
			case 148: // TCPSEND(fd=R0, data=R1, len=R2)
				e.loadReg(0); e.storeReg(7)
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(1))
				e.loadReg(2); e.raw(0x48, 0x89, 0xC2) // rdx = len
				e.loadReg(7); e.raw(0x48, 0x89, 0xC7) // rdi = fd
				e.raw(0xB8); e.imm32(1)
				e.raw(0x0F, 0x05)
				e.storeReg(0)
			case 149: // TCPRECV(fd=R0, buf=R1, max=R2)
				e.loadReg(0); e.storeReg(7)
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(1))
				e.loadReg(2); e.raw(0x48, 0x89, 0xC2)
				e.loadReg(7); e.raw(0x48, 0x89, 0xC7)
				e.raw(0x31, 0xC0)
				e.raw(0x0F, 0x05)
				e.storeReg(0)
			case 150: // TCPCLOSE(fd=R0)
				e.loadReg(0); e.raw(0x48, 0x89, 0xC7)
				e.raw(0xB8); e.imm32(3)
				e.raw(0x0F, 0x05)
				e.storeReg(0)
			case 91: // TIMEMS: 单调时钟毫秒
				e.raw(0x48, 0x83, 0xEC, 0x10)       // sub rsp, 16
				e.raw(0xBF); e.imm32(1)             // edi = CLOCK_MONOTONIC
				e.raw(0x48, 0x89, 0xE6)             // rsi = &ts
				e.raw(0xB8); e.imm32(228)           // clock_gettime
				e.raw(0x0F, 0x05)
				e.raw(0x48, 0x8B, 0x04, 0x24)       // rax = sec
				e.raw(0x48, 0x69, 0xC0); e.imm32(1000)
				e.raw(0x49, 0x89, 0xC1)             // r9 = sec*1000
				e.raw(0x48, 0x8B, 0x44, 0x24, 0x08) // rax = nsec
				e.raw(0x48, 0x31, 0xD2)
				e.raw(0x48, 0xC7, 0xC1); e.imm32(1000000)
				e.raw(0x48, 0xF7, 0xF1)             // div rcx
				e.raw(0x49, 0x01, 0xC1)             // r9 += rax
				e.raw(0x4C, 0x89, 0xC8)             // rax = r9
				e.raw(0x48, 0x83, 0xC4, 0x10)
				e.storeReg(0)
			case 92: // SLEEPMS: R0 毫秒
				e.loadReg(0)
				e.raw(0x48, 0x31, 0xD2)
				e.raw(0x48, 0xC7, 0xC1); e.imm32(1000)
				e.raw(0x48, 0xF7, 0xF1)             // rax=sec, rdx=ms%1000
				e.raw(0x48, 0x83, 0xEC, 0x10)
				e.raw(0x48, 0x89, 0x04, 0x24)
				e.raw(0x48, 0x69, 0xD2); e.imm32(1000000)
				e.raw(0x48, 0x89, 0x54, 0x24, 0x08)
				e.raw(0x48, 0x89, 0xE7)             // rdi = &ts
				e.raw(0x31, 0xF6)                   // rsi = NULL
				e.raw(0xB8); e.imm32(35)            // nanosleep
				e.raw(0x0F, 0x05)
				e.raw(0x48, 0x83, 0xC4, 0x10)
				e.raw(0x48, 0x31, 0xC0)
				e.storeReg(0)
			case 66: // CWD -> R0 = 当前目录字符串
				e.raw(0x48, 0xC7, 0xC0); e.imm32(4096)
				e.storeReg(0)
				e.emitMalloc()
				e.loadReg(0); e.storeReg(8)
				e.raw(0x4C, 0x89, 0xFF)                            // rdi = r15
				e.raw(0x48, 0x03, 0x3C, 0x25); e.imm32(regAddr(8)) // rdi += buf
				e.raw(0xBE); e.imm32(4096)
				e.raw(0xB8); e.imm32(79)            // getcwd
				e.raw(0x0F, 0x05)
				e.loadReg(8); e.storeReg(0)
			case 90: // CHDIR(path=R0)
				e.raw(0x4C, 0x89, 0xFF)
				e.raw(0x48, 0x03, 0x3C, 0x25); e.imm32(regAddr(0))
				e.raw(0xB8); e.imm32(80)            // chdir
				e.raw(0x0F, 0x05)
				e.storeReg(0)
			case 153: // UDPOPEN(port=R0) -> fd
				e.loadReg(0); e.storeReg(8)        // 绑定端口 (0=自动)
				e.raw(0xB8); e.imm32(41)           // socket
				e.raw(0xBF); e.imm32(2)
				e.raw(0xBE); e.imm32(2)            // SOCK_DGRAM
				e.raw(0x31, 0xD2)
				e.raw(0x0F, 0x05)
				e.storeReg(0)
			case 154: // UDPSENDTO(fd=R0, host=R1, port=R2, data=R3, len=R4)
				e.loadReg(0); e.storeReg(7)        // fd
				e.loadReg(3); e.storeReg(8)        // data
				e.loadReg(4); e.storeReg(9)        // len
				e.raw(0x48, 0xC7, 0xC0); e.imm32(16)
				e.storeReg(0)
				e.emitMalloc()
				e.loadReg(0); e.storeReg(10)       // sockaddr vm
				e.raw(0x4C, 0x89, 0xFF)            // rdi = r15
				e.raw(0x48, 0x03, 0x3C, 0x25); e.imm32(regAddr(10))
				e.raw(0xC7, 0x07); e.imm32(2)      // AF_INET
				e.loadReg(2)                        // port
				e.raw(0x48, 0x89, 0xC6)            // rsi = port
				e.raw(0x66, 0xC1, 0xC6, 0x08)      // rol si,8
				e.raw(0x66, 0x89, 0x77, 0x02)
				// 解析 host
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(1))
				e.raw(0x45, 0x31, 0xC9)
				e.raw(0x31, 0xC0)
				ipLoop2 := len(e.code)
				e.raw(0x0F, 0xB6, 0x0E)
				e.raw(0x84, 0xC9)
				e.raw(0x0F, 0x84)
				ipEnd2 := len(e.code); e.imm32(0)
				e.raw(0x80, 0xF9, 0x2E)
				e.raw(0x0F, 0x84)
				ipDot2 := len(e.code); e.imm32(0)
				e.raw(0x6B, 0xC0, 0x0A)
				e.raw(0x83, 0xE9, 0x30)
				e.raw(0x01, 0xC8)
				e.raw(0x48, 0xFF, 0xC6)
				e.raw(0xE9); e.imm32(int32(ipLoop2 - (len(e.code) + 4)))
				ipDot2Addr := len(e.code); e.patchRel(ipDot2, ipDot2Addr)
				e.raw(0x42, 0x88, 0x44, 0x0F, 0x04)
				e.raw(0x49, 0xFF, 0xC1)
				e.raw(0x31, 0xC0)
				e.raw(0x48, 0xFF, 0xC6)
				e.raw(0xE9); e.imm32(int32(ipLoop2 - (len(e.code) + 4)))
				ipEnd2Addr := len(e.code); e.patchRel(ipEnd2, ipEnd2Addr)
				e.raw(0x42, 0x88, 0x44, 0x0F, 0x04)
				// sendto(fd, buf, len, 0, sa, 16)
				e.loadReg(7); e.raw(0x48, 0x89, 0xC7)   // rdi = fd
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(8))
				e.loadReg(9); e.raw(0x48, 0x89, 0xC2)   // rdx = len
				e.raw(0x45, 0x31, 0xD2)                 // r10 = 0
				e.raw(0x4D, 0x89, 0xF8)                 // r8 = r15
				e.raw(0x4D, 0x03, 0x04, 0x25); e.imm32(regAddr(10)) // r8 += sa
				e.raw(0x41, 0xB9); e.imm32(16)          // r9 = 16
				e.raw(0xB8); e.imm32(44)                // sendto
				e.raw(0x0F, 0x05)
				e.storeReg(0)
			case 155: // UDPRECVFROM(fd=R0, buf=R1, max=R2, timeout=R3)
				e.loadReg(0); e.storeReg(7)
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(1))
				e.loadReg(2); e.raw(0x48, 0x89, 0xC2)
				e.loadReg(7); e.raw(0x48, 0x89, 0xC7)
				e.raw(0x45, 0x31, 0xD2)   // flags
				e.raw(0x45, 0x31, 0xC0)   // sa NULL
				e.raw(0x45, 0x31, 0xC9)   // len NULL
				e.raw(0xB8); e.imm32(45)  // recvfrom
				e.raw(0x0F, 0x05)
				e.storeReg(0)
			case 156: // UDPCLOSE(fd=R0)
				e.loadReg(0); e.raw(0x48, 0x89, 0xC7)
				e.raw(0xB8); e.imm32(3)
				e.raw(0x0F, 0x05)
				e.storeReg(0)
			case 63: // OSNAME -> "linux"
				e.raw(0x48, 0xC7, 0xC0); e.imm32(6)
				e.storeReg(0)
				e.emitMalloc()
				e.loadReg(0)
				e.raw(0x4C, 0x89, 0xFF); e.raw(0x48, 0x01, 0xC7)
				e.raw(0xC7, 0x07); e.imm32(0x756E696C) // "linu"
				e.raw(0xC6, 0x47, 0x04, 0x78)          // 'x'
				e.raw(0xC6, 0x47, 0x05, 0x00)
			case 89: // TEMPDIR -> "/tmp"
				e.raw(0x48, 0xC7, 0xC0); e.imm32(5)
				e.storeReg(0)
				e.emitMalloc()
				e.loadReg(0)
				e.raw(0x4C, 0x89, 0xFF); e.raw(0x48, 0x01, 0xC7)
				e.raw(0xC7, 0x07); e.imm32(0x706D742F) // "/tmp"
				e.raw(0xC6, 0x47, 0x04, 0x00)
			case 25: // STRCONCAT: R0 = R0 + R1
				e.loadReg(0); e.storeReg(7)
				e.loadReg(1); e.storeReg(8)
				e.loadReg(7); e.emitStrlen(); e.storeReg(9)
				e.loadReg(8); e.emitStrlen()
				e.raw(0x48, 0x03, 0x04, 0x25); e.imm32(regAddr(9))
				e.raw(0x48, 0xFF, 0xC0)
				e.storeReg(0)
				e.emitMalloc()
				e.loadReg(0); e.storeReg(10)
				e.raw(0x4C, 0x89, 0xFA)   // rdx = r15
				e.raw(0x48, 0x01, 0xC2)   // rdx += dst
				e.raw(0x48, 0x89, 0xD7)   // rdi = dst host
				e.loadReg(7); e.emitStrCopy()
				e.loadReg(8); e.emitStrCopy()
				e.loadReg(10); e.storeReg(0)
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
