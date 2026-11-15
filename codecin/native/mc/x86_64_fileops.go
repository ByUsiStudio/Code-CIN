package mc

// x86_64_fileops.go: Linux x86-64 的 SYS 84..88。
//
// AArch64 后端早就实现了这几个功能号, x86-64 侧一直空缺 —— io.cin 里的
// file_copy 会直接编译不过。这里按 Linux 系统调用补齐, 语义与引擎
// (engine/system.go) 对齐: 成功 0, 失败 -1; ISDIR 返回 1/0, FILEMTIME
// 返回秒数或 -1。
//
// 寄存器分工与 x86_64_dirlist.go 一致: rbx / r12 / r13 / r14 作局部状态,
// r15 是 VM 基址不能碰。

// Linux x86-64 的 struct stat 关键偏移 (总长 144 字节)。
const (
	x86StSize  = 152 // 留一点余量, 保持栈 8 字节对齐
	x86StMode  = 24  // st_mode (u32)
	x86StMtim  = 88  // st_mtim.tv_sec
	x86CopyBuf = 1 << 20
)

// failIfNeg 生成 "rax >= 0 保持, 否则改成 -1" 的收尾并写回 R0。
func (e *Encoder) failToMinusOne() {
	e.raw(0x48, 0x85, 0xC0)                      // test rax, rax
	e.raw(0x0F, 0x89)                            // jns 保持
	jOK := e.imm32Pos()
	e.raw(0x48, 0xC7, 0xC0); e.imm32(-1)         // mov rax, -1
	e.patchRel(jOK, len(e.code))
	e.storeReg(0)
}

// linFileCopy 生成 SYS 84 FILECOPY(src=R0, dst=R1)。
//
// 读全部 -> 一次性写出。缓冲区上限 1 MiB, 超过的部分会被截断 (与 AArch64
// 后端的单次写模型一致)。
func (e *Encoder) linFileCopy() {
	e.loadReg(0)
	e.storeReg(7) // R7 = src
	e.loadReg(1)
	e.storeReg(8) // R8 = dst

	// 缓冲区
	e.raw(0x48, 0xC7, 0xC0); e.imm32(x86CopyBuf)
	e.storeReg(0)
	e.emitMalloc()
	e.loadReg(0)
	e.raw(0x49, 0x89, 0xC4) // mov r12, rax
	e.raw(0x45, 0x31, 0xED) // xor r13d, r13d (已读字节数)

	// fd = open(src, O_RDONLY)
	e.vmAddr(7, 0)
	e.raw(0x48, 0x89, 0xC7) // mov rdi, rax
	e.raw(0x31, 0xF6)       // xor esi, esi
	e.raw(0xB8); e.imm32(2) // mov eax, SYS_open
	e.raw(0x0F, 0x05)
	e.raw(0x48, 0x85, 0xC0) // test rax, rax
	e.raw(0x0F, 0x88)       // js 失败
	jFailOf := e.imm32Pos()
	e.raw(0x48, 0x89, 0xC3) // mov rbx, rax (fd)

	// 循环读满缓冲区
	readLoop := len(e.code)
	e.raw(0x49, 0x81, 0xFD); e.imm32(x86CopyBuf) // cmp r13, 缓冲区大小
	e.raw(0x0F, 0x83)                            // jae 读完了
	jFull := e.imm32Pos()
	e.raw(0x48, 0x89, 0xDF)       // mov rdi, rbx
	e.vmAddr(12, 0)               // rax = buf host
	e.raw(0x4C, 0x01, 0xE8)       // add rax, r13
	e.raw(0x48, 0x89, 0xC6)       // mov rsi, rax
	e.raw(0x48, 0xC7, 0xC2); e.imm32(x86CopyBuf) // mov rdx, 缓冲区大小
	e.raw(0x4C, 0x29, 0xEA)       // sub rdx, r13  (剩余空间)
	e.raw(0x31, 0xC0)             // xor eax, eax (SYS_read)
	e.raw(0x0F, 0x05)
	e.raw(0x48, 0x85, 0xC0)       // test rax, rax
	e.raw(0x0F, 0x8E)             // jle 读完了
	jEOF := e.imm32Pos()
	e.raw(0x49, 0x01, 0xC5)       // add r13, rax
	e.raw(0xE9); e.imm32(int32(readLoop - (len(e.code) + 4)))

	e.patchRel(jFull, len(e.code))
	e.patchRel(jEOF, len(e.code))

	// close(src)
	e.raw(0x48, 0x89, 0xDF) // mov rdi, rbx
	e.raw(0xB8); e.imm32(3) // mov eax, SYS_close
	e.raw(0x0F, 0x05)

	// fd = open(dst, O_WRONLY|O_CREAT|O_TRUNC, 0644)
	e.vmAddr(8, 0)
	e.raw(0x48, 0x89, 0xC7)                      // mov rdi, rax
	e.raw(0xBE); e.imm32(0x241)                  // mov esi, flags
	e.raw(0xBA); e.imm32(0644)                   // mov edx, mode
	e.raw(0xB8); e.imm32(2)                      // mov eax, SYS_open
	e.raw(0x0F, 0x05)
	e.raw(0x48, 0x85, 0xC0)                      // test rax, rax
	e.raw(0x0F, 0x88)                            // js 失败
	jFailDst := e.imm32Pos()
	e.raw(0x49, 0x89, 0xC6)                      // mov r14, rax (fd)

	// write(dst, buf, len)
	e.raw(0x4C, 0x89, 0xF7)                      // mov rdi, r14
	e.vmAddr(12, 0)                              // rax = buf host
	e.raw(0x48, 0x89, 0xC6)                      // mov rsi, rax
	e.raw(0x4C, 0x89, 0xEA)                      // mov rdx, r13
	e.raw(0xB8); e.imm32(1)                      // mov eax, SYS_write
	e.raw(0x0F, 0x05)

	// close(dst)
	e.raw(0x4C, 0x89, 0xF7) // mov rdi, r14
	e.raw(0xB8); e.imm32(3) // mov eax, SYS_close
	e.raw(0x0F, 0x05)

	e.raw(0x31, 0xC0)       // xor eax, eax
	e.storeReg(0)
	e.raw(0xE9)             // 跳过失败分支
	jDone := e.imm32Pos()

	e.patchRel(jFailOf, len(e.code))
	e.patchRel(jFailDst, len(e.code))
	e.raw(0x48, 0xC7, 0xC0); e.imm32(-1) // mov rax, -1
	e.storeReg(0)
	e.patchRel(jDone, len(e.code))
}

// linFileMove 生成 SYS 85 FILEMOVE(src=R0, dst=R1): rename(2)。
func (e *Encoder) linFileMove() {
	e.vmAddr(0, 0)
	e.raw(0x48, 0x89, 0xC7) // mov rdi, rax
	e.vmAddr(1, 0)
	e.raw(0x48, 0x89, 0xC6)      // mov rsi, rax
	e.raw(0xB8); e.imm32(82)     // mov eax, SYS_rename
	e.raw(0x0F, 0x05)
	e.failToMinusOne()
}

// linDirRemove 生成 SYS 86 DIRREMOVE(path=R0): rmdir(2)。
func (e *Encoder) linDirRemove() {
	e.vmAddr(0, 0)
	e.raw(0x48, 0x89, 0xC7)      // mov rdi, rax
	e.raw(0xB8); e.imm32(84)     // mov eax, SYS_rmdir
	e.raw(0x0F, 0x05)
	e.failToMinusOne()
}

// linStat 把 path=R0 的 stat 结果压到栈上并保留; 返回跳转占位 (失败时跳过)。
//
// 失败时 rax != 0, 调用方自己决定返回值; 无论成败都要 add rsp 收栈。
func (e *Encoder) linStat() int {
	e.raw(0x48, 0x81, 0xEC); e.imm32(x86StSize) // sub rsp, x86StSize
	e.vmAddr(0, 0)
	e.raw(0x48, 0x89, 0xC7)       // mov rdi, rax
	e.raw(0x48, 0x89, 0xE6)       // mov rsi, rsp
	e.raw(0xB8); e.imm32(4)       // mov eax, SYS_stat
	e.raw(0x0F, 0x05)
	e.raw(0x48, 0x85, 0xC0)       // test rax, rax
	e.raw(0x0F, 0x85)             // jnz 失败
	return e.imm32Pos()
}

// linIsDir 生成 SYS 87 ISDIR(path=R0) -> 1/0。
func (e *Encoder) linIsDir() {
	jBad := e.linStat()
	e.raw(0x8B, 0x4C, 0x24, x86StMode)     // mov ecx, [rsp+24] (st_mode)
	e.raw(0x81, 0xE1); e.imm32(0xF000)     // and ecx, S_IFMT
	e.raw(0x81, 0xF9); e.imm32(0x4000)     // cmp ecx, S_IFDIR
	e.raw(0x0F, 0x94, 0xC1)                // sete cl
	e.raw(0x0F, 0xB6, 0xC9)                // movzx ecx, cl
	e.raw(0x48, 0x89, 0xC8)                // mov rax, rcx
	e.raw(0x48, 0x81, 0xC4); e.imm32(x86StSize) // add rsp, x86StSize
	e.raw(0xE9)                            // 跳过失败分支
	jDone := e.imm32Pos()

	e.patchRel(jBad, len(e.code))
	e.raw(0x31, 0xC0)                      // xor eax, eax
	e.raw(0x48, 0x81, 0xC4); e.imm32(x86StSize)
	e.patchRel(jDone, len(e.code))
	e.storeReg(0)
}

// linFileMtime 生成 SYS 88 FILEMTIME(path=R0) -> 秒数 / -1。
func (e *Encoder) linFileMtime() {
	jBad := e.linStat()
	e.raw(0x48, 0x8B, 0x44, 0x24, x86StMtim)    // mov rax, [rsp+88] (st_mtim.tv_sec)
	e.raw(0x48, 0x81, 0xC4); e.imm32(x86StSize) // add rsp, x86StSize
	e.raw(0xE9)
	jDone := e.imm32Pos()

	e.patchRel(jBad, len(e.code))
	e.raw(0x48, 0xC7, 0xC0); e.imm32(-1)        // mov rax, -1
	e.raw(0x48, 0x81, 0xC4); e.imm32(x86StSize)
	e.patchRel(jDone, len(e.code))
	e.storeReg(0)
}
