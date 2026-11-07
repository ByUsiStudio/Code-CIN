package mc

import (
	"encoding/binary"
	"fmt"

	"codecin-native/ir"
)

// AArch64 (ARM64) 最小后端: MOV/ADD/SUB/HALT/OUT(str) 与 MOV reg,reg。
// 说明: 本机 WSL 无 qemu-aarch64, 只能做结构校验 (e_machine=0xB7/入口/段),
// 无法像 x86-64 那样真实执行——见 aarch64_test.go 的说明。

const aarchEntryOff = 0x78

type a8 struct{ code []byte }

func (a *a8) word(w uint32) {
	var b [4]byte
	binary.LittleEndian.PutUint32(b[:], w)
	a.code = append(a.code, b[:]...)
}

func (a *a8) movz(d int, imm16 uint32, shift uint) {
	a.word(0xD2800000 | uint32(shift)<<21 | (imm16&0xFFFF)<<5 | uint32(d))
}

func (a *a8) movk(d int, imm16 uint32, shift uint) {
	a.word(0xF2800000 | uint32(shift)<<21 | (imm16&0xFFFF)<<5 | uint32(d))
}

// movImm 把 64 位常量装入 x<d>。
func (a *a8) movImm(d int, v uint64) {
	a.movz(d, uint32(v&0xFFFF), 0)
	for s := uint(1); s < 4; s++ {
		if part := uint32((v >> (16 * s)) & 0xFFFF); part != 0 {
			a.movk(d, part, s)
		}
	}
}

func (a *a8) ldr(t, n int) { a.word(0xF9400000 | uint32(n)<<5 | uint32(t)) }
func (a *a8) str(t, n int) { a.word(0xF9000000 | uint32(n)<<5 | uint32(t)) }
func (a *a8) addImm(d, n int, imm uint32) {
	a.word(0x91000000 | (imm&0xFFF)<<10 | uint32(n)<<5 | uint32(d))
}
func (a *a8) subImm(d, n int, imm uint32) {
	a.word(0xD1000000 | (imm&0xFFF)<<10 | uint32(n)<<5 | uint32(d))
}
func (a *a8) movReg(d, m int)  { a.word(0xAA0003E0 | uint32(m)<<16 | uint32(d)) }
func (a *a8) addReg(d, n, m int) { a.word(0x8B000000 | uint32(m)<<16 | uint32(n)<<5 | uint32(d)) }
func (a *a8) subReg(d, n, m int) { a.word(0xCB000000 | uint32(m)<<16 | uint32(n)<<5 | uint32(d)) }
func (a *a8) svc()             { a.word(0xD4000001) }

// loadSlot: x<d> = VM 寄存器槽 [slot]
func (a *a8) loadSlot(d int, slot int32) {
	a.movImm(16, uint64(uint32(slot)))
	a.ldr(d, 16)
}

func (a *a8) storeSlot(s int, slot int32) {
	a.movImm(16, uint64(uint32(slot)))
	a.str(s, 16)
}

// EncodeAArch64 把 IR 编译为 AArch64 机器码。
func EncodeAArch64(p *ir.Program) ([]byte, error) {
	a := &a8{}
	for _, ins := range p.Instructions {
		switch ins.Op {
		case "MOV":
			if len(ins.Args) == 2 && ins.Args[0].Kind == "reg" && ins.Args[1].Kind == "imm" {
				a.movImm(0, uint64(ins.Args[1].V))
				a.storeSlot(0, regAddr(int(ins.Args[0].V)))
				continue
			}
			if len(ins.Args) == 2 && ins.Args[0].Kind == "reg" && ins.Args[1].Kind == "reg" {
				a.loadSlot(0, regAddr(int(ins.Args[1].V)))
				a.storeSlot(0, regAddr(int(ins.Args[0].V)))
				continue
			}
			return nil, fmt.Errorf("aarch64: MOV 只支持 reg,imm / reg,reg")
		case "ADD", "SUB":
			if len(ins.Args) == 2 && ins.Args[1].Kind == "imm" {
				slot := regAddr(int(ins.Args[0].V))
				a.loadSlot(0, slot)
				if ins.Op == "ADD" {
					a.addImm(0, 0, uint32(ins.Args[1].V))
				} else {
					a.subImm(0, 0, uint32(ins.Args[1].V))
				}
				a.storeSlot(0, slot)
				continue
			}
			if len(ins.Args) == 2 && ins.Args[1].Kind == "reg" {
				rd := regAddr(int(ins.Args[0].V))
				a.loadSlot(0, rd)
				a.loadSlot(1, regAddr(int(ins.Args[1].V)))
				if ins.Op == "ADD" {
					a.addReg(0, 0, 1)
				} else {
					a.subReg(0, 0, 1)
				}
				a.storeSlot(0, rd)
				continue
			}
			return nil, fmt.Errorf("aarch64: %s 操作数不支持", ins.Op)
		case "OUT":
			if len(ins.Args) == 1 && ins.Args[0].Kind == "str" {
				addr := ins.Args[0].V
				n := dataLen(p, addr)
				a.movImm(0, 1)                        // fd
				a.movImm(1, uint64(uint32(dataBase+int(addr)))) // buf
				a.movImm(2, uint64(n))                // len
				a.movImm(8, 64)                       // write
				a.svc()
				continue
			}
			return nil, fmt.Errorf("aarch64: OUT 只支持静态字符串")
		case "HALT":
			a.loadSlot(0, regAddr(0))
			a.movImm(8, 93) // exit
			a.svc()
			return a.code, nil
		default:
			return nil, fmt.Errorf("aarch64 后端暂不支持指令: %s", ins.Op)
		}
	}
	return nil, fmt.Errorf("程序缺少 HALT")
}

// WrapAArch64ELF 包装为 AArch64 ELF64。
func WrapAArch64ELF(p *ir.Program, code []byte) []byte {
	dataOff := dataBase - baseAddr
	end := dataOff + 0x500
	for _, w := range p.DataWrites {
		if e := dataOff + w.Addr + len(w.Data); e > end {
			end = e
		}
	}
	img := make([]byte, end)
	copy(img[aarchEntryOff:], code)
	for _, w := range p.DataWrites {
		copy(img[dataOff+w.Addr:], w.Data)
	}
	le := binary.LittleEndian
	copy(img[0:16], []byte{0x7F, 'E', 'L', 'F', 2, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0})
	le.PutUint16(img[16:], 2)                             // ET_EXEC
	le.PutUint16(img[18:], 0xB7)                          // EM_AARCH64
	le.PutUint32(img[20:], 1)
	le.PutUint64(img[24:], uint64(baseAddr+aarchEntryOff)) // e_entry
	le.PutUint64(img[32:], ehdrSize)
	le.PutUint16(img[52:], ehdrSize)
	le.PutUint16(img[54:], phdrSize)
	le.PutUint16(img[56:], 1)
	ph := ehdrSize
	le.PutUint32(img[ph:], 1)
	le.PutUint32(img[ph+4:], 7)
	le.PutUint64(img[ph+16:], uint64(baseAddr))
	le.PutUint64(img[ph+24:], uint64(baseAddr))
	le.PutUint64(img[ph+32:], uint64(len(img)))
	le.PutUint64(img[ph+40:], uint64(len(img)))
	le.PutUint64(img[ph+48:], 0x1000)
	return img
}
