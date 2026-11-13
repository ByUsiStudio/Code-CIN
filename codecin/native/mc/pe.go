package mc

import "encoding/binary"

// PE32+ (x86-64 Windows) 写出器: 生成可被 Windows 直接执行的 .exe。
// 布局: [DOS 头][PE 头 + 可选头][节表][.text][.rdata][.data]
// 关闭 ASLR(DllCharacteristics=0), 图像固定装载在 0x140000000。

const (
	peBase        = 0x140000000
	peFileAlign   = 0x200
	peSectAlign   = 0x1000
	peTextRVA     = 0x1000
	peRdataRVA    = 0x2000
	peDataRVA     = 0x3000
	peHeadersSize = 0x200
)

type peImport struct {
	DLL   string
	Names []string
}

type peLayout struct {
	img        []byte
	textRVA    uint32
	dataRVA    uint32
	imports    map[string]uint32 // 函数名 -> IAT 槽 RVA
	entryPoint uint32
}

func alignUp(v, a uint32) uint32 { return (v + a - 1) / a * a }

// buildPE 生成 PE32+ 镜像。code 会被放进 .text, data 放进 .data。
func buildPE(code []byte, data []byte, imp peImport) (*peLayout, error) {
	rdata := buildRdata(imp, 0)

	rdataSize := alignUp(uint32(len(rdata)), peFileAlign)
	textSize := alignUp(uint32(len(code)), peFileAlign)
	dataSize := alignUp(uint32(len(data)), peFileAlign)
	if dataSize == 0 {
		dataSize = peFileAlign
	}

	imgSize := alignUp(peDataRVA+dataSize, peSectAlign)

	buf := make([]byte, peHeadersSize+textSize+rdataSize+dataSize)
	le := binary.LittleEndian

	// --- DOS 头 ---
	le.PutUint16(buf[0:], 0x5A4D)
	le.PutUint32(buf[0x3C:], 0x40)

	// --- PE 签名 + COFF ---
	copy(buf[0x40:], []byte{'P', 'E', 0, 0})
	coff := 0x44
	le.PutUint16(buf[coff:], 0x8664)          // Machine AMD64
	le.PutUint16(buf[coff+2:], 3)             // NumberOfSections
	le.PutUint16(buf[coff+16:], 240)          // SizeOfOptionalHeader
	le.PutUint16(buf[coff+18:], 0x0022)       // EXECUTABLE | LARGE_ADDRESS_AWARE

	opt := coff + 20 // 0x58
	le.PutUint16(buf[opt:], 0x20B)            // PE32+
	buf[opt+2] = 1                            // linker version
	buf[opt+3] = 0
	le.PutUint32(buf[opt+4:], textSize)       // SizeOfCode
	le.PutUint32(buf[opt+8:], rdataSize+dataSize) // SizeOfInitializedData
	le.PutUint32(buf[opt+16:], peTextRVA)     // AddressOfEntryPoint
	le.PutUint32(buf[opt+20:], peTextRVA)     // BaseOfCode
	le.PutUint64(buf[opt+24:], peBase)        // ImageBase
	le.PutUint32(buf[opt+32:], peSectAlign)   // SectionAlignment
	le.PutUint32(buf[opt+36:], peFileAlign)   // FileAlignment
	le.PutUint16(buf[opt+40:], 6)             // MajorOperatingSystemVersion
	le.PutUint16(buf[opt+48:], 6)             // MajorSubsystemVersion
	le.PutUint32(buf[opt+56:], imgSize)       // SizeOfImage
	le.PutUint32(buf[opt+60:], peHeadersSize) // SizeOfHeaders
	le.PutUint16(buf[opt+68:], 3)             // Subsystem: console
	le.PutUint16(buf[opt+70:], 0)             // DllCharacteristics: 无 ASLR
	le.PutUint64(buf[opt+72:], 0x100000)      // SizeOfStackReserve
	le.PutUint64(buf[opt+80:], 0x10000)       // SizeOfStackCommit
	le.PutUint64(buf[opt+88:], 0x100000)      // SizeOfHeapReserve
	le.PutUint64(buf[opt+96:], 0x10000)       // SizeOfHeapCommit
	le.PutUint32(buf[opt+108:], 16)           // NumberOfRvaAndSizes

	// 导入目录在数据目录数组的索引 1
	impDirOff := opt + 112 + 8
	if len(imp.Names) > 0 {
		le.PutUint32(buf[impDirOff:], peRdataRVA)
		le.PutUint32(buf[impDirOff+4:], uint32(len(rdata)))
	}

	// --- 节表 ---
	sec := opt + 240
	putSection := func(i int, name string, vsize, vaddr, rawSize, rawPtr uint32, chars uint32) {
		o := sec + i*40
		copy(buf[o:], name)
		le.PutUint32(buf[o+8:], vsize)
		le.PutUint32(buf[o+12:], vaddr)
		le.PutUint32(buf[o+16:], rawSize)
		le.PutUint32(buf[o+20:], rawPtr)
		le.PutUint32(buf[o+36:], chars)
	}
	putSection(0, ".text", textSize, peTextRVA, textSize, peHeadersSize, 0x60000020)                  // CODE|EXEC|READ
	putSection(1, ".rdata", rdataSize, peRdataRVA, rdataSize, peHeadersSize+textSize, 0xC0000040)    // INITIALIZED|READ|WRITE (装载器要写 IAT)
	putSection(2, ".data", dataSize, peDataRVA, dataSize, peHeadersSize+textSize+rdataSize, 0xC0000040) // INITIALIZED|READ|WRITE

	copy(buf[peHeadersSize:], code)
	copy(buf[peHeadersSize+textSize:], rdata)
	copy(buf[peHeadersSize+textSize+rdataSize:], data)

	return &peLayout{img: buf, textRVA: peTextRVA, dataRVA: peDataRVA,
		imports: rdataIATs(imp), entryPoint: peTextRVA}, nil
}

// rdataIATs 计算每个导入函数在 IAT 中的 RVA。
func rdataIATs(imp peImport) map[string]uint32 {
	// 布局: [IMPORT_DESCRIPTOR 1 个 + 终止][ILT][IAT][hint/name...][dll name]
	const descSize = 20
	const descTotal = descSize * 2
	iltBase := descTotal
	iatBase := iltBase + (len(imp.Names)+1)*8
	out := map[string]uint32{}
	for i, n := range imp.Names {
		out[n] = uint32(peRdataRVA + iatBase + i*8)
	}
	return out
}

// buildRdata 构造导入描述符 / ILT / IAT / 名称表。
func buildRdata(imp peImport, _ int) []byte {
	const descSize = 20
	descTotal := descSize * 2
	iltBase := descTotal
	iltCount := len(imp.Names) + 1
	iatBase := iltBase + iltCount*8
	hintBase := iatBase + iltCount*8

	// 先算 hint/name 区域
	type hn struct {
		off  int
		name string
	}
	var hns []hn
	cur := hintBase
	for _, n := range imp.Names {
		hns = append(hns, hn{cur, n})
		cur += 2 + len(n) + 1
		cur = (cur + 1) &^ 1
	}
	dllNameOff := cur
	total := dllNameOff + len(imp.DLL) + 1
	buf := make([]byte, total)
	le := binary.LittleEndian

	// Import descriptor
	le.PutUint32(buf[0:], uint32(peRdataRVA+iltBase))       // OriginalFirstThunk
	le.PutUint32(buf[12:], uint32(peRdataRVA+dllNameOff))   // Name
	le.PutUint32(buf[16:], uint32(peRdataRVA+iatBase))      // FirstThunk

	for i, h := range hns {
		le.PutUint64(buf[iltBase+i*8:], uint64(peRdataRVA+h.off))
		le.PutUint64(buf[iatBase+i*8:], uint64(peRdataRVA+h.off))
		le.PutUint16(buf[h.off:], 0) // hint
		copy(buf[h.off+2:], h.name)
		buf[h.off+2+len(h.name)] = 0
	}
	copy(buf[dllNameOff:], imp.DLL)
	buf[dllNameOff+len(imp.DLL)] = 0
	return buf
}
