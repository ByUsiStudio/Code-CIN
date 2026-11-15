package mc

import "fmt"

// aarch64_io.go: ARM64 后端的文件 / 目录 / 路径功能号。
//
// Linux 走 openat/read/write/close/newfstatat/mkdirat/unlinkat/renameat/
// getdents64, Darwin 走 open/read/write/close/stat64/mkdir/unlink/rename/
// getdirentries64。两者的结构体偏移不同, 由 statOff* 系列函数区分。

// 打开方式
const (
	a64ORdOnly = 0
	// Linux: O_WRONLY|O_CREAT|O_TRUNC / O_WRONLY|O_CREAT|O_APPEND
	a64TruncLin = 0x241
	a64AppLin   = 0x441
	// Darwin: O_WRONLY|O_CREAT|O_TRUNC / O_WRONLY|O_CREAT|O_APPEND
	a64TruncDar = 0x601
	a64AppDar   = 0x209
	a64DirLin   = 0x10000 // O_DIRECTORY
	a64DirDar   = 0x100000
)

func (a *a8) statModeOff() int32 {
	if a.isDarwin() {
		return 4
	}
	return 16
}

func (a *a8) statSizeOff() int32 {
	if a.isDarwin() {
		return 96
	}
	return 48
}

func (a *a8) statMtimeOff() int32 {
	if a.isDarwin() {
		return 48
	}
	return 88
}

// emitOpen: 输入 x21 = 路径 (VM 地址), 输出 x0 = fd (失败为负 errno)。
func (a *a8) emitOpen(flagsLin, flagsDar int) {
	a.addReg(9, a64VMB, a64T1)
	if a.isDarwin() {
		a.movReg(0, 9)
		a.movImm(1, uint64(flagsDar))
		a.movImm(2, 0x1A4) // 0644
		a.sys(5, 5)
		return
	}
	a.movImm(0, ^uint64(99)) // AT_FDCWD = -100
	a.movReg(1, 9)
	a.movImm(2, uint64(flagsLin))
	a.movImm(3, 0x1A4)
	a.sys(56, 0)
}

// emitStat: 输入 x21 = 路径, 结果写进 a64Tmp。返回"出错跳转"的待补位置。
func (a *a8) emitStat() int {
	// 路径先存到 a64T2: vmCon 会占用 x9, 直接用它会被覆盖。
	a.addReg(a64T2, a64VMB, a64T1)
	a.vmCon(a64Tmp)
	if a.isDarwin() {
		a.movReg(0, a64T2)
		a.movReg(1, 9)
		a.sys(0, 338) // stat64
		return a.bIfErr()
	}
	a.movImm(0, ^uint64(99)) // AT_FDCWD
	a.movReg(1, a64T2)
	a.movReg(2, 9)
	a.movz(3, 0, 0)
	a.sys(79, 0) // newfstatat
	return a.bIfErr()
}

// emitReadAll: x21 = 路径 -> R0 = 内容 (VM 地址, NUL 结尾) / 失败为空串。
// 读取上限 1 MiB, 超出部分截断。
func (a *a8) emitReadAll() {
	const cap = 1 << 20
	a.emitOpen(a64ORdOnly, a64ORdOnly)
	openErr := a.bIfErr()
	a.movReg(a64T2, 0) // fd

	a.movImm(0, cap+1)
	a.emitMalloc()
	a.loadRegTo(a64T3, 0) // buf

	a.movReg(0, a64T2)
	a.addReg(1, a64VMB, a64T3)
	a.movImm(2, cap)
	a.sys(63, 3) // read
	readErr := a.bIfErr()
	a.movReg(a64T4, 0) // n

	a.movReg(0, a64T2)
	a.sys(57, 6) // close

	a.addReg(9, a64VMB, a64T3)
	a.addReg(9, 9, a64T4)
	a.strb(31, 9, 0) // 结尾 NUL
	a.str(a64T3, a64Regs, 0)
	done := a.phB()

	a.patch(openErr, len(a.code), a64FixBCond)
	a.patch(readErr, len(a.code), a64FixBCond)
	a.emitCString("")
	a.patch(done, len(a.code), a64FixB)
}

// emitCwd: R0 = 当前工作目录。
func (a *a8) emitCwd() {
	a.movImm(0, 1024)
	a.emitMalloc()
	a.loadRegTo(a64T1, 0)
	a.addReg(0, a64VMB, a64T1)
	a.movImm(1, 1024)
	a.sys(17, 326)
	bad := a.bIfErr()
	a.str(a64T1, a64Regs, 0)
	done := a.phB()
	a.patch(bad, len(a.code), a64FixBCond)
	a.emitCString("")
	a.patch(done, len(a.code), a64FixB)
}

// sysFile 分发需要操作系统的文件 / 目录功能号。
func (a *a8) sysFile(id int64) error {
	switch id {
	case 51: // FILEREAD
		a.loadRegTo(a64T1, 0)
		a.emitReadAll()
	case 52, 53: // FILEWRITE / FILEAPPEND
		a.loadRegTo(a64T1, 0) // path
		a.loadRegTo(a64T2, 1) // data
		fl, fd := a64TruncLin, a64TruncDar
		if id == 53 {
			fl, fd = a64AppLin, a64AppDar
		}
		a.emitOpen(fl, fd)
		bad := a.bIfErr()
		a.movReg(a64T3, 0) // fd
		a.movReg(0, a64T2)
		a.emitStrlen() // x0 = len
		a.movReg(a64T4, 0)
		a.movReg(0, a64T3)
		a.addReg(1, a64VMB, a64T2)
		a.movReg(2, a64T4)
		a.sys(64, 4) // write
		a.movReg(0, a64T3)
		a.sys(57, 6) // close
		a.movz(9, 0, 0)
		a.str(9, a64Regs, 0)
		done := a.phB()
		a.patch(bad, len(a.code), a64FixBCond)
		a.movImm(9, ^uint64(0))
		a.str(9, a64Regs, 0)
		a.patch(done, len(a.code), a64FixB)
	case 54: // FILEEXISTS
		a.loadRegTo(a64T1, 0)
		bad := a.emitStat()
		a.movz(9, 1, 0)
		a.str(9, a64Regs, 0)
		done := a.phB()
		a.patch(bad, len(a.code), a64FixBCond)
		a.movz(9, 0, 0)
		a.str(9, a64Regs, 0)
		a.patch(done, len(a.code), a64FixB)
	case 55: // FILEDELETE
		a.loadRegTo(a64T1, 0)
		a.addReg(9, a64VMB, a64T1)
		if a.isDarwin() {
			a.movReg(0, 9)
			a.sys(10, 10) // unlink
		} else {
			a.movImm(0, ^uint64(99))
			a.movReg(1, 9)
			a.movz(2, 0, 0)
			a.sys(35, 0) // unlinkat
		}
		a.bIfOK()
	case 56: // FILESIZE
		a.loadRegTo(a64T1, 0)
		bad := a.emitStat()
		a.ldr(9, a64Regs, scrTmp+a.statSizeOff())
		a.str(9, a64Regs, 0)
		done := a.phB()
		a.patch(bad, len(a.code), a64FixBCond)
		a.movImm(9, ^uint64(0))
		a.str(9, a64Regs, 0)
		a.patch(done, len(a.code), a64FixB)
	case 57: // MKDIR
		a.loadRegTo(a64T1, 0)
		a.addReg(9, a64VMB, a64T1)
		a.movImm(10, 0x1ED) // 0755
		if a.isDarwin() {
			a.movReg(0, 9)
			a.movReg(1, 10)
			a.sys(0, 136)
		} else {
			a.movImm(0, ^uint64(99))
			a.movReg(1, 9)
			a.movImm(2, 0x1ED)
			a.sys(34, 0) // mkdirat
		}
		a.bIfOK()
	case 58: // DIRLIST
		a.emitDirList()
	case 66: // CWD
		a.emitCwd()
	case 84: // FILECOPY
		a.loadRegTo(a64T5, 0) // src
		a.loadRegTo(a64T6, 1) // dst
		a.movReg(a64T1, a64T5)
		a.emitReadAll() // R0 = 内容
		a.loadRegTo(a64T2, 0)
		a.movReg(0, a64T2)
		a.emitStrlen()
		a.movReg(a64T3, 0) // len
		a.movReg(a64T1, a64T6)
		a.emitOpen(a64TruncLin, a64TruncDar)
		bad := a.bIfErr()
		a.movReg(a64T4, 0)
		a.movReg(0, a64T4)
		a.addReg(1, a64VMB, a64T2)
		a.movReg(2, a64T3)
		a.sys(64, 4)
		a.movReg(0, a64T4)
		a.sys(57, 6)
		a.movz(9, 0, 0)
		a.str(9, a64Regs, 0)
		done := a.phB()
		a.patch(bad, len(a.code), a64FixBCond)
		a.movImm(9, ^uint64(0))
		a.str(9, a64Regs, 0)
		a.patch(done, len(a.code), a64FixB)
	case 85: // FILEMOVE
		a.loadRegTo(a64T1, 0)
		a.loadRegTo(a64T2, 1)
		a.addReg(9, a64VMB, a64T1)
		a.addReg(10, a64VMB, a64T2)
		if a.isDarwin() {
			a.movReg(0, 9)
			a.movReg(1, 10)
			a.sys(0, 128) // rename
		} else {
			a.movImm(0, ^uint64(99))
			a.movReg(1, 9)
			a.movImm(2, ^uint64(99))
			a.movReg(3, 10)
			a.sys(38, 0) // renameat
		}
		a.bIfOK()
	case 86: // DIRREMOVE
		a.loadRegTo(a64T1, 0)
		a.addReg(9, a64VMB, a64T1)
		if a.isDarwin() {
			a.movReg(0, 9)
			a.sys(0, 137) // rmdir
		} else {
			a.movImm(0, ^uint64(99))
			a.movReg(1, 9)
			a.movImm(2, 0x200) // AT_REMOVEDIR
			a.sys(35, 0)
		}
		a.bIfOK()
	case 87: // ISDIR
		a.loadRegTo(a64T1, 0)
		bad := a.emitStat()
		a.ldr(9, a64Regs, scrTmp+a.statModeOff())
		a.movImm(10, 0xF000)
		a.andReg(9, 9, 10)
		a.movImm(10, 0x4000)
		a.cmpReg(9, 10)
		a.cset(9, condEQ)
		a.str(9, a64Regs, 0)
		done := a.phB()
		a.patch(bad, len(a.code), a64FixBCond)
		a.movz(9, 0, 0)
		a.str(9, a64Regs, 0)
		a.patch(done, len(a.code), a64FixB)
	case 88: // FILEMTIME
		a.loadRegTo(a64T1, 0)
		bad := a.emitStat()
		a.ldr(9, a64Regs, scrTmp+a.statMtimeOff())
		a.str(9, a64Regs, 0)
		done := a.phB()
		a.patch(bad, len(a.code), a64FixBCond)
		a.movImm(9, ^uint64(0))
		a.str(9, a64Regs, 0)
		a.patch(done, len(a.code), a64FixB)
	case 90: // CHDIR
		a.loadRegTo(a64T1, 0)
		a.addReg(0, a64VMB, a64T1)
		a.sys(49, 12)
		a.bIfOK()
	default:
		return ErrUnsupportedARM{Op: fmt.Sprintf("SYS %d", id)}
	}
	return nil
}

// bIfOK 把"成功 0 / 失败 -1"的分支补齐 (返回值语义与引擎一致)。
func (a *a8) bIfOK() {
	bad := a.bIfErr()
	a.movz(9, 0, 0)
	a.str(9, a64Regs, 0)
	done := a.phB()
	a.patch(bad, len(a.code), a64FixBCond)
	a.movImm(9, ^uint64(0))
	a.str(9, a64Regs, 0)
	a.patch(done, len(a.code), a64FixB)
}

// emitDirList: SYS 58。条目用 '\n' 分隔 (末尾无换行), 目录名带尾部 '/',
// "." 与 ".." 跳过 —— 与引擎 / Windows 后端逐字对齐。
func (a *a8) emitDirList() {
	const (
		bufSize = 32768
		outSize = 65536
	)
	nameOff := int32(19) // Linux dirent64
	typeOff := int32(18)
	if a.isDarwin() {
		nameOff = 21 // Darwin dirent64 (d_namlen 占 2 字节)
		typeOff = 20
	}

	a.loadRegTo(a64T1, 0)
	a.emitOpen(a64DirLin, a64DirDar)
	bad := a.bIfErr()

	a.movReg(a64T1, 0) // fd

	// 输出缓冲
	a.movImm(0, outSize)
	a.emitMalloc()
	a.loadRegTo(a64T2, 0) // out
	a.movz(a64T3, 0, 0)   // out 长度

	// dirent 缓冲
	a.movImm(0, bufSize)
	a.emitMalloc()
	a.loadRegTo(a64T4, 0) // dirent 缓冲

	// 结尾先写 NUL
	a.addReg(9, a64VMB, a64T2)
	a.strb(31, 9, 0)

	readLoop := len(a.code)
	a.movReg(0, a64T1)
	a.addReg(1, a64VMB, a64T4)
	a.movImm(2, bufSize)
	if a.isDarwin() {
		a.sys(0, 344) // getdirentries64(fd, buf, n, &base)
	} else {
		a.sys(61, 0) // getdents64
	}
	readErr := a.bIfErr()
	a.movReg(a64T5, 0) // n
	a.cbzAt(a64T5, 0)
	readDone := len(a.code) - 4

	a.addReg(a64T6, a64VMB, a64T4) // 游标
	a.addReg(15, a64T6, a64T5)     // 结束
	entryLoop := len(a.code)
	a.cmpReg(a64T6, 15)
	nextRead := a.phBC(condHS)

	// 输出缓冲快满了就直接收尾, 宁可少列几条也不越界写。
	a.movImm(9, outSize-256)
	a.cmpReg(a64T3, 9)
	full := a.phBC(condHS)

	// d_name
	a.addImm(9, a64T6, uint32(nameOff))
	a.ldrb(10, a64T6, typeOff)

	// 跳过 "." 与 ".."
	a.ldrb(11, 9, 0)
	a.movz(12, '.', 0)
	a.cmpReg(11, 12)
	notDot := a.phBC(condNE)
	a.ldrb(11, 9, 1)
	a.cbzWAt(11, 0)
	skipOne := len(a.code) - 4
	a.movz(12, '.', 0)
	a.cmpReg(11, 12)
	skipTwo := a.phBC(condEQ)

	a.patch(notDot, len(a.code), a64FixBCond)
	// 条目之间补分隔符
	a.cbzAt(a64T3, 0)
	noSep := len(a.code) - 4
	a.addReg(13, a64VMB, a64T2)
	a.addReg(13, 13, a64T3)
	a.movz(14, '\n', 0)
	a.strb(14, 13, 0)
	a.addImm(a64T3, a64T3, 1)
	a.patch(noSep, len(a.code), a64FixCB)

	// 拷贝名字
	copyName := len(a.code)
	a.ldrb(11, 9, 0)
	a.cbzWAt(11, 0)
	nameDone := len(a.code) - 4
	a.addReg(13, a64VMB, a64T2)
	a.addReg(13, 13, a64T3)
	a.strb(11, 13, 0)
	a.addImm(a64T3, a64T3, 1)
	a.addImm(9, 9, 1)
	back := len(a.code)
	a.bAt(copyName - back)
	a.patch(nameDone, len(a.code), a64FixCB)

	// 目录补 '/'
	a.movz(14, 4, 0) // DT_DIR
	a.cmpReg(10, 14)
	noSlash := a.phBC(condNE)
	a.addReg(13, a64VMB, a64T2)
	a.addReg(13, 13, a64T3)
	a.movz(14, '/', 0)
	a.strb(14, 13, 0)
	a.addImm(a64T3, a64T3, 1)
	a.patch(noSlash, len(a.code), a64FixBCond)

	// 结尾 NUL + 下一个条目
	a.addReg(13, a64VMB, a64T2)
	a.addReg(13, 13, a64T3)
	a.strb(31, 13, 0)

	a.patch(skipOne, len(a.code), a64FixCB)
	a.patch(skipTwo, len(a.code), a64FixBCond)

	a.ldrhU(9, a64T6, 16) // d_reclen
	a.cbzAt(9, 0)
	zeroRec := len(a.code) - 4
	a.addReg(a64T6, a64T6, 9)
	back2 := len(a.code)
	a.bAt(entryLoop - back2)
	a.patch(zeroRec, len(a.code), a64FixCB) // 防御: reclen=0 时结束

	a.patch(nextRead, len(a.code), a64FixBCond)
	back3 := len(a.code)
	a.bAt(readLoop - back3)
	a.patch(readDone, len(a.code), a64FixCB)
	a.patch(readErr, len(a.code), a64FixBCond)

	closeLbl := len(a.code)
	a.movReg(0, a64T1)
	a.sys(57, 6) // close
	a.str(a64T2, a64Regs, 0)
	a.patch(full, closeLbl, a64FixBCond)
	done := a.phB()

	a.patch(bad, len(a.code), a64FixBCond)
	a.emitCString("")
	a.patch(done, len(a.code), a64FixB)
}

// sysPath 分发不碰操作系统的路径字符串功能号。
func (a *a8) sysPath(id int64) error {
	switch id {
	case 80: // PATHJOIN(dir, name)
		a.loadRegTo(a64T1, 0)
		a.loadRegTo(a64T2, 1)
		a.movReg(0, a64T1)
		a.emitStrlen()
		a.movReg(a64T3, 0)
		a.movReg(0, a64T2)
		a.emitStrlen()
		a.movReg(a64T4, 0)
		a.addReg(0, a64T3, a64T4)
		a.addImm(0, 0, 2)
		a.emitMalloc()
		a.loadRegTo(a64T5, 0)
		a.addReg(1, a64VMB, a64T1)
		a.addReg(2, a64VMB, a64T5)
		a.emitCopyZ()
		// 目录末尾已有 '/' 就不再补
		a.subImm(2, 2, 1)
		a.subImm(11, 2, 1)
		a.ldrb(9, 11, 0)
		a.movz(10, '/', 0)
		a.cmpReg(9, 10)
		hasSlash := a.phBC(condEQ)
		a.movz(10, '/', 0)
		a.strb(10, 2, 0)
		a.addImm(2, 2, 1)
		a.patch(hasSlash, len(a.code), a64FixBCond)
		a.addReg(1, a64VMB, a64T2)
		a.emitCopyZ()
		a.str(a64T5, a64Regs, 0)
	case 81: // PATHBASENAME
		a.loadRegTo(a64T1, 0)
		a.movReg(0, a64T1)
		a.emitStrlen()
		a.movReg(a64T2, 0)
		a.addReg(1, a64VMB, a64T1)
		a.addReg(2, 1, a64T2)
		// 去掉尾部 '/'
		trail := len(a.code)
		a.cmpReg(1, 2)
		trailDone := a.phBC(condHS)
		a.subImm(11, 2, 1)
		a.ldrb(9, 11, 0)
		a.movz(10, '/', 0)
		a.cmpReg(9, 10)
		trailStop := a.phBC(condNE)
		a.subImm(2, 2, 1)
		back := len(a.code)
		a.bAt(trail - back)
		a.patch(trailDone, len(a.code), a64FixBCond)
		a.patch(trailStop, len(a.code), a64FixBCond)
		// 从后往前找 '/', 找到时 x3 指向 '/' 本身
		a.movReg(3, 2)
		scan := len(a.code)
		a.cmpReg(3, 1)
		scanDone := a.phBC(condLS)
		a.subImm(3, 3, 1)
		a.ldrb(9, 3, 0)
		a.movz(10, '/', 0)
		a.cmpReg(9, 10)
		scanHit := a.phBC(condEQ)
		back2 := len(a.code)
		a.bAt(scan - back2)
		a.patch(scanHit, len(a.code), a64FixBCond)
		a.addImm(3, 3, 1) // 跳过 '/'
		a.patch(scanDone, len(a.code), a64FixBCond)
		// 结果 = [x3, x2)
		a.subReg(a64T3, 2, 3)
		a.movReg(0, a64T3)
		a.addImm(0, 0, 1)
		a.emitMalloc()
		a.loadRegTo(a64T4, 0)
		a.addReg(1, 1, 3)
		a.addReg(2, a64VMB, a64T4)
		a.movReg(3, a64T3)
		a.emitCopyN()
		a.strb(31, 2, 0)
		a.str(a64T4, a64Regs, 0)
	case 83: // PATHABS
		a.loadRegTo(a64T1, 0)
		a.addReg(9, a64VMB, a64T1)
		a.ldrb(9, 9, 0)
		a.movz(10, '/', 0)
		a.cmpReg(9, 10)
		isAbs := a.phBC(condEQ)
		// 相对路径: cwd + '/' + path
		a.emitCwd()
		a.loadRegTo(a64T2, 0) // cwd
		a.movReg(0, a64T2)
		a.emitStrlen()
		a.movReg(a64T3, 0)
		a.movReg(0, a64T1)
		a.emitStrlen()
		a.movReg(a64T4, 0)
		a.addReg(0, a64T3, a64T4)
		a.addImm(0, 0, 2)
		a.emitMalloc()
		a.loadRegTo(a64T5, 0)
		a.addReg(1, a64VMB, a64T2)
		a.addReg(2, a64VMB, a64T5)
		a.emitCopyZ()
		a.subImm(2, 2, 1)
		a.movz(10, '/', 0)
		a.strb(10, 2, 0)
		a.addImm(2, 2, 1)
		a.addReg(1, a64VMB, a64T1)
		a.emitCopyZ()
		a.str(a64T5, a64Regs, 0)
		done := a.phB()
		// 已经是绝对路径: 原样复制
		a.patch(isAbs, len(a.code), a64FixBCond)
		a.movReg(0, a64T1)
		a.emitStrlen()
		a.movReg(a64T3, 0)
		a.movReg(0, a64T3)
		a.addImm(0, 0, 1)
		a.emitMalloc()
		a.loadRegTo(a64T5, 0)
		a.addReg(1, a64VMB, a64T1)
		a.addReg(2, a64VMB, a64T5)
		a.emitCopyZ()
		a.str(a64T5, a64Regs, 0)
		a.patch(done, len(a.code), a64FixB)
	case 82: // PATHDIRNAME
		a.loadRegTo(a64T1, 0)
		a.movReg(0, a64T1)
		a.emitStrlen()
		a.movReg(a64T2, 0)
		a.addReg(1, a64VMB, a64T1)
		a.addReg(2, 1, a64T2)
		// 去掉尾部 '/'
		trail := len(a.code)
		a.cmpReg(1, 2)
		trailDone := a.phBC(condHS)
		a.subImm(11, 2, 1)
		a.ldrb(9, 11, 0)
		a.movz(10, '/', 0)
		a.cmpReg(9, 10)
		trailStop := a.phBC(condNE)
		a.subImm(2, 2, 1)
		back := len(a.code)
		a.bAt(trail - back)
		a.patch(trailDone, len(a.code), a64FixBCond)
		a.patch(trailStop, len(a.code), a64FixBCond)
		// 找最后一个 '/'
		a.movReg(3, 2)
		scan := len(a.code)
		a.cmpReg(3, 1)
		scanDone := a.phBC(condLS)
		a.subImm(11, 3, 1)
		a.ldrb(9, 11, 0)
		a.movz(10, '/', 0)
		a.cmpReg(9, 10)
		scanHit := a.phBC(condEQ)
		a.subImm(3, 3, 1)
		back2 := len(a.code)
		a.bAt(scan - back2)
		a.patch(scanDone, len(a.code), a64FixBCond)
		a.patch(scanHit, len(a.code), a64FixBCond)
		// 长度 = x3 - x1 (不含 '/')
		a.subReg(a64T3, 3, 1)
		a.cmpImm(a64T3, 0)
		noDir := a.phBC(condGT)
		// 无 '/' -> "."
		a.emitCString(".")
		done := a.phB()
		a.patch(noDir, len(a.code), a64FixBCond)
		a.movReg(0, a64T3)
		a.addImm(0, 0, 1)
		a.emitMalloc()
		a.loadRegTo(a64T5, 0)
		a.addReg(1, a64VMB, a64T1)
		a.addReg(2, a64VMB, a64T5)
		a.emitCopyN()
		a.strb(31, 2, 0)
		a.str(a64T5, a64Regs, 0)
		a.patch(done, len(a.code), a64FixB)
	default:
		return ErrUnsupportedARM{Op: fmt.Sprintf("SYS %d", id)}
	}
	return nil
}
