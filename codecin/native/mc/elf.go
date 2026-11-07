package mc

import (
	"encoding/binary"
	"fmt"

	"codecin-native/ir"
)

const (
	ehdrSize = 0x40
	phdrSize = 0x38
	entryOff = ehdrSize + phdrSize // 0x78
)

// WrapELF 把入口处的机器码 + 程序数据包装成可执行的 ELF64 (Linux, x86-64)。
//
// 布局: [ELF 头 + 1 个 PT_LOAD][代码][寄存器组/标志][数据段]。
// IR 数据地址 A 落在虚地址 dataBase+A。
func WrapELF(p *ir.Program, code []byte) []byte {
	if len(code) == 0 {
		return nil
	}
	dataOff := dataBase - baseAddr
	end := dataOff + regCount*8
	if flags := dataOff + flagOff + 8; flags > end {
		end = flags
	}
	if buf := dataOff + itoaBuf + 64; buf > end {
		end = buf
	}
	for _, w := range p.DataWrites {
		if e := dataOff + w.Addr + len(w.Data); e > end {
			end = e
		}
	}
	img := make([]byte, end)
	copy(img[entryOff:], code)
	for _, w := range p.DataWrites {
		copy(img[dataOff+w.Addr:], w.Data)
	}

	// e_ident
	copy(img[0:16], []byte{0x7F, 'E', 'L', 'F', 2, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0})
	le := binary.LittleEndian
	le.PutUint16(img[16:], 2)                 // ET_EXEC
	le.PutUint16(img[18:], 62)                // EM_X86_64
	le.PutUint32(img[20:], 1)                 // EV_CURRENT
	le.PutUint64(img[24:], uint64(baseAddr+entryOff)) // e_entry
	le.PutUint64(img[32:], ehdrSize)          // e_phoff
	le.PutUint64(img[40:], 0)                 // e_shoff
	le.PutUint32(img[48:], 0)                 // e_flags
	le.PutUint16(img[52:], ehdrSize)          // e_ehsize
	le.PutUint16(img[54:], phdrSize)          // e_phentsize
	le.PutUint16(img[56:], 1)                 // e_phnum
	le.PutUint16(img[58:], 0)                 // e_shentsize
	le.PutUint16(img[60:], 0)                 // e_shnum
	le.PutUint16(img[62:], 0)                 // e_shstrndx

	ph := ehdrSize
	le.PutUint32(img[ph:], 1)                 // PT_LOAD
	le.PutUint32(img[ph+4:], 7)               // PF_R|PF_W|PF_X
	le.PutUint64(img[ph+8:], 0)               // p_offset
	le.PutUint64(img[ph+16:], uint64(baseAddr)) // p_vaddr
	le.PutUint64(img[ph+24:], uint64(baseAddr)) // p_paddr
	le.PutUint64(img[ph+32:], uint64(len(img))) // p_filesz
	le.PutUint64(img[ph+40:], uint64(len(img))) // p_memsz
	le.PutUint64(img[ph+48:], 0x1000)         // p_align
	return img
}

// Entry 返回代码段入口的虚拟地址 (供测试校验)。
func Entry() uint64 { return uint64(baseAddr + entryOff) }

// CheckELF 校验产物是 x86-64 ELF 且入口正确。
func CheckELF(img []byte) error {
	if len(img) < entryOff || string(img[:4]) != "\x7fELF" {
		return fmt.Errorf("不是 ELF")
	}
	le := binary.LittleEndian
	if machine := le.Uint16(img[18:]); machine != 62 {
		return fmt.Errorf("e_machine=%d, 期望 62", machine)
	}
	if entry := le.Uint64(img[24:]); entry != Entry() {
		return fmt.Errorf("e_entry=%#x, 期望 %#x", entry, Entry())
	}
	return nil
}
