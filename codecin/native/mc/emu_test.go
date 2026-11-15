package mc

// emu_test.go: 一个只支持后端实际会发出的指令子集的 AArch64 解释器。
//
// 它的唯一用途是测试: 当前环境 (Windows, 无 qemu, WSL 被安全策略拦住) 无法
// 真正执行 AArch64 / ELF 产物, 于是把生成的机器码**原样**喂给这里的解释器,
// 用同一份 CIN 源码跑引擎与机器码两条路径, 对比 stdout 与退出码。
//
// 解码按位模式匹配 (不是通用反汇编): 只有后端真的会生成的编码才需要支持,
// 新增指令时这里会明确报 "未实现的指令", 而不是悄悄算错。

import (
	"encoding/binary"
	"fmt"
	"math"
	"os"
	"sort"
	"strings"
	"time"
)

const emuPageSize = 4096

type emuMem struct {
	pages map[uint64][]byte
}

func newEmuMem() *emuMem { return &emuMem{pages: map[uint64][]byte{}} }

func (m *emuMem) page(addr uint64) []byte {
	p := addr / emuPageSize * emuPageSize
	b, ok := m.pages[p]
	if !ok {
		b = make([]byte, emuPageSize)
		m.pages[p] = b
	}
	return b
}

func (m *emuMem) read(addr uint64, n int) []byte {
	out := make([]byte, n)
	for i := 0; i < n; i++ {
		pg := m.page(addr + uint64(i))
		out[i] = pg[(addr+uint64(i))%emuPageSize]
	}
	return out
}

func (m *emuMem) write(addr uint64, b []byte) {
	for i := 0; i < len(b); i++ {
		pg := m.page(addr + uint64(i))
		pg[(addr+uint64(i))%emuPageSize] = b[i]
	}
}

func (m *emuMem) u8(a uint64) uint8   { return m.read(a, 1)[0] }
func (m *emuMem) u16(a uint64) uint16 { return binary.LittleEndian.Uint16(m.read(a, 2)) }
func (m *emuMem) u32(a uint64) uint32 { return binary.LittleEndian.Uint32(m.read(a, 4)) }
func (m *emuMem) u64(a uint64) uint64 { return binary.LittleEndian.Uint64(m.read(a, 8)) }

func (m *emuMem) set8(a uint64, v uint8)   { m.write(a, []byte{v}) }
func (m *emuMem) set16(a uint64, v uint16) { m.write(a, []byte{byte(v), byte(v >> 8)}) }
func (m *emuMem) set32(a uint64, v uint32) {
	m.write(a, []byte{byte(v), byte(v >> 8), byte(v >> 16), byte(v >> 24)})
}
func (m *emuMem) set64(a uint64, v uint64) {
	b := make([]byte, 8)
	binary.LittleEndian.PutUint64(b, v)
	m.write(a, b)
}

type emuFile struct {
	f     *os.File
	dir   []os.DirEntry
	off   int
	isDir bool
	path  string
}

type a64CPU struct {
	x          [31]uint64
	sp         uint64
	pc         uint64
	z, c, n, v bool
	mem        *emuMem
	out        strings.Builder
	code       int64
	halt       bool
	err        string
	base       uint64 // VM 基址 (mmap 返回)
	files      map[int]*emuFile
	nextFd     int
	ms         int64
	cwd        string
	darwinMode bool
}

func (e *a64CPU) reg(i int) uint64 {
	if i == 31 {
		return 0
	}
	return e.x[i]
}

func (e *a64CPU) setReg(i int, v uint64) {
	if i != 31 {
		e.x[i] = v
	}
}

// readCStr 读 NUL 结尾字符串
func (e *a64CPU) readCStr(addr uint64) string {
	var b []byte
	for i := 0; i < 1<<16; i++ {
		ch := e.mem.u8(addr + uint64(i))
		if ch == 0 {
			break
		}
		b = append(b, ch)
	}
	return string(b)
}

func (e *a64CPU) writeCStr(addr uint64, s string) {
	for i := 0; i < len(s); i++ {
		e.mem.set8(addr+uint64(i), s[i])
	}
	e.mem.set8(addr+uint64(len(s)), 0)
}

func (e *a64CPU) pushFile(fl *emuFile) int {
	if e.nextFd < 2 { // 0/1/2 已经被 stdin/stdout/stderr 占用
		e.nextFd = 2
	}
	e.nextFd++
	fd := e.nextFd
	e.files[fd] = fl
	return fd
}

// run 从 pc 开始执行, 直到 exit 系统调用或遇到错误。
func (e *a64CPU) run(limit int) {
	e.runFrom(uint64(baseAddr+a64EntryOff), limit)
}

func (e *a64CPU) runFrom(start uint64, limit int) {
	e.pc = start
	for i := 0; i < limit; i++ {
		if e.halt || e.err != "" {
			return
		}
		w := e.mem.u32(e.pc)
		e.step(w)
	}
	e.err = "执行步数超限"
}

func (e *a64CPU) bad(w uint32, what string) {
	e.err = fmt.Sprintf("%s: 未实现的指令 %#08x @ %#x", what, w, e.pc)
	e.pc += 4
}

func (e *a64CPU) condOK(cond uint32) bool {
	switch cond {
	case 0:
		return e.z
	case 1:
		return !e.z
	case 2:
		return e.c
	case 3:
		return !e.c
	case 4:
		return e.n
	case 5:
		return !e.n
	case 6:
		return e.v
	case 7:
		return !e.v
	case 8:
		return e.c && !e.z
	case 9:
		return !e.c || e.z
	case 10:
		return e.n == e.v
	case 11:
		return e.n != e.v
	case 12:
		return !e.z && e.n == e.v
	case 13:
		return e.z || e.n != e.v
	}
	return false
}

func (e *a64CPU) freg(i int) float64    { return math.Float64frombits(e.x[i]) }
func (e *a64CPU) setF(i int, v float64) { e.x[i] = math.Float64bits(v) }

func (e *a64CPU) step(w uint32) {
	pc := e.pc
	next := pc + 4
	switch {
	// ---- 立即数搬运 ----
	case w&0x7F800000 == 0x52800000: // MOVZ
		d := int(w & 31)
		hw := uint((w >> 21) & 3)
		imm := uint64((w>>5)&0xFFFF) << (16 * hw)
		e.setReg(d, imm)
	case w&0x7F800000 == 0x72800000: // MOVK
		d := int(w & 31)
		hw := uint((w >> 21) & 3)
		imm := uint64((w>>5)&0xFFFF) << (16 * hw)
		mask := uint64(0xFFFF) << (16 * hw)
		e.setReg(d, e.reg(d)&^mask|imm)
	case w&0x7F800000 == 0x12800000: // MOVN
		d := int(w & 31)
		hw := uint((w >> 21) & 3)
		imm := ^(uint64((w>>5)&0xFFFF) << (16 * hw))
		e.setReg(d, imm)

	// ---- 加减立即数 ----
	case w&0x1F000000 == 0x11000000: // ADD/SUB/ADDS/SUBS (imm)
		sub := w&(1<<30) != 0
		imm := uint64((w>>10)&0xFFF) << (((w >> 22) & 1) * 12) // sh: LSL #0 / #12
		rn := int((w >> 5) & 31)
		rd := int(w & 31)
		a := e.baseReg(rn) // rn=31 => SP
		var res uint64
		if sub {
			res = a - imm
			e.setFlagsSub(a, imm, res)
		} else {
			res = a + imm
			e.setFlagsAdd(a, imm, res)
		}
		if w&(1<<29) == 0 { // S=1 是 CMP/CMN 之类的形式, 不写回
			if rd == 31 {
				e.sp = res
			} else {
				e.setReg(rd, res)
			}
		}

	// ---- 加减寄存器 ----
	//
	// 掩码只认 bits 28..24 (0001011) 且要求 bit21=0 (排除扩展寄存器形式),
	// 这样 ADD / SUB / ADDS / SUBS 四种组合 (bit30=op, bit29=S) 都走这里。
	case w&0x1F200000 == 0x0B000000: // ADD/SUB/ADDS/SUBS (reg, shifted)
		sub := w&(1<<30) != 0
		rm := int((w >> 16) & 31)
		rn := int((w >> 5) & 31)
		rd := int(w & 31)
		b := e.reg(rm)
		if w&(1<<22) != 0 { // shift by imm6
			sh := (w >> 22) & 3
			amt := (w >> 10) & 63
			switch sh {
			case 0:
				b <<= amt
			case 1:
				b >>= amt
			case 2:
				b = uint64(int64(b) >> amt)
			}
		}
		a := e.reg(rn)
		var res uint64
		if sub {
			res = a - b
			e.setFlagsSub(a, b, res)
		} else {
			res = a + b
			e.setFlagsAdd(a, b, res)
		}
		e.setReg(rd, res) // rd=31 时 setReg 内部自动丢弃 (XZR)

	// ---- 乘除 ----
	case w&0x7FE08000 == 0x1B000000: // MADD
		rm := int((w >> 16) & 31)
		ra := int((w >> 10) & 31)
		rn := int((w >> 5) & 31)
		rd := int(w & 31)
		e.setReg(rd, e.reg(ra)+e.reg(rn)*e.reg(rm))
	case w&0x7FE08000 == 0x1B008000: // MSUB
		rm := int((w >> 16) & 31)
		ra := int((w >> 10) & 31)
		rn := int((w >> 5) & 31)
		rd := int(w & 31)
		e.setReg(rd, e.reg(ra)-e.reg(rn)*e.reg(rm))
	case w&0x7FE0FC00 == 0x1AC00C00: // SDIV
		rm := int((w >> 16) & 31)
		rn := int((w >> 5) & 31)
		rd := int(w & 31)
		b := int64(e.reg(rm))
		a := int64(e.reg(rn))
		if b == 0 {
			e.setReg(rd, 0)
		} else {
			e.setReg(rd, uint64(a/b))
		}
	case w&0x7FE0FC00 == 0x1AC00800: // UDIV
		rm := int((w >> 16) & 31)
		rn := int((w >> 5) & 31)
		rd := int(w & 31)
		b := e.reg(rm)
		if b == 0 {
			e.setReg(rd, 0)
		} else {
			e.setReg(rd, e.reg(rn)/b)
		}

	// ---- 逻辑 ----
	case w&0x7F200000 == 0x0A000000, w&0x7F200000 == 0x2A000000, w&0x7F200000 == 0x4A000000,
		w&0x7F200000 == 0x0A200000, w&0x7F200000 == 0x2A200000, w&0x7F200000 == 0x4A200000:
		op := (w >> 29) & 3 // 0 AND, 1 ORR, 2 EOR, 3 ANDS
		neg := w&(1<<21) != 0
		rm := int((w >> 16) & 31)
		rn := int((w >> 5) & 31)
		rd := int(w & 31)
		b := e.reg(rm)
		if neg {
			b = ^b
		}
		a := e.reg(rn)
		var res uint64
		switch op {
		case 0, 3:
			res = a & b
		case 1:
			res = a | b
		case 2:
			res = a ^ b
		}
		e.setReg(rd, res)

	// ---- 移位 ----
	case w&0x7FE0FC00 == 0x1AC02000: // LSLV
		rm := int((w >> 16) & 31)
		rn := int((w >> 5) & 31)
		rd := int(w & 31)
		e.setReg(rd, e.reg(rn)<<(e.reg(rm)&63))
	case w&0x7FE0FC00 == 0x1AC02400: // LSRV
		rm := int((w >> 16) & 31)
		rn := int((w >> 5) & 31)
		rd := int(w & 31)
		e.setReg(rd, e.reg(rn)>>(e.reg(rm)&63))
	case w&0x7FE0FC00 == 0x1AC02800: // ASRV
		rm := int((w >> 16) & 31)
		rn := int((w >> 5) & 31)
		rd := int(w & 31)
		e.setReg(rd, uint64(int64(e.reg(rn))>>(e.reg(rm)&63)))

	// ---- 条件选择 / 扩展 ----
	case w&0x7FE00C00 == 0x1A800400: // CSINC (CSET 也走这里)
		cond := (w >> 12) & 0xF
		rm := int((w >> 16) & 31)
		rn := int((w >> 5) & 31)
		rd := int(w & 31)
		if e.condOK(cond) {
			e.setReg(rd, e.reg(rn))
		} else {
			e.setReg(rd, e.reg(rm)+1)
		}
	case w&0xFFFFFC00 == 0x93407C00: // SXTW
		rn := int((w >> 5) & 31)
		rd := int(w & 31)
		e.setReg(rd, uint64(int64(int32(e.reg(rn)))))
	case w&0xFFFFFC00 == 0x53001C00: // UXTB
		rn := int((w >> 5) & 31)
		rd := int(w & 31)
		e.setReg(rd, uint64(uint8(e.reg(rn))))

	// ---- 访存 ----
	case w&0xFFC00000 == 0xF9400000: // LDR (imm)
		rn := int((w >> 5) & 31)
		rt := int(w & 31)
		off := uint64((w>>10)&0xFFF) * 8
		e.setReg(rt, e.mem.u64(e.baseReg(rn)+off))
	case w&0xFFC00000 == 0xF9000000: // STR (imm)
		rn := int((w >> 5) & 31)
		rt := int(w & 31)
		off := uint64((w>>10)&0xFFF) * 8
		e.mem.set64(e.baseReg(rn)+off, e.reg(rt))
	case w&0xFFC00000 == 0x39400000: // LDRB (imm)
		rn := int((w >> 5) & 31)
		rt := int(w & 31)
		off := uint64((w >> 10) & 0xFFF)
		e.setReg(rt, uint64(e.mem.u8(e.baseReg(rn)+off)))
	case w&0xFFC00000 == 0x39000000: // STRB (imm)
		rn := int((w >> 5) & 31)
		rt := int(w & 31)
		off := uint64((w >> 10) & 0xFFF)
		e.mem.set8(e.baseReg(rn)+off, uint8(e.reg(rt)))
	case w&0xFFC00000 == 0xFD400000, w&0xFFC00000 == 0xFD000000: // LDR/STR D (imm)
		rn := int((w >> 5) & 31)
		rt := int(w & 31)
		off := uint64((w>>10)&0xFFF) * 8
		if w&(1<<22) != 0 {
			e.x[rt] = e.mem.u64(e.baseReg(rn) + off)
		} else {
			e.mem.set64(e.baseReg(rn)+off, e.x[rt])
		}
	case w&0xFFE00C00 == 0xF8400C00, w&0xFFE00C00 == 0xF8400400: // LDR pre/post index
		rn := int((w >> 5) & 31)
		rt := int(w & 31)
		imm := int64((w >> 12) & 0x1FF)
		if imm&0x100 != 0 {
			imm -= 0x200
		}
		base := int64(e.baseReg(rn))
		if w&0xFFE00C00 == 0xF8400C00 { // pre
			base += imm
			e.setBaseReg(rn, uint64(base))
		}
		e.setReg(rt, e.mem.u64(uint64(base)))
		if w&0xFFE00C00 == 0xF8400400 { // post
			e.setBaseReg(rn, uint64(base+imm))
		}
	case w&0xFFE00C00 == 0xF8000C00, w&0xFFE00C00 == 0xF8000400: // STR pre/post index
		rn := int((w >> 5) & 31)
		rt := int(w & 31)
		imm := int64((w >> 12) & 0x1FF)
		if imm&0x100 != 0 {
			imm -= 0x200
		}
		base := int64(e.baseReg(rn))
		if w&0xFFE00C00 == 0xF8000C00 {
			base += imm
			e.setBaseReg(rn, uint64(base))
		}
		e.mem.set64(uint64(base), e.reg(rt))
		if w&0xFFE00C00 == 0xF8000400 {
			e.setBaseReg(rn, uint64(base+imm))
		}
	case w&0xFFE00C00 == 0x38800000: // LDRSB
		rn := int((w >> 5) & 31)
		rt := int(w & 31)
		off := int64((w>>12)&0x1FF) << 55 >> 55
		e.setReg(rt, uint64(int64(int8(e.mem.u8(e.baseReg(rn)+uint64(off))))))
	case w&0xFFE00C00 == 0x78800000: // LDRSH
		rn := int((w >> 5) & 31)
		rt := int(w & 31)
		off := int64((w>>12)&0x1FF) << 55 >> 55
		e.setReg(rt, uint64(int64(int16(e.mem.u16(e.baseReg(rn)+uint64(off))))))
	case w&0xFFE00C00 == 0xB8800000: // LDRSW
		rn := int((w >> 5) & 31)
		rt := int(w & 31)
		off := int64((w>>12)&0x1FF) << 55 >> 55
		e.setReg(rt, uint64(int64(int32(e.mem.u32(e.baseReg(rn)+uint64(off))))))
	case w&0xFFE00C00 == 0x78400000: // LDRH (unscaled)
		rn := int((w >> 5) & 31)
		rt := int(w & 31)
		off := int64((w>>12)&0x1FF) << 55 >> 55
		e.setReg(rt, uint64(e.mem.u16(e.baseReg(rn)+uint64(off))))

	// ---- 寄存器偏移访存 ----
	case w&0xFFE00C00 == 0xF8600800: // LDR (reg)
		rn := int((w >> 5) & 31)
		rm := int((w >> 16) & 31)
		rt := int(w & 31)
		e.setReg(rt, e.mem.u64(e.baseReg(rn)+e.reg(rm)))
	case w&0xFFE00C00 == 0xF8200800: // STR (reg)
		rn := int((w >> 5) & 31)
		rm := int((w >> 16) & 31)
		rt := int(w & 31)
		e.mem.set64(e.baseReg(rn)+e.reg(rm), e.reg(rt))
	case w&0xFFE00C00 == 0x38600800: // LDRB (reg)
		rn := int((w >> 5) & 31)
		rm := int((w >> 16) & 31)
		rt := int(w & 31)
		e.setReg(rt, uint64(e.mem.u8(e.baseReg(rn)+e.reg(rm))))
	case w&0xFFE00C00 == 0x38200800: // STRB (reg)
		rn := int((w >> 5) & 31)
		rm := int((w >> 16) & 31)
		rt := int(w & 31)
		e.mem.set8(e.baseReg(rn)+e.reg(rm), uint8(e.reg(rt)))

	// ---- 分支 ----
	case w&0xFC000000 == 0x14000000: // B
		imm := int64(w&0x3FFFFFF) << 38 >> 38
		next = pc + uint64(imm*4)
	case w&0xFC000000 == 0x94000000: // BL
		imm := int64(w&0x3FFFFFF) << 38 >> 38
		e.x[30] = pc + 4
		next = pc + uint64(imm*4)
	case w&0xFF000010 == 0x54000000: // B.cond
		cond := w & 0xF
		imm := int64((w>>5)&0x7FFFF) << 45 >> 45
		if e.condOK(cond) {
			next = pc + uint64(imm*4)
		}
	// CBZ / CBNZ 的四条编码只在 bit31 (sf) 和 bit24 (NZ) 上不同,
	// 掩码必须含 bit24, 否则 CBNZ 会被当成 CBZ 执行。
	case w&0xFF000000 == 0x34000000: // CBZ (32)
		rt := int(w & 31)
		imm := int64((w>>5)&0x7FFFF) << 45 >> 45
		if uint32(e.reg(rt)) == 0 {
			next = pc + uint64(imm*4)
		}
	case w&0xFF000000 == 0x35000000: // CBNZ (32)
		rt := int(w & 31)
		imm := int64((w>>5)&0x7FFFF) << 45 >> 45
		if uint32(e.reg(rt)) != 0 {
			next = pc + uint64(imm*4)
		}
	case w&0xFF000000 == 0xB4000000: // CBZ (64)
		rt := int(w & 31)
		imm := int64((w>>5)&0x7FFFF) << 45 >> 45
		if e.reg(rt) == 0 {
			next = pc + uint64(imm*4)
		}
	case w&0xFF000000 == 0xB5000000: // CBNZ (64)
		rt := int(w & 31)
		imm := int64((w>>5)&0x7FFFF) << 45 >> 45
		if e.reg(rt) != 0 {
			next = pc + uint64(imm*4)
		}
	case w == 0xD65F03C0: // RET
		next = e.x[30]

	// ---- 系统调用 ----
	case w == 0xD4000001:
		// syscall 只改状态，不改变控制流；next 保持 pc+4。
		e.syscall()

	// ---- 浮点 ----
	case w&0xFFE0FC00 == 0x1E602800: // FADD
		e.setF(int(w&31), e.freg(int((w>>5)&31))+e.freg(int((w>>16)&31)))
	case w&0xFFE0FC00 == 0x1E603800: // FSUB
		e.setF(int(w&31), e.freg(int((w>>5)&31))-e.freg(int((w>>16)&31)))
	case w&0xFFE0FC00 == 0x1E600800: // FMUL
		e.setF(int(w&31), e.freg(int((w>>5)&31))*e.freg(int((w>>16)&31)))
	case w&0xFFE0FC00 == 0x1E601800: // FDIV
		e.setF(int(w&31), e.freg(int((w>>5)&31))/e.freg(int((w>>16)&31)))
	case w&0xFFFFFC00 == 0x1E61C000: // FSQRT
		e.setF(int(w&31), math.Sqrt(e.freg(int((w>>5)&31))))
	case w&0xFFFFFC00 == 0x1E60C000: // FABS
		e.setF(int(w&31), math.Abs(e.freg(int((w>>5)&31))))
	case w&0xFFFFFC00 == 0x1E614000: // FNEG
		e.setF(int(w&31), -e.freg(int((w>>5)&31)))
	case w&0xFFE0FC00 == 0x1E602000: // FCMP
		a := e.freg(int((w >> 5) & 31))
		b := e.freg(int((w >> 16) & 31))
		e.z = a == b
		e.n = a < b
		e.c = a >= b || math.IsNaN(a) || math.IsNaN(b)
		e.v = math.IsNaN(a) || math.IsNaN(b)
		if math.IsNaN(a) || math.IsNaN(b) {
			e.z, e.n = false, false
		}
	case w&0xFFFFFC00 == 0x9E780000: // FCVTZS
		e.setReg(int(w&31), uint64(int64(e.freg(int((w>>5)&31)))))
	case w&0xFFFFFC00 == 0x9E620000: // SCVTF
		e.setF(int(w&31), float64(int64(e.reg(int((w>>5)&31)))))
	case w&0xFFFFFC00 == 0x1E654000: // FRINTM (floor)
		e.setF(int(w&31), math.Floor(e.freg(int((w>>5)&31))))
	case w&0xFFFFFC00 == 0x1E64C000: // FRINTP (ceil)
		e.setF(int(w&31), math.Ceil(e.freg(int((w>>5)&31))))
	case w&0xFFFFFC00 == 0x1E664000: // FRINTA
		e.setF(int(w&31), math.Round(e.freg(int((w>>5)&31))))
	case w&0xFFFFFC00 == 0x1E604000: // FMOV Dd, Dn
		e.setF(int(w&31), e.freg(int((w>>5)&31)))
	case w&0xFFFFFC00 == 0x9E660000: // FMOV Xd, Dn
		e.setReg(int(w&31), e.x[int((w>>5)&31)])
	case w&0xFFFFFC00 == 0x9E670000: // FMOV Dd, Xn
		e.x[int(w&31)] = e.reg(int((w >> 5) & 31))
	default:
		e.bad(w, "AArch64")
		return
	}
	e.pc = next
}

func (e *a64CPU) baseReg(rn int) uint64 {
	if rn == 31 {
		return e.sp
	}
	return e.x[rn]
}

func (e *a64CPU) setBaseReg(rn int, v uint64) {
	if rn == 31 {
		e.sp = v
		return
	}
	e.x[rn] = v
}

func (e *a64CPU) setFlagsAdd(a, b, res uint64) {
	e.z = res == 0
	e.c = res < a
	e.n = res>>63 != 0
	e.v = (a>>63 == b>>63) && (res>>63 != a>>63)
}

func (e *a64CPU) setFlagsSub(a, b, res uint64) {
	e.z = res == 0
	e.c = a >= b
	e.n = res>>63 != 0
	e.v = (a>>63 != b>>63) && (res>>63 != a>>63)
}

// syscall 在测试里模拟 Linux / Darwin 的系统调用 (只实现后端会用到的那些)。
func (e *a64CPU) syscall() {
	num := e.x[8]
	if e.darwinMode {
		num = e.x[16]
	}
	defer func() {
		// 出错标志: 后端 Linux 用 "x0 是负 errno", Darwin 用进位标志。
		// 模拟器统一把失败写成 x0 = -1, 再据此置进位标志, 两种判法都能工作。
		e.c = e.x[0] >= 0xFFFFFFFFFFFFF000
	}()
	switch num {
	case 222, 197: // mmap
		e.x[0] = e.base
	case 64, 4: // write
		fd := int(e.x[0])
		buf := e.x[1]
		n := int(e.x[2])
		if fd == 1 || fd == 2 {
			e.out.Write(e.mem.read(buf, n))
		} else if f, ok := e.files[fd]; ok && f.f != nil {
			f.f.Write(e.mem.read(buf, n))
		}
		e.x[0] = uint64(n)
	case 63, 3: // read
		fd := int(e.x[0])
		n := int(e.x[2])
		if f, ok := e.files[fd]; ok && f.f != nil {
			b := make([]byte, n)
			got, _ := f.f.Read(b)
			e.mem.write(e.x[1], b[:got])
			e.x[0] = uint64(got)
		} else {
			e.x[0] = 0
		}
	case 93, 1: // exit
		e.code = int64(e.x[0])
		e.halt = true
	case 56: // openat (linux)
		e.linuxOpen()
	case 5: // open (darwin)
		e.darwinOpen()
	case 57, 6: // close
		fd := int(e.x[0])
		if f, ok := e.files[fd]; ok && f.f != nil {
			f.f.Close()
		}
		delete(e.files, fd)
		e.x[0] = 0
	case 79: // newfstatat
		e.linuxStat()
	case 338: // stat64 (darwin)
		e.darwinStat()
	case 34: // mkdirat
		p := e.readCStr(e.x[1])
		if err := os.Mkdir(p, os.FileMode(e.x[2])); err != nil {
			e.x[0] = uint64(^uint64(0))
		} else {
			e.x[0] = 0
		}
	case 136: // mkdir (darwin)
		p := e.readCStr(e.x[0])
		if err := os.Mkdir(p, os.FileMode(e.x[1])); err != nil {
			e.x[0] = uint64(^uint64(0))
		} else {
			e.x[0] = 0
		}
	case 35: // unlinkat
		p := e.readCStr(e.x[1])
		if err := os.Remove(p); err != nil {
			e.x[0] = uint64(^uint64(0))
		} else {
			e.x[0] = 0
		}
	case 10: // unlink (darwin)
		p := e.readCStr(e.x[0])
		if err := os.Remove(p); err != nil {
			e.x[0] = uint64(^uint64(0))
		} else {
			e.x[0] = 0
		}
	case 137: // rmdir (darwin)
		p := e.readCStr(e.x[0])
		if err := os.Remove(p); err != nil {
			e.x[0] = uint64(^uint64(0))
		} else {
			e.x[0] = 0
		}
	case 38: // renameat
		if err := os.Rename(e.readCStr(e.x[1]), e.readCStr(e.x[3])); err != nil {
			e.x[0] = uint64(^uint64(0))
		} else {
			e.x[0] = 0
		}
	case 128: // rename (darwin)
		if err := os.Rename(e.readCStr(e.x[0]), e.readCStr(e.x[1])); err != nil {
			e.x[0] = uint64(^uint64(0))
		} else {
			e.x[0] = 0
		}
	case 17, 326: // getcwd：Linux 返回字节数（含 NUL），Darwin 返回缓冲区指针
		d, _ := os.Getwd()
		e.writeCStr(e.x[0], d)
		if !e.darwinMode {
			e.x[0] = uint64(len(d) + 1)
		}
	case 49, 12: // chdir
		if err := os.Chdir(e.readCStr(e.x[0])); err != nil {
			e.x[0] = uint64(^uint64(0))
		} else {
			e.x[0] = 0
		}
	case 61: // getdents64 (linux)
		e.getdents(19, 16, 18)
	case 344: // getdirentries64 (darwin)
		e.getdents(21, 16, 20)
	case 113: // clock_gettime
		now := time.Now()
		e.mem.set64(e.x[1], uint64(now.Unix()))
		e.mem.set64(e.x[1]+8, uint64(now.Nanosecond()))
		e.x[0] = 0
	case 116: // gettimeofday (darwin)
		now := time.Now()
		e.mem.set64(e.x[0], uint64(now.Unix()))
		e.mem.set64(e.x[0]+8, uint64(now.Nanosecond()/1000))
		e.x[0] = 0
	case 101, 240: // nanosleep: 测试里不真的睡
		e.x[0] = 0
	default:
		e.err = fmt.Sprintf("未模拟的系统调用 %d", num)
	}
}

func (e *a64CPU) linuxOpen() {
	path := e.readCStr(e.x[1])
	flags := int(e.x[2])
	e.openCommon(path, flags)
}

func (e *a64CPU) darwinOpen() {
	path := e.readCStr(e.x[0])
	flags := int(e.x[1])
	e.openCommon(path, flags)
}

func (e *a64CPU) openCommon(path string, flags int) {
	// O_CREAT / O_TRUNC / O_APPEND 的位值两个平台不同, 不能混着判:
	//   Linux : CREAT=0x40 TRUNC=0x200 APPEND=0x400
	//   Darwin: CREAT=0x200 TRUNC=0x400 APPEND=0x8
	oflag := os.O_RDONLY
	if flags&1 != 0 { // O_WRONLY
		oflag = os.O_WRONLY
	}
	if e.darwinMode {
		if flags&0x200 != 0 {
			oflag |= os.O_CREATE
		}
		if flags&0x400 != 0 {
			oflag |= os.O_TRUNC
		}
		if flags&0x8 != 0 {
			oflag |= os.O_APPEND
		}
	} else {
		if flags&0x40 != 0 {
			oflag |= os.O_CREATE
		}
		if flags&0x200 != 0 {
			oflag |= os.O_TRUNC
		}
		if flags&0x400 != 0 {
			oflag |= os.O_APPEND
		}
	}
	if fi, err := os.Stat(path); err == nil && fi.IsDir() {
		ents, err := os.ReadDir(path)
		if err != nil {
			e.x[0] = uint64(^uint64(0))
			return
		}
		sort.Slice(ents, func(i, j int) bool { return ents[i].Name() < ents[j].Name() })
		e.x[0] = uint64(e.pushFile(&emuFile{dir: ents, isDir: true, path: path}))
		return
	}
	f, err := os.OpenFile(path, oflag, 0644)
	if err != nil {
		e.x[0] = uint64(^uint64(0))
		return
	}
	e.x[0] = uint64(e.pushFile(&emuFile{f: f, path: path}))
}

func (e *a64CPU) linuxStat() {
	path := e.readCStr(e.x[1])
	buf := e.x[2]
	e.statCommon(path, buf, 16, 48, 88)
}

func (e *a64CPU) darwinStat() {
	path := e.readCStr(e.x[0])
	buf := e.x[1]
	e.statCommon(path, buf, 4, 96, 48)
}

func (e *a64CPU) statCommon(path string, buf uint64, modeOff, sizeOff, mtimeOff int) {
	fi, err := os.Stat(path)
	if err != nil {
		e.x[0] = uint64(^uint64(0))
		return
	}
	mode := uint32(0o100000) // 普通文件
	if fi.IsDir() {
		mode = 0o040000
	}
	e.mem.set32(buf+uint64(modeOff), mode)
	e.mem.set64(buf+uint64(sizeOff), uint64(fi.Size()))
	e.mem.set64(buf+uint64(mtimeOff), uint64(fi.ModTime().Unix()))
	e.x[0] = 0
}

// getdents 生成 dirent 记录 (nameOff/reclenOff/typeOff 由平台决定)。
func (e *a64CPU) getdents(nameOff, reclenOff, typeOff int) {
	fd := int(e.x[0])
	buf := e.x[1]
	size := int(e.x[2])
	f, ok := e.files[fd]
	if !ok || !f.isDir {
		e.x[0] = 0
		return
	}
	written := 0
	for f.off < len(f.dir) {
		ent := f.dir[f.off]
		name := ent.Name()
		rec := (nameOff + len(name) + 1 + 7) / 8 * 8
		if written+rec > size {
			break
		}
		p := buf + uint64(written)
		e.mem.set64(p, 1) // d_ino
		e.mem.set64(p+8, uint64(f.off+1))
		e.mem.set16(p+uint64(reclenOff), uint16(rec))
		if ent.IsDir() {
			e.mem.set8(p+uint64(typeOff), 4)
			if nameOff == 21 {
				e.mem.set16(p+18, uint16(len(name)))
			}
		} else {
			e.mem.set8(p+uint64(typeOff), 8)
		}
		e.writeCStr(p+uint64(nameOff), name)
		written += rec
		f.off++
	}
	if written == 0 {
		e.x[0] = 0 // EOF
		return
	}
	e.x[0] = uint64(written)
}
