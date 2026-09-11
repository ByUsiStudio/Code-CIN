package engine

import (
	"encoding/binary"
	"fmt"
	"math"

	"codecin-native/ir"
)

// UCBC 字节码编码 (与 Python codecin/native.py encode_program 严格一致)。
const (
	bcMagic   = "UCBC"
	bcVersion = 1
)

// condCode: 条件助记符 -> 编号 (与 Python Cond.NAMES 一致)
var condCode = map[string]uint8{
	"EQ": 0, "NE": 1, "CS": 2, "CC": 3, "MI": 4, "PL": 5, "VS": 6, "VC": 7,
	"HI": 8, "LS": 9, "GE": 10, "LT": 11, "GT": 12, "LE": 13, "AL": 14, "NV": 15,
}

// EncodeProgram 把 IR 程序编码为 UCBC 字节码。
func EncodeProgram(prog ir.Program, entry int) ([]byte, error) {
	labels := map[string]int{}
	for k, v := range prog.Labels {
		labels[k] = v
	}
	for k, v := range prog.DataLabels {
		labels[k] = v
	}

	buf := make([]byte, 0, 13+len(prog.Instructions)*8)
	buf = append(buf, bcMagic...)
	buf = append(buf, bcVersion)
	var tmp [8]byte
	binary.LittleEndian.PutUint32(tmp[:4], uint32(entry&0xFFFFFFFF))
	buf = append(buf, tmp[:4]...)
	binary.LittleEndian.PutUint32(tmp[:4], uint32(len(prog.Instructions)&0xFFFFFFFF))
	buf = append(buf, tmp[:4]...)

	for _, ins := range prog.Instructions {
		opv, ok := opcodeByName[ins.Op]
		if !ok {
			return nil, fmt.Errorf("unknown opcode: %s", ins.Op)
		}
		buf = append(buf, opv, byte(len(ins.Args)))
		for _, a := range ins.Args {
			kind, value, extra, err := encodeOperand(a, labels)
			if err != nil {
				return nil, err
			}
			buf = append(buf, kind)
			binary.LittleEndian.PutUint64(tmp[:8], uint64(value))
			buf = append(buf, tmp[:8]...)
			binary.LittleEndian.PutUint64(tmp[:8], uint64(extra))
			buf = append(buf, tmp[:8]...)
		}
	}
	return buf, nil
}

func encodeOperand(op ir.Operand, labels map[string]int) (kind uint8, value, extra int64, err error) {
	switch op.Kind {
	case "reg":
		return kindReg, op.V, 0, nil
	case "imm":
		return kindImm, op.V, 0, nil
	case "vec":
		return kindVec, op.V, 0, nil
	case "veclane":
		return kindVecLane, op.V, op.Off, nil
	case "mem":
		return kindMem, op.V, op.Off, nil
	case "cond":
		c, ok := condCode[op.Name]
		if !ok {
			return 0, 0, 0, fmt.Errorf("unknown cond: %s", op.Name)
		}
		return kindCond, int64(c), 0, nil
	case "float":
		return kindFloat, int64(math.Float64bits(op.F)), 0, nil
	case "str":
		return kindStr, op.V, 0, nil
	case "label":
		addr, ok := labels[op.Name]
		if !ok {
			return 0, 0, 0, fmt.Errorf("undefined label: %s", op.Name)
		}
		return kindImm, int64(addr), 0, nil
	}
	return 0, 0, 0, fmt.Errorf("unknown operand kind: %s", op.Kind)
}
