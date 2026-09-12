package main

/*
#include <stdlib.h>
*/
import "C"

import (
	"encoding/binary"
	"fmt"
	"sync"
	"unsafe"

	"codecin-native/engine"
)

// 结果结构布局 (与 codecin/native.py _parse_result 严格一致):
//   status u8 + 3B pad
//   pc u64  sp u64  heap_ptr u64  steps u64
//   regs 33×u64
//   vec  32×4×f64
//   mem_len u64 | mem ...
//   out_len u64 | out ...
//   err_len u16 | err ...

// buildVersion 由构建时注入: -ldflags "-X main.buildVersion=<版本>"
// (install.sh / install.ps1 / build.ps1 会传入 codecin 包版本)。
var buildVersion = "dev"

// marshalResult 把执行结果序列化为 ABI 缓冲。
// mem 为执行后的内存镜像; panic 降级路径传 nil (调用方按 mem_len=0 处理)。
func marshalResult(res *engine.Result, mem []byte,
	sp0, heap0 int64) unsafe.Pointer {

	const regBytes = 33 * 8
	const vecBytes = 32 * 4 * 8

	status := engine.StatusError
	pc := 0
	sp := uint64(sp0)
	heap := uint64(heap0)
	var steps uint64
	var regs [33]uint64
	out := []byte{}
	errb := []byte{}
	if res != nil {
		status = res.Status
		pc = res.Pc
		sp = res.Sp
		heap = res.HeapPtr
		steps = res.Steps
		regs = res.Regs
		out = []byte(res.Output)
		errb = []byte(res.ErrMsg)
	}

	total := 36 + regBytes + vecBytes + 8 + len(mem) + 8 + len(out) + 2 + len(errb)
	buf := make([]byte, total)

	buf[0] = byte(status)
	pos := 4
	binary.LittleEndian.PutUint64(buf[pos:], uint64(pc))
	binary.LittleEndian.PutUint64(buf[pos+8:], sp)
	binary.LittleEndian.PutUint64(buf[pos+16:], heap)
	binary.LittleEndian.PutUint64(buf[pos+24:], steps)
	pos = 36 // 状态头 36 字节: status+pad(4) + 4×u64(32)
	for i := 0; i < 33; i++ {
		binary.LittleEndian.PutUint64(buf[pos+i*8:], regs[i])
	}
	pos += regBytes
	pos += vecBytes // 向量区保持 0
	binary.LittleEndian.PutUint64(buf[pos:], uint64(len(mem)))
	pos += 8
	copy(buf[pos:], mem)
	pos += len(mem)
	binary.LittleEndian.PutUint64(buf[pos:], uint64(len(out)))
	pos += 8
	copy(buf[pos:], out)
	pos += len(out)
	binary.LittleEndian.PutUint16(buf[pos:], uint16(len(errb)))
	pos += 2
	copy(buf[pos:], errb)

	cbuf := C.malloc(C.size_t(total))
	if cbuf == nil {
		return nil
	}
	copy(unsafe.Slice((*byte)(cbuf), total), buf)
	return cbuf
}

// errResult 是 panic / 参数非法时的降级结果 (仍返回合法缓冲, 供 Python 解析)。
func errResult(msg string, sp0, heap0 int64) unsafe.Pointer {
	return marshalResult(&engine.Result{
		Status:  engine.StatusError,
		ErrMsg:  msg,
		Sp:      uint64(sp0),
		HeapPtr: uint64(heap0),
	}, nil, sp0, heap0)
}

//export codecin_run
func codecin_run(bcPtr unsafe.Pointer, bcLen C.int,
	memPtr unsafe.Pointer, memLen C.int,
	entry C.longlong, sp C.longlong, heapBase C.longlong,
	inPtr unsafe.Pointer, inLen C.int,
	maxSteps C.longlong) (ret unsafe.Pointer) {

	// panic 不得跨越 CGO 边界: Go 运行时会把未捕获 panic 当作致命错误直接
	// 终止宿主进程 (Python 无法拦截)。这里降级为"一次调用返回错误结果"。
	defer func() {
		if r := recover(); r != nil {
			ret = errResult(fmt.Sprintf("native panic: %v", r),
				int64(sp), int64(heapBase))
		}
	}()

	if bcLen < 0 || memLen < 0 || inLen < 0 {
		return errResult("native: negative buffer length",
			int64(sp), int64(heapBase))
	}

	var bc, mem []byte
	if bcPtr != nil && bcLen > 0 {
		bc = C.GoBytes(bcPtr, bcLen)
	}
	if memPtr != nil && memLen > 0 {
		mem = C.GoBytes(memPtr, memLen)
	}
	in := []byte{}
	if inPtr != nil && inLen > 0 {
		in = C.GoBytes(inPtr, inLen)
	}

	res := engine.Run(bc, mem, int64(entry), int64(sp), int64(heapBase),
		in, int64(maxSteps))
	return marshalResult(res, mem, int64(sp), int64(heapBase))
}

//export codecin_free
func codecin_free(ptr unsafe.Pointer) {
	if ptr != nil {
		C.free(ptr)
	}
}

//export codecin_crom_pack
func codecin_crom_pack(dataPtr unsafe.Pointer, dataLen C.int, compress C.int,
	outLen *C.int) (ret unsafe.Pointer) {
	defer func() {
		if r := recover(); r != nil {
			ret = nil
		}
	}()
	if outLen == nil || dataLen < 0 || dataPtr == nil {
		return nil
	}
	data := C.GoBytes(dataPtr, dataLen)
	packed := engine.CromPack(data, compress != 0)
	if packed == nil {
		return nil
	}
	cbuf := C.malloc(C.size_t(len(packed)))
	if cbuf == nil {
		return nil
	}
	copy(unsafe.Slice((*byte)(cbuf), len(packed)), packed)
	*outLen = C.int(len(packed))
	return cbuf
}

//export codecin_crom_unpack
func codecin_crom_unpack(dataPtr unsafe.Pointer, dataLen C.int,
	memLen *C.int, flags *C.int) (ret unsafe.Pointer) {
	defer func() {
		if r := recover(); r != nil {
			ret = nil
		}
	}()
	if memLen == nil || flags == nil || dataLen < 0 || dataPtr == nil {
		return nil
	}
	data := C.GoBytes(dataPtr, dataLen)
	raw, flg, ok := engine.CromUnpack(data)
	if !ok {
		return nil
	}
	cbuf := C.malloc(C.size_t(len(raw)))
	if cbuf == nil {
		return nil
	}
	copy(unsafe.Slice((*byte)(cbuf), len(raw)), raw)
	*memLen = C.int(len(raw))
	*flags = C.int(flg)
	return cbuf
}

var (
	versionOnce sync.Once
	versionCStr *C.char
)

// codecin_version 返回静态版本字符串。
// 旧实现每次调用都 C.CString, 而 Python 侧以 c_char_p 取走后永不 free → 每次调用泄漏。
// 返回的指针为进程级常量, 调用方不要 free。
//
//export codecin_version
func codecin_version() *C.char {
	versionOnce.Do(func() {
		versionCStr = C.CString("codecin-native " + buildVersion + " (Go)")
	})
	return versionCStr
}

func main() {}
