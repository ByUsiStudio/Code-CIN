package mc

// x86_64_dirlist.go: Linux x86-64 的 SYS 58 (DIRLIST)。
//
// 与 AArch64 版算法一致: open(路径, O_DIRECTORY) -> getdents64 循环 -> 逐条
// 拷贝名字。输出格式对齐引擎: 条目之间 '\n' 分隔 (末尾不带换行), "." / ".."
// 不计入, 目录名带尾部 '/'。
//
// 寄存器分工 (整段内联展开, 不涉及函数调用约定):
//
//	rbx = 目录 fd
//	r12 = dirent 缓冲区 VM 地址
//	r13 = 输出长度
//	r14 = 输出缓冲区 VM 地址
//	r15 = VM 基址 (绝对不能碰)
//	rsi = 当前 dirent 条目起点, rdi = 缓冲区结尾, rdx = 名字游标
//	rax / rcx / r8..r11 作临时
//
// Linux 的 dirent64 布局 (与 AArch64 相同): d_reclen@16 (u16)、d_type@18、
// d_name@19。

// linDirList 生成 Linux x86-64 的目录列举代码。
func (e *Encoder) linDirList() {
	const (
		bufSize = 32768
		outSize = 65536
	)

	// ---- R7 = 路径 VM 地址; fd = open(path, O_RDONLY|O_DIRECTORY) ----
	e.loadReg(0)
	e.storeReg(7)
	e.vmAddr(7, 0)                                // rax = 路径 host 指针
	e.raw(0x48, 0x89, 0xC7)                       // mov rdi, rax
	e.raw(0xBE); e.imm32(0x10000)                 // mov esi, O_DIRECTORY
	e.raw(0xB8); e.imm32(2)                       // mov eax, SYS_open
	e.raw(0x0F, 0x05)                             // syscall
	e.raw(0x48, 0x85, 0xC0)                       // test rax, rax
	e.raw(0x0F, 0x88)                             // js 失败
	jFail := e.imm32Pos()
	e.raw(0x48, 0x89, 0xC3)                       // mov rbx, rax (fd)

	// ---- 输出缓冲区: 先落一个 NUL, 打开失败/空目录时就是空串 ----
	e.raw(0x48, 0xC7, 0xC0); e.imm32(outSize)     // mov rax, outSize
	e.storeReg(0)
	e.emitMalloc()
	e.loadReg(0)
	e.raw(0x49, 0x89, 0xC6)                       // mov r14, rax
	e.raw(0x45, 0x31, 0xED)                       // xor r13d, r13d
	e.vmAddr(14, 0)
	e.raw(0xC6, 0x00, 0x00)                       // mov byte [rax], 0

	// ---- dirent 缓冲区 ----
	e.raw(0x48, 0xC7, 0xC0); e.imm32(bufSize)     // mov rax, bufSize
	e.storeReg(0)
	e.emitMalloc()
	e.loadReg(0)
	e.raw(0x49, 0x89, 0xC4)                       // mov r12, rax

	// ---- 读一批目录项 ----
	readLoop := len(e.code)
	e.raw(0x48, 0x89, 0xDF)                       // mov rdi, rbx (fd)
	e.vmAddr(12, 0)                               // rax = buf host
	e.raw(0x48, 0x89, 0xC6)                       // mov rsi, rax
	e.raw(0xBA); e.imm32(bufSize)                 // mov edx, bufSize
	e.raw(0xB8); e.imm32(217)                     // mov eax, SYS_getdents64
	e.raw(0x0F, 0x05)                             // syscall
	e.raw(0x48, 0x85, 0xC0)                       // test rax, rax
	e.raw(0x0F, 0x8E)                             // jle 读完
	jReadDone := e.imm32Pos()
	e.raw(0x48, 0x89, 0xC1)                       // mov rcx, rax (n)
	e.raw(0x48, 0x89, 0xF7)                       // mov rdi, rsi
	e.raw(0x48, 0x01, 0xCF)                       // add rdi, rcx (end)

	// ---- 逐条处理 ----
	entryLoop := len(e.code)
	e.raw(0x48, 0x39, 0xFE)                       // cmp rsi, rdi
	e.raw(0x0F, 0x83)                             // jae 下一批
	jNextRead := e.imm32Pos()

	// 输出缓冲快满了就直接收尾, 宁可少列几条也不越界写。
	e.raw(0x49, 0x81, 0xFD); e.imm32(outSize - 256) // cmp r13, outSize-256
	e.raw(0x0F, 0x83)                               // jae 收尾
	jFull := e.imm32Pos()

	// 跳过 "." 与 ".."
	e.raw(0x80, 0x7E, 0x13, '.')                  // cmp byte [rsi+19], '.'
	e.raw(0x0F, 0x85)                             // jne 正常条目
	jNotDot := e.imm32Pos()
	e.raw(0x8A, 0x46, 0x14)                       // mov al, [rsi+20]
	e.raw(0x84, 0xC0)                             // test al, al
	e.raw(0x0F, 0x84)                             // jz 跳过
	jAdv1 := e.imm32Pos()
	e.raw(0x3C, '.')                              // cmp al, '.'
	e.raw(0x0F, 0x84)                             // je 跳过
	jAdv2 := e.imm32Pos()

	e.patchRel(jNotDot, len(e.code))

	// 非首条先补分隔换行
	e.raw(0x4D, 0x85, 0xED)                       // test r13, r13
	e.raw(0x0F, 0x84)                             // jz 不补
	jNoSep := e.imm32Pos()
	e.vmAddr(14, 0)
	e.raw(0x42, 0xC6, 0x04, 0x28, '\n')           // mov byte [rax+r13], '\n'
	e.raw(0x49, 0xFF, 0xC5)                       // inc r13
	e.patchRel(jNoSep, len(e.code))

	// 拷贝名字
	e.raw(0x48, 0x8D, 0x56, 0x13)                 // lea rdx, [rsi+19]
	copyName := len(e.code)
	e.raw(0x8A, 0x0A)                             // mov cl, [rdx]
	e.raw(0x84, 0xC9)                             // test cl, cl
	e.raw(0x0F, 0x84)                             // jz 名字结束
	jNameEnd := e.imm32Pos()
	e.vmAddr(14, 0)
	e.raw(0x42, 0x88, 0x0C, 0x28)                 // mov [rax+r13], cl
	e.raw(0x49, 0xFF, 0xC5)                       // inc r13
	e.raw(0x48, 0xFF, 0xC2)                       // inc rdx
	e.raw(0xE9); e.imm32(int32(copyName - (len(e.code) + 4)))
	e.patchRel(jNameEnd, len(e.code))

	// 目录补 '/'
	e.raw(0x80, 0x7E, 0x12, 0x04)                 // cmp byte [rsi+18], DT_DIR(4)
	e.raw(0x0F, 0x85)                             // jne 不补
	jNoSlash := e.imm32Pos()
	e.vmAddr(14, 0)
	e.raw(0x42, 0xC6, 0x04, 0x28, '/')            // mov byte [rax+r13], '/'
	e.raw(0x49, 0xFF, 0xC5)                       // inc r13
	e.patchRel(jNoSlash, len(e.code))

	// 结尾 NUL (每轮都刷新, 中途 break 出去也是合法 C 字符串)
	e.vmAddr(14, 0)
	e.raw(0x42, 0xC6, 0x04, 0x28, 0x00)           // mov byte [rax+r13], 0

	// 走到下一个条目
	advance := len(e.code)
	e.raw(0x0F, 0xB7, 0x46, 0x10)                 // movzx eax, word [rsi+16]
	e.raw(0x48, 0x85, 0xC0)                       // test rax, rax
	e.raw(0x0F, 0x84)                             // jz 下一批 (防御 reclen=0)
	jRecZero := e.imm32Pos()
	e.raw(0x48, 0x01, 0xC6)                       // add rsi, rax
	e.raw(0xE9); e.imm32(int32(entryLoop - (len(e.code) + 4)))

	e.patchRel(jAdv1, advance)
	e.patchRel(jAdv2, advance)

	// ---- 回到读取循环 ----
	nextLbl := len(e.code)
	e.raw(0xE9); e.imm32(int32(readLoop - (len(e.code) + 4)))
	e.patchRel(jNextRead, nextLbl)
	e.patchRel(jRecZero, nextLbl)

	// ---- 收尾: close(fd), R0 = 输出缓冲区 ----
	e.patchRel(jReadDone, len(e.code))
	e.patchRel(jFull, len(e.code))
	e.raw(0x48, 0x89, 0xDF)                       // mov rdi, rbx
	e.raw(0xB8); e.imm32(3)                       // mov eax, SYS_close
	e.raw(0x0F, 0x05)                             // syscall
	e.raw(0x4C, 0x89, 0xF0)                       // mov rax, r14
	e.storeReg(0)
	e.raw(0xE9)                                   // 跳过失败分支
	jDone := e.imm32Pos()

	// ---- 打开失败: 返回空串 ----
	e.patchRel(jFail, len(e.code))
	e.raw(0x48, 0xC7, 0xC0); e.imm32(1)           // mov rax, 1
	e.storeReg(0)
	e.emitMalloc()
	e.loadReg(0)
	e.vmAddr(0, 0)
	e.raw(0xC6, 0x00, 0x00)                       // mov byte [rax], 0

	e.patchRel(jDone, len(e.code))
}
