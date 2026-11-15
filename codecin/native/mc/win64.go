package mc

import (
	"codecin-native/ir"
)

// Windows x86-64 (Win64 ABI) 后端。
//
// 指令编码、虚拟寄存器布局、VM 内存模型与 Linux 版完全一致 (共用
// x86_64.go 里的 encodeWith), 这里只负责三件事:
//
//  1. 声明 kernel32 导入表;
//  2. 构造 .data 初值 —— 寄存器组、标志字节、堆指针、静态数据;
//  3. 把 .text/.data/.rdata 交给 PE 写出器。
//
// ImageBase 固定 0x200000 且关闭 ASLR (见 pe.go), 因此 .data 落在 32 位
// 地址空间内, 编码器可以照旧用 `mov rax, [abs32]` 访问虚拟寄存器。

const (
	// winStdoutSlot: GetStdHandle(STD_OUTPUT_HANDLE) 的结果
	winStdoutSlot = dataBase + 0x490
	// winScratch: WriteFile 的 lpNumberOfBytesWritten
	winScratch = dataBase + 0x498
	// winScratch2: GetSystemTimeAsFileTime 的 FILETIME
	winScratch2 = dataBase + 0x4A0
)

// winImports 是 PE 后端唯一的导入清单 (代码与 IAT 必须一致)。
var winImports = peImport{DLL: "kernel32.dll", Names: []string{
	"GetStdHandle",             // 取标准输出句柄
	"WriteFile",                // 控制台输出 / 写文件
	"ExitProcess",              // HALT
	"VirtualAlloc",             // VM 内存
	"GetSystemTimeAsFileTime",  // SYS 15 TIME
	"GetTickCount64",           // SYS 91 TIMEMS
	"Sleep",                    // SYS 92 SLEEPMS
	"GetTempPathA",             // SYS 89 TEMPDIR
	// 文件 / 目录 (见 win64_fileio.go)
	"CreateFileA",              // 51/52/53/56/88 打开文件
	"ReadFile",                 // SYS 51 FILEREAD
	"CloseHandle",              // 关闭上面打开的句柄
	"DeleteFileA",              // SYS 55 FILEDELETE
	"GetFileAttributesA",       // SYS 54/87 存在性 / 是否目录
	"CreateDirectoryA",         // SYS 57 MKDIR
	"RemoveDirectoryA",         // SYS 86 DIRREMOVE
	"MoveFileA",                // SYS 85 FILEMOVE
	"GetFileSizeEx",            // SYS 56 FILESIZE
	"GetFileTime",              // SYS 88 FILEMTIME
	"GetCurrentDirectoryA",     // SYS 66 CWD
	"SetCurrentDirectoryA",     // SYS 90 CHDIR
}}

// dataOffWin 把宿主虚地址换算成 .data 内的偏移。
// .data 起始即 RVA 0x202000, 也就是 dataAddr。
func dataOffWin(va int64) int { return int(va - dataAddr) }

// winImageData 构造 .data 初值与其虚拟大小。
//
// .data 内的分布 (偏移相对 dataAddr):
//
//	0x000  虚拟寄存器 R0..R32 (33*8 字节)
//	0x400  ZF/LF 标志字节
//	0x480  VM 堆顶指针 (dataBase+heapPtrOff)
//	0x488  xorshift 随机数状态
//	0x490  stdout 句柄
//	0x498  WriteFile 写回字节数
//	0x4A0  FILETIME 缓冲
//	0xE000 IR 静态数据 (dataBase)
func winImageData(p *ir.Program) ([]byte, uint32) {
	const staticBase = dataBase - dataAddr // 0xE000
	end := staticBase + 0x500
	for _, w := range p.DataWrites {
		if e := staticBase + w.Addr + len(w.Data); e > end {
			end = e
		}
	}
	vsize := alignUp(uint32(end), peSectAlign)
	data := make([]byte, end)
	for _, w := range p.DataWrites {
		copy(data[staticBase+w.Addr:], w.Data)
	}
	return data, vsize
}

// WrapPE 生成最终 .exe 字节。
func WrapPE(p *ir.Program) ([]byte, error) {
	data, vsize := winImageData(p)
	// IAT 槽 RVA 依赖 .data 的虚拟大小 (rdata 排在 .data 之后), 先算一遍。
	rdataRVA := uint32(peDataRVA) + alignUp(vsize, peSectAlign)
	iat := rdataIATs(winImports, rdataRVA)

	code, err := EncodeWin(p, peTextRVA, iat)
	if err != nil {
		return nil, err
	}
	l, err := buildPE(code, data, vsize, winImports)
	if err != nil {
		return nil, err
	}
	return l.img, nil
}
