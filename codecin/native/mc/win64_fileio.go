package mc

// Windows 后端的文件 / 目录能力 (SYS 51-57, 66, 85-90)。
//
// Linux 侧这些功能是一条条系统调用; Windows 侧改成 Win32 API, 参数按
// Win64 约定进 rcx/rdx/r8/r9, 第 5 个及以后落在 [rsp+0x20] 起的调用者栈帧上。
//
// 调用点栈空间有两条硬约束, 改这里的代码前先看一遍:
//
//  1. 函数序言只 `sub rsp, 0x28` (32 字节 shadow space + 8 字节对齐填充),
//     因此 [rsp+0x20] 是安全的, [rsp+0x28] 已经盖到返回地址 —— 参数超过 5 个
//     必须先自己 `sub rsp, 0x40` 再写栈参数。
//  2. 序言之后 rsp 是 16 字节对齐的, 临时收缩栈必须用 16 的倍数, 否则 `call`
//     时对齐破坏, SSE 指令会崩。
//
// 返回值与 Linux 版对齐: 成功 0, 失败 -1 (Linux 是 -errno, 调用方只判正负)。

const (
	// winScratch3: GetFileSizeEx 的 LARGE_INTEGER / GetFileTime 的 FILETIME
	winScratch3 = dataBase + 0x4A8
)

// Win32 常量 (只放这里用到的)。
const (
	winGenericRead    = 0x80000000
	winGenericWrite   = 0x40000000
	winFileAppendData = 0x00000004
	winShareAll       = 3 // FILE_SHARE_READ | FILE_SHARE_WRITE
	winCreateAlways   = 2 // CREATE_ALWAYS
	winOpenExisting   = 3 // OPEN_EXISTING
	winOpenAlways     = 4 // OPEN_ALWAYS
	winAttrNormal     = 0x80
	winInvalidHandle  = -1
	winInvalidAttr    = -1
	winAttrDirectory  = 0x10
	winFiletimePerSec = 10000000    // FILETIME 单位 100ns
	winFiletimeEpoch  = 11644473600 // 1601-01-01 到 1970-01-01 的秒数
)

// winSyscall 实现 Windows 独有的文件/目录功能号。
//
// handled=false 表示这个功能号不归本文件管 (交给 x86_64.go 里的共用实现)。
func (e *Encoder) winSyscall(nr int64) (handled bool, err error) {
	switch nr {
	case 51:
		return true, e.winFileRead()
	case 52:
		return true, e.winFileWrite(false)
	case 53:
		return true, e.winFileWrite(true)
	case 54:
		return true, e.winFileExists()
	case 55:
		return true, e.winFileDelete()
	case 56:
		return true, e.winFileSize()
	case 57:
		return true, e.winMkdir()
	case 58:
		return true, e.winDirList()
	case 66:
		return true, e.winCwd()
	case 84:
		return true, e.winFileCopy()
	case 85:
		return true, e.winFileMove()
	case 86:
		return true, e.winDirRemove()
	case 87:
		return true, e.winIsDir()
	case 88:
		return true, e.winFileMtime()
	case 90:
		return true, e.winChdir()
	}
	return false, nil
}

// winOkResult 把 eax (Win32 的 BOOL, 非零为成功) 转成 R0 = 成功 0 / 失败 -1。
func (e *Encoder) winOkResult() {
	e.raw(0x85, 0xC0)       // test eax, eax
	e.raw(0x0F, 0x94, 0xC0) // setz al
	e.raw(0x0F, 0xB6, 0xC0) // movzx eax, al
	e.raw(0xF7, 0xD8)       // neg eax
	e.raw(0x48, 0x98)       // cdqe
	e.storeReg(0)
}

// winCallRax1 调用只吃一个寄存器参数的导入函数, 参数取自 rax。
func (e *Encoder) winCallRax1(name string) error {
	e.raw(0x48, 0x89, 0xC1) // mov rcx, rax
	return e.callIAT(name)
}

// winCreateFile 打开/创建文件, 句柄留在 rax。
//
// CreateFileA 有 7 个参数, 后 3 个必须写在调用者栈帧上, 所以这里临时
// `sub rsp, 0x40` (16 的倍数, 保持 call 时对齐)。
func (e *Encoder) winCreateFile(pathReg int, access int64, disposition int32) error {
	e.raw(0x48, 0x83, 0xEC, 0x40) // sub rsp, 0x40
	e.vmAddr(pathReg, 0)          // rax = 路径宿主指针
	e.raw(0x48, 0x89, 0xC1)       // mov rcx, rax (lpFileName)
	e.raw(0x48, 0xBA)
	e.imm64(access) // mov rdx, access
	e.raw(0x41, 0xB8)
	e.imm32(winShareAll)    // r8d = 共享模式
	e.raw(0x45, 0x31, 0xC9) // xor r9d, r9d (安全属性 = NULL)
	e.raw(0x48, 0xC7, 0x44, 0x24, 0x20)
	e.imm32(disposition)
	e.raw(0x48, 0xC7, 0x44, 0x24, 0x28)
	e.imm32(winAttrNormal)
	e.raw(0x48, 0xC7, 0x44, 0x24, 0x30)
	e.imm32(0) // 模板文件 = NULL
	if err := e.callIAT("CreateFileA"); err != nil {
		return err
	}
	e.raw(0x48, 0x83, 0xC4, 0x40) // add rsp, 0x40
	return nil
}

// winFileRead: SYS 51 FILEREAD(path=R0) -> R0 = 文件内容。
//
// 缓冲区固定 64 KiB (含结尾 NUL), 与 Linux 侧的 65535 上限一致。
func (e *Encoder) winFileRead() error {
	e.loadReg(0)
	e.storeReg(7) // R7 = 路径

	e.raw(0x48, 0xC7, 0xC0)
	e.imm32(65536)
	e.storeReg(0)
	e.emitMalloc()
	e.loadReg(0)
	e.storeReg(8) // R8 = 缓冲区 VM 地址

	// 先把首字节清零: 打不开文件时也返回空串而不是垃圾。
	e.vmAddr(8, 0)
	e.raw(0xC6, 0x00, 0x00) // mov byte [rax], 0

	if err := e.winCreateFile(7, winGenericRead, winOpenExisting); err != nil {
		return err
	}
	e.raw(0x48, 0x83, 0xF8, 0xFF) // cmp rax, INVALID_HANDLE_VALUE
	e.raw(0x0F, 0x84)
	jFail := e.imm32Pos()
	e.storeReg(9) // R9 = 句柄

	e.loadReg(9)
	e.raw(0x48, 0x89, 0xC1) // rcx = hFile
	e.vmAddr(8, 0)
	e.raw(0x48, 0x89, 0xC2) // rdx = 缓冲区
	e.raw(0x41, 0xB8)
	e.imm32(65535) // r8d = 最多读多少
	e.raw(0x41, 0xB9)
	e.imm32(int32(winScratch)) // r9d = &bytesRead
	e.raw(0x48, 0xC7, 0x44, 0x24, 0x20)
	e.imm32(0) // lpOverlapped = NULL
	if err := e.callIAT("ReadFile"); err != nil {
		return err
	}

	// buf[bytesRead] = 0
	e.vmAddr(8, 0)
	e.raw(0x48, 0x89, 0xC1) // rcx = 缓冲区
	e.raw(0x8B, 0x14, 0x25)
	e.imm32(int32(winScratch))    // edx = bytesRead
	e.raw(0xC6, 0x04, 0x11, 0x00) // mov byte [rcx+rdx], 0

	e.loadReg(9)
	if err := e.winCallRax1("CloseHandle"); err != nil {
		return err
	}
	e.patchRel(jFail, len(e.code))

	e.loadReg(8)
	e.storeReg(0) // R0 = 缓冲区 VM 地址
	return nil
}

// winFileWrite: SYS 52/53 FILEWRITE / FILEAPPEND(path=R0, data=R1)。
//
// append 用 FILE_APPEND_DATA 打开: Win32 保证这类句柄的写入总是追加到末尾,
// 不用先 Seek 到 EOF。
func (e *Encoder) winFileWrite(appendMode bool) error {
	e.loadReg(0)
	e.storeReg(7) // R7 = 路径
	e.loadReg(1)
	e.storeReg(8) // R8 = 数据

	access, disp := int64(winGenericWrite), int32(winCreateAlways)
	if appendMode {
		access, disp = winFileAppendData, winOpenAlways
	}
	if err := e.winCreateFile(7, access, disp); err != nil {
		return err
	}
	e.raw(0x48, 0x83, 0xF8, 0xFF) // cmp rax, INVALID_HANDLE_VALUE
	e.raw(0x0F, 0x84)
	jFail := e.imm32Pos()
	e.storeReg(9) // R9 = 句柄

	e.loadReg(8)
	e.emitStrlen() // rax = 长度 (破坏 rcx/rdx)
	e.storeReg(10)

	e.loadReg(9)
	e.raw(0x48, 0x89, 0xC1) // rcx = hFile
	e.vmAddr(8, 0)
	e.raw(0x48, 0x89, 0xC2) // rdx = 数据
	e.loadReg(10)
	e.raw(0x49, 0x89, 0xC0) // r8 = 长度
	e.raw(0x41, 0xB9)
	e.imm32(int32(winScratch)) // r9d = &written
	e.raw(0x48, 0xC7, 0x44, 0x24, 0x20)
	e.imm32(0)
	if err := e.callIAT("WriteFile"); err != nil {
		return err
	}
	e.loadReg(9)
	if err := e.winCallRax1("CloseHandle"); err != nil {
		return err
	}

	e.patchRel(jFail, len(e.code))
	e.raw(0x48, 0x31, 0xC0) // xor rax, rax
	e.storeReg(0)
	return nil
}

// winFileExists: SYS 54 FILEEXISTS(path=R0) -> 0/1。
func (e *Encoder) winFileExists() error {
	e.vmAddr(0, 0)
	if err := e.winCallRax1("GetFileAttributesA"); err != nil {
		return err
	}
	e.raw(0x3D)
	e.imm32(winInvalidAttr) // cmp eax, INVALID_FILE_ATTRIBUTES
	e.raw(0x0F, 0x95, 0xC0) // setne al
	e.raw(0x0F, 0xB6, 0xC0) // movzx eax, al
	e.storeReg(0)
	return nil
}

// winFileDelete: SYS 55 FILEDELETE(path=R0)。
func (e *Encoder) winFileDelete() error {
	e.vmAddr(0, 0)
	if err := e.winCallRax1("DeleteFileA"); err != nil {
		return err
	}
	e.winOkResult()
	return nil
}

// winFileSize: SYS 56 FILESIZE(path=R0) -> 字节数, 打不开时 -1。
func (e *Encoder) winFileSize() error {
	e.loadReg(0)
	e.storeReg(7)
	if err := e.winCreateFile(7, winGenericRead, winOpenExisting); err != nil {
		return err
	}
	e.raw(0x48, 0x83, 0xF8, 0xFF) // cmp rax, -1
	e.raw(0x0F, 0x84)
	jFail := e.imm32Pos()
	e.storeReg(9)

	e.loadReg(9)
	e.raw(0x48, 0x89, 0xC1) // rcx = hFile
	e.raw(0xBA)
	e.imm32(int32(winScratch3)) // edx = &li (零扩展即绝对地址)
	if err := e.callIAT("GetFileSizeEx"); err != nil {
		return err
	}
	e.loadReg(9)
	if err := e.winCallRax1("CloseHandle"); err != nil {
		return err
	}

	e.raw(0x48, 0x8B, 0x04, 0x25)
	e.imm32(int32(winScratch3)) // rax = li.QuadPart
	e.raw(0xE9)
	jDone := e.imm32Pos()

	e.patchRel(jFail, len(e.code))
	e.raw(0x48, 0xB8)
	e.imm64(-1) // 打不开: -1

	e.patchRel(jDone, len(e.code))
	e.storeReg(0)
	return nil
}

// winMkdir: SYS 57 MKDIR(path=R0)。
func (e *Encoder) winMkdir() error {
	e.vmAddr(0, 0)
	e.raw(0x48, 0x89, 0xC1) // rcx = 路径
	e.raw(0x31, 0xD2)       // xor edx, edx (安全属性 = NULL)
	if err := e.callIAT("CreateDirectoryA"); err != nil {
		return err
	}
	e.winOkResult()
	return nil
}

// winCwd: SYS 66 CWD -> R0 = 当前目录。
func (e *Encoder) winCwd() error {
	const size = 4096
	e.raw(0x48, 0xC7, 0xC0)
	e.imm32(size)
	e.storeReg(0)
	e.emitMalloc()
	e.loadReg(0)
	e.storeReg(8) // R8 = 缓冲区

	e.raw(0xB9)
	e.imm32(size) // ecx = 缓冲区长度
	e.vmAddr(8, 0)
	e.raw(0x48, 0x89, 0xC2) // rdx = 缓冲区
	if err := e.callIAT("GetCurrentDirectoryA"); err != nil {
		return err
	}
	e.loadReg(8)
	e.storeReg(0)
	return nil
}

// winFileMove: SYS 85 FILEMOVE(src=R0, dst=R1)。
func (e *Encoder) winFileMove() error {
	e.vmAddr(0, 0)
	e.raw(0x48, 0x89, 0xC1) // rcx = src
	e.vmAddr(1, 0)
	e.raw(0x48, 0x89, 0xC2) // rdx = dst
	if err := e.callIAT("MoveFileA"); err != nil {
		return err
	}
	e.winOkResult()
	return nil
}

// winDirRemove: SYS 86 DIRREMOVE(path=R0)。
func (e *Encoder) winDirRemove() error {
	e.vmAddr(0, 0)
	if err := e.winCallRax1("RemoveDirectoryA"); err != nil {
		return err
	}
	e.winOkResult()
	return nil
}

// winIsDir: SYS 87 ISDIR(path=R0) -> 0/1。
func (e *Encoder) winIsDir() error {
	e.vmAddr(0, 0)
	if err := e.winCallRax1("GetFileAttributesA"); err != nil {
		return err
	}
	e.raw(0x3D)
	e.imm32(winInvalidAttr) // cmp eax, INVALID_FILE_ATTRIBUTES
	e.raw(0x0F, 0x84)
	jFail := e.imm32Pos()
	e.raw(0xA8, winAttrDirectory) // test al, FILE_ATTRIBUTE_DIRECTORY
	e.raw(0x0F, 0x95, 0xC0)       // setne al
	e.raw(0x0F, 0xB6, 0xC0)       // movzx eax, al
	e.raw(0xE9)
	jDone := e.imm32Pos()

	e.patchRel(jFail, len(e.code))
	e.raw(0x31, 0xC0) // xor eax, eax (不存在 -> 不是目录)

	e.patchRel(jDone, len(e.code))
	e.storeReg(0)
	return nil
}

// winFileMtime: SYS 88 FILEMTIME(path=R0) -> 修改时间 (Unix 秒)。
//
// FILETIME 是「1601-01-01 起的 100ns 数」, 换算成 Unix 秒要整除 1e7 再减
// 11644473600 (1601 到 1970 的秒数)。
func (e *Encoder) winFileMtime() error {
	e.loadReg(0)
	e.storeReg(7)
	if err := e.winCreateFile(7, winGenericRead, winOpenExisting); err != nil {
		return err
	}
	e.raw(0x48, 0x83, 0xF8, 0xFF) // cmp rax, -1
	e.raw(0x0F, 0x84)
	jFail := e.imm32Pos()
	e.storeReg(9)

	e.loadReg(9)
	e.raw(0x48, 0x89, 0xC1) // rcx = hFile
	e.raw(0x31, 0xD2)       // xor edx, edx (创建时间 = NULL)
	e.raw(0x45, 0x31, 0xC0) // xor r8d, r8d (访问时间 = NULL)
	e.raw(0x49, 0xC7, 0xC1)
	e.imm32(int32(winScratch3)) // r9 = &ft
	if err := e.callIAT("GetFileTime"); err != nil {
		return err
	}
	e.loadReg(9)
	if err := e.winCallRax1("CloseHandle"); err != nil {
		return err
	}

	e.raw(0x48, 0x8B, 0x04, 0x25)
	e.imm32(int32(winScratch3)) // rax = ft
	e.raw(0x31, 0xD2)           // xor edx, edx
	e.raw(0x48, 0xB9)
	e.imm64(winFiletimePerSec) // rcx = 1e7
	e.raw(0x48, 0xF7, 0xF1)    // div rcx
	e.raw(0x48, 0xB9)
	e.imm64(winFiletimeEpoch) // rcx = 11644473600
	e.raw(0x48, 0x29, 0xC8)   // sub rax, rcx
	e.raw(0xE9)
	jDone := e.imm32Pos()

	e.patchRel(jFail, len(e.code))
	e.raw(0x48, 0x31, 0xC0) // xor rax, rax

	e.patchRel(jDone, len(e.code))
	e.storeReg(0)
	return nil
}

// winChdir: SYS 90 CHDIR(path=R0)。
func (e *Encoder) winChdir() error {
	e.vmAddr(0, 0)
	if err := e.winCallRax1("SetCurrentDirectoryA"); err != nil {
		return err
	}
	e.winOkResult()
	return nil
}

// winFileCopy: SYS 84 FILECOPY(src=R0, dst=R1)。
//
// 直接用 CopyFileA, 不自己开两个句柄搬字节 —— 大文件也不必在 VM 里过一遍。
func (e *Encoder) winFileCopy() error {
	e.vmAddr(0, 0)
	e.raw(0x48, 0x89, 0xC1) // rcx = src
	e.vmAddr(1, 0)
	e.raw(0x48, 0x89, 0xC2) // rdx = dst
	e.raw(0x45, 0x31, 0xC0) // xor r8d, r8d (bFailIfExists = FALSE)
	if err := e.callIAT("CopyFileA"); err != nil {
		return err
	}
	e.winOkResult()
	return nil
}

// winDirList: SYS 58 DIRLIST(path=R0) -> 换行分隔的条目, 失败为空串。
//
// 流程: 把路径拼成 "路径\*" 的通配串 -> FindFirstFileA / FindNextFileA 遍历
// -> 每条的名字从 WIN32_FIND_DATAA 的偏移 44 (cFileName) 开始。R14 始终保存
// 「当前写入位置」这个 VM 地址, R13 记录已写入的条目数, 因此不需要跨 API
// 调用保活 rdi/rsi 之外的寄存器。
//
// 输出格式与引擎的 dir_list 对齐: 条目之间用 '\n' 分隔 (末尾不带换行),
// "." / ".." 不计入, 目录名带尾部 '/'。
func (e *Encoder) winDirList() error {
	const (
		findDataSize = 320 // sizeof(WIN32_FIND_DATAA)
		outSize      = 65536
		nameOff      = 44 // offsetof(WIN32_FIND_DATAA, cFileName)
	)

	e.loadReg(0)
	e.storeReg(7) // R7 = 路径

	// ---- 构造通配串: 路径 + "\*" + NUL ----
	e.loadReg(7)
	e.emitStrlen() // rax = 路径长度
	e.storeReg(9)
	e.raw(0x48, 0x83, 0xC0, 0x03) // add rax, 3
	e.storeReg(0)
	e.emitMalloc()
	e.loadReg(0)
	e.storeReg(10) // R10 = 通配串

	e.vmAddr(10, 0)
	e.raw(0x48, 0x89, 0xC7) // rdi = 通配串
	e.vmAddr(7, 0)
	e.raw(0x48, 0x89, 0xC6) // rsi = 路径
	e.raw(0x48, 0x8B, 0x0C, 0x25)
	e.imm32(regAddr(9))           // rcx = 路径长度
	e.raw(0xF3, 0xA4)             // rep movsb
	e.raw(0xC6, 0x07, 0x5C)       // mov byte [rdi], '\'
	e.raw(0xC6, 0x47, 0x01, 0x2A) // mov byte [rdi+1], '*'
	e.raw(0xC6, 0x47, 0x02, 0x00) // mov byte [rdi+2], 0

	// ---- FindFirstFileA ----
	e.raw(0x48, 0xC7, 0xC0)
	e.imm32(findDataSize)
	e.storeReg(0)
	e.emitMalloc()
	e.loadReg(0)
	e.storeReg(11) // R11 = WIN32_FIND_DATAA

	e.vmAddr(10, 0)
	e.raw(0x48, 0x89, 0xC1) // rcx = 通配串
	e.vmAddr(11, 0)
	e.raw(0x48, 0x89, 0xC2) // rdx = 查找数据
	if err := e.callIAT("FindFirstFileA"); err != nil {
		return err
	}
	e.storeReg(12) // R12 = 查找句柄

	// ---- 输出缓冲区 (先落一个 NUL: 打不开目录时返回空串) ----
	e.raw(0x48, 0xC7, 0xC0)
	e.imm32(outSize)
	e.storeReg(0)
	e.emitMalloc()
	e.loadReg(0)
	e.storeReg(8)   // R8 = 输出缓冲区起点
	e.storeReg(14)  // R14 = 写入游标 (VM 地址)
	e.movImm(13, 0) // R13 = 已写入条目数
	e.vmAddr(14, 0)
	e.raw(0xC6, 0x00, 0x00) // mov byte [rax], 0

	e.loadReg(12)
	e.raw(0x48, 0x83, 0xF8, 0xFF) // cmp rax, INVALID_HANDLE_VALUE
	e.raw(0x0F, 0x84)
	jDone := e.imm32Pos()

	// ---- 每轮: 处理当前 findData, 再取下一个 ----
	entry := len(e.code)
	e.vmAddr(11, nameOff)
	e.raw(0x48, 0x89, 0xC6) // rsi = cFileName

	// 跳过 "." 与 ".."
	e.raw(0x8A, 0x06) // mov al, [rsi]
	e.raw(0x3C, 0x2E) // cmp al, '.'
	e.raw(0x0F, 0x85)
	jAppend := e.imm32Pos()
	e.raw(0x8A, 0x46, 0x01) // mov al, [rsi+1]
	e.raw(0x84, 0xC0)       // test al, al
	e.raw(0x0F, 0x84)
	jAdvance := e.imm32Pos()
	e.raw(0x3C, 0x2E) // cmp al, '.'
	e.raw(0x0F, 0x84)
	jAdvance2 := e.imm32Pos()

	e.patchRel(jAppend, len(e.code))

	// 非首条先补分隔换行
	e.raw(0x48, 0x83, 0x3C, 0x25) // cmp qword [R13], 0
	e.imm32(regAddr(13))
	e.raw(0x00)
	e.raw(0x0F, 0x84)
	jNoSep := e.imm32Pos()
	e.vmAddr(14, 0)
	e.raw(0xC6, 0x00, 0x0A) // mov byte [rax], '\n'
	e.raw(0x48, 0xFF, 0x04, 0x25)
	e.imm32(regAddr(14))
	e.patchRel(jNoSep, len(e.code))

	// 追加名字: 不能把字符放在 al —— vmAddr 会覆盖 rax。
	copyName := len(e.code)
	e.raw(0x8A, 0x16) // mov dl, [rsi]
	e.raw(0x84, 0xD2) // test dl, dl
	e.raw(0x0F, 0x84)
	jNameEnd := e.imm32Pos()
	e.vmAddr(14, 0)
	e.raw(0x88, 0x10) // mov [rax], dl
	e.raw(0x48, 0xFF, 0x04, 0x25)
	e.imm32(regAddr(14))
	e.raw(0x48, 0xFF, 0xC6) // inc rsi
	e.raw(0xE9)
	e.imm32(int32(copyName - (len(e.code) + 4)))
	e.patchRel(jNameEnd, len(e.code))

	// 目录补尾部 '/'
	e.vmAddr(11, 0)
	e.raw(0x8B, 0x00) // mov eax, [rax] (dwFileAttributes)
	e.raw(0xA8, winAttrDirectory)
	e.raw(0x0F, 0x84)
	jNoSlash := e.imm32Pos()
	e.vmAddr(14, 0)
	e.raw(0xC6, 0x00, 0x2F) // mov byte [rax], '/'
	e.raw(0x48, 0xFF, 0x04, 0x25)
	e.imm32(regAddr(14))
	e.patchRel(jNoSlash, len(e.code))
	e.raw(0x48, 0xFF, 0x04, 0x25)
	e.imm32(regAddr(13)) // 条目数 +1

	// ---- FindNextFileA ----
	adv := len(e.code)
	e.patchRel(jAdvance, adv)
	e.patchRel(jAdvance2, adv)
	e.loadReg(12)
	e.raw(0x48, 0x89, 0xC1) // rcx = 查找句柄
	e.vmAddr(11, 0)
	e.raw(0x48, 0x89, 0xC2) // rdx = 查找数据
	if err := e.callIAT("FindNextFileA"); err != nil {
		return err
	}
	e.raw(0x85, 0xC0) // test eax, eax
	e.raw(0x0F, 0x85)
	e.imm32(int32(entry - (len(e.code) + 4))) // 还有下一个 -> 回到 entry

	e.loadReg(12)
	if err := e.winCallRax1("FindClose"); err != nil {
		return err
	}

	e.patchRel(jDone, len(e.code))
	e.vmAddr(14, 0)
	e.raw(0xC6, 0x00, 0x00) // 结尾 NUL
	e.loadReg(8)
	e.storeReg(0)
	return nil
}
