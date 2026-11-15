package mc

import (
	"encoding/binary"
	"fmt"

	"codecin-native/ir"
)

// AArch64 的 ELF 容器。布局与 x86-64 版相同:
// [ELF 头 + 1 个 PT_LOAD][代码][静态数据], 只是一次装进同一个 RWX 段。
const (
	a64EntryOff = ehdrSize + phdrSize // 0x78
	a64CodeMax  = dataBase - baseAddr - a64EntryOff
)

// WrapAArch64ELF 把 AArch64 机器码包装成可执行的 ELF64 (Linux)。
func WrapAArch64ELF(p *ir.Program, code []byte) ([]byte, error) {
	if len(code) == 0 {
		return nil, fmt.Errorf("aarch64: 代码为空")
	}
	if len(code) > a64CodeMax {
		return nil, fmt.Errorf("aarch64: 代码段 %d 字节超出上限 %d", len(code), a64CodeMax)
	}
	dataOff := dataBase - baseAddr
	end := dataOff + 0x1000
	for _, w := range p.DataWrites {
		if e := dataOff + w.Addr + len(w.Data); e > end {
			end = e
		}
	}
	img := make([]byte, end)
	copy(img[a64EntryOff:], code)
	for _, w := range p.DataWrites {
		copy(img[dataOff+w.Addr:], w.Data)
	}

	le := binary.LittleEndian
	copy(img[0:16], []byte{0x7F, 'E', 'L', 'F', 2, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0})
	le.PutUint16(img[16:], 2)                          // ET_EXEC
	le.PutUint16(img[18:], 183)                        // EM_AARCH64
	le.PutUint32(img[20:], 1)                          // EV_CURRENT
	le.PutUint64(img[24:], uint64(baseAddr+a64EntryOff)) // e_entry
	le.PutUint64(img[32:], ehdrSize)                   // e_phoff
	le.PutUint32(img[48:], 0)                          // e_flags
	le.PutUint16(img[52:], ehdrSize)
	le.PutUint16(img[54:], phdrSize)
	le.PutUint16(img[56:], 1) // e_phnum

	ph := ehdrSize
	le.PutUint32(img[ph:], 1)                   // PT_LOAD
	le.PutUint32(img[ph+4:], 7)                 // PF_R|PF_W|PF_X
	le.PutUint64(img[ph+8:], 0)                 // p_offset
	le.PutUint64(img[ph+16:], uint64(baseAddr)) // p_vaddr
	le.PutUint64(img[ph+24:], uint64(baseAddr)) // p_paddr
	le.PutUint64(img[ph+32:], uint64(len(img))) // p_filesz
	le.PutUint64(img[ph+40:], uint64(len(img))) // p_memsz
	le.PutUint64(img[ph+48:], 0x1000)           // p_align
	return img, nil
}

// CheckAArch64ELF 校验产物是 AArch64 ELF 且入口正确。
func CheckAArch64ELF(img []byte) error {
	if len(img) < a64EntryOff || string(img[:4]) != "\x7fELF" {
		return fmt.Errorf("不是 ELF")
	}
	le := binary.LittleEndian
	if m := le.Uint16(img[18:]); m != 183 {
		return fmt.Errorf("e_machine=%d, 期望 183 (AArch64)", m)
	}
	want := uint64(baseAddr + a64EntryOff)
	if e := le.Uint64(img[24:]); e != want {
		return fmt.Errorf("e_entry=%#x, 期望 %#x", e, want)
	}
	// 入口的第一条指令必须是 4 字节对齐的合法 A64 指令 (低两位为 0)
	if w := binary.LittleEndian.Uint32(img[a64EntryOff:]); w&0x3 != 0 {
		return fmt.Errorf("入口指令未对齐: %#x", w)
	}
	return nil
}
