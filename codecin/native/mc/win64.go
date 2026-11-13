package mc

import (
	"encoding/binary"
	"fmt"

	"codecin-native/ir"
)

// Windows x86-64 (Win64 ABI) 最小后端: MOV / ADD / SUB / OUT(str) / HALT。
// 通过 kernel32 导入(GetStdHandle / WriteFile / ExitProcess)完成控制台输出。
// 地址固定(无 ASLR), 使用绝对 imm64 寻址。

const (
	winStrBase  = uint64(peDataRVA)       // 字符串基址 (IR 数据地址在此之上)
	winSlotBase = uint64(peDataRVA + 0x800) // VM 寄存器槽位
	winScratch  = 0x780                   // 临时区 (写回字节数)
)

// winImports 是 PE 后端唯一的导入清单 (代码与 IAT 必须一致)。
var winImports = peImport{DLL: "kernel32.dll",
	Names: []string{"GetStdHandle", "WriteFile", "ExitProcess", "VirtualAlloc"}}

type winEnc struct {
	code    []byte
	data    []byte
	imports *peImport
	layout  map[string]uint32
}

func (w *winEnc) raw(b ...byte) { w.code = append(w.code, b...) }
func (w *winEnc) imm32(v uint32) {
	var b [4]byte
	binary.LittleEndian.PutUint32(b[:], v)
	w.raw(b[:]...)
}
func (w *winEnc) imm64(v uint64) {
	var b [8]byte
	binary.LittleEndian.PutUint64(b[:], v)
	w.raw(b[:]...)
}

func winSlot(r int) uint64 { return peBase + winSlotBase + uint64(r*8) }

// movImmTo 把 64 位立即数装入 rax/rcx/r9 等。
func (w *winEnc) movRegImm(prefix byte, v uint64) {
	w.raw(0x48, prefix)
	w.imm64(v)
}

func (w *winEnc) storeSlot(r int) { // rax -> [slot]
	w.movRegImm(0xB9, winSlot(r)) // mov rcx, imm64
	w.raw(0x48, 0x89, 0x01)       // mov [rcx], rax
}

func (w *winEnc) loadSlot(r int) { // rax = [slot]
	w.movRegImm(0xB9, winSlot(r))
	w.raw(0x48, 0x8B, 0x01)
}

// callIAT 生成 call qword ptr [rip+disp32]。
func (w *winEnc) callIAT(name string, insRVA uint32) error {
	slot, ok := w.layout[name]
	if !ok {
		return fmt.Errorf("未导入函数: %s", name)
	}
	next := insRVA + 6
	disp := int64(slot) - int64(next)
	if disp < -1<<31 || disp >= 1<<31 {
		return fmt.Errorf("导入槽超出 32 位相对范围: %s", name)
	}
	w.raw(0xFF, 0x15)
	w.imm32(uint32(int32(disp)))
	return nil
}

// EncodeWin64 生成 .text 代码与 .data 初始数据。
func EncodeWin64(p *ir.Program) ([]byte, []byte, error) {
	w := &winEnc{imports: &winImports, layout: rdataIATs(winImports)}

	// .data: 程序静态数据在 0x0 起, VM 槽位在 winDataBase+winRegBase
	maxEnd := int(winSlotBase-peDataRVA) + 33*8 + 16
	for _, dw := range p.DataWrites {
		if e := dw.Addr + len(dw.Data); e > maxEnd {
			maxEnd = e
		}
	}
	w.data = make([]byte, maxEnd)
	for _, dw := range p.DataWrites {
		copy(w.data[dw.Addr:], dw.Data)
	}
	w.data[winScratch+8] = 10

	// 序言: 影子空间 + VirtualAlloc(1 GiB) 作为 VM 内存
	w.raw(0x48, 0x83, 0xEC, 0x28)
	w.raw(0x48, 0x31, 0xC9)             // xor rcx, rcx (lpAddress=NULL)
	w.movRegImm(0xBA, 64<<20)           // rdx = 64 MiB
	w.raw(0x41, 0xB8); w.imm32(0x3000)  // r8d = MEM_COMMIT|MEM_RESERVE
	w.raw(0x41, 0xB9); w.imm32(4)       // r9d = PAGE_READWRITE
	proRVA := peTextRVA + uint32(len(w.code))
	if err := w.callIAT("VirtualAlloc", proRVA); err != nil {
		return nil, nil, err
	}
	w.raw(0x49, 0x89, 0xC5)             // mov r13, rax
	// 把 .data 初始内容复制进 VM 内存 (先清方向标志)
	w.raw(0xFC)                                 // cld
	w.movRegImm(0xBE, peBase+uint64(peDataRVA)) // rsi = .data
	w.raw(0x4C, 0x89, 0xEF)                     // rdi = r13
	w.movRegImm(0xB9, uint64(len(w.data)))      // rcx = 长度
	w.raw(0xF3, 0xA4)                           // rep movsb
	// R32 = memSize-8
	w.movRegImm(0xB8, (64<<20)-8)
	w.storeSlot(32)

	type wfix struct{ pos, idx int }
	var fixups []wfix
	sawHalt := false
	offs := make([]int, len(p.Instructions))
	for i, ins := range p.Instructions {
		offs[i] = len(w.code)
		insRVA := peTextRVA + uint32(len(w.code))
		switch ins.Op {
		case "MOV":
			if len(ins.Args) == 2 && ins.Args[0].Kind == "reg" && ins.Args[1].Kind == "imm" {
				w.movRegImm(0xB8, uint64(ins.Args[1].V))
				w.storeSlot(int(ins.Args[0].V))
				continue
			}
			if len(ins.Args) == 2 && ins.Args[0].Kind == "reg" && ins.Args[1].Kind == "reg" {
				w.loadSlot(int(ins.Args[1].V))
				w.storeSlot(int(ins.Args[0].V))
				continue
			}
			return nil, nil, fmt.Errorf("win64: MOV 只支持 reg,imm / reg,reg")
		case "ADD", "SUB":
			if len(ins.Args) == 2 && ins.Args[1].Kind == "imm" {
				r := int(ins.Args[0].V)
				w.loadSlot(r)
				if ins.Op == "ADD" {
					w.raw(0x48, 0x05)
				} else {
					w.raw(0x48, 0x2D)
				}
				w.imm32(uint32(int32(ins.Args[1].V)))
				w.storeSlot(r)
				continue
			}
			return nil, nil, fmt.Errorf("win64: %s 只支持 imm", ins.Op)
		case "CALL":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "label" {
				return nil, nil, fmt.Errorf("win64: CALL 需要 label")
			}
			target, ok := p.Labels[ins.Args[0].Name]
			if !ok {
				return nil, nil, fmt.Errorf("win64: 未定义标签 %s", ins.Args[0].Name)
			}
			w.raw(0xE8)
			fixups = append(fixups, wfix{pos: len(w.code), idx: target})
			w.imm32(0)
			continue
		case "RET":
			w.raw(0xC3)
			continue
		case "PUSH":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "reg" {
				return nil, nil, fmt.Errorf("win64: PUSH 需要 reg")
			}
			r := int(ins.Args[0].V)
			w.loadSlot(32)                       // rax = SP
			w.raw(0x48, 0x2D); w.imm32(8)        // sub rax, 8
			w.storeSlot(32)
			w.raw(0x4C, 0x89, 0xE9)              // mov rcx, r13
			w.raw(0x48, 0x01, 0xC1)              // add rcx, rax
			w.raw(0x49, 0x89, 0xC8)              // mov r8, rcx (保存地址)
			w.loadSlot(r)                        // rax = 值
			w.raw(0x49, 0x89, 0x00)              // mov [r8], rax
			continue
		case "POP":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "reg" {
				return nil, nil, fmt.Errorf("win64: POP 需要 reg")
			}
			r := int(ins.Args[0].V)
			w.loadSlot(32)
			w.raw(0x4C, 0x89, 0xE9)              // rcx = r13
			w.raw(0x48, 0x01, 0xC1)
			w.raw(0x49, 0x89, 0xC8)              // r8 = 地址
			w.raw(0x49, 0x8B, 0x00)              // rax = [r8]
			w.storeSlot(r)
			w.loadSlot(32)
			w.raw(0x48, 0x05); w.imm32(8)
			w.storeSlot(32)
			continue
		case "SYS":
			if len(ins.Args) != 1 || ins.Args[0].Kind != "imm" {
				return nil, nil, fmt.Errorf("win64: SYS 需要立即数")
			}
			switch ins.Args[0].V {
			case 24:
				return nil, nil, fmt.Errorf("win64: 控制台输出暂不稳定 (SYS 24)")
			case 137: // ALLOCFRAME: R32 -= n; R0 = R32
				w.loadSlot(0)                    // rax = n
				w.raw(0x48, 0x89, 0xC2)          // rdx = n
				w.loadSlot(32)
				w.raw(0x48, 0x29, 0xD0)          // rax = SP - n
				w.storeSlot(32)
				w.storeSlot(0)
			default:
				return nil, nil, fmt.Errorf("win64 后端暂不支持 SYS %d", ins.Args[0].V)
			}
			continue
		case "OUT":
			return nil, nil, fmt.Errorf("win64: 控制台输出暂不稳定 (OUT)")

		case "HALT":
			w.loadSlot(0)
			w.raw(0x48, 0x89, 0xC1) // mov rcx, rax
			insRVA = peTextRVA + uint32(len(w.code))
			if err := w.callIAT("ExitProcess", insRVA); err != nil {
				return nil, nil, err
			}
			sawHalt = true
			continue
		default:
			return nil, nil, fmt.Errorf("win64 后端暂不支持指令: %s", ins.Op)
		}
	}
	if !sawHalt {
		return nil, nil, fmt.Errorf("程序缺少 HALT")
	}
	for _, f := range fixups {
		rel := int32(offs[f.idx] - (f.pos + 4))
		binary.LittleEndian.PutUint32(w.code[f.pos:], uint32(rel))
	}
	return w.code, w.data, nil
}

// WrapPE 生成最终 .exe 字节。
func WrapPE(p *ir.Program) ([]byte, error) {
	code, data, err := EncodeWin64(p)
	if err != nil {
		return nil, err
	}
	l, err := buildPE(code, data, winImports)
	if err != nil {
		return nil, err
	}
	return l.img, nil
}
