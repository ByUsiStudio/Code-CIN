package mc

import (
	"encoding/binary"
	"testing"

	"codecin-native/ir"
)

func TestAArch64ELFAndEncoding(t *testing.T) {
	p := &ir.Program{Instructions: []ir.Instr{
		{Op: "MOV", Args: []ir.Operand{ir.Reg(0), ir.Imm(42)}},
		{Op: "HALT"},
	}}
	code, err := EncodeAArch64(p)
	if err != nil {
		t.Fatalf("EncodeAArch64: %v", err)
	}
	img := WrapAArch64ELF(p, code)
	if binary.LittleEndian.Uint16(img[18:]) != 0xB7 {
		t.Fatalf("e_machine != AArch64")
	}
	if binary.LittleEndian.Uint64(img[24:]) != uint64(baseAddr+aarchEntryOff) {
		t.Fatalf("入口不正确")
	}
	last := len(code) - 4
	if w := binary.LittleEndian.Uint32(code[last:]); w != 0xD4000001 {
		t.Fatalf("最后一条应为 svc #0, got %#x", w)
	}
}
