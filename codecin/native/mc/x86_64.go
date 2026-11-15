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
	// FTOA 输出缓冲区 (VM 地址)。放在堆起点 (0x100000) 之下的空隙里: 静态数据
	// 从 VM 0 向上长、堆自 0x100000 向上长、栈从内存顶端向下长, 这一段不会
	// 被任何一方占用; 挨着 itoaBuf 会与体积稍大的静态字符串区撞车。
	ftoaBuf = 0xF0000
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


// emitF64Round 生成 SYS 32/33/34 (FLOOR/CEIL/ROUND) 的取整序列, 结果按
// double 位模式写回 R0。
//
// 走 SSE2 的 cvttsd2si + cvtsi2sd + ucomisd, 不用 SSE4.1 的 roundsd: 后者
// 在没有该扩展的 CPU 上直接 #UD, 而机器码后端没有做 CPU 特性探测的余地。
// ROUND 按 ISA 表的定义实现为 floor(x + 0.5)。
func (e *Encoder) emitF64Round(id int) {
	e.raw(0xF2, 0x0F, 0x10, 0x04, 0x25); e.imm32(regAddr(0)) // movsd xmm0, [R0]
	if id == 34 {
		e.raw(0x48, 0xB8); e.imm64(0x3FE0000000000000) // mov rax, 0.5 的位模式
		e.raw(0x66, 0x48, 0x0F, 0x6E, 0xC8)            // movq xmm1, rax
		e.raw(0xF2, 0x0F, 0x58, 0xC1)                  // addsd xmm0, xmm1
	}
	e.raw(0xF2, 0x48, 0x0F, 0x2C, 0xC0) // cvttsd2si rax, xmm0 (向零截断)
	e.raw(0xF2, 0x48, 0x0F, 0x2A, 0xC8) // cvtsi2sd xmm1, rax (转回 double)
	e.raw(0x66, 0x0F, 0x2E, 0xC8)       // ucomisd xmm1, xmm0
	e.raw(0x0F, 0x8A); jpPos := e.imm32Pos() // jp done: NaN 保持截断值
	if id == 33 {
		// ceil: 截断值已经 >= x 就不用修正
		e.raw(0x0F, 0x83); skipPos := e.imm32Pos() // jae done
		e.raw(0x48, 0x83, 0xC0, 0x01)              // inc rax
		e.raw(0xF2, 0x48, 0x0F, 0x2A, 0xC8)        // cvtsi2sd xmm1, rax
		e.patchRel(skipPos, len(e.code))
	} else {
		// floor: 截断值已经 <= x 就不用修正
		e.raw(0x0F, 0x86); skipPos := e.imm32Pos() // jbe done
		e.raw(0x48, 0x83, 0xE8, 0x01)              // dec rax
		e.raw(0xF2, 0x48, 0x0F, 0x2A, 0xC8)        // cvtsi2sd xmm1, rax
		e.patchRel(skipPos, len(e.code))
	}
	e.patchRel(jpPos, len(e.code))
	e.raw(0xF2, 0x0F, 0x11, 0x0C, 0x25); e.imm32(regAddr(0)) // movsd [R0], xmm1
}

// emitFTOA 生成 SYS 23 (FTOA): R0 = 指向 VM 里 NUL 结尾十进制串的地址。
//
// 输出格式是"符号 + 整数部分 + 最多 16 位小数并去掉尾随零", 与引擎用的
// strconv.FormatFloat(f, 'g', -1, 64) 并不逐位一致: 常见值 (0.5 / 3.5 /
// 1.414213562373095) 输出相同, 循环小数末位可能少一位, 极大/极小值不做
// 指数改写。NaN 与 ±Inf 按位判定, 输出 "NaN" / "+Inf" / "-Inf"。
func (e *Encoder) emitFTOA() {
	// rdi = 写指针 (= r15 + ftoaBuf)
	e.raw(0x4C, 0x89, 0xF8)             // mov rax, r15
	e.raw(0x48, 0x05); e.imm32(ftoaBuf) // add rax, ftoaBuf
	e.raw(0x48, 0x89, 0xC7)             // mov rdi, rax

	// 指数位全 1 才是 NaN / ±Inf, 其余走常规路径。
	e.loadReg(0)                                   // rax = 位模式
	e.raw(0x48, 0xBA); e.imm64(0x7FF0000000000000) // mov rdx, 指数掩码
	e.raw(0x48, 0x89, 0xC1)                        // mov rcx, rax
	e.raw(0x48, 0x21, 0xD1)                        // and rcx, rdx
	e.raw(0x48, 0x39, 0xD1)                        // cmp rcx, rdx
	e.raw(0x0F, 0x85); jNormal := e.imm32Pos()     // jne normal
	e.raw(0x48, 0xBA); e.imm64(0x000FFFFFFFFFFFFF) // mov rdx, 尾数掩码
	e.raw(0x48, 0x89, 0xC1)                        // mov rcx, rax
	e.raw(0x48, 0x21, 0xD1)                        // and rcx, rdx
	e.raw(0x48, 0x85, 0xC9)                        // test rcx, rcx
	e.raw(0x0F, 0x85); jNaN := e.imm32Pos()        // jnz nan
	e.raw(0x48, 0x85, 0xC0)                        // test rax, rax
	e.raw(0x0F, 0x88); jNegInf := e.imm32Pos()     // js neginf

	// +Inf
	e.raw(0x48, 0xB8); e.imm64(0x666E492B) // mov rax, "+Inf"
	e.raw(0x48, 0x89, 0x07)                // mov [rdi], rax
	e.raw(0xC6, 0x47, 0x04, 0x00)          // mov byte [rdi+4], 0
	e.raw(0xE9); jPosInfStore := e.imm32Pos()

	e.patchRel(jNaN, len(e.code))
	e.raw(0x48, 0xB8); e.imm64(0x4E614E) // mov rax, "NaN"
	e.raw(0x48, 0x89, 0x07)              // mov [rdi], rax
	e.raw(0xC6, 0x47, 0x03, 0x00)        // mov byte [rdi+3], 0
	e.raw(0xE9); jNaNStore := e.imm32Pos()

	e.patchRel(jNegInf, len(e.code))
	e.raw(0x48, 0xB8); e.imm64(0x666E492D) // mov rax, "-Inf"
	e.raw(0x48, 0x89, 0x07)                // mov [rdi], rax
	e.raw(0xC6, 0x47, 0x04, 0x00)          // mov byte [rdi+4], 0
	e.raw(0xE9); jNegInfStore := e.imm32Pos()

	// ---- 常规路径: 符号 + 整数部分 + 小数部分 ----
	e.patchRel(jNormal, len(e.code))
	e.raw(0x48, 0x85, 0xC0)                    // test rax, rax
	e.raw(0x0F, 0x89); jPositive := e.imm32Pos() // jns positive
	e.raw(0xC6, 0x07, 0x2D)                    // mov byte [rdi], '-'
	e.raw(0x48, 0xFF, 0xC7)                    // inc rdi
	e.raw(0x48, 0xB9); e.imm64(0x7FFFFFFFFFFFFFFF) // mov rcx, 清符号位掩码
	e.raw(0x48, 0x21, 0xC8)                        // and rax, rcx
	e.patchRel(jPositive, len(e.code))
	e.raw(0x66, 0x48, 0x0F, 0x6E, 0xC0) // movq xmm0, rax (|x|)
	e.raw(0xF2, 0x48, 0x0F, 0x2C, 0xC0) // cvttsd2si rax, xmm0 (整数部分)
	e.raw(0x49, 0x89, 0xC4)             // mov r12, rax
	e.raw(0xF2, 0x48, 0x0F, 0x2A, 0xC8) // cvtsi2sd xmm1, rax
	e.raw(0xF2, 0x0F, 0x5C, 0xC1)       // subsd xmm0, xmm1 (小数部分)

	// 整数部分转十进制: 复用 ITOA (它只碰 rax/rdx/rsi/r8/r9, 不动 rdi/r12)
	e.raw(0x4C, 0x89, 0x24, 0x25); e.imm32(regAddr(0)) // mov [R0], r12
	e.emitITOA()                                       // R0 = 十进制串的 VM 地址
	e.raw(0x4C, 0x89, 0xFE)                            // mov rsi, r15
	e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(0)) // add rsi, [R0]
	copyLoop := len(e.code)
	e.raw(0x8A, 0x06)                     // mov al, [rsi]
	e.raw(0x84, 0xC0)                     // test al, al
	e.raw(0x0F, 0x84); jCopyEnd := e.imm32Pos() // jz copyEnd
	e.raw(0x88, 0x07)                     // mov [rdi], al
	e.raw(0x48, 0xFF, 0xC6)               // inc rsi
	e.raw(0x48, 0xFF, 0xC7)               // inc rdi
	e.raw(0xE9); jCopyLoop := e.imm32Pos()
	e.patchRel(jCopyLoop, copyLoop)
	e.patchRel(jCopyEnd, len(e.code))

	// 小数部分为 0 时直接收尾
	e.raw(0x66, 0x0F, 0x57, 0xD2)       // xorpd xmm2, xmm2
	e.raw(0x66, 0x0F, 0x2E, 0xC2)       // ucomisd xmm0, xmm2
	e.raw(0x0F, 0x84); jIntOnly := e.imm32Pos() // je intOnly
	e.raw(0xC6, 0x07, 0x2E)             // mov byte [rdi], '.'
	e.raw(0x48, 0xFF, 0xC7)             // inc rdi
	e.raw(0x49, 0x89, 0xFD)             // mov r13, rdi (最后一个非零位之后)
	e.raw(0x49, 0x89, 0xFE)             // mov r14, rdi (小数点之后)
	e.raw(0x48, 0xC7, 0xC1, 0x0F, 0x00, 0x00, 0x00) // mov rcx, 15 (最多 15 位)
	e.raw(0x48, 0xBA); e.imm64(0x4024000000000000)  // mov rdx, 10.0
	e.raw(0x66, 0x48, 0x0F, 0x6E, 0xDA)             // movq xmm3, rdx
	digitLoop := len(e.code)
	e.raw(0xF2, 0x0F, 0x59, 0xC3)       // mulsd xmm0, xmm3
	e.raw(0xF2, 0x48, 0x0F, 0x2C, 0xC0) // cvttsd2si rax, xmm0
	e.raw(0xF2, 0x48, 0x0F, 0x2A, 0xC8) // cvtsi2sd xmm1, rax
	e.raw(0xF2, 0x0F, 0x5C, 0xC1)       // subsd xmm0, xmm1
	e.raw(0x04, 0x30)                   // add al, '0'
	e.raw(0x88, 0x07)                   // mov [rdi], al
	e.raw(0x48, 0xFF, 0xC7)             // inc rdi
	e.raw(0x3C, 0x30)                   // cmp al, '0'
	e.raw(0x74, 0x03)                   // je +3 (非零才记位置)
	e.raw(0x49, 0x89, 0xFD)             // mov r13, rdi
	e.raw(0x66, 0x0F, 0x2E, 0xC2)       // ucomisd xmm0, xmm2
	e.raw(0x0F, 0x84); jFracDone := e.imm32Pos() // je fracDone
	e.raw(0x48, 0xFF, 0xC9)             // dec rcx
	e.raw(0x0F, 0x85); jDigit := e.imm32Pos()    // jnz digitLoop
	e.patchRel(jDigit, digitLoop)
	e.patchRel(jFracDone, len(e.code))

	// 一个非零小数位都没有 (极小的数): 把小数点也去掉
	e.raw(0x4D, 0x39, 0xF5)                 // cmp r13, r14
	e.raw(0x0F, 0x85); jHasDigit := e.imm32Pos() // jne term
	e.raw(0x49, 0xFF, 0xCD)                 // dec r13
	e.patchRel(jHasDigit, len(e.code))
	e.raw(0x41, 0xC6, 0x45, 0x00, 0x00) // mov byte [r13], 0
	e.raw(0xE9); jFracStore := e.imm32Pos()

	e.patchRel(jIntOnly, len(e.code))
	e.raw(0xC6, 0x07, 0x00) // mov byte [rdi], 0

	// ---- 收尾: R0 = 缓冲区的 VM 地址 (r15 + ftoaBuf 是它的宿主指针) ----
	store := len(e.code)
	for _, pos := range []int{jPosInfStore, jNaNStore, jNegInfStore, jFracStore} {
		e.patchRel(pos, store)
	}
	e.movImm(0, ftoaBuf)
}

const memSize = 1 << 30 // VM 地址空间 (mmap 惰性分配)
// memSizeWin 是 Windows 下提交的 VM 内存大小。
// Linux 的 mmap 是惰性分配, 1 GiB 也不占物理页; VirtualAlloc 提交多少就实占
// 多少, 所以按 64 MiB 提交 —— 覆盖内置标准库的实际用量。
const memSizeWin = 64 << 20

// prologue 分配 VM 内存, 基址存入 r15, 并初始化 R32 (SP)。
func (e *Encoder) prologue(writes []ir.DataWrite) error {
	if e.isWin() {
		if err := e.prologueWin(); err != nil {
			return err
		}
	} else {
		e.raw(0x31, 0xFF)             // xor edi, edi   (addr = NULL)
		e.raw(0xBE); e.imm32(memSize) // mov esi, memSize
		e.raw(0xBA); e.imm32(3)       // mov edx, PROT_READ|PROT_WRITE
		e.raw(0x41, 0xBA); e.imm32(0x22) // mov r10d, MAP_PRIVATE|MAP_ANONYMOUS
		e.raw(0x41, 0xB8); e.imm32(-1)   // mov r8d, -1 (fd)
		e.raw(0x45, 0x31, 0xC9)       // xor r9d, r9d (offset)
		e.raw(0xB8); e.imm32(9)       // mov eax, SYS_mmap
		e.raw(0x0F, 0x05)
		e.raw(0x49, 0x89, 0xC7)       // mov r15, rax
	}
	// 把静态数据搬进 VM 内存 (源: 映像数据段, 目标: r15 + addr)
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
	// R32 = 栈顶 - 8
	top := int64(memSize - 8)
	if e.isWin() {
		top = memSizeWin - 8
	}
	e.raw(0x48, 0xB8); e.imm64(top)
	e.raw(0x48, 0x89, 0x04, 0x25); e.imm32(regAddr(32))
	return nil
}

// prologueWin 是 Windows 序言: 拿 stdout 句柄 + VirtualAlloc 一块 RW 内存。
//
// Win64 ABI 要求调用点 rsp 16 字节对齐, 且调用者提供 32 字节 shadow space。
// 入口 rsp ≡ 8 (mod 16), 因此 ``sub rsp, 0x28`` 之后恰为 16 的倍数。
func (e *Encoder) prologueWin() error {
	e.raw(0x48, 0x83, 0xEC, 0x28)                              // sub rsp, 0x28
	e.raw(0xB9); e.imm32(-11)                                  // mov ecx, STD_OUTPUT_HANDLE
	if err := e.callIAT("GetStdHandle"); err != nil {
		return err
	}
	e.raw(0x48, 0x89, 0x04, 0x25); e.imm32(int32(winStdoutSlot)) // mov [hStdout], rax
	e.raw(0x31, 0xC9)                                          // xor ecx, ecx (lpAddress = NULL)
	e.raw(0xBA); e.imm32(memSizeWin)                           // mov edx, size
	e.raw(0x41, 0xB8); e.imm32(0x3000)                         // mov r8d, MEM_COMMIT|MEM_RESERVE
	e.raw(0x41, 0xB9); e.imm32(4)                              // mov r9d, PAGE_READWRITE
	if err := e.callIAT("VirtualAlloc"); err != nil {
		return err
	}
	e.raw(0x49, 0x89, 0xC7)  // mov r15, rax
	return nil
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

// shiftOp 编码 SHL/SHR/ASR。
//
// IR 有两种形状: 两操作数 (rd, 计数) 表示 rd 自身移位, 三操作数
// (rd, rs, 计数) 表示 rd = rs 移位。计数可以是寄存器或立即数; x86 的移位
// 指令本身按 64 位操作数把计数掩到低 6 位, 与 VM 的 ``v & 63`` 一致。
func (e *Encoder) shiftOp(op string, ins ir.Instr) error {
	var rd, rs int
	var cnt ir.Operand
	switch len(ins.Args) {
	case 2:
		if ins.Args[0].Kind != "reg" {
			return fmt.Errorf("%s: 需要 reg, reg|imm", op)
		}
		rd, rs, cnt = int(ins.Args[0].V), int(ins.Args[0].V), ins.Args[1]
	case 3:
		if ins.Args[0].Kind != "reg" || ins.Args[1].Kind != "reg" {
			return fmt.Errorf("%s: 需要 reg, reg, reg|imm", op)
		}
		rd, rs, cnt = int(ins.Args[0].V), int(ins.Args[1].V), ins.Args[2]
	default:
		return fmt.Errorf("%s: 需要 2 或 3 个操作数", op)
	}
	e.loadReg(rs)
	switch cnt.Kind {
	case "reg":
		e.raw(0x48, 0x8B, 0x0C, 0x25) // mov rcx, [Rcnt]
		e.imm32(regAddr(int(cnt.V)))
		switch op {
		case "SHL":
			e.raw(0x48, 0xD3, 0xE0) // shl rax, cl
		case "SHR":
			e.raw(0x48, 0xD3, 0xE8) // shr rax, cl
		default:
			e.raw(0x48, 0xD3, 0xF8) // sar rax, cl (算术右移, 保留符号)
		}
	case "imm":
		v := byte(cnt.V & 63)
		switch op {
		case "SHL":
			e.raw(0x48, 0xC1, 0xE0, v) // shl rax, imm8
		case "SHR":
			e.raw(0x48, 0xC1, 0xE8, v) // shr rax, imm8
		default:
			e.raw(0x48, 0xC1, 0xF8, v) // sar rax, imm8
		}
	default:
		return fmt.Errorf("%s: 计数需要 reg 或 imm", op)
	}
	e.storeReg(rd)
	return nil
}

// logicOp 编码 ANDI/ORI/XORI: rd = rs op src。
//
// 立即数放不进 int32 时改走寄存器形式, 避免 ``op rax, imm32`` 的符号扩展
// 把 0xFFFFFFFF 变成全 1 掩码。
func (e *Encoder) logicOp(op string, ins ir.Instr) error {
	if len(ins.Args) != 3 || ins.Args[0].Kind != "reg" || ins.Args[1].Kind != "reg" {
		return fmt.Errorf("%s: 需要 reg, reg, reg|imm", op)
	}
	rd, rs, src := int(ins.Args[0].V), int(ins.Args[1].V), ins.Args[2]
	e.loadReg(rs)
	switch src.Kind {
	case "imm":
		if src.V > 0x7FFFFFFF || src.V < -0x80000000 {
			e.raw(0x48, 0xBA) // mov rdx, imm64
			e.imm64(src.V)
			switch op {
			case "ANDI":
				e.raw(0x48, 0x21, 0xD0) // and rax, rdx
			case "ORI":
				e.raw(0x48, 0x09, 0xD0) // or rax, rdx
			default:
				e.raw(0x48, 0x31, 0xD0) // xor rax, rdx
			}
			break
		}
		switch op {
		case "ANDI":
			e.raw(0x48, 0x25) // and rax, imm32
		case "ORI":
			e.raw(0x48, 0x0D) // or rax, imm32
		default:
			e.raw(0x48, 0x35) // xor rax, imm32
		}
		e.imm32(int32(src.V))
	case "reg":
		switch op {
		case "ANDI":
			e.raw(0x48, 0x23, 0x04, 0x25) // and rax, [abs32]
		case "ORI":
			e.raw(0x48, 0x0B, 0x04, 0x25) // or rax, [abs32]
		default:
			e.raw(0x48, 0x33, 0x04, 0x25) // xor rax, [abs32]
		}
		e.imm32(regAddr(int(src.V)))
	default:
		return fmt.Errorf("%s: 第三个操作数需要 reg 或 imm", op)
	}
	e.storeReg(rd)
	return nil
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

// outStr 打印静态字符串 (dataBase+addr 处, 长度 n)。
func (e *Encoder) outStr(addr int64, n int) error {
	if e.isWin() {
		e.raw(0x4C, 0x89, 0xFE)                       // mov rsi, r15
		e.raw(0x48, 0x81, 0xC6); e.imm32(int32(addr)) // rsi += addr
		e.raw(0x48, 0xC7, 0xC2); e.imm32(int32(n))    // rdx = n
		return e.emitWriteStdout()
	}
	e.raw(0xB8)
	e.imm32(1) // rax = SYS_write
	e.raw(0x48, 0xC7, 0xC7)
	e.imm32(1) // rdi = fd 1
	e.raw(0x4C, 0x89, 0xFE)                          // mov rsi, r15
	e.raw(0x48, 0x81, 0xC6); e.imm32(int32(addr))    // rsi += addr
	e.raw(0x48, 0xC7, 0xC2)
	e.imm32(int32(n)) // rdx = len
	e.raw(0x0F, 0x05)
	return nil
}

// targetKind 区分同一套 x86-64 指令编码要产出的操作系统目标。
//
// 指令编码、寄存器布局、VM 内存模型两边完全一致, 差别只在 5 个"落到
// 操作系统"的原语: 分配 VM 内存、写标准输出、取时间、取临时目录、退出。
// 因此 Linux 与 Windows 共用同一个 Encoder, 由 tgt 决定这几处的字节序列。
type targetKind int

const (
	targetLinux targetKind = iota
	targetWindows
)

// Encoder 生成 x86-64 代码字节。
type Encoder struct {
	code    []byte
	tgt     targetKind
	textRVA uint32            // Windows: .text 在映像中的 RVA (IAT 相对调用要用)
	iat     map[string]uint32 // Windows: 导入函数 -> IAT 槽 RVA
}

func (e *Encoder) isWin() bool { return e.tgt == targetWindows }

// callIAT 生成 ``call qword ptr [rip+disp32]``, 调用 kernel32 导入函数。
// 目标地址是 IAT 槽, disp 相对**下一条指令**计算。
func (e *Encoder) callIAT(name string) error {
	slot, ok := e.iat[name]
	if !ok {
		return fmt.Errorf("未导入函数: %s", name)
	}
	next := e.textRVA + uint32(len(e.code)) + 6
	disp := int64(slot) - int64(next)
	if disp < -1<<31 || disp >= 1<<31 {
		return fmt.Errorf("导入槽超出 32 位相对范围: %s", name)
	}
	e.raw(0xFF, 0x15)
	e.imm32(int32(disp))
	return nil
}

// emitWriteStdout 把 rsi 指向的 buf、rdx 长度写到标准输出。
//
// 破坏 rcx/rdx/rsi/r8/r9/rax 与 xmm 之外的调用者保存寄存器 —— CIN 的
// 虚拟寄存器全部存在内存里, 不需要跨这条序列保活。
func (e *Encoder) emitWriteStdout() error {
	if e.isWin() {
		e.raw(0x49, 0x89, 0xD0)                    // mov r8, rdx (nNumberOfBytesToWrite)
		e.raw(0x48, 0x89, 0xF2)                    // mov rdx, rsi (lpBuffer)
		e.raw(0x48, 0x8B, 0x0C, 0x25)              // mov rcx, [hStdout]
		e.imm32(int32(winStdoutSlot))
		e.raw(0x41, 0xB9); e.imm32(int32(winScratch)) // mov r9d, &written (零扩展后是 32 位绝对地址)
		e.raw(0x48, 0xC7, 0x44, 0x24, 0x20); e.imm32(0) // mov qword [rsp+0x20], 0 (lpOverlapped)
		return e.callIAT("WriteFile")
	}
	e.raw(0xB8); e.imm32(1)                        // eax = SYS_write
	e.raw(0x48, 0xC7, 0xC7); e.imm32(1)            // rdi = 1
	e.raw(0x0F, 0x05)
	return nil
}

// winTempDir 调用 GetTempPathA 把临时目录写进 VM 内存, 返回 VM 地址。
// 失败时退化成 "C:\\Windows\\Temp"。
func (e *Encoder) winTempDir() error {
	const size = 260
	e.raw(0x48, 0xC7, 0xC0); e.imm32(size) // R0 = size
	e.storeReg(0)
	e.emitMalloc()
	e.loadReg(0); e.storeReg(9)                                     // R9 = dst (VM 地址)
	e.raw(0x4C, 0x89, 0xFF); e.raw(0x48, 0x03, 0x0C, 0x25)          // rcx = r15 + dst
	e.imm32(regAddr(9))
	e.raw(0xBA); e.imm32(size)                                      // rdx = size
	if err := e.callIAT("GetTempPathA"); err != nil {
		return err
	}
	e.loadReg(9); e.storeReg(0)
	return nil
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

func (e *Encoder) exitWithReg(r int) error {
	if e.isWin() {
		e.loadReg(r)             // rax = 退出码
		e.raw(0x48, 0x89, 0xC1)  // mov rcx, rax
		if err := e.callIAT("ExitProcess"); err != nil {
			return err
		}
		e.raw(0xEB, 0xFE) // ExitProcess 不返回; 万一返回就原地挂住
		return nil
	}
	e.loadReg(r)
	e.raw(0x48, 0x89, 0xC7) // mov rdi, rax
	e.raw(0xB8)
	e.imm32(60)       // SYS_exit
	e.raw(0x0F, 0x05) // syscall
	return nil
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

// Encode 把 IR 编译为 Linux x86-64 机器码 (入口为偏移 0)。
func Encode(p *ir.Program) ([]byte, error) {
	return encodeWith(p, &Encoder{tgt: targetLinux})
}

// EncodeWin 把 IR 编译为 Windows x86-64 机器码 (入口为偏移 0)。
//
// 指令编码与 Linux 版共用, 只有序言/输出/退出/时间这类落到操作系统的
// 原语走 IAT 调用。textRVA 是 .text 的 RVA (核对 IAT 相对位移用), iat 是
// 导入函数名到 IAT 槽 RVA 的映射。
func EncodeWin(p *ir.Program, textRVA uint32, iat map[string]uint32) ([]byte, error) {
	return encodeWith(p, &Encoder{tgt: targetWindows, textRVA: textRVA, iat: iat})
}

// winSyscalls 是 Windows 机器码后端已实现的功能号 (不依赖 Unix 系统调用)。
//
// 文件、目录、TCP、UDP 这些能力在 Linux 版直接映射 syscall, Windows 侧需要
// 另走 CreateFileA/WinSock 一整套封装, 暂未实现 —— 明确报错好过生成跑飞
// 的二进制。
var winSyscalls = map[int64]bool{
	0: true, 1: true, 2: true, // SQRT/POW/ABS
	3: true, 4: true, 5: true, // SIN/COS/TAN
	6: true, 7: true, 8: true, 9: true, // FADD/FSUB/FMUL/FDIV
	10: true,           // FCMP
	11: true, 12: true, // FTOI/ITOF
	13: true, 14: true, // RAND/SRAND
	15: true, 16: true, 17: true, // TIME/STRLEN/STRCMP
	20: true,                  // MALLOC
	22: true, 23: true, 24: true, 25: true, 26: true, // ITOA/FTOA/PRINT_STR/STR_CONCAT/BOOL_STR
	28: true, 29: true, 30: true, 31: true, // SUBSTR/INDEXOF/TOUPPER/TOLOWER
	32: true, 33: true, 34: true, // FLOOR/CEIL/ROUND
	35: true, 36: true, 37: true, 38: true, // ATOI/TRIM/LTRIM/RTRIM
	// 文件 / 目录 (实现在 win64_fileio.go)
	51: true, 52: true, 53: true, 54: true, 55: true, 56: true, 57: true,
	58: true, 66: true, 84: true, 85: true, 86: true, 87: true, 88: true,
	90: true,
	63: true, 89: true, // OSNAME/TEMPDIR
	91: true, 92: true, // TIMEMS/SLEEPMS
	137: true, // ALLOCFRAME
}

func encodeWith(p *ir.Program, e *Encoder) ([]byte, error) {
	if err := e.prologue(p.DataWrites); err != nil {
		return nil, err
	}
	offs := make([]int, len(p.Instructions))
	type fix struct {
		pos   int
		label string
	}
	var fixups []fix
	sawHalt := false

	// Windows: 被调函数要有自己的序言。Win64 要求 caller 在调用点留出 32
	// 字节 shadow space, 被调者可以往里 spill 参数; 没有序言的话这块正好压
	// 在返回地址上, 函数一调 kernel32 就把自己的返回地址写坏。
	funcEntry := map[int]bool{}
	if e.isWin() {
		for _, ins := range p.Instructions {
			if ins.Op == "CALL" && len(ins.Args) == 1 && ins.Args[0].Kind == "label" {
				if idx, ok := p.Labels[ins.Args[0].Name]; ok {
					funcEntry[idx] = true
				}
			}
		}
	}

	for i, ins := range p.Instructions {
		// label 必须指向函数序言的起点, 否则 CALL 会跳过 sub rsp。
		offs[i] = len(e.code)
		if funcEntry[i] {
			e.raw(0x48, 0x83, 0xEC, 0x28) // 函数序言: sub rsp, 0x28
		}
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
				if e.isWin() {
					// 换行字节不能放 [rsp]: 那是 shadow space, 被调者 spill 参数
					// 时会直接覆盖掉。挪到第 5 参数槽之后 ([rsp+0x30]) 才安全。
					e.raw(0x48, 0x83, 0xEC, 0x40)       // sub rsp, 0x40
					e.raw(0xC6, 0x44, 0x24, 0x30, 0x0A) // mov byte [rsp+0x30], 10
					e.raw(0x48, 0x8D, 0x74, 0x24, 0x30) // lea rsi, [rsp+0x30]
					e.raw(0xBA); e.imm32(1)             // mov edx, 1
					if err := e.emitWriteStdout(); err != nil {
						return nil, err
					}
					e.raw(0x48, 0x83, 0xC4, 0x40) // add rsp, 0x40
					continue
				}
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
			if err := e.outStr(addr, dataLen(p, addr)); err != nil {
				return nil, err
			}
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
		case "SHL", "SHR", "ASR":
			if err := e.shiftOp(ins.Op, ins); err != nil {
				return nil, err
			}
		case "ANDI", "ORI", "XORI":
			if err := e.logicOp(ins.Op, ins); err != nil {
				return nil, err
			}
		case "MVN":
			if len(ins.Args) != 2 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("MVN: 需要 reg, reg|imm")
			}
			if ins.Args[1].Kind == "reg" {
				e.loadReg(int(ins.Args[1].V))
			} else if ins.Args[1].Kind == "imm" {
				e.raw(0x48, 0xB8) // mov rax, imm64
				e.imm64(ins.Args[1].V)
			} else {
				return nil, fmt.Errorf("MVN: 第二个操作数需要 reg 或 imm")
			}
			e.raw(0x48, 0xF7, 0xD0) // not rax
			e.storeReg(int(ins.Args[0].V))
		case "LB", "LH", "LW":
			// 窄读: 与 VM 一致, 先按有符号扩展, 需要无符号时由后续 ANDI 掩码。
			if len(ins.Args) != 2 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("%s: 需要 reg, mem|reg", ins.Op)
			}
			rd, src := int(ins.Args[0].V), ins.Args[1]
			switch src.Kind {
			case "mem":
				e.vmAddr(int(src.V), int32(src.Off))
			case "reg":
				e.raw(0x4C, 0x89, 0xF8) // mov rax, r15
				e.raw(0x48, 0x03, 0x04, 0x25)
				e.imm32(regAddr(int(src.V)))
			default:
				return nil, fmt.Errorf("%s: 第二个操作数需要 mem 或 reg", ins.Op)
			}
			switch ins.Op {
			case "LB":
				e.raw(0x48, 0x0F, 0xBE, 0x00) // movsx rax, byte [rax]
			case "LH":
				e.raw(0x48, 0x0F, 0xBF, 0x00) // movsx rax, word [rax]
			default:
				e.raw(0x48, 0x63, 0x00) // movsxd rax, dword [rax]
			}
			e.storeReg(rd)
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
			if e.isWin() {
				// 与函数序言的 sub rsp,0x28 配对。
				e.raw(0x48, 0x83, 0xC4, 0x28) // add rsp, 0x28
			}
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
			if e.isWin() {
				if !winSyscalls[ins.Args[0].V] {
					return nil, ErrUnsupported{Op: fmt.Sprintf(
						"SYS %d (Windows 后端暂未实现网络/图形能力)", ins.Args[0].V)}
				}
				// 文件 / 目录这类需要 Win32 API 的功能号在 win64_fileio.go 里,
				// 其余功能号继续走下面与 Linux 共用的实现 (内部再按目标分支)。
				handled, err := e.winSyscall(ins.Args[0].V)
				if err != nil {
					return nil, err
				}
				if handled {
					continue
				}
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
				if err := e.emitWriteStdout(); err != nil {
					return nil, err
				}
			case 1: // POW: R0 = pow(R0, R1) —— x87 的 y*log2(x) 再 2^z
				e.raw(0xDD, 0x04, 0x25); e.imm32(regAddr(1)) // fld qword [R1] (y)
				e.raw(0xDD, 0x04, 0x25); e.imm32(regAddr(0)) // fld qword [R0] (x)
				e.raw(0xD9, 0xF1)                             // fyl2x: st0 = y*log2(x)
				e.raw(0xD9, 0xC0)                             // fld st0
				e.raw(0xD9, 0xFC)                             // frndint: st0 = 整数部分
				e.raw(0xDC, 0xE9)                             // fsub st1, st0: st1 = 小数部分
				e.raw(0xD9, 0xC9)                             // fxch: st0 = 小数, st1 = 整数
				e.raw(0xD9, 0xF0)                             // f2xm1: st0 = 2^小数 - 1
				e.raw(0xD9, 0xE8)                             // fld1: st0 = 1
				e.raw(0xDE, 0xC1)                             // faddp: st0 = 2^小数
				e.raw(0xD9, 0xFD)                             // fscale: 乘上 2^整数
				e.raw(0xDD, 0xD9)                             // fstp st1 (弹出整数部分)
				e.raw(0xDD, 0x1C, 0x25); e.imm32(regAddr(0)) // fstp qword [R0]
			case 3, 4, 5: // SIN/COS/TAN: x87 内建三角函数 (|x| 很大时精度退化)
				e.raw(0xDD, 0x04, 0x25); e.imm32(regAddr(0)) // fld qword [R0]
				switch ins.Args[0].V {
				case 3:
					e.raw(0xD9, 0xFE) // fsin
				case 4:
					e.raw(0xD9, 0xFF) // fcos
				default:
					e.raw(0xD9, 0xF2) // fptan: st0 = 1.0, st1 = tan
					e.raw(0xDD, 0xD8) // fstp st0 (丢掉压入的 1.0)
				}
				e.raw(0xDD, 0x1C, 0x25); e.imm32(regAddr(0)) // fstp qword [R0]
			case 0: // SQRT: R0 = sqrt(double(R0)) 的位模式 (sqrtsd 是精确的)
				e.raw(0xF2, 0x0F, 0x10, 0x04, 0x25); e.imm32(regAddr(0)) // movsd xmm0, [R0]
				e.raw(0xF2, 0x0F, 0x51, 0xC0)                             // sqrtsd xmm0, xmm0
				e.raw(0xF2, 0x0F, 0x11, 0x04, 0x25); e.imm32(regAddr(0)) // movsd [R0], xmm0
			case 2: // ABS: R0 = |int64(R0)| (无分支: 用符号掩码取反加一)
				e.loadReg(0)
				e.raw(0x48, 0x89, 0xC2)       // mov rdx, rax
				e.raw(0x48, 0xC1, 0xFA, 0x3F) // sar rdx, 63
				e.raw(0x48, 0x31, 0xD0)       // xor rax, rdx
				e.raw(0x48, 0x29, 0xD0)       // sub rax, rdx
				e.storeReg(0)
			case 10: // FCMP: R0 = -1 / 0 / 1
				e.raw(0xF2, 0x0F, 0x10, 0x04, 0x25); e.imm32(regAddr(0)) // movsd xmm0, [R0]
				e.raw(0xF2, 0x0F, 0x10, 0x0C, 0x25); e.imm32(regAddr(1)) // movsd xmm1, [R1]
				e.raw(0x31, 0xC0)             // xor eax, eax (默认相等)
				e.raw(0x66, 0x0F, 0x2E, 0xC1) // ucomisd xmm0, xmm1
				e.raw(0x0F, 0x8A); fpDone := e.imm32Pos() // jp done (无序/NaN 记 0)
				e.raw(0x0F, 0x97, 0xC1) // seta cl  (cl = xmm0 > xmm1)
				e.raw(0x0F, 0x92, 0xC2) // setb dl  (dl = xmm0 < xmm1)
				e.raw(0x0F, 0xB6, 0xC9) // movzx ecx, cl
				e.raw(0x0F, 0xB6, 0xD2) // movzx edx, dl
				e.raw(0x29, 0xD1)       // sub ecx, edx
				e.raw(0x48, 0x63, 0xC1) // movsxd rax, ecx
				e.patchRel(fpDone, len(e.code))
				e.storeReg(0)
			case 32, 33, 34: // FLOOR/CEIL/ROUND: R0 = 取整后的 double 位模式
				e.emitF64Round(int(ins.Args[0].V))
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
			case 23: // FTOA: 真正的十进制小数输出 (见 emitFTOA)
				e.emitFTOA()
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
				if e.isWin() {
					// GetSystemTimeAsFileTime 给的是 1601 起的 100ns 计数
					e.raw(0x48, 0x8D, 0x0C, 0x25); e.imm32(int32(winScratch2)) // lea rcx, [ft]
					if err := e.callIAT("GetSystemTimeAsFileTime"); err != nil {
						return nil, err
					}
					e.raw(0x48, 0x8B, 0x04, 0x25); e.imm32(int32(winScratch2)) // rax = ft
					e.raw(0x48, 0xB9); e.imm64(116444736000000000)            // rcx = 1601->1970
					e.raw(0x48, 0x29, 0xC8)                                   // sub rax, rcx
					e.raw(0x31, 0xD2)                                         // xor edx, edx
					e.raw(0x48, 0xB9); e.imm64(10000000)                      // rcx = 每秒 100ns 数
					e.raw(0x48, 0xF7, 0xF1)                                   // div rcx
					e.storeReg(0)
					break
				}
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
			case 86: // DIRREMOVE(path=R0)
				e.raw(0x4C, 0x89, 0xFF)
				e.raw(0x48, 0x03, 0x3C, 0x25); e.imm32(regAddr(0))
				e.raw(0xB8); e.imm32(84) // rmdir
				e.raw(0x0F, 0x05)
				e.storeReg(0)
			case 85: // FILEMOVE(src=R0, dst=R1)
				e.raw(0x4C, 0x89, 0xFF)
				e.raw(0x48, 0x03, 0x3C, 0x25); e.imm32(regAddr(0))
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(1))
				e.raw(0xB8); e.imm32(82) // rename
				e.raw(0x0F, 0x05)
				e.storeReg(0)
			case 87: // ISDIR(path=R0) -> 0/1
				e.loadReg(0); e.storeReg(7)
				e.raw(0xB8); e.imm32(160); e.storeReg(0)
				e.emitMalloc()
				e.loadReg(0); e.storeReg(8)
				e.raw(0x4C, 0x89, 0xFF); e.raw(0x48, 0x03, 0x3C, 0x25); e.imm32(regAddr(7))
				e.raw(0x4C, 0x89, 0xFE); e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(8))
				e.raw(0xB8); e.imm32(4) // stat
				e.raw(0x0F, 0x05)
				e.raw(0x48, 0x85, 0xC0)
				e.raw(0x0F, 0x88)
				fail := len(e.code); e.imm32(0)
				e.raw(0x4C, 0x89, 0xF9); e.raw(0x48, 0x03, 0x0C, 0x25); e.imm32(regAddr(8))
				e.raw(0x8B, 0x41, 0x18)             // eax = st_mode
				e.raw(0x25); e.imm32(0xF000)
				e.raw(0x3D); e.imm32(0x4000)
				e.raw(0x0F, 0x94, 0xC0)
				e.raw(0x0F, 0xB6, 0xC0)
				e.raw(0xE9)
				endPos := len(e.code); e.imm32(0)
				failAddr := len(e.code); e.patchRel(fail, failAddr)
				e.raw(0x31, 0xC0)
				endAddr := len(e.code); e.patchRel(endPos, endAddr)
				e.storeReg(0)
			case 88: // FILEMTIME(path=R0) -> 秒
				e.loadReg(0); e.storeReg(7)
				e.raw(0xB8); e.imm32(160); e.storeReg(0)
				e.emitMalloc()
				e.loadReg(0); e.storeReg(8)
				e.raw(0x4C, 0x89, 0xFF); e.raw(0x48, 0x03, 0x3C, 0x25); e.imm32(regAddr(7))
				e.raw(0x4C, 0x89, 0xFE); e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(8))
				e.raw(0xB8); e.imm32(4)
				e.raw(0x0F, 0x05)
				e.raw(0x48, 0x85, 0xC0)
				e.raw(0x0F, 0x88)
				fail2 := len(e.code); e.imm32(0)
				e.raw(0x4C, 0x89, 0xF9); e.raw(0x48, 0x03, 0x0C, 0x25); e.imm32(regAddr(8))
				e.raw(0x48, 0x8B, 0x41, 0x58) // rax = st_mtim.tv_sec
				e.raw(0xE9)
				end2 := len(e.code); e.imm32(0)
				fail2Addr := len(e.code); e.patchRel(fail2, fail2Addr)
				e.raw(0x31, 0xC0)
				end2Addr := len(e.code); e.patchRel(end2, end2Addr)
				e.storeReg(0)
			case 53: // FILEAPPEND(path=R0, data=R1)
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(0))
				e.raw(0xB8); e.imm32(2)
				e.raw(0x48, 0x89, 0xF7)
				e.raw(0xBE); e.imm32(0x441) // O_WRONLY|O_CREAT|O_APPEND
				e.raw(0xBA); e.imm32(0644)
				e.raw(0x0F, 0x05)
				e.raw(0x49, 0x89, 0xC0)
				e.loadReg(1); e.emitStrlen()
				e.raw(0x48, 0x89, 0xC2)
				e.raw(0x4C, 0x89, 0xFE)
				e.raw(0x48, 0x03, 0x34, 0x25); e.imm32(regAddr(1))
				e.raw(0x44, 0x89, 0xC7)
				e.raw(0xB8); e.imm32(1)
				e.raw(0x0F, 0x05)
				e.raw(0x44, 0x89, 0xC7)
				e.raw(0xB8); e.imm32(3)
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
			case 58: // DIRLIST(path=R0) -> 换行分隔的条目
				e.linDirList()
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
				if e.isWin() {
					if err := e.callIAT("GetTickCount64"); err != nil {
						return nil, err
					}
					e.storeReg(0)
					break
				}
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
				if e.isWin() {
					e.loadReg(0)
					e.raw(0x48, 0x89, 0xC1) // mov rcx, rax
					if err := e.callIAT("Sleep"); err != nil {
						return nil, err
					}
					e.raw(0x48, 0x31, 0xC0)
					e.storeReg(0)
					break
				}
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
			case 63: // OSNAME -> "linux" / "windows"
				if e.isWin() {
					e.raw(0x48, 0xC7, 0xC0); e.imm32(8)
					e.storeReg(0)
					e.emitMalloc()
					e.loadReg(0)
					e.raw(0x4C, 0x89, 0xFF); e.raw(0x48, 0x01, 0xC7)
					e.raw(0xC7, 0x07); e.imm32(0x646E6977)       // "wind"
					e.raw(0xC7, 0x47, 0x04); e.imm32(0x0073776F) // "ows\0"
					break
				}
				e.raw(0x48, 0xC7, 0xC0); e.imm32(6)
				e.storeReg(0)
				e.emitMalloc()
				e.loadReg(0)
				e.raw(0x4C, 0x89, 0xFF); e.raw(0x48, 0x01, 0xC7)
				e.raw(0xC7, 0x07); e.imm32(0x756E696C) // "linu"
				e.raw(0xC6, 0x47, 0x04, 0x78)          // 'x'
				e.raw(0xC6, 0x47, 0x05, 0x00)
			case 89: // TEMPDIR
				if e.isWin() {
					if err := e.winTempDir(); err != nil {
						return nil, err
					}
					break
				}
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
			if err := e.exitWithReg(0); err != nil {
				return nil, err
			}
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
