package main

/*
#include <stdlib.h>
*/
import "C"

import (
	"encoding/binary"
	"unsafe"
)

// 结果结构布局 (与 ucpu/native.py _parse_result 严格一致):
//   status u8 + 3B pad
//   pc u64  sp u64  heap_ptr u64  steps u64
//   regs 33×u64
//   vec  32×4×f64
//   mem_len u64 | mem ...
//   out_len u64 | out ...
//   err_len u16 | err ...

const (
	statusOK          = 0
	statusDone        = 1
	statusUnsupported = 2
	statusError       = 3
)

//export ucpu_run
func ucpu_run(bcPtr unsafe.Pointer, bcLen C.int,
	memPtr unsafe.Pointer, memLen C.int,
	entry C.longlong, sp C.longlong, heapBase C.longlong,
	inPtr unsafe.Pointer, inLen C.int,
	maxSteps C.longlong) unsafe.Pointer {

	bc := C.GoBytes(bcPtr, bcLen)
	mem := C.GoBytes(memPtr, memLen)
	in := []byte{}
	if inPtr != nil && inLen > 0 {
		in = C.GoBytes(inPtr, inLen)
	}

	status, state, errMsg := runVM(bc, mem, int64(entry), int64(sp), int64(heapBase),
		in, int64(maxSteps))

	// 将输出与错误收集
	out := []byte{}
	if state != nil {
		out = []byte(state.out.String())
	}
	errb := []byte{}
	if errMsg != "" {
		errb = []byte(errMsg)
	}

	// 结果内存 (vec 区填 0: CIN 不使用向量寄存器)
	regBytes := 33 * 8
	vecBytes := 32 * 4 * 8
	total := 36 + regBytes + vecBytes + 8 + len(mem) + 8 + len(out) + 2 + len(errb)
	buf := make([]byte, total)

	pos := 0
	buf[0] = byte(status)
	pos = 4
	pc, sp2, heap, steps := 0, uint64(sp), uint64(heapBase), uint64(0)
	if state != nil {
		pc = state.pc
		sp2 = state.sp
		heap = state.heapPtr
		steps = state.steps
	}
	binary.LittleEndian.PutUint64(buf[pos:], uint64(pc))
	binary.LittleEndian.PutUint64(buf[pos+8:], sp2)
	binary.LittleEndian.PutUint64(buf[pos+16:], heap)
	binary.LittleEndian.PutUint64(buf[pos+24:], steps)
	pos = 36 // 状态头 36 字节: status+pad(4) + 4×u64(32)
	if state != nil {
		for i := 0; i < 33; i++ {
			binary.LittleEndian.PutUint64(buf[pos+i*8:], state.regs[i])
		}
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
	dest := unsafe.Slice((*byte)(cbuf), total)
	copy(dest, buf)
	return cbuf
}

//export ucpu_free
func ucpu_free(ptr unsafe.Pointer) {
	if ptr != nil {
		C.free(ptr)
	}
}

//export ucpu_crom_pack
func ucpu_crom_pack(dataPtr unsafe.Pointer, dataLen C.int, compress C.int, outLen *C.int) unsafe.Pointer {
	data := C.GoBytes(dataPtr, dataLen)
	packed := cromPack(data, compress != 0)
	if packed == nil {
		return nil
	}
	*outLen = C.int(len(packed))
	cbuf := C.malloc(C.size_t(len(packed)))
	copy(unsafe.Slice((*byte)(cbuf), len(packed)), packed)
	return cbuf
}

//export ucpu_crom_unpack
func ucpu_crom_unpack(dataPtr unsafe.Pointer, dataLen C.int,
	memLen *C.int, flags *C.int) unsafe.Pointer {
	data := C.GoBytes(dataPtr, dataLen)
	raw, flg, ok := cromUnpack(data)
	if !ok {
		return nil
	}
	*memLen = C.int(len(raw))
	*flags = C.int(flg)
	cbuf := C.malloc(C.size_t(len(raw)))
	copy(unsafe.Slice((*byte)(cbuf), len(raw)), raw)
	return cbuf
}

//export ucpu_version
func ucpu_version() *C.char {
	return C.CString("ucpu-native 1.0 (Go)")
}

func main() {}
