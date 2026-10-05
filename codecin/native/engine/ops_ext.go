package engine

// ops_ext.go: ISA 其余指令的 Go 实现 (语义与已删除的 Python 解释器 cpu.py
// 的 _op_* 方法严格一致)。execute() 的 default 分支转到这里。

import (
	"encoding/binary"
	"math"
	"math/bits"
)

func (vm *vmState) vecScalar(n int) float64 { return vm.vec[n&31][0] }
func (vm *vmState) setVecScalar(n int, v float64) {
	vm.vec[n&31][0] = v
}

func (vm *vmState) setFlagsAddGo(a, b, result uint64) {
	res := result & mask64
	sa := int64(a)
	sb := int64(b)
	ss := sa + sb
	vm.flags.Z = res == 0
	vm.flags.N = res&(1<<63) != 0
	vm.flags.C = a+b > mask64
	vm.flags.V = ss > math.MaxInt64 || ss < math.MinInt64
}

// execExt 处理 execute 主 switch 之外的指令。返回 (halt, err)。
func (vm *vmState) execExt(ins instruction) (bool, string) {
	args := ins.args
	switch ins.opcode {
	// ---- ARM64 条件运算 (带标志) ----
	case opADDS:
		rd, rn := int(args[0].value), int(args[1].value)
		v, ok := vm.val(args[2])
		if !ok {
			return false, "bad ADDS operand"
		}
		r := (vm.reg(rn) + v) & mask64
		vm.setFlagsAddGo(vm.reg(rn), v, r)
		vm.setReg(rd, r)
	case opSUBS:
		rd, rn := int(args[0].value), int(args[1].value)
		v, ok := vm.val(args[2])
		if !ok {
			return false, "bad SUBS operand"
		}
		r := (vm.reg(rn) - v) & mask64
		vm.setFlagsSub(vm.reg(rn), v)
		vm.setReg(rd, r)
	case opADDC:
		rd, rn := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		c := uint64(0)
		if vm.flags.C {
			c = 1
		}
		r := (vm.reg(rn) + v + c) & mask64
		vm.setFlagsAddGo(vm.reg(rn), v, r)
		vm.setReg(rd, r)
	case opSUBC:
		rd, rn := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		c := uint64(1)
		if vm.flags.C {
			c = 0
		}
		r := (vm.reg(rn) - v - c) & mask64
		vm.setFlagsSub(vm.reg(rn), v)
		vm.setReg(rd, r)
	case opROR:
		rd, rn := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		amt := v & 63
		x := vm.reg(rn)
		r := x
		if amt != 0 {
			r = (x >> amt) | (x << (64 - amt))
		}
		vm.setReg(rd, r&mask64)
	case opEOR:
		rd, rn := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		vm.setReg(rd, vm.reg(rn)^v)
	case opBIC:
		rd, rn := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		vm.setReg(rd, vm.reg(rn)&(^v&mask64))
	case opORN:
		rd, rn := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		vm.setReg(rd, vm.reg(rn)|(^v&mask64))
	// ---- ARM64 宽度 load/store ----
	case opLDR:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		if e := vm.checkAddr(addr, 4); e != "" {
			return false, e
		}
		vm.setReg(int(args[0].value), uint64(binary.LittleEndian.Uint32(vm.mem[addr:addr+4])))
	case opSTR:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		if e := vm.checkAddr(addr, 4); e != "" {
			return false, e
		}
		binary.LittleEndian.PutUint32(vm.mem[addr:addr+4], uint32(vm.reg(int(args[0].value))))
		vm.touch(addr, 4)
	case opLDP:
		rt, rt2 := int(args[0].value), int(args[1].value)
		addr, e := vm.loadStoreAddr(args[2])
		if e != "" {
			return false, e
		}
		v1, e := vm.readQ(addr)
		if e != "" {
			return false, e
		}
		v2, e := vm.readQ(addr + 8)
		if e != "" {
			return false, e
		}
		vm.setReg(rt, v1)
		vm.setReg(rt2, v2)
	case opSTP:
		rt, rt2 := int(args[0].value), int(args[1].value)
		addr, e := vm.loadStoreAddr(args[2])
		if e != "" {
			return false, e
		}
		if e := vm.writeQ(addr, vm.reg(rt)); e != "" {
			return false, e
		}
		if e := vm.writeQ(addr+8, vm.reg(rt2)); e != "" {
			return false, e
		}
	// ---- ARM64 分支 ----
	case opCBZ:
		if vm.reg(int(args[0].value)) == 0 {
			t, _ := vm.val(args[1])
			vm.pc = int(t)
		}
	case opCBNZ:
		if vm.reg(int(args[0].value)) != 0 {
			t, _ := vm.val(args[1])
			vm.pc = int(t)
		}
	case opTBZ:
		rn := vm.reg(int(args[0].value))
		bitv, _ := vm.val(args[1])
		bit := bitv & 63
		if rn&(1<<bit) == 0 {
			t, _ := vm.val(args[2])
			vm.pc = int(t)
		}
	case opTBNZ:
		rn := vm.reg(int(args[0].value))
		bitv, _ := vm.val(args[1])
		bit := bitv & 63
		if rn&(1<<bit) != 0 {
			t, _ := vm.val(args[2])
			vm.pc = int(t)
		}
	case opBR:
		vm.pc = int(vm.reg(int(args[0].value)))
	case opWFE, opWFI, opSEV:
		// 无事件/中断模型: 语义等价 NOP (与解释器别名一致)
	// ---- ARM64 条件选择 ----
	case opCSEL, opCSINC, opCSINV, opCSNEG:
		rd, rn := int(args[0].value), int(args[1].value)
		cond := uint8(14) // AL
		if args[3].kind == kindCond {
			cond = uint8(args[3].value)
		}
		taken := vm.condition(cond)
		switch ins.opcode {
		case opCSEL:
			if taken {
				vm.setReg(rd, vm.reg(rn))
			} else {
				v, _ := vm.val(args[2])
				vm.setReg(rd, v)
			}
		case opCSINC:
			if taken {
				vm.setReg(rd, vm.reg(rn))
			} else {
				v, _ := vm.val(args[2])
				vm.setReg(rd, v+1)
			}
		case opCSINV:
			if taken {
				vm.setReg(rd, vm.reg(rn))
			} else {
				v, _ := vm.val(args[2])
				vm.setReg(rd, ^v&mask64)
			}
		case opCSNEG:
			if taken {
				vm.setReg(rd, vm.reg(rn))
			} else {
				v, _ := vm.val(args[2])
				vm.setReg(rd, uint64(-int64(v))&mask64)
			}
		}
	// ---- 符号/零扩展 ----
	case opSXTB:
		v, _ := vm.val(args[1])
		vm.setReg(int(args[0].value), uint64(int64(int8(v)))&mask64)
	case opSXTH:
		v, _ := vm.val(args[1])
		vm.setReg(int(args[0].value), uint64(int64(int16(v)))&mask64)
	case opSXTW:
		v, _ := vm.val(args[1])
		vm.setReg(int(args[0].value), uint64(int64(int32(v)))&mask64)
	case opUXTB:
		v, _ := vm.val(args[1])
		vm.setReg(int(args[0].value), v&0xFF)
	case opUXTH:
		v, _ := vm.val(args[1])
		vm.setReg(int(args[0].value), v&0xFFFF)
	// ---- 位操作 ----
	case opCLZ:
		v, _ := vm.val(args[1])
		vm.setReg(int(args[0].value), uint64(bits.LeadingZeros64(v)))
	case opCLS:
		v, _ := vm.val(args[1])
		sv := int64(v)
		if sv >= 0 {
			if sv == 0 {
				vm.setReg(int(args[0].value), 63)
			} else {
				vm.setReg(int(args[0].value), uint64(63-bits.Len64(uint64(sv))))
			}
		} else {
			// 与解释器一致: 63 - bit_length(~sv) + 1
			vm.setReg(int(args[0].value), uint64(63-bits.Len64(^uint64(sv))+1))
		}
	case opRBIT:
		v, _ := vm.val(args[1])
		vm.setReg(int(args[0].value), bits.Reverse64(v))
	case opREV:
		v, _ := vm.val(args[1])
		vm.setReg(int(args[0].value), bits.ReverseBytes64(v))
	// ---- 浮点 (向量寄存器标量语义) ----
	case opFADD:
		vm.setVecScalar(int(args[0].value),
			vm.vecScalar(int(args[1].value))+vm.vecScalar(int(args[2].value)))
	case opFSUB:
		vm.setVecScalar(int(args[0].value),
			vm.vecScalar(int(args[1].value))-vm.vecScalar(int(args[2].value)))
	case opFMUL:
		vm.setVecScalar(int(args[0].value),
			vm.vecScalar(int(args[1].value))*vm.vecScalar(int(args[2].value)))
	case opFDIV:
		b := vm.vecScalar(int(args[2].value))
		if b == 0 {
			return false, "Float division by zero"
		}
		vm.setVecScalar(int(args[0].value), vm.vecScalar(int(args[1].value))/b)
	case opFCMP:
		a, b := vm.vecScalar(int(args[0].value)), vm.vecScalar(int(args[1].value))
		vm.flags.Z = a == b
		vm.flags.N = a < b
		vm.flags.C = a >= b
		vm.flags.V = false
	case opFCVT:
		rd, rs := int(args[0].value), int(args[1].value)
		if args[1].kind == kindVec || args[1].kind == kindVecLane {
			// float -> int (向零截断)
			var v float64
			if args[1].kind == kindVec {
				v = vm.vec[rs&31][0]
			} else {
				v = vm.vec[rs&31][args[1].extra&3]
			}
			vm.setReg(rd, uint64(int64(v))&mask64)
		} else {
			// int -> float
			vm.setVecScalar(rd, float64(vm.reg(rs)))
		}
	case opFABS:
		vm.setVecScalar(int(args[0].value), math.Abs(vm.vecScalar(int(args[1].value))))
	case opFNEG:
		vm.setVecScalar(int(args[0].value), -vm.vecScalar(int(args[1].value)))
	case opLDRS:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		if e := vm.checkAddr(addr, 4); e != "" {
			return false, e
		}
		f := math.Float32frombits(binary.LittleEndian.Uint32(vm.mem[addr : addr+4]))
		vm.setVecScalar(int(args[0].value), float64(f))
	case opSTRS:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		if e := vm.checkAddr(addr, 4); e != "" {
			return false, e
		}
		binary.LittleEndian.PutUint32(vm.mem[addr:addr+4],
			math.Float32bits(float32(vm.vecScalar(int(args[0].value)))))
		vm.touch(addr, 4)
	// ---- 向量 (4 lane) ----
	case opVADD, opVSUB, opVMUL, opVDIV:
		rd, rn, rm := int(args[0].value), int(args[1].value), int(args[2].value)
		a := vm.vec[rn&31]
		b := vm.vec[rm&31]
		out := [4]float64{}
		for i := 0; i < 4; i++ {
			switch ins.opcode {
			case opVADD:
				out[i] = a[i] + b[i]
			case opVSUB:
				out[i] = a[i] - b[i]
			case opVMUL:
				out[i] = a[i] * b[i]
			case opVDIV:
				if b[i] == 0 {
					return false, "Vector division by zero"
				}
				out[i] = a[i] / b[i]
			}
		}
		vm.vec[rd&31] = out
	case opVLD1:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		if e := vm.checkAddr(addr, 16); e != "" {
			return false, e
		}
		data := vm.mem[addr : addr+16 : addr+16]
		vm.vec[int(args[0].value)&31] = [4]float64{
			float64(math.Float32frombits(binary.LittleEndian.Uint32(data[0:4]))),
			float64(math.Float32frombits(binary.LittleEndian.Uint32(data[4:8]))),
			float64(math.Float32frombits(binary.LittleEndian.Uint32(data[8:12]))),
			float64(math.Float32frombits(binary.LittleEndian.Uint32(data[12:16]))),
		}
	case opVST1:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		if e := vm.checkAddr(addr, 16); e != "" {
			return false, e
		}
		v := vm.vec[int(args[0].value)&31]
		for i := 0; i < 4; i++ {
			binary.LittleEndian.PutUint32(vm.mem[addr+uint64(i)*4:], math.Float32bits(float32(v[i])))
		}
		vm.touch(addr, 16)
	// ---- RISC-V 扩展 ----
	case opSLTI:
		rd, rs1 := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		if int64(vm.reg(rs1)) < int64(v) {
			vm.setReg(rd, 1)
		} else {
			vm.setReg(rd, 0)
		}
	case opSLTIU:
		rd, rs1 := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		if vm.reg(rs1)&mask64 < v&mask64 {
			vm.setReg(rd, 1)
		} else {
			vm.setReg(rd, 0)
		}
	case opSLLI:
		rd, rs1 := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		vm.setReg(rd, (vm.reg(rs1)<<(v&63))&mask64)
	case opSRLI:
		rd, rs1 := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		vm.setReg(rd, vm.reg(rs1)>>(v&63))
	case opSRAI:
		rd, rs1 := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		vm.setReg(rd, uint64(int64(vm.reg(rs1))>>(v&63))&mask64)
	case opBEQ:
		if vm.reg(int(args[0].value)) == vm.reg(int(args[1].value)) {
			t, _ := vm.val(args[2])
			vm.pc = int(t)
		}
	case opBNE:
		if vm.reg(int(args[0].value)) != vm.reg(int(args[1].value)) {
			t, _ := vm.val(args[2])
			vm.pc = int(t)
		}
	case opBLT:
		if int64(vm.reg(int(args[0].value))) < int64(vm.reg(int(args[1].value))) {
			t, _ := vm.val(args[2])
			vm.pc = int(t)
		}
	case opBGE:
		if int64(vm.reg(int(args[0].value))) >= int64(vm.reg(int(args[1].value))) {
			t, _ := vm.val(args[2])
			vm.pc = int(t)
		}
	case opBLTU:
		if vm.reg(int(args[0].value)) < vm.reg(int(args[1].value)) {
			t, _ := vm.val(args[2])
			vm.pc = int(t)
		}
	case opBGEU:
		if vm.reg(int(args[0].value)) >= vm.reg(int(args[1].value)) {
			t, _ := vm.val(args[2])
			vm.pc = int(t)
		}
	case opJALR:
		// JALR rd, rs1, imm / jalr rs1 (单寄存器形式视为 ret, rd 丢弃)
		var regs []int
		imm := int64(0)
		for _, a := range args {
			if a.kind == kindReg {
				regs = append(regs, int(a.value))
			}
		}
		for _, a := range args {
			if a.kind == kindImm {
				imm = a.value
			} else if a.kind == kindMem {
				imm = a.extra
				if a.value >= 0 && len(regs) < 2 {
					regs = append(regs, int(a.value))
				}
			}
		}
		rd, rs1 := 31, 31
		if len(regs) >= 2 {
			rd, rs1 = regs[0], regs[1]
		} else if len(regs) == 1 {
			rs1 = regs[0]
		}
		if rd != 31 {
			vm.setReg(rd, uint64(vm.pc))
		}
		vm.pc = int((vm.reg(rs1) + uint64(imm)) & mask64)
	case opJAL:
		rd := 31
		if args[0].kind == kindReg {
			rd = int(args[0].value)
		}
		if rd != 31 {
			vm.setReg(rd, uint64(vm.pc))
		}
		var t operand
		if args[0].kind == kindReg {
			t = args[1]
		} else {
			t = args[0]
		}
		v, ok := vm.val(t)
		if !ok {
			return false, "bad JAL operand"
		}
		vm.pc = int(v)
	case opLUI:
		rd := int(args[0].value)
		v, ok := vm.val(args[1])
		if !ok && args[1].kind != kindImm {
			return false, "bad LUI operand"
		}
		if args[1].kind == kindImm {
			v = uint64(args[1].value)
		}
		vm.setReg(rd, (v&0xFFFFF)<<12)
	case opAUIPC:
		rd := int(args[0].value)
		v, ok := vm.val(args[1])
		if !ok && args[1].kind != kindImm {
			return false, "bad AUIPC operand"
		}
		if args[1].kind == kindImm {
			v = uint64(args[1].value)
		}
		vm.setReg(rd, uint64(vm.pc+int((v&0xFFFFF)<<12))&mask64)
	default:
		return false, "Unimplemented instruction: opcode " +
			intToString(int(ins.opcode))
	}
	return false, ""
}

func intToString(v int) string {
	if v == 0 {
		return "0"
	}
	neg := v < 0
	if neg {
		v = -v
	}
	buf := [20]byte{}
	i := len(buf)
	for v > 0 {
		i--
		buf[i] = byte('0' + v%10)
		v /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}
