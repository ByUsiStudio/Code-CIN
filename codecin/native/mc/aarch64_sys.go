package mc

import "fmt"

// aarch64_sys.go: ARM64 后端的内核功能号实现 (不依赖操作系统的部分)。
//
// 每个功能号都直接从 VM 内存取参数、把结果写回 R0, 与 x86-64 后端语义一致;
// 需要落到操作系统的 (文件/目录/时间) 在 aarch64_io.go。

// 寄存器偏移形式的内存访问 (Rm 参与寻址), option=011 (LSL)
func (a *a8) ldrbRO(rt, rn, rm int) { a.w(0x38606800 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rt)) }
func (a *a8) strbRO(rt, rn, rm int) { a.w(0x38206800 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rt)) }
func (a *a8) ldrRO(rt, rn, rm int)  { a.w(0xF8606800 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rt)) }
func (a *a8) strRO(rt, rn, rm int)  { a.w(0xF8206800 | uint32(rm)<<16 | uint32(rn)<<5 | uint32(rt)) }

// putBytes 把字面量写进 VM 缓冲 (x<base> 是宿址), 返回写指针前进后的偏移。
func (a *a8) putBytes(base int, s string) {
	for i := 0; i < len(s); i++ {
		a.movz(11, uint16(s[i]), 0)
		a.strb(11, base, int32(i))
	}
}

// emitCString: malloc(len+1), 写字面量, R0 = VM 地址。
func (a *a8) emitCString(s string) {
	a.movImm(0, uint64(len(s)+1))
	a.emitMalloc()
	a.loadRegTo(a64T1, 0)
	a.addReg(9, a64VMB, a64T1)
	a.putBytes(9, s)
	a.movz(10, 0, 0)
	a.strb(10, 9, int32(len(s)))
	a.str(a64T1, a64Regs, 0)
}

// sysCall 分发一个 SYS 功能号。
func (a *a8) sysCall(id int64) error {
	switch id {
	case 0: // SQRT
		a.ldrD(0, a64Regs, 0)
		a.fsqrt(0, 0)
		a.strD(0, a64Regs, 0)
	case 1: // POW
		a.emitPow()
	case 2: // ABS
		a.emitIntAbs()
	case 3, 4, 5: // SIN / COS / TAN
		a.emitSinCosTan(id)
	case 6, 7, 8, 9: // FADD / FSUB / FMUL / FDIV
		a.ldrD(0, a64Regs, 0)
		a.ldrD(1, a64Regs, 8)
		switch id {
		case 6:
			a.fadd(0, 0, 1)
		case 7:
			a.fsub(0, 0, 1)
		case 8:
			a.fmul(0, 0, 1)
		default:
			a.fdiv(0, 0, 1)
		}
		a.strD(0, a64Regs, 0)
	case 10: // FCMP -> -1 / 0 / 1 (NaN 记 0)
		a.ldrD(0, a64Regs, 0)
		a.ldrD(1, a64Regs, 8)
		// ARM 的 FCMP 在无序时也会让 LT 成立, 所以交换操作数各判一次 "大于"
		a.fcmp(0, 1)
		a.cset(9, condGT)
		a.fcmp(1, 0)
		a.cset(10, condGT)
		a.subReg(9, 9, 10)
		a.str(9, a64Regs, 0)
	case 11: // FTOI
		a.ldr(0, a64Regs, 0)
		a.fmovFromX(0, 0)
		a.fcvtzs(0, 0)
		a.storeReg(0)
	case 12: // ITOF
		a.ldr(0, a64Regs, 0)
		a.scvtf(0, 0)
		a.fmovToX(0, 0)
		a.storeReg(0)
	case 13: // RAND: xorshift64
		a.ldr(1, a64Regs, scrRand)
		a.movImm(10, 13)
		a.lslv(2, 1, 10)
		a.eorReg(1, 1, 2)
		a.movImm(10, 7)
		a.lsrv(2, 1, 10)
		a.eorReg(1, 1, 2)
		a.movImm(10, 17)
		a.lslv(2, 1, 10)
		a.eorReg(1, 1, 2)
		a.str(1, a64Regs, scrRand)
		a.movImm(10, 0x7FFFFFFF)
		a.andReg(1, 1, 10)
		a.str(1, a64Regs, 0)
	case 14: // SRAND
		a.ldr(1, a64Regs, 0)
		a.str(1, a64Regs, scrRand)
	case 15: // TIME: unix 秒
		a.vmCon(a64Tmp)
		if a.isDarwin() {
			a.movz(1, 0, 0) // tz = NULL
			a.sys(0, 116)
		} else {
			a.movReg(1, 9) // ts
			a.movz(0, 0, 0)
			a.sys(113, 0)
		}
		a.ldr(0, a64Regs, scrTmp)
		a.storeReg(0)
	case 16: // STRLEN
		a.loadReg(0)
		a.emitStrlen()
		a.storeReg(0)
	case 17: // STRCMP
		a.loadRegTo(a64T1, 0)
		a.loadRegTo(a64T2, 1)
		a.addReg(1, a64VMB, a64T1)
		a.addReg(2, a64VMB, a64T2)
		loop := len(a.code)
		a.ldrb(9, 1, 0)
		a.ldrb(10, 2, 0)
		a.cmpReg(9, 10)
		nePos := a.phBC(condNE)
		a.cbzWAt(9, 0)
		eqPos := len(a.code) - 4
		a.addImm(1, 1, 1)
		a.addImm(2, 2, 1)
		back := len(a.code)
		a.bAt(loop - back)
		// 不等: 返回字节差
		a.patch(nePos, len(a.code), a64FixBCond)
		a.subReg(9, 9, 10)
		a.str(9, a64Regs, 0)
		skip := a.phB()
		// 相等
		a.patch(eqPos, len(a.code), a64FixCB)
		a.movz(9, 0, 0)
		a.str(9, a64Regs, 0)
		a.patch(skip, len(a.code), a64FixB)
	case 20: // MALLOC
		a.emitMalloc()
	case 22: // ITOA
		a.emitITOA()
	case 23: // FTOA
		a.emitFTOA()
	case 24: // PRINT_STR
		a.loadRegTo(1, 0)
		a.addReg(1, a64VMB, 1)
		a.movz(2, 0, 0)
		loop := len(a.code)
		a.ldrbRO(9, 1, 2)
		done := a.phCbzW(9)
		a.addImm(2, 2, 1)
		back := len(a.code)
		a.bAt(loop - back)
		a.patch(done, len(a.code), a64FixCB)
		a.writeStdout()
	case 25: // STR_CONCAT
		a.loadRegTo(a64T1, 0)
		a.loadRegTo(a64T2, 1)
		a.movReg(0, a64T1)
		a.emitStrlen()
		a.movReg(a64T3, 0)
		a.movReg(0, a64T2)
		a.emitStrlen()
		a.addReg(0, a64T3, 0)
		a.addImm(0, 0, 1)
		a.emitMalloc()
		a.loadRegTo(a64T4, 0)
		a.addReg(1, a64VMB, a64T1)
		a.addReg(2, a64VMB, a64T4)
		a.emitCopyZ()
		a.subImm(2, 2, 1) // 覆盖掉第一段末尾的 NUL
		a.addReg(1, a64VMB, a64T2)
		a.emitCopyZ()
		a.str(a64T4, a64Regs, 0)
	case 26: // BOOL_STR
		a.loadRegTo(a64T1, 0)
		a.movImm(0, 6)
		a.emitMalloc()
		a.loadRegTo(a64T2, 0)
		a.addReg(9, a64VMB, a64T2)
		a.cbnzAt(a64T1, 0)
		falsePos := len(a.code) - 4
		a.putBytes(9, "false")
		a.movz(10, 0, 0)
		a.strb(10, 9, 5)
		skip := a.phB()
		a.patch(falsePos, len(a.code), a64FixCB)
		a.putBytes(9, "true")
		a.movz(10, 0, 0)
		a.strb(10, 9, 4)
		a.patch(skip, len(a.code), a64FixB)
		a.str(a64T2, a64Regs, 0)
	case 28: // SUBSTR(str, start, len)
		a.loadRegTo(a64T1, 0)
		a.loadRegTo(a64T2, 1)
		a.loadRegTo(a64T3, 2)
		a.movReg(0, a64T1)
		a.emitStrlen()
		a.movReg(a64T4, 0) // 总长
		// start < 0 -> 0
		a.cmpImm(a64T2, 0)
		s1 := a.phBC(condGE)
		a.movz(a64T2, 0, 0)
		a.patch(s1, len(a.code), a64FixBCond)
		// start > len -> len
		a.cmpReg(a64T2, a64T4)
		s2 := a.phBC(condLS)
		a.movReg(a64T2, a64T4)
		a.patch(s2, len(a.code), a64FixBCond)
		// len < 0 -> 0
		a.cmpImm(a64T3, 0)
		s3 := a.phBC(condGE)
		a.movz(a64T3, 0, 0)
		a.patch(s3, len(a.code), a64FixBCond)
		// start+len > total -> len = total-start
		a.addReg(9, a64T2, a64T3)
		a.cmpReg(9, a64T4)
		s4 := a.phBC(condLS)
		a.subReg(a64T3, a64T4, a64T2)
		a.patch(s4, len(a.code), a64FixBCond)
		a.movReg(0, a64T3)
		a.addImm(0, 0, 1)
		a.emitMalloc()
		a.loadRegTo(a64T5, 0)
		a.addReg(1, a64VMB, a64T1)
		a.addReg(1, 1, a64T2)
		a.addReg(2, a64VMB, a64T5)
		a.movReg(3, a64T3)
		a.emitCopyN()
		a.strb(31, 2, 0)
		a.str(a64T5, a64Regs, 0)
	case 29: // INDEXOF
		a.loadRegTo(a64T1, 0)
		a.loadRegTo(a64T2, 1)
		a.addReg(1, a64VMB, a64T1)
		a.addReg(2, a64VMB, a64T2)
		a.movz(3, 0, 0) // i
		outer := len(a.code)
		a.ldrbRO(9, 1, 3)
		nfPre := a.phCbzW(9)
		a.movz(4, 0, 0) // j
		inner := len(a.code)
		a.ldrbRO(10, 2, 4)
		hitPre := len(a.code)
		a.cbzWAt(10, 0)
		a.addReg(11, 3, 4)
		a.ldrbRO(12, 1, 11)
		a.cmpReg(12, 10)
		mism := a.phBC(condNE)
		a.addImm(4, 4, 1)
		backIn := len(a.code)
		a.bAt(inner - backIn)
		// 不匹配: 下一轮
		a.patch(mism, len(a.code), a64FixBCond)
		a.addImm(3, 3, 1)
		backOut := len(a.code)
		a.bAt(outer - backOut)
		// 命中
		a.patch(hitPre, len(a.code), a64FixCB)
		a.str(3, a64Regs, 0)
		skip := a.phB()
		// 未找到
		a.patch(nfPre, len(a.code), a64FixCB)
		a.movImm(0, ^uint64(0))
		a.storeReg(0)
		a.patch(skip, len(a.code), a64FixB)
	case 30, 31: // UPPER / LOWER
		upper := id == 30
		a.loadRegTo(a64T1, 0)
		a.movReg(0, a64T1)
		a.emitStrlen()
		a.movReg(a64T2, 0)
		a.movReg(0, a64T2)
		a.addImm(0, 0, 1)
		a.emitMalloc()
		a.loadRegTo(a64T3, 0)
		a.addReg(1, a64VMB, a64T1)
		a.addReg(2, a64VMB, a64T3)
		a.movReg(3, a64T2)
		loop := len(a.code)
		a.cbzAt(3, 0)
		loopEnd := len(a.code) - 4
		a.ldrb(9, 1, 0)
		lo, hi := uint16('a'), uint16('z')
		if !upper {
			lo, hi = 'A', 'Z'
		}
		a.movz(10, lo, 0)
		a.cmpReg(9, 10)
		skip1 := a.phBC(condLO)
		a.movz(10, hi+1, 0)
		a.cmpReg(9, 10)
		skip2 := a.phBC(condHS)
		if upper {
			a.subImm(9, 9, 32)
		} else {
			a.addImm(9, 9, 32)
		}
		conv := len(a.code)
		a.patch(skip1, conv, a64FixBCond)
		a.patch(skip2, conv, a64FixBCond)
		a.strb(9, 2, 0)
		a.addImm(1, 1, 1)
		a.addImm(2, 2, 1)
		a.subImm(3, 3, 1)
		back := len(a.code)
		a.bAt(loop - back)
		a.patch(loopEnd, len(a.code), a64FixCB)
		a.strb(31, 2, 0)
		a.str(a64T3, a64Regs, 0)
	case 32, 33, 34: // FLOOR / CEIL / ROUND: R0 = 取整后的 double 位模式
		//
		// ROUND 按 ISA 表的定义是 floor(x + 0.5), 不是"四舍六入五成双",
		// 所以先加 0.5 再走 FRINTM。用硬件取整指令而不是"截断 + 修正",
		// 顺带把超出 int64 范围的大数和 NaN / ±Inf 也交给硬件处理。
		a.ldrD(0, a64Regs, 0)
		switch id {
		case 33:
			a.frintp(0, 0)
		case 34:
			a.movImm(9, 0x3FE0000000000000) // 0.5 的 double 位模式
			a.fmovFromX(1, 9)
			a.fadd(0, 0, 1)
			a.frintm(0, 0)
		default:
			a.frintm(0, 0)
		}
		a.strD(0, a64Regs, 0)
	case 35: // ATOI
		a.loadRegTo(a64T1, 0)
		a.addReg(1, a64VMB, a64T1)
		a.movz(a64T2, 0, 0) // 负号标记
		a.ldrb(9, 1, 0)
		a.movz(10, '-', 0)
		a.cmpReg(9, 10)
		notNeg := a.phBC(condNE)
		a.movz(a64T2, 1, 0)
		a.addImm(1, 1, 1)
		a.patch(notNeg, len(a.code), a64FixBCond)
		a.movz(3, 0, 0)
		digits := len(a.code)
		a.ldrb(9, 1, 0)
		a.subImm(9, 9, '0')
		a.cmpImm(9, 9)
		digDone := a.phBC(condHI)
		a.movz(10, 10, 0)
		a.mulReg(3, 3, 10)
		a.addReg(3, 3, 9)
		a.addImm(1, 1, 1)
		back := len(a.code)
		a.bAt(digits - back)
		a.patch(digDone, len(a.code), a64FixBCond)
		a.cbzAt(a64T2, 0)
		noNeg := len(a.code) - 4
		a.subReg(3, 31, 3)
		a.patch(noNeg, len(a.code), a64FixCB)
		a.str(3, a64Regs, 0)
	case 36, 37, 38: // TRIM / LTRIM / RTRIM
		doLeft := id != 38
		doRight := id != 37
		a.loadRegTo(a64T1, 0)
		a.movReg(0, a64T1)
		a.emitStrlen()
		a.addReg(1, a64VMB, a64T1) // 起点
		a.addReg(2, 1, 0)          // 终点 = 起点 + len
		if doLeft {
			left := len(a.code)
			a.cmpReg(1, 2)
			leftDone := a.phBC(condHS)
			a.ldrb(9, 1, 0)
			a.movz(10, ' ', 0)
			a.cmpReg(9, 10)
			inc1 := a.phBC(condEQ)
			a.subImm(9, 9, 9)
			a.cmpImm(9, 4)
			inc2 := a.phBC(condLS)
			stop := a.phB()
			incAt := len(a.code)
			a.patch(inc1, incAt, a64FixBCond)
			a.patch(inc2, incAt, a64FixBCond)
			a.addImm(1, 1, 1)
			back := len(a.code)
			a.bAt(left - back)
			a.patch(leftDone, len(a.code), a64FixBCond)
			a.patch(stop, len(a.code), a64FixB)
		}
		if doRight {
			right := len(a.code)
			a.cmpReg(1, 2)
			rightDone := a.phBC(condHS)
			a.subImm(11, 2, 1)
			a.ldrb(9, 11, 0)
			a.movz(10, ' ', 0)
			a.cmpReg(9, 10)
			dec1 := a.phBC(condEQ)
			a.subImm(9, 9, 9)
			a.cmpImm(9, 4)
			dec2 := a.phBC(condLS)
			stop := a.phB()
			decAt := len(a.code)
			a.patch(dec1, decAt, a64FixBCond)
			a.patch(dec2, decAt, a64FixBCond)
			a.subImm(2, 2, 1)
			back := len(a.code)
			a.bAt(right - back)
			a.patch(rightDone, len(a.code), a64FixBCond)
			a.patch(stop, len(a.code), a64FixB)
		}
		// 结果串: malloc(len+1) + 拷贝。
		// 起点必须先进 a64T4: emitMalloc 会把 x1/x2 当临时寄存器用掉。
		a.subReg(3, 2, 1)
		a.movReg(a64T4, 1)
		a.movReg(0, 3)
		a.addImm(0, 0, 1)
		a.emitMalloc()
		a.loadRegTo(a64T3, 0)
		a.movReg(1, a64T4)
		a.addReg(2, a64VMB, a64T3)
		a.emitCopyN()
		a.strb(31, 2, 0)
		a.str(a64T3, a64Regs, 0)
	case 51, 52, 53, 54, 55, 56, 57, 58, 66, 84, 85, 86, 87, 88, 90:
		return a.sysFile(id)
	case 80, 81, 82, 83:
		return a.sysPath(id)
	case 63: // OSNAME
		if a.isDarwin() {
			a.emitCString("darwin")
		} else {
			a.emitCString("linux")
		}
	case 89: // TEMPDIR
		a.emitCString("/tmp")
	case 91: // TIMEMS
		a.vmCon(a64Tmp)
		if a.isDarwin() {
			a.movz(1, 0, 0)
			a.sys(0, 116)
		} else {
			a.movReg(1, 9)
			a.movz(0, 0, 0)
			a.sys(113, 0)
		}
		a.ldr(0, a64Regs, scrTmp)   // 秒
		a.ldr(1, a64Regs, scrTmp+8) // 纳秒 (Linux) / 微秒 (Darwin)
		a.movImm(10, 1000)
		a.mulReg(0, 0, 10) // 秒 * 1000
		if a.isDarwin() {
			a.movImm(10, 1000)
		} else {
			a.movImm(10, 1000000)
		}
		a.udiv(1, 1, 10)
		a.addReg(0, 0, 1)
		a.storeReg(0)
	case 92: // SLEEPMS
		a.loadRegTo(a64T1, 0) // 毫秒
		a.movImm(10, 1000)
		a.udiv(11, a64T1, 10)     // 秒
		a.msub(12, 11, 10, a64T1) // 毫秒 % 1000
		a.movImm(10, 1000000)
		a.mulReg(12, 12, 10) // 纳秒
		a.vmCon(a64Tmp)
		a.str(11, 9, 0)
		a.str(12, 9, 8)
		a.movReg(0, 9)
		a.movz(1, 0, 0)
		a.sys(101, 240) // nanosleep
	case 137: // ALLOCFRAME
		a.ldr(9, a64Regs, 32*8)
		a.ldr(10, a64Regs, 0)
		a.subReg(9, 9, 10)
		a.str(9, a64Regs, 32*8)
		a.str(9, a64Regs, 0)
	default:
		return ErrUnsupportedARM{Op: fmt.Sprintf("SYS %d", id)}
	}
	return nil
}
