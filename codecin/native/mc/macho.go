package mc

import (
	"crypto/sha256"
	"encoding/binary"
	"fmt"

	"codecin-native/ir"
)

const machIdent = "__codecin_exec"

func machAlign(v, a uint64) uint64 { return (v + a - 1) / a * a }

// macho.go: macOS (arm64) 的容器。
//
// 布局按 arm64 的 16 KiB 页对齐:
//
//	[__TEXT: mach 头 + 装载命令 + 代码][__DATA: 静态数据][__LINKEDIT: 签名]
//
// Apple Silicon 上的可执行文件必须带代码签名 (至少是 ad-hoc), 否则内核在
// exec 时直接杀掉进程。这里自己生成 ad-hoc 的 CodeDirectory + SuperBlob,
// 不依赖 macOS 上的 codesign 工具。
const (
	machPageSize  = 0x4000
	machTextVAddr = 0x100000000
	machTextSize  = 0x10000 // 固定的 __TEXT 尺寸: 让 __DATA 的地址可编译期确定
	machDataVAddr = machTextVAddr + machTextSize
	machCodeMax   = machTextSize - 0x8000 // 装载命令留足余量
)

// macho 段的读写权限
const (
	protRead    = 1
	protWrite   = 2
	protExecute = 4
)

// WrapMachO 把 IR 编译成 macOS arm64 的可执行文件。
func WrapMachO(p *ir.Program) ([]byte, error) {
	code, err := EncodeAArch64Darwin(p, machDataVAddr)
	if err != nil {
		return nil, err
	}
	if len(code) > machCodeMax {
		return nil, fmt.Errorf("macho: 代码段 %d 字节超出上限 %d", len(code), machCodeMax)
	}

	dataSize := uint64(0x1000)
	for _, w := range p.DataWrites {
		if e := uint64(w.Addr) + uint64(len(w.Data)); e > dataSize {
			dataSize = e
		}
	}
	dataFileSize := machAlign(dataSize, machPageSize)

	// 装载命令
	const (
		dylib    = "/usr/lib/dyld"
		pagezero = 72
		segFull  = 72 + 80 // 带一个节
		segBare  = 72
		mainSize = 24
		buildVer = 24
		sigCmd   = 16
	)
	dylibSize := machAlign(uint64(12+len(dylib)+1), 8)
	ncmds := 8
	cmdsSize := uint64(pagezero) + segFull + segFull + segBare +
		mainSize + dylibSize + buildVer + sigCmd

	codeOff := machAlign(32+cmdsSize, 16)
	if codeOff+uint64(len(code)) > machTextSize {
		return nil, fmt.Errorf("macho: 代码超出 __TEXT (%d + %d > %d)",
			codeOff, len(code), machTextSize)
	}
	dataOff := uint64(machTextSize)
	sigOff := dataOff + dataFileSize

	body := make([]byte, sigOff)
	copy(body[codeOff:], code)
	for _, w := range p.DataWrites {
		copy(body[dataOff+uint64(w.Addr):], w.Data)
	}

	sig := buildAdhoc(body, sigOff)
	sigSize := machAlign(uint64(len(sig)), 16)

	le := binary.LittleEndian
	put := func(off uint64, v uint64) { le.PutUint64(body[off:], v) }
	put32 := func(off uint64, v uint32) { le.PutUint32(body[off:], v) }

	put32(0, 0xFEEDFACF) // MH_MAGIC_64
	put32(4, 0x0100000C) // CPU_TYPE_ARM64
	put32(8, 0)          // CPU_SUBTYPE_ARM64_ALL
	put32(12, 2)         // MH_EXECUTE
	put32(16, uint32(ncmds))
	put32(20, uint32(cmdsSize))
	put32(24, 0x00200001) // MH_PIE | MH_NOUNDEFS
	put32(28, 0)

	pos := uint64(32)
	seg := func(name string, vmaddr, vmsize, fileoff, filesize uint64, prot uint32, nsects uint32) {
		put32(pos, 0x19) // LC_SEGMENT_64
		put32(pos+4, 72+nsects*80)
		copy(body[pos+8:pos+24], name)
		put(pos+24, vmaddr)
		put(pos+32, vmsize)
		put(pos+40, fileoff)
		put(pos+48, filesize)
		put32(pos+56, prot)
		put32(pos+60, prot)
		put32(pos+64, nsects)
		put32(pos+68, 0)
		pos += 72 + uint64(nsects)*80
	}
	sect := func(off uint64, sname, segname string, addr, size, fileoff uint64, align, flags uint32) {
		copy(body[off:off+16], sname)
		copy(body[off+16:off+32], segname)
		put(off+32, addr)
		put(off+40, size)
		put32(off+48, uint32(fileoff))
		put32(off+52, align)
		put32(off+56, 0)
		put32(off+60, 0)
		put32(off+64, flags)
		put32(off+68, 0)
		put32(off+72, 0)
		put32(off+76, 0)
	}

	seg("__PAGEZERO", 0, machTextVAddr, 0, 0, 0, 0)
	textPos := pos
	seg("__TEXT", machTextVAddr, machTextSize, 0, machTextSize,
		protRead|protExecute, 1)
	sect(textPos+72, "__text", "__TEXT", machTextVAddr+codeOff, uint64(len(code)), codeOff, 2, 0x80000400)

	dataPos := pos
	seg("__DATA", machDataVAddr, dataFileSize, dataOff, dataFileSize,
		protRead|protWrite, 1)
	sect(dataPos+72, "__data", "__DATA", machDataVAddr, dataSize, dataOff, 3, 0)

	seg("__LINKEDIT", machDataVAddr+dataFileSize, machAlign(sigSize, machPageSize),
		sigOff, sigSize, protRead, 0)

	// LC_MAIN
	put32(pos, 0x80000028)
	put32(pos+4, mainSize)
	put(pos+8, codeOff)
	put(pos+16, 0)
	pos += mainSize

	// LC_LOAD_DYLINKER
	put32(pos, 0x80000007)
	put32(pos+4, uint32(dylibSize))
	copy(body[pos+12:pos+12+uint64(len(dylib))], dylib)
	pos += dylibSize

	// LC_BUILD_VERSION (macOS 11.0)
	put32(pos, 0x32)
	put32(pos+4, buildVer)
	put32(pos+8, 1)           // PLATFORM_MACOS
	put32(pos+12, 0x000B0000) // minos 11.0
	put32(pos+16, 0x000B0000) // sdk 11.0
	put32(pos+20, 0)
	pos += buildVer

	// LC_CODE_SIGNATURE
	put32(pos, 0x1D)
	put32(pos+4, sigCmd)
	put32(pos+8, uint32(sigOff))
	put32(pos+12, uint32(sigSize))
	pos += sigCmd

	if pos != 32+cmdsSize {
		return nil, fmt.Errorf("macho: 装载命令长度不符 (算 %d, 写 %d)", 32+cmdsSize, pos)
	}

	out := make([]byte, sigOff+sigSize)
	copy(out, body)
	copy(out[sigOff:], sig)
	return out, nil
}

// buildAdhoc 生成 ad-hoc 的嵌入签名 (SuperBlob + CodeDirectory),
// 哈希覆盖 body 的每一页 4 KiB。
func buildAdhoc(body []byte, codeLimit uint64) []byte {
	const (
		cdMagic   = 0xfade0c02
		blobMagic = 0xfade0cc0
		cdVersion = 0x20400
		hashSize  = 32
		pageSize  = 12 // log2(4096)
	)
	ident := machIdent + "\x00"
	const header = 88
	nCode := (codeLimit + 4095) / 4096
	hashOff := machAlign(uint64(header)+uint64(len(ident)), 32)
	cdLen := hashOff + nCode*hashSize

	cd := make([]byte, cdLen)
	le := binary.LittleEndian
	put32 := func(off int, v uint32) { le.PutUint32(cd[off:], v) }
	put64 := func(off int, v uint64) { le.PutUint64(cd[off:], v) }
	put32(0, cdMagic)
	put32(4, uint32(cdLen))
	put32(8, cdVersion)
	put32(12, 0x2) // CS_ADHOC
	put32(16, uint32(hashOff))
	put32(20, header)
	put32(24, 0) // nSpecialSlots
	put32(28, uint32(nCode))
	put32(32, uint32(codeLimit))
	cd[36] = hashSize
	cd[37] = 2 // SHA-256
	cd[38] = 0
	cd[39] = pageSize
	put32(40, 0)            // spare2
	put32(44, 0)            // scatterOffset
	put32(48, 0)            // teamOffset
	put32(52, 0)            // spare3
	put64(56, 0)            // codeLimit64
	put64(64, 0)            // execSegBase
	put64(72, machTextSize) // execSegLimit
	put64(80, 0)            // execSegFlags
	copy(cd[header:], ident)

	for i := uint64(0); i < nCode; i++ {
		start := i * 4096
		end := start + 4096
		if end > codeLimit {
			end = codeLimit
		}
		var sum [32]byte
		if int(end) <= len(body) {
			sum = sha256.Sum256(body[start:end])
		}
		copy(cd[hashOff+i*hashSize:], sum[:])
	}

	blob := make([]byte, 20+len(cd))
	le.PutUint32(blob[0:], blobMagic)
	le.PutUint32(blob[4:], uint32(len(blob)))
	le.PutUint32(blob[8:], 1)  // count
	le.PutUint32(blob[12:], 0) // CSSLOT_CODEDIRECTORY
	le.PutUint32(blob[16:], 20)
	copy(blob[20:], cd)
	return blob
}

// CheckMachO 校验产物是 arm64 Mach-O 且入口落在 __TEXT 内。
func CheckMachO(img []byte) error {
	if len(img) < 32 {
		return fmt.Errorf("macho: 文件过短")
	}
	le := binary.LittleEndian
	if le.Uint32(img) != 0xFEEDFACF {
		return fmt.Errorf("macho: 魔数不是 MH_MAGIC_64")
	}
	if le.Uint32(img[4:]) != 0x0100000C {
		return fmt.Errorf("macho: cputype 不是 arm64")
	}
	if le.Uint32(img[12:]) != 2 {
		return fmt.Errorf("macho: filetype 不是 MH_EXECUTE")
	}
	return nil
}
