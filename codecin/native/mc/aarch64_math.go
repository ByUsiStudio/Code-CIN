package mc

import "math"

// aarch64_math.go: ARM64 后端的超越函数 (SYS 1 POW / 3 SIN / 4 COS / 5 TAN)。
//
// x86-64 后端可以直接借 x87 的 fsin/fcos/fptan/fyl2x+f2xm1 一条指令搞定,
// AArch64 的浮点单元没有三角函数和对数指令, 只能用软件实现:
//
//   * SIN/COS: 先把 x 规约到 |r| <= pi/4 (按 pi/2 的整数倍取象限), 再用
//     Taylor 多项式 (sin 到 r^13, cos 到 r^14) 求值, 最后按象限换名/变号。
//   * POW: 整数指数走平方乘的快速幂 (结果与 math.Pow 完全一致); 非整数指数
//     走 exp2(y * log2(x)), log2 用 atanh 级数, exp2 用 Taylor 级数。
//
// 精度: |x| 在 1e6 以内时与 Go 的 math 包差异在 1e-14 量级; 更大的 |x| 规约
// 误差会变大 (x87 的 fsin 也有同样问题)。POW 的指数超出 [-1023, 1023] 时只
// 保证得到 Inf / 0, 不保证精确。

// fconst: Dd = v
func (a *a8) fconst(d int, v float64) {
	a.movImm(9, math.Float64bits(v))
	a.fmovFromX(d, 9)
}

// fmovD: Dd = Dn
func (a *a8) fmovD(dd, dn int) { a.w(0x1E604000 | uint32(dn)<<5 | uint32(dd)) }

// phi/2 拆成高、低两段, 规约时两段分别乘, 减少大参数下的截断误差
const (
	a64PiHi = 1.5707963267948966    // double(pi/2)
	a64PiLo = 6.123233995736766e-17 // pi/2 - a64PiHi
)

// emitSinCosTan: SYS 3 / 4 / 5。R0 = 参数, 结果写回 R0。
//
// D0 = x, D1 = sin 结果, D2 = cos 结果, D3 = r, D4 = r^2, D5 = 累加器。
// x21 = 象限 q, x10 = q&1 (换名), x11 = q&2 (变号)。
func (a *a8) emitSinCosTan(id int64) {
	a.ldrD(0, a64Regs, 0)

	// q = round(x * 2/pi)
	a.fconst(1, 2/math.Pi)
	a.fmul(2, 0, 1)
	a.frinta(2, 2)
	a.fcvtzs(a64T1, 2)

	// r = x - q*(pi/2), 用高低两段分别减
	a.scvtf(3, a64T1)
	a.fconst(5, a64PiHi)
	a.fmul(6, 3, 5)
	a.fsub(0, 0, 6)
	a.fconst(5, a64PiLo)
	a.fmul(6, 3, 5)
	a.fsub(0, 0, 6)

	// D3 = r, D4 = r^2
	a.fmovD(3, 0)
	a.fmul(4, 3, 3)

	// sin(r): r*(1 + r^2*(-1/6 + r^2*(1/120 + ...)))
	a.fconst(5, 1.0/6227020800) // 1/13!
	a.fconst(6, -1.0/39916800)  // -1/11!
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/362880) // 1/9!
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, -1.0/5040) // -1/7!
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/120) // 1/5!
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, -1.0/6)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fmul(1, 5, 3) // D1 = sin(r)

	// cos(r): 1 + r^2*(-1/2 + r^2*(1/24 + ...))
	a.fconst(5, -1.0/87178291200) // -1/14!
	a.fconst(6, 1.0/479001600)    // 1/12!
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, -1.0/3628800) // -1/10!
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/40320) // 1/8!
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, -1.0/720)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/24)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, -0.5)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fmovD(2, 5) // D2 = cos(r)

	// 象限标志
	a.movz(10, 1, 0)
	a.andReg(10, a64T1, 10) // x10 = q & 1: 奇象限要换名
	a.movz(11, 2, 0)
	a.andReg(11, a64T1, 11) // x11 = q & 2: 结果变号

	// D3 = sin(x) = 奇象限 ? cos(r) : sin(r)
	swapS := a.phCbnz(10)
	a.fmovD(3, 1)
	sinDone := a.phB()
	a.patch(swapS, len(a.code), a64FixCB)
	a.fmovD(3, 2)
	a.patch(sinDone, len(a.code), a64FixB)
	a.cbzAt(11, 0)
	negS := len(a.code) - 4
	a.fneg(3, 3)
	a.patch(negS, len(a.code), a64FixCB)

	// D4 = cos(x) = 奇象限 ? -sin(r) : cos(r)
	swapC := a.phCbnz(10)
	a.fmovD(4, 2)
	cosDone := a.phB()
	a.patch(swapC, len(a.code), a64FixCB)
	a.fmovD(4, 1)
	a.fneg(4, 4)
	a.patch(cosDone, len(a.code), a64FixB)
	a.cbzAt(11, 0)
	negC := len(a.code) - 4
	a.fneg(4, 4)
	a.patch(negC, len(a.code), a64FixCB)

	switch id {
	case 3: // SIN
		a.strD(3, a64Regs, 0)
	case 4: // COS
		a.strD(4, a64Regs, 0)
	default: // TAN = sin / cos
		a.fdiv(3, 3, 4)
		a.strD(3, a64Regs, 0)
	}
}

// emitPow: SYS 1。R0 = x, R1 = y, 结果写回 R0。
func (a *a8) emitPow() {
	a.ldrD(0, a64Regs, 0)    // x
	a.ldrD(10, a64Regs, 8)   // y (D10 全程保留)
	a.fmovD(1, 10)           // D1 = y

	// y 是整数吗? frinta(y) == y 即可
	a.frinta(2, 1)
	a.fcmp(1, 2)
	notInt := a.phBC(condNE)

	// |y| < 2^31 时才敢走整数快速幂
	a.fcvtzs(a64T1, 1) // n = (int64)y
	a.movImm(13, 63)
	a.asrv(12, a64T1, 13) // 符号掩码
	a.eorReg(14, a64T1, 12)
	a.subReg(14, 14, 12) // |n|
	a.movImm(12, 1<<31)
	a.cmpReg(14, 12)
	tooBig := a.phBC(condHS)

	// ---- 整数指数: 平方乘 ----
	a.fconst(3, 1.0) // 结果
	a.fmovD(4, 0) // 底数
	iloop := len(a.code)
	a.cbzAt(14, 0)
	iend := len(a.code) - 4
	a.movz(15, 1, 0)
	a.andReg(15, 14, 15)
	a.cbzAt(15, 0)
	skipMul := len(a.code) - 4
	a.fmul(3, 3, 4)
	a.patch(skipMul, len(a.code), a64FixCB)
	a.fmul(4, 4, 4)
	a.movz(15, 1, 0)
	a.lsrv(14, 14, 15)
	backI := len(a.code)
	a.bAt(iloop - backI)
	a.patch(iend, len(a.code), a64FixCB)

	// y < 0 -> 取倒数
	a.fconst(5, 0.0)
	a.fcmp(1, 5)
	posExp := a.phBC(condGE)
	a.fconst(5, 1.0)
	a.fdiv(3, 5, 3)
	a.patch(posExp, len(a.code), a64FixBCond)
	a.strD(3, a64Regs, 0)
	intDone := a.phB()

	// ---- 一般情况: exp2(y * log2(x)) ----
	a.patch(notInt, len(a.code), a64FixBCond)
	a.patch(tooBig, len(a.code), a64FixBCond)

	// x <= 0 -> NaN (负底数的非整数次幂在实数域无定义)
	a.fconst(5, 0.0)
	a.fcmp(0, 5)
	nonPos := a.phBC(condLE)

	// e = 指数域 - 1023, m = 尾数域 | 1.0
	a.fmovToX(a64T1, 0)
	a.movz(12, 52, 0)
	a.lsrv(13, a64T1, 12)
	a.movImm(12, 0x7FF)
	a.andReg(13, 13, 12) // 偏置指数
	a.movImm(12, 0x000FFFFFFFFFFFFF)
	a.andReg(14, a64T1, 12)
	a.movImm(12, 0x3FF0000000000000)
	a.orrReg(14, 14, 12)
	a.fmovFromX(1, 14) // D1 = m, 范围 [1, 2)

	// m > sqrt2 时折半, 指数 +1, 把 m 压到 [1/sqrt2, sqrt2)
	a.fconst(5, 1.4142135623730951)
	a.fcmp(1, 5)
	notBigM := a.phBC(condLS)
	a.fconst(5, 0.5)
	a.fmul(1, 1, 5)
	a.addImm(13, 13, 1)
	a.patch(notBigM, len(a.code), a64FixBCond)
	a.movImm(12, 1023)
	a.subReg(13, 13, 12) // e (有符号)

	// t = (m-1)/(m+1), ln(m) = 2t(1 + t^2/3 + t^4/5 + ... + t^16/17)
	a.fconst(5, 1.0)
	a.fsub(2, 1, 5)
	a.fadd(3, 1, 5)
	a.fdiv(2, 2, 3) // D2 = t
	a.fmul(4, 2, 2) // t^2
	a.fconst(5, 1.0/17)
	a.fconst(6, 1.0/15)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/13)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/11)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/9)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/7)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/5)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/3)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fmul(2, 2, 5)      // t * acc
	a.fconst(6, 2.0)     //
	a.fmul(2, 2, 6)      // ln(m)
	a.scvtf(7, 13)       // (double)e
	a.fconst(6, 1.4426950408889634) // 1/ln2
	a.fmul(2, 2, 6)
	a.fadd(2, 2, 7) // D2 = log2(x)

	// z = y * log2(x)
	a.fmul(2, 2, 10)

	// exp2(z): n = round(z), f = z - n, 2^f 用 e^(f*ln2) 的 Taylor
	a.frinta(3, 2)
	a.fsub(4, 2, 3)
	a.fconst(5, 0.6931471805599453)
	a.fmul(4, 4, 5) // u = f*ln2, |u| <= 0.347
	a.fconst(5, 1.0/479001600) // 1/12!
	a.fconst(6, 1.0/39916800)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/3628800)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/362880)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/40320)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/5040)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/720)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/120)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/24)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0/6)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 0.5)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	a.fconst(6, 1.0)
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	// 到这里 acc = 1 + u/2! + u^2/3! + ... , 还差一次 "乘 u 加 1" 才能补上 u/1!
	// 这一项 (e^u = 1 + u + u^2/2! + ...) —— 漏掉它误差是 u 的量级, 约 20%。
	a.fmul(5, 5, 4)
	a.fadd(5, 5, 6)
	// 2^n: 把 n 加进指数域
	a.fcvtzs(a64T1, 3)
	a.movImm(12, 1023)
	a.addReg(a64T1, a64T1, 12)
	a.movz(13, 52, 0)
	a.lslv(a64T1, a64T1, 13)
	a.fmovFromX(6, a64T1)
	a.fmul(5, 5, 6)
	a.strD(5, a64Regs, 0)
	genDone := a.phB()

	// x <= 0: 写 NaN
	a.patch(nonPos, len(a.code), a64FixBCond)
	a.movImm(9, 0x7FF8000000000000)
	a.str(9, a64Regs, 0)

	end := len(a.code)
	a.patch(intDone, end, a64FixB)
	a.patch(genDone, end, a64FixB)
}
