package mc

import (
	"encoding/binary"
	"fmt"

	"codecin-native/ir"
)

// AArch64 (ARM64) 机器码后端: Linux ELF 与 macOS Mach-O 共用同一套编码。
//
// 与 x86-64 后端语义一致, 但寻址方式不同: x86-64 版把虚拟寄存器组放在映像里
// 靠绝对地址 (mov rax,[abs32]) 访问; 这里改成相对基址寄存器 ——
//
//	x19 = 虚拟寄存器组 (VM 偏移 0)
//	x20 = VM 内存基址 (mmap 的返回值)
//
// 这样生成的代码与映像落在哪个虚拟地址无关, 且不受 4 GiB 寻址限制。
//
// 虚拟寄存器全部存在内存里 (R0..R32), 任何一条指令序列都不需要跨指令保活
// 物理寄存器, 于是 SYS 的内联实现可以随意使用 x0..x17 作临时寄存器;
// x21..x27 则留给需要跨内联片段保存值的地方。
// 注意: 虚拟寄存器组**不能**放在 VM 偏移 0。编译器的静态数据也是从偏移 0
// 开始铺的, 两者会直接撞上 (第一个字符串字面量就会被寄存器写坏)。所以把
// 寄存器组和内部缓冲区一起放到 VM 高端的保留区, 中间留给栈和堆。
//
// 于是出现两套偏移: scr* 是"相对 x19 (寄存器组基址)"的, 用于 ldr/str 的
// 立即数; a64* 是"绝对 VM 偏移"的, 用于 add x20 和 vmCon。
const (
	a64MemSize   = 1 << 30
	a64HeapStart = 0x100000
	a64BankBase  = a64MemSize - 0x200000 // 寄存器组的绝对 VM 偏移 (留 2 MiB 给栈/堆)
	a64RegBank   = a64BankBase

	scrFlags   = 0x200 // zf @ +0, lf @ +1
	scrNL      = 0x2F8 // OUT 10 的换行字节落点
	scrItoa    = 0x300 // ITOA 缓冲区 (自 +0x40 向前增长)
	scrFtoa    = 0x400 // FTOA 输出缓冲区 (向后增长, 64 字节)
	scrTmp     = 0x480 // 各种系统调用用的临时结构 (timespec / stat)
	scrHeapPtr = 0x500 // bump 分配器的堆指针
	scrRand    = 0x508 // xorshift 状态

	a64Flags   = a64BankBase + scrFlags
	a64NL      = a64BankBase + scrNL
	a64Itoa    = a64BankBase + scrItoa
	a64Ftoa    = a64BankBase + scrFtoa
	a64Tmp     = a64BankBase + scrTmp
	a64HeapPtr = a64BankBase + scrHeapPtr
	a64Rand    = a64BankBase + scrRand

	// 物理寄存器分工
	a64Regs = 19
	a64VMB  = 20
	// 跨片段保存值用的临时寄存器 (内联片段不碰)
	a64T1 = 21
	a64T2 = 22
	a64T3 = 23
	a64T4 = 24
	a64T5 = 25
	a64T6 = 26
)

// AArch64 条件码
const (
	condEQ = 0
	condNE = 1
	condHS = 2 // 无符号 >=, 也是"有进位"
	condLO = 3
	condMI = 4
	condPL = 5
	condVS = 6
	condVC = 7
	condHI = 8
	condLS = 9
	condGE = 10
	condLT = 11
	condGT = 12
	condLE = 13
	condAL = 14
)

// a64Target 区分落到操作系统的那些原语。
type a64Target int

const (
	a64Linux a64Target = iota
	a64Darwin
)

func (a *a8) isDarwin() bool { return a.tgt == a64Darwin }

type a8 struct {
	code []byte
	tgt  a64Target
	// dataVAddr 是静态数据在映像里的**虚地址**: 序言从这里把初值搬进 VM。
	// ELF 用 0x410000 (与 x86-64 后端同布局), Mach-O 用 __DATA 段的 vmaddr。
	dataVAddr uint64
}

// ---------- 指令编码 ----------

func (a *a8) w(v uint32) {
	var b [4]byte
	binary.LittleEndian.PutUint32(b[:], v)
	a.code = append(a.code, b[:]...)
}

func (a *a8) at(pos int) uint32 { return binary.LittleEndian.Uint32(a.code[pos:]) }
func (a *a8) setAt(pos int, v uint32) {
	binary.LittleEndian.PutUint32(a.code[pos:], v)
}

func (a *a8) movz(rd int, imm uint16, shift int) {
	a.w(0xD2800000 | uint32(shift/16)<<21 | uint32(imm)<<5 | uint32(rd))
}

func (a *a8) movk(rd int, imm uint16, shift int) {
	a.w(0xF2800000 | uint32(shift/16)<<21 | uint32(imm)<<5 | uint32(rd))
}

// movImm 把 64 位常量装进 x<rd> (movz + 至多三条 movk)。
func (a *a8) movImm(rd int, v uint64) {
	a.movz(rd, uint16(v), 0)
	for s := 16; s < 64; s += 16 {
		if part := uint16(v >> uint(s)); part != 0 {
			a.movk(rd, part, s)
		}
	}
}

func (a *a8) movReg(rd, rm int) { a.w(0xAA0003E0 | uint32(rm)<<16 | uint32(rd)) }

func (a *a8) addImm(rd, rn int, imm uint32) {
	a.w(0x91000000 | (imm&0xFFF)<<10 | uint32(rn)<<5 | uint32(rd))
}

func (a *a8) subImm(rd, rn int, imm uint32) {
	a.w(0xD1000000 | (imm&0xFFF)<<10 | uint32(rn)<<5 | uint32(rd))
}

func (a *a8) cmpImm(rn int, imm uint32) { a.w(0xF1000000 | (imm&0xFFF)<<10 | uint32(rn)<<5 | 31) }

func (a *a8) addReg(rd, rn, rm int) { a.w(0x8B000000 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rd)) }
func (a *a8) subReg(rd, rn, rm int) { a.w(0xCB000000 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rd)) }
func (a *a8) cmpReg(rn, rm int)     { a.w(0xEB00001F | uint32(rm)<<16 | uint32(rn)<<5) }
func (a *a8) mulReg(rd, rn, rm int) {
	a.w(0x9B000000 | uint32(rm)<<16 | 31<<10 | uint32(rn)<<5 | uint32(rd))
}

// msub: Xd = Xa - Xn*Xm (取模用)
func (a *a8) msub(rd, rn, rm, ra int) {
	a.w(0x9B008000 | uint32(rm)<<16 | uint32(ra)<<10 | uint32(rn)<<5 | uint32(rd))
}

func (a *a8) sdiv(rd, rn, rm int)   { a.w(0x9AC00C00 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rd)) }
func (a *a8) udiv(rd, rn, rm int)   { a.w(0x9AC00800 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rd)) }
func (a *a8) andReg(rd, rn, rm int) { a.w(0x8A000000 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rd)) }
func (a *a8) orrReg(rd, rn, rm int) { a.w(0xAA000000 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rd)) }
func (a *a8) eorReg(rd, rn, rm int) { a.w(0xCA000000 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rd)) }
func (a *a8) andW(rd, rn, rm int)   { a.w(0x0A000000 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rd)) }
func (a *a8) orrW(rd, rn, rm int)   { a.w(0x2A000000 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rd)) }
func (a *a8) mvn(rd, rm int)        { a.w(0xAA2003E0 | uint32(rm)<<16 | uint32(rd)) }

// addsXZR: 计算 xn + xm 并置标志 (只为了拿进位)
func (a *a8) addsXZR(rn, rm int) { a.w(0xAB000000 | uint32(rm)<<16 | uint32(rn)<<5 | 31) }

func (a *a8) lslv(rd, rn, rm int) { a.w(0x9AC02000 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rd)) }
func (a *a8) lsrv(rd, rn, rm int) { a.w(0x9AC02400 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rd)) }
func (a *a8) asrv(rd, rn, rm int) { a.w(0x9AC02800 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rd)) }

// cset: rd = (cond) ? 1 : 0
func (a *a8) cset(rd int, cond uint32) { a.w(0x9A9F07E0 | (cond^1)<<12 | uint32(rd)) }

func (a *a8) sxtw(rd, rn int) { a.w(0x93407C00 | uint32(rn)<<5 | uint32(rd)) }
func (a *a8) uxtb(rd, rn int) { a.w(0x53001C00 | uint32(rn)<<5 | uint32(rd)) }

func (a *a8) ldr(rt, rn int, off int32) {
	a.w(0xF9400000 | uint32(off/8)<<10 | uint32(rn)<<5 | uint32(rt))
}

func (a *a8) str(rt, rn int, off int32) {
	a.w(0xF9000000 | uint32(off/8)<<10 | uint32(rn)<<5 | uint32(rt))
}

func (a *a8) ldrb(rt, rn int, off int32) {
	a.w(0x39400000 | uint32(off)<<10 | uint32(rn)<<5 | uint32(rt))
}

func (a *a8) strb(rt, rn int, off int32) {
	a.w(0x39000000 | uint32(off)<<10 | uint32(rn)<<5 | uint32(rt))
}

func (a *a8) ldrsb(rt, rn int, off int32) {
	a.w(0x38800000 | uint32(off&0x1FF)<<12 | uint32(rn)<<5 | uint32(rt))
}

func (a *a8) ldrsh(rt, rn int, off int32) {
	a.w(0x78800000 | uint32(off&0x1FF)<<12 | uint32(rn)<<5 | uint32(rt))
}

func (a *a8) ldrhU(rt, rn int, off int32) {
	a.w(0x78400000 | uint32(off&0x1FF)<<12 | uint32(rn)<<5 | uint32(rt))
}

func (a *a8) ldrsw(rt, rn int, off int32) {
	a.w(0xB8800000 | uint32(off&0x1FF)<<12 | uint32(rn)<<5 | uint32(rt))
}

func (a *a8) ldrD(ft, rn int, off int32) {
	a.w(0xFD400000 | uint32(off/8)<<10 | uint32(rn)<<5 | uint32(ft))
}

func (a *a8) strD(ft, rn int, off int32) {
	a.w(0xFD000000 | uint32(off/8)<<10 | uint32(rn)<<5 | uint32(ft))
}

// ---------- 分支 (偏移以字节计, 目标必须 4 字节对齐) ----------

func (a *a8) bAt(off int)  { a.w(0x14000000 | uint32(off/4)&0x3FFFFFF) }
func (a *a8) blAt(off int) { a.w(0x94000000 | uint32(off/4)&0x3FFFFFF) }

func (a *a8) bcondAt(cond uint32, off int) {
	a.w(0x54000000 | (uint32(off/4)&0x7FFFF)<<5 | cond)
}

func (a *a8) cbzAt(rt int, off int)  { a.w(0xB4000000 | (uint32(off/4)&0x7FFFF)<<5 | uint32(rt)) }
func (a *a8) cbnzAt(rt int, off int) { a.w(0xB5000000 | (uint32(off/4)&0x7FFFF)<<5 | uint32(rt)) }

func (a *a8) cbzWAt(rt int, off int) {
	a.w(0x34000000 | (uint32(off/4)&0x7FFFF)<<5 | uint32(rt))
}

func (a *a8) cbnzWAt(rt int, off int) {
	a.w(0x35000000 | (uint32(off/4)&0x7FFFF)<<5 | uint32(rt))
}

func (a *a8) ret() { a.w(0xD65F03C0) }
func (a *a8) svc() { a.w(0xD4000001) }

// ---------- 浮点 ----------

func (a *a8) fadd(dd, dn, dm int) { a.w(0x1E602800 | uint32(dm)<<16 | uint32(dn)<<5 | uint32(dd)) }
func (a *a8) fsub(dd, dn, dm int) { a.w(0x1E603800 | uint32(dm)<<16 | uint32(dn)<<5 | uint32(dd)) }
func (a *a8) fmul(dd, dn, dm int) { a.w(0x1E600800 | uint32(dm)<<16 | uint32(dn)<<5 | uint32(dd)) }
func (a *a8) fdiv(dd, dn, dm int) { a.w(0x1E601800 | uint32(dm)<<16 | uint32(dn)<<5 | uint32(dd)) }
func (a *a8) fsqrt(dd, dn int)    { a.w(0x1E61C000 | uint32(dn)<<5 | uint32(dd)) }
func (a *a8) fabs(dd, dn int)     { a.w(0x1E60C000 | uint32(dn)<<5 | uint32(dd)) }
func (a *a8) fneg(dd, dn int)     { a.w(0x1E614000 | uint32(dn)<<5 | uint32(dd)) }
func (a *a8) fcmp(dn, dm int)     { a.w(0x1E602000 | uint32(dm)<<16 | uint32(dn)<<5) }

// fcvtzs: Xd = (int64) Dn (向零截断)
func (a *a8) fcvtzs(rd, dn int) { a.w(0x9E780000 | uint32(dn)<<5 | uint32(rd)) }

// scvtf: Dd = (double) Xn
func (a *a8) scvtf(dd, rn int) { a.w(0x9E620000 | uint32(rn)<<5 | uint32(dd)) }

func (a *a8) frintm(dd, dn int) { a.w(0x1E654000 | uint32(dn)<<5 | uint32(dd)) } // floor
func (a *a8) frintp(dd, dn int) { a.w(0x1E64C000 | uint32(dn)<<5 | uint32(dd)) } // ceil
func (a *a8) frinta(dd, dn int) { a.w(0x1E664000 | uint32(dn)<<5 | uint32(dd)) } // round

func (a *a8) fmovToX(rd, dn int)   { a.w(0x9E660000 | uint32(dn)<<5 | uint32(rd)) }
func (a *a8) fmovFromX(dd, rn int) { a.w(0x9E670000 | uint32(rn)<<5 | uint32(dd)) }

// ---------- VM 辅助 ----------

func (a *a8) loadReg(r int)          { a.ldr(0, a64Regs, int32(r*8)) }
func (a *a8) storeReg(r int)         { a.str(0, a64Regs, int32(r*8)) }
func (a *a8) loadRegTo(rd, r int)    { a.ldr(rd, a64Regs, int32(r*8)) }
func (a *a8) storeRegFrom(rs, r int) { a.str(rs, a64Regs, int32(r*8)) }

// addImmBig: xd = xn + imm (立即数超过 12 位时走 movImm + 加法)
func (a *a8) addImmBig(rd, rn int, imm int64) {
	switch {
	case imm == 0:
		if rd != rn {
			a.movReg(rd, rn)
		}
	case imm > 0 && imm <= 0xFFF:
		a.addImm(rd, rn, uint32(imm))
	case imm < 0 && -imm <= 0xFFF:
		a.subImm(rd, rn, uint32(-imm))
	default:
		a.movImm(9, uint64(imm))
		a.addReg(rd, rn, 9)
	}
}

// vmAddr: x9 = x20 + R[base] + off
func (a *a8) vmAddr(base int, off int32) {
	a.loadRegTo(9, base)
	a.addReg(9, a64VMB, 9)
	if off != 0 {
		a.addImmBig(9, 9, int64(off))
	}
}

// vmCon: x9 = x20 + vmOff (VM 里固定位置的宿主地址)
func (a *a8) vmCon(vmOff int32) { a.addImmBig(9, a64VMB, int64(vmOff)) }

// ---------- 分支修补 ----------

type a64FixKind int

const (
	a64FixB a64FixKind = iota
	a64FixBCond
	a64FixCB
)

// patch 回填一条占位分支。B/BL 用 26 位偏移, B.cond 与 CBZ/CBNZ 用 19 位,
// 两者的位宽不同, 不能先用 19 位掩码再截断 —— 否则超过 ±128 KiB 的 B/BL
// 会被截成错误的符号位, 跳到完全不相干的位置。
func (a *a8) patch(pos, target int, kind a64FixKind) {
	delta := target - pos
	if delta%4 != 0 {
		panic("aarch64: 分支目标未对齐")
	}
	w := a.at(pos)
	units := uint32(delta/4) // 负数按补码参与与运算
	if kind == a64FixB {
		a.setAt(pos, w&^0x03FFFFFF|(units&0x3FFFFFF))
		return
	}
	a.setAt(pos, w&^0x00FFFFE0|(units&0x7FFFF)<<5)
}

// 占位分支, 返回字节偏移 (供上层 patch)
func (a *a8) phB() int             { pos := len(a.code); a.bAt(0); return pos }
func (a *a8) phBC(cond uint32) int { pos := len(a.code); a.bcondAt(cond, 0); return pos }
func (a *a8) phCbnz(rt int) int    { pos := len(a.code); a.cbnzAt(rt, 0); return pos }
func (a *a8) phCbz(rt int) int     { pos := len(a.code); a.cbzAt(rt, 0); return pos }
func (a *a8) phCbnzW(rt int) int   { pos := len(a.code); a.cbnzWAt(rt, 0); return pos }

// ---------- 系统调用 ----------

// sys 发系统调用: Linux 用 x8, Darwin 用 x16 传号。
func (a *a8) sys(linux, darwin int) {
	if a.isDarwin() {
		a.movImm(16, uint64(darwin))
	} else {
		a.movImm(8, uint64(linux))
	}
	a.svc()
}

// bIfErr 在系统调用之后发一条"出错则跳转"的分支, 返回待补位置。
//
// Linux 的 errno 用返回值表示 (x0 = -errno), Darwin 置进位标志且必须立刻判定
// —— 所以 Darwin 上这条分支前面不能插入任何会改标志的指令。
func (a *a8) bIfErr() int {
	if a.isDarwin() {
		return a.phBC(condHS)
	}
	a.movImm(9, 0x1000)
	a.addsXZR(0, 9)
	return a.phBC(condHS)
}

// ---------- 输出 ----------

// writeStdout: 把 x1 指向的 buf、x2 长度写到标准输出。
func (a *a8) writeStdout() {
	a.movz(0, 1, 0)
	a.sys(64, 4)
}

// outStr 打印静态字符串 (VM 地址 addr, 长度 n)。
func (a *a8) outStr(addr int64, n int) error {
	a.addImmBig(1, a64VMB, addr)
	a.movImm(2, uint64(n))
	a.writeStdout()
	return nil
}

// ---------- 序言 ----------

// prologue 分配 VM 内存并摆放初始状态。
func (a *a8) prologue(writes []ir.DataWrite) error {
	a.movz(0, 0, 0) // addr = NULL
	a.movImm(1, a64MemSize)
	a.movz(2, 3, 0) // PROT_READ|PROT_WRITE
	if a.isDarwin() {
		a.movImm(3, 0x1002) // MAP_PRIVATE|MAP_ANON
	} else {
		a.movImm(3, 0x22) // MAP_PRIVATE|MAP_ANONYMOUS
	}
	a.movImm(4, ^uint64(0)) // fd = -1
	a.movz(5, 0, 0)         // offset = 0
	a.sys(222, 197)
	errPos := a.bIfErr()

	a.movReg(a64VMB, 0)
	// x19 = 寄存器组基址 (VM 高端保留区), 不放在 VM 偏移 0 —— 那里是静态数据。
	a.addImmBig(a64Regs, a64VMB, int64(a64RegBank))

	// 静态数据: 映像 (dataVAddr + addr) -> VM (x20 + addr)
	for _, wt := range writes {
		a.movImm(9, a.dataVAddr+uint64(wt.Addr))
		a.addImmBig(10, a64VMB, int64(wt.Addr))
		a.movImm(11, uint64(len(wt.Data)))
		a.movz(12, 0, 0)
		loop := len(a.code)
		a.cmpReg(12, 11)
		done := a.phBC(condHS)
		a.ldrb(13, 9, 0)
		a.strb(13, 10, 0)
		a.addImm(9, 9, 1)
		a.addImm(10, 10, 1)
		a.addImm(12, 12, 1)
		back := len(a.code)
		a.bAt(loop - back)
		a.patch(done, len(a.code), a64FixBCond)
	}

	a.movImm(9, a64HeapStart)
	a.str(9, a64Regs, scrHeapPtr)
	a.movImm(9, a64MemSize-8)
	a.str(9, a64Regs, 32*8) // R32 = 栈顶 - 8
	a.movImm(9, 0x2545F4914F6CDD1D)
	a.str(9, a64Regs, scrRand)

	// mmap 失败 -> 退出码 1。这段必须跳过, 否则正常路径会直接掉进错误处理。
	skipErr := a.phB()
	a.patch(errPos, len(a.code), a64FixBCond)
	a.movz(0, 1, 0)
	a.exitWithX0()
	a.patch(skipErr, len(a.code), a64FixB)
	return nil
}

// exitWithX0 用 x0 作退出码终止进程 (不返回)。
func (a *a8) exitWithX0() {
	a.sys(93, 1)
	a.bAt(-4) // 万一返回就原地跳到自身
}

func (a *a8) exitWithReg(r int) error {
	a.loadReg(r)
	a.exitWithX0()
	return nil
}

// ---------- 运行时片段 ----------

// emitStrlen: x0 = VM 地址 -> x0 = 长度
func (a *a8) emitStrlen() {
	a.addReg(1, a64VMB, 0)
	a.movz(2, 0, 0)
	loop := len(a.code)
	a.ldrb(9, 1, 0)
	end := len(a.code)
	a.cbzWAt(9, 0)
	a.addImm(1, 1, 1)
	a.addImm(2, 2, 1)
	back := len(a.code)
	a.bAt(loop - back)
	a.patch(end, len(a.code), a64FixCB)
	a.movReg(0, 2)
}

// emitMalloc: R0 = 分配 R0 字节 (bump), 返回 VM 地址。
func (a *a8) emitMalloc() {
	a.loadReg(0)
	a.addImm(0, 0, 7)
	a.movz(12, 3, 0) // 移位量 3
	a.lsrv(0, 0, 12)
	a.lslv(0, 0, 12) // R0 字节数 -> 8 字节对齐
	a.ldr(1, a64Regs, scrHeapPtr)
	a.addReg(2, 1, 0)
	a.str(2, a64Regs, scrHeapPtr)
	a.str(1, a64Regs, 0) // R0 = 旧堆指针
}

// emitCopyLoop: 从 x1 (源宿址) 复制到 x2 (目标宿址), 直到 NUL 也被复制。
// 结束时 x2 指向 NUL 之后。
func (a *a8) emitCopyZ() {
	loop := len(a.code)
	a.ldrb(9, 1, 0)
	a.strb(9, 2, 0)
	// 先自增再判零: 循环结束时 x2 必须停在 NUL **之后**。调用方 (字符串拼接)
	// 靠 subImm(2,2,1) 回退一格覆盖掉 NUL, 停在 NUL 上就会把前一段的末字符写掉。
	a.addImm(1, 1, 1)
	a.addImm(2, 2, 1)
	a.cbnzAt(9, loop-len(a.code))
}

// emitCopyN: 复制 x3 字节 (x1 -> x2), 不复制结尾 NUL。
func (a *a8) emitCopyN() {
	loop := len(a.code)
	a.cbzAt(3, 0)
	skip := len(a.code) - 4 // patch 的 pos 是分支指令自身的位置
	a.ldrb(9, 1, 0)
	a.strb(9, 2, 0)
	a.addImm(1, 1, 1)
	a.addImm(2, 2, 1)
	a.subImm(3, 3, 1)
	back := len(a.code)
	a.bAt(loop - back)
	a.patch(skip, len(a.code), a64FixCB)
}

// emitITOA: R0 的十进制表示写到 a64Itoa, 结果指针回填 R0。
func (a *a8) emitITOA() {
	a.loadRegTo(a64T1, 0)
	a.addImmBig(2, a64VMB, a64Itoa+0x40) // 写指针 = 缓冲区尾
	a.movReg(3, a64T1)
	a.movz(a64T2, 0, 0) // 负号标记
	a.cmpImm(3, 0)
	pos := a.phBC(condGE)
	a.movz(a64T2, 1, 0)
	a.subReg(3, 31, 3) // neg
	a.patch(pos, len(a.code), a64FixBCond)

	loop := len(a.code)
	a.movz(10, 10, 0)
	a.udiv(11, 3, 10)
	a.msub(12, 11, 10, 3) // 余数
	a.movReg(3, 11)
	a.subImm(2, 2, 1)
	a.addImm(12, 12, '0')
	a.strb(12, 2, 0)
	a.cbnzAt(3, loop-len(a.code))

	skipMinus := a.phCbz(a64T2) // 非负数直接跳过负号
	a.subImm(2, 2, 1)
	a.movz(12, '-', 0)
	a.strb(12, 2, 0)
	a.patch(skipMinus, len(a.code), a64FixCB)
	a.subReg(0, 2, a64VMB)
	a.storeReg(0)
}

// emitFTOA: R0 (double 位模式) -> R0 = 十进制字符串的 VM 地址。
//
// 整数部分按 ITOA 的办法取, 小数部分逐位乘 10 取整 (与 x86-64 后端同一策略);
// 末尾的 0 与孤立的 '.' 会去掉, NaN/±Inf 单独处理。
func (a *a8) emitFTOA() {
	buf := int64(a64Ftoa)

	a.loadRegTo(a64T1, 0) // 位模式
	a.movImm(9, 0x7FFFFFFFFFFFFFFF)
	a.andReg(9, a64T1, 9) // |bits|
	a.movImm(10, 0x7FF0000000000000)

	a.cmpReg(9, 10)
	notSpecial := a.phBC(condLS) // <= Inf: 有限数或 Inf
	// NaN
	a.vmCon(int32(buf))
	a.movz(11, 'n', 0)
	a.strb(11, 9, 0)
	a.movz(11, 'a', 0)
	a.strb(11, 9, 1)
	a.movz(11, 'n', 0)
	a.strb(11, 9, 2)
	a.strb(31, 9, 3)
	a.movImm(0, uint64(buf))
	a.storeReg(0)
	toEndNan := a.phB()

	a.patch(notSpecial, len(a.code), a64FixBCond)
	normal := a.phBC(condNE) // != Inf => 有限数
	// ±Inf
	a.vmCon(int32(buf))
	a.movReg(2, 9)
	a.cmpImm(a64T1, 0)
	infSign := a.phBC(condGE)
	a.movz(11, '-', 0)
	a.strb(11, 2, 0)
	a.addImm(2, 2, 1)
	a.patch(infSign, len(a.code), a64FixBCond)
	a.movz(11, 'i', 0)
	a.strb(11, 2, 0)
	a.movz(11, 'n', 0)
	a.strb(11, 2, 1)
	a.movz(11, 'f', 0)
	a.strb(11, 2, 2)
	a.strb(31, 2, 3)
	a.movImm(0, uint64(buf))
	a.storeReg(0)
	toEndInf := a.phB()

	// ---- 有限数 ----
	a.patch(normal, len(a.code), a64FixBCond)
	a.addImmBig(2, a64VMB, buf) // x2 = 写指针
	a.fmovFromX(0, a64T1)
	a.cmpImm(a64T1, 0)
	finSign := a.phBC(condGE)
	a.movz(11, '-', 0)
	a.strb(11, 2, 0)
	a.addImm(2, 2, 1)
	a.fabs(0, 0)
	a.patch(finSign, len(a.code), a64FixBCond)

	a.fcvtzs(3, 0)  // x3 = 整数部分
	a.scvtf(1, 3)   // 回成 double
	a.fsub(0, 0, 1) // x0 = 小数部分

	// 小数部分一次性量化成 15 位整数: n = round(frac * 1e15)。
	// 逐位"乘 10 取整数位"会把 3.4 打成 3.399999...: 3.4 的 double 是
	// 3.399999999999999911, 减掉整数部分后乘 10 得到 3.9999999999999991,
	// 截断就丢了那一位。先整体四舍五入再取位, 结果与引擎的
	// strconv.FormatFloat(f, 'g', -1, 64) 在常见值上一致。
	a.movImm(10, 0x430C6BF526340000) // 1e15 的 double 位模式
	a.fmovFromX(10, 10)
	a.fmul(0, 0, 10)
	a.frinta(0, 0) // 四舍五入 (frac >= 0, 舍入方向无歧义)
	a.fcvtzs(11, 0)
	a.movImm(10, 1000000000000000)
	a.cmpReg(11, 10)
	noCarry := a.phBC(condNE)
	a.addImm(3, 3, 1) // 小数进到 1.0: 整数位 +1, 小数位清零
	a.movz(11, 0, 0)
	a.patch(noCarry, len(a.code), a64FixBCond)
	// n 存进 a64T4: 下面整数部分的除法循环和 '.' 都会用掉 x11。
	a.movReg(a64T4, 11)

	// 整数部分反着写进 a64Itoa+0x30, 再顺序抄到写指针
	a.addImmBig(4, a64VMB, a64Itoa+0x30)
	a.movReg(5, 3)
	iloop := len(a.code)
	a.movz(10, 10, 0)
	a.udiv(11, 5, 10)
	a.msub(12, 11, 10, 5)
	a.movReg(5, 11)
	a.subImm(4, 4, 1)
	a.addImm(12, 12, '0')
	a.strb(12, 4, 0)
	a.cbnzAt(5, iloop-len(a.code))

	a.addImmBig(6, a64VMB, a64Itoa+0x30)
	copyLoop := len(a.code)
	a.cmpReg(4, 6)
	copyDone := a.phBC(condHS)
	a.ldrb(12, 4, 0)
	a.strb(12, 2, 0)
	a.addImm(4, 4, 1)
	a.addImm(2, 2, 1)
	back := len(a.code)
	a.bAt(copyLoop - back)
	a.patch(copyDone, len(a.code), a64FixBCond)

	a.movz(11, '.', 0)
	a.strb(11, 2, 0)
	a.addImm(2, 2, 1)

	// 把 n 反着写成定长 15 位 (不足的高位补 '0'), 再顺序抄到写指针。
	a.addImmBig(4, a64VMB, a64Itoa+0x40)
	a.movReg(5, a64T4)
	a.movz(13, 15, 0)
	a.movz(10, 10, 0)
	floop := len(a.code)
	a.udiv(11, 5, 10)
	a.msub(12, 11, 10, 5)
	a.movReg(5, 11)
	a.subImm(4, 4, 1)
	a.addImm(12, 12, '0')
	a.strb(12, 4, 0)
	a.subImm(13, 13, 1)
	a.cbnzAt(13, floop-len(a.code))

	a.addImmBig(6, a64VMB, a64Itoa+0x40)
	fcopy := len(a.code)
	a.cmpReg(4, 6)
	fdone := a.phBC(condHS)
	a.ldrb(12, 4, 0)
	a.strb(12, 2, 0)
	a.addImm(4, 4, 1)
	a.addImm(2, 2, 1)
	backF := len(a.code)
	a.bAt(fcopy - backF)
	a.patch(fdone, len(a.code), a64FixBCond)

	// 去尾零: 退到最后一个非 '0' 字符
	trim := len(a.code)
	a.subImm(2, 2, 1)
	a.ldrb(12, 2, 0)
	a.movz(11, '0', 0)
	a.cmpReg(12, 11)
	tc := len(a.code)
	a.bcondAt(condEQ, trim-tc)

	a.movz(11, '.', 0)
	a.cmpReg(12, 11)
	keepAll := a.phBC(condNE)
	// 结尾正是 '.': 用它自己的位置当结尾
	a.strb(31, 2, 0)
	a.movImm(0, uint64(buf))
	a.storeReg(0)
	toEndFin := a.phB()

	a.patch(keepAll, len(a.code), a64FixBCond)
	a.addImm(2, 2, 1)
	a.strb(31, 2, 0)
	a.movImm(0, uint64(buf))
	a.storeReg(0)

	end := len(a.code)
	a.patch(toEndNan, end, a64FixB)
	a.patch(toEndInf, end, a64FixB)
	a.patch(toEndFin, end, a64FixB)
}

// emitIntAbs: R0 = |R0| (无分支: 用符号掩码)
func (a *a8) emitIntAbs() {
	a.loadReg(0)
	a.movImm(9, 63)
	a.asrv(1, 0, 9)
	a.eorReg(0, 0, 1)
	a.subReg(0, 0, 1)
	a.storeReg(0)
}

// ErrUnsupportedARM 表示 IR 里出现 ARM64 后端尚未实现的指令。
type ErrUnsupportedARM struct{ Op string }

func (e ErrUnsupportedARM) Error() string { return "ARM64 后端暂不支持指令: " + e.Op }

// pushLR / popLR 保护返回地址 (函数体里可能还有嵌套 CALL)。
func (a *a8) pushLR() { a.w(0xF81F0FFE) } // str x30, [sp, #-16]!
func (a *a8) popLR()  { a.w(0xF84107FE) } // ldr x30, [sp], #16

// EncodeAArch64 把 IR 编译为 Linux AArch64 机器码 (入口为偏移 0)。
func EncodeAArch64(p *ir.Program) ([]byte, error) {
	return encodeA64(p, &a8{tgt: a64Linux, dataVAddr: uint64(dataBase)})
}

// EncodeAArch64Darwin 编译为 macOS AArch64 机器码。
// dataVAddr 是 Mach-O 里静态数据的虚地址 (由 __DATA 段的 vmaddr 决定)。
func EncodeAArch64Darwin(p *ir.Program, dataVAddr uint64) ([]byte, error) {
	return encodeA64(p, &a8{tgt: a64Darwin, dataVAddr: dataVAddr})
}

// encodeA64 是 ARM64 后端的入口: 序言 + 逐条 IR + 分支回填。
func encodeA64(p *ir.Program, a *a8) ([]byte, error) {
	if err := a.prologue(p.DataWrites); err != nil {
		return nil, err
	}
	type fix struct {
		pos   int
		label string
		kind  a64FixKind
	}
	var fixups []fix
	offs := make([]int, len(p.Instructions))

	// 被 CALL 的目标需要保存返回地址: 函数体内部还可能再 CALL 别人,
	// x30 是唯一的链接寄存器, 不保存就会被下一次 bl 覆盖。
	funcEntry := map[int]bool{}
	for _, ins := range p.Instructions {
		if ins.Op == "CALL" && len(ins.Args) == 1 && ins.Args[0].Kind == "label" {
			if idx, ok := p.Labels[ins.Args[0].Name]; ok {
				funcEntry[idx] = true
			}
		}
	}

	sawHalt := false
	for i, ins := range p.Instructions {
		offs[i] = len(a.code)
		if funcEntry[i] {
			a.pushLR()
		}
		switch ins.Op {
		case "MOV":
			if len(ins.Args) == 2 && ins.Args[0].Kind == "reg" && ins.Args[1].Kind == "reg" {
				a.loadReg(int(ins.Args[1].V))
				a.storeReg(int(ins.Args[0].V))
				continue
			}
			r, v, err := regImm(ins)
			if err != nil {
				return nil, err
			}
			a.movImm(0, uint64(v))
			a.storeReg(r)
		case "INC":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("INC: 需要 reg 操作数")
			}
			a.loadReg(int(ins.Args[0].V))
			a.addImm(0, 0, 1)
			a.storeReg(int(ins.Args[0].V))
		case "DEC":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("DEC: 需要 reg 操作数")
			}
			a.loadReg(int(ins.Args[0].V))
			a.subImm(0, 0, 1)
			a.storeReg(int(ins.Args[0].V))
		case "CMP":
			if len(ins.Args) == 2 && ins.Args[0].Kind == "reg" && ins.Args[1].Kind == "reg" {
				a.loadReg(int(ins.Args[0].V))
				a.loadRegTo(1, int(ins.Args[1].V))
				a.cmpReg(0, 1)
			} else {
				r, v, err := regImm(ins)
				if err != nil {
					return nil, err
				}
				a.loadReg(r)
				a.movImm(1, uint64(v))
				a.cmpReg(0, 1)
			}
			a.cset(9, condEQ)
			a.strb(9, a64Regs, scrFlags)
			a.cset(9, condLT)
			a.strb(9, a64Regs, scrFlags+1)
		case "JMP":
			name, err := labelOf(ins)
			if err != nil {
				return nil, err
			}
			fixups = append(fixups, fix{pos: a.phB(), label: name, kind: a64FixB})
		case "B":
			if len(ins.Args) != 2 || ins.Args[0].Kind != "label" || ins.Args[1].Kind != "cond" {
				return nil, fmt.Errorf("B: 需要 label, cond")
			}
			name := ins.Args[0].Name
			a.ldrb(9, a64Regs, scrFlags)    // zf
			a.ldrb(10, a64Regs, scrFlags+1) // lf
			switch ins.Args[1].Name {
			case "EQ":
				fixups = append(fixups, fix{pos: a.phCbnzW(9), label: name, kind: a64FixCB})
			case "NE":
				fixups = append(fixups, fix{pos: a.phCbzW(9), label: name, kind: a64FixCB})
			case "LT":
				fixups = append(fixups, fix{pos: a.phCbnzW(10), label: name, kind: a64FixCB})
			case "GE":
				fixups = append(fixups, fix{pos: a.phCbzW(10), label: name, kind: a64FixCB})
			case "LE":
				a.orrW(9, 9, 10)
				fixups = append(fixups, fix{pos: a.phCbnzW(9), label: name, kind: a64FixCB})
			case "GT":
				a.orrW(9, 9, 10)
				fixups = append(fixups, fix{pos: a.phCbzW(9), label: name, kind: a64FixCB})
			default:
				return nil, fmt.Errorf("B: 未知条件 %q", ins.Args[1].Name)
			}
		case "JZ", "JE":
			name, err := labelOf(ins)
			if err != nil {
				return nil, err
			}
			a.ldrb(9, a64Regs, scrFlags)
			fixups = append(fixups, fix{pos: a.phCbnzW(9), label: name, kind: a64FixCB})
		case "JNZ":
			name, err := labelOf(ins)
			if err != nil {
				return nil, err
			}
			a.ldrb(9, a64Regs, scrFlags)
			fixups = append(fixups, fix{pos: a.phCbzW(9), label: name, kind: a64FixCB})
		case "OUT":
			if len(ins.Args) == 1 && ins.Args[0].Kind == "imm" && ins.Args[0].V == 10 {
				a.movz(9, '\n', 0)
				a.strb(9, a64Regs, scrNL)
				a.addImmBig(1, a64VMB, a64NL)
				a.movz(2, 1, 0)
				a.writeStdout()
				continue
			}
			if len(ins.Args) != 1 || ins.Args[0].Kind != "str" {
				return nil, ErrUnsupportedARM{Op: "OUT(非字符串)"}
			}
			addr := ins.Args[0].V
			if err := a.outStr(addr, dataLen(p, addr)); err != nil {
				return nil, err
			}
		case "ADD", "SUB", "AND", "OR", "XOR":
			if len(ins.Args) == 2 && ins.Args[0].Kind == "reg" && ins.Args[1].Kind == "reg" {
				rd, rs := int(ins.Args[0].V), int(ins.Args[1].V)
				a.loadReg(rd)
				a.loadRegTo(1, rs)
				switch ins.Op {
				case "ADD":
					a.addReg(0, 0, 1)
				case "SUB":
					a.subReg(0, 0, 1)
				case "AND":
					a.andReg(0, 0, 1)
				case "OR":
					a.orrReg(0, 0, 1)
				default:
					a.eorReg(0, 0, 1)
				}
				a.storeReg(rd)
				continue
			}
			r, v, err := regImm(ins)
			if err != nil {
				return nil, err
			}
			a.loadReg(r)
			a.movImm(1, uint64(v))
			if ins.Op == "ADD" {
				a.addReg(0, 0, 1)
			} else {
				a.subReg(0, 0, 1)
			}
			a.storeReg(r)
		case "ADDI":
			if len(ins.Args) != 3 || ins.Args[0].Kind != "reg" || ins.Args[1].Kind != "reg" || ins.Args[2].Kind != "imm" {
				return nil, fmt.Errorf("ADDI: 需要 reg, reg, imm")
			}
			a.loadRegTo(0, int(ins.Args[1].V))
			a.movImm(1, uint64(ins.Args[2].V))
			a.addReg(0, 0, 1)
			a.storeReg(int(ins.Args[0].V))
		case "SHL", "SHR", "ASR":
			// 两种形式：
			//   2 操作数：Rd <- Rd op src   （src = reg|imm）
			//   3 操作数：Rd <- Rn op src   （codegen 对 >> 会发 ASR rd, rn, rm）
			var rdReg int
			switch len(ins.Args) {
			case 2:
				if ins.Args[0].Kind != "reg" {
					return nil, fmt.Errorf("%s: 需要 reg, reg|imm", ins.Op)
				}
				rdReg = int(ins.Args[0].V)
				a.loadReg(rdReg)
			case 3:
				if ins.Args[0].Kind != "reg" || ins.Args[1].Kind != "reg" {
					return nil, fmt.Errorf("%s: 需要 reg, reg, reg|imm", ins.Op)
				}
				rdReg = int(ins.Args[0].V)
				a.loadRegTo(0, int(ins.Args[1].V))
			default:
				return nil, fmt.Errorf("%s: 需要 reg, reg|imm  或  reg, reg, reg|imm", ins.Op)
			}
			shiftArg := ins.Args[len(ins.Args)-1]
			if shiftArg.Kind == "imm" {
				a.movImm(1, uint64(shiftArg.V))
			} else {
				a.loadRegTo(1, int(shiftArg.V))
			}
			switch ins.Op {
			case "SHL":
				a.lslv(0, 0, 1)
			case "SHR":
				a.lsrv(0, 0, 1)
			default:
				a.asrv(0, 0, 1)
			}
			a.storeReg(rdReg)
		case "ANDI", "ORI", "XORI":
			if len(ins.Args) != 3 || ins.Args[0].Kind != "reg" || ins.Args[1].Kind != "reg" || ins.Args[2].Kind != "imm" {
				return nil, fmt.Errorf("%s: 需要 reg, reg, imm", ins.Op)
			}
			a.loadRegTo(0, int(ins.Args[1].V))
			a.movImm(1, uint64(ins.Args[2].V))
			switch ins.Op {
			case "ANDI":
				a.andReg(0, 0, 1)
			case "ORI":
				a.orrReg(0, 0, 1)
			default:
				a.eorReg(0, 0, 1)
			}
			a.storeReg(int(ins.Args[0].V))
		case "MVN":
			if len(ins.Args) != 2 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("MVN: 需要 reg, reg|imm")
			}
			if ins.Args[1].Kind == "reg" {
				a.loadRegTo(1, int(ins.Args[1].V))
			} else if ins.Args[1].Kind == "imm" {
				a.movImm(1, uint64(ins.Args[1].V))
			} else {
				return nil, fmt.Errorf("MVN: 第二个操作数需要 reg 或 imm")
			}
			a.mvn(0, 1)
			a.storeReg(int(ins.Args[0].V))
		case "LB", "LH", "LW":
			if len(ins.Args) != 2 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("%s: 需要 reg, mem|reg", ins.Op)
			}
			src := ins.Args[1]
			switch src.Kind {
			case "mem":
				a.vmAddr(int(src.V), int32(src.Off))
			case "reg":
				a.loadRegTo(9, int(src.V))
				a.addReg(9, a64VMB, 9)
			default:
				return nil, fmt.Errorf("%s: 第二个操作数需要 mem 或 reg", ins.Op)
			}
			switch ins.Op {
			case "LB":
				a.ldrsb(0, 9, 0)
			case "LH":
				a.ldrsh(0, 9, 0)
			default:
				a.ldrsw(0, 9, 0)
			}
			a.storeReg(int(ins.Args[0].V))
		case "LD", "SD":
			if len(ins.Args) != 2 || ins.Args[1].Kind != "mem" {
				return nil, fmt.Errorf("%s: 需要 reg, mem", ins.Op)
			}
			base := int(ins.Args[1].V)
			off := int32(ins.Args[1].Off)
			if ins.Op == "LD" {
				a.vmAddr(base, off)
				a.ldr(0, 9, 0)
				a.storeReg(int(ins.Args[0].V))
			} else {
				a.vmAddr(base, off)
				a.loadRegTo(1, int(ins.Args[0].V))
				a.str(1, 9, 0)
			}
		case "MUL":
			if len(ins.Args) != 2 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("MUL: 需要 reg, reg|imm")
			}
			a.loadReg(int(ins.Args[0].V))
			if ins.Args[1].Kind == "reg" {
				a.loadRegTo(1, int(ins.Args[1].V))
			} else if ins.Args[1].Kind == "imm" {
				a.movImm(1, uint64(ins.Args[1].V))
			} else {
				return nil, fmt.Errorf("MUL: 操作数不支持")
			}
			a.mulReg(0, 0, 1)
			a.storeReg(int(ins.Args[0].V))
		case "DIV", "MOD", "REM":
			if len(ins.Args) != 2 || ins.Args[0].Kind != "reg" || ins.Args[1].Kind != "reg" {
				return nil, fmt.Errorf("%s: 需要 reg, reg", ins.Op)
			}
			rd, rs := int(ins.Args[0].V), int(ins.Args[1].V)
			a.loadReg(rd)
			a.loadRegTo(1, rs)
			a.sdiv(2, 0, 1)
			if ins.Op == "DIV" {
				a.storeRegFrom(2, rd)
			} else {
				a.msub(0, 2, 1, 0) // a - (a/b)*b
				a.storeReg(rd)
			}
		case "CALL":
			name, err := labelOf(ins)
			if err != nil {
				return nil, err
			}
			// VM 帧指针下移 8 (与 x86-64 后端一致)
			a.ldr(9, a64Regs, 32*8)
			a.subImm(9, 9, 8)
			a.str(9, a64Regs, 32*8)
			fixups = append(fixups, fix{pos: a.phBL(), label: name, kind: a64FixB})
		case "RET":
			a.ldr(9, a64Regs, 32*8)
			a.addImm(9, 9, 8)
			a.str(9, a64Regs, 32*8)
			a.popLR()
			a.ret()
		case "PUSH":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("PUSH: 需要 reg 操作数")
			}
			a.ldr(9, a64Regs, 32*8)
			a.subImm(9, 9, 8)
			a.str(9, a64Regs, 32*8)
			a.loadRegTo(10, int(ins.Args[0].V))
			a.addReg(11, a64VMB, 9)
			a.str(10, 11, 0)
		case "POP":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "reg" {
				return nil, fmt.Errorf("POP: 需要 reg 操作数")
			}
			a.ldr(9, a64Regs, 32*8)
			a.addReg(11, a64VMB, 9)
			a.ldr(10, 11, 0)
			a.storeRegFrom(10, int(ins.Args[0].V))
			a.addImm(9, 9, 8)
			a.str(9, a64Regs, 32*8)
		case "SYS":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "imm" {
				return nil, fmt.Errorf("SYS: 需要立即数功能号")
			}
			if err := a.sysCall(ins.Args[0].V); err != nil {
				return nil, err
			}
		case "HALT":
			sawHalt = true
			if err := a.exitWithReg(0); err != nil {
				return nil, err
			}
		default:
			return nil, ErrUnsupportedARM{Op: ins.Op}
		}
	}
	if !sawHalt {
		return nil, fmt.Errorf("程序缺少 HALT")
	}

	// 回填分支目标
	for _, f := range fixups {
		target, ok := p.Labels[f.label]
		if !ok {
			return nil, fmt.Errorf("未定义的标签: %s", f.label)
		}
		a.patch(f.pos, offs[target], f.kind)
	}
	return a.code, nil
}

// cbWPh 生成 cbz(32 位) 占位分支并返回位置。
func (a *a8) phCbzW(rt int) int {
	pos := len(a.code)
	a.cbzWAt(rt, 0)
	return pos
}

// phBL 生成 bl 占位并返回位置。
func (a *a8) phBL() int {
	pos := len(a.code)
	a.blAt(0)
	return pos
}
