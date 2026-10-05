package main

/*
#include <stdlib.h>
*/
import "C"

import (
	"encoding/binary"
	"fmt"
	"math"
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

// v2 结果缓冲布局 (与 codecin/native.py _parse_result_v2 严格一致):
//   status u8 | NZCV 标志 u8 (bit0=N bit1=Z bit2=C bit3=V) | 2B pad
//   pc u64  sp u64  heap_ptr u64  steps u64
//   regs 33×u64
//   vec  32×4×f64
//   seg_count u64 | 每段: addr u64 + len u64 + data (脏段, 按地址升序)
//   out_len u64 | out ...
//   err_len u16 | err ...

// v2 请求缓冲布局 (与 codecin/native.py _build_request_v2 严格一致):
//   bc_len u32 | bc ...
//   seg_count u32 | 每段: addr u64 + len u32 + data (输入内存段)
//   in_len u32 | in ... (stdin 重定向数据)

// buildVersion 由构建时注入: -ldflags "-X main.buildVersion=<版本>"
// (codecin/native/build.ps1 / build.sh 会传入 codecin 包版本)。
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

// marshalResultV2 把执行结果序列化为 v2 ABI 缓冲 (段式内存, 带 NZCV 与真实向量)。
func marshalResultV2(res *engine.Result) unsafe.Pointer {
	const head = 4 + 32 + 33*8 + 32*4*8 + 8 // 1332

	var status byte
	var flg byte
	pc, sp, heap, steps := uint64(0), uint64(0), uint64(0), uint64(0)
	var regs [33]uint64
	var vec [32][4]float64
	var segs []engine.MemSeg
	out := []byte{}
	errb := []byte{}
	if res != nil {
		status = byte(res.Status)
		pc, sp, heap, steps = uint64(res.Pc), res.Sp, res.HeapPtr, res.Steps
		regs = res.Regs
		vec = res.Vec
		segs = res.Segments
		out = []byte(res.Output)
		errb = []byte(res.ErrMsg)
		if res.Flags.N {
			flg |= 1
		}
		if res.Flags.Z {
			flg |= 2
		}
		if res.Flags.C {
			flg |= 4
		}
		if res.Flags.V {
			flg |= 8
		}
	}

	segsBytes := 0
	for _, s := range segs {
		segsBytes += 16 + len(s.Data)
	}
	total := head + segsBytes + 8 + len(out) + 2 + len(errb)
	buf := make([]byte, total)

	buf[0] = status
	buf[1] = flg
	pos := 4
	binary.LittleEndian.PutUint64(buf[pos:], pc)
	binary.LittleEndian.PutUint64(buf[pos+8:], sp)
	binary.LittleEndian.PutUint64(buf[pos+16:], heap)
	binary.LittleEndian.PutUint64(buf[pos+24:], steps)
	pos = 36
	for i := 0; i < 33; i++ {
		binary.LittleEndian.PutUint64(buf[pos+i*8:], regs[i])
	}
	pos += 33 * 8
	for i := 0; i < 32; i++ {
		for j := 0; j < 4; j++ {
			binary.LittleEndian.PutUint64(buf[pos:], math.Float64bits(vec[i][j]))
			pos += 8
		}
	}
	binary.LittleEndian.PutUint64(buf[pos:], uint64(len(segs)))
	pos += 8
	for _, s := range segs {
		binary.LittleEndian.PutUint64(buf[pos:], s.Addr)
		binary.LittleEndian.PutUint64(buf[pos+8:], uint64(len(s.Data)))
		copy(buf[pos+16:], s.Data)
		pos += 16 + len(s.Data)
	}
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

// errResultV2 是 panic / 参数非法时的 v2 降级结果。
func errResultV2(msg string, sp0, heap0 int64) unsafe.Pointer {
	return marshalResultV2(&engine.Result{
		Status:  engine.StatusError,
		ErrMsg:  msg,
		Sp:      uint64(sp0),
		HeapPtr: uint64(heap0),
	})
}

//export codecin_run_v2
func codecin_run_v2(reqPtr unsafe.Pointer, reqLen C.int,
	entry, sp, heapBase, memSize, maxSteps, seed, flags C.longlong) (ret unsafe.Pointer) {

	// panic 不得跨越 CGO 边界 (同 codecin_run)。
	defer func() {
		if r := recover(); r != nil {
			ret = errResultV2(fmt.Sprintf("native panic: %v", r),
				int64(sp), int64(heapBase))
		}
	}()

	if reqLen < 0 {
		return errResultV2("native: negative buffer length",
			int64(sp), int64(heapBase))
	}

	var req []byte
	if reqPtr != nil && reqLen > 0 {
		req = C.GoBytes(reqPtr, reqLen)
	}
	readU32 := func(pos *int) uint32 {
		if *pos+4 > len(req) {
			panic("malformed v2 request")
		}
		v := binary.LittleEndian.Uint32(req[*pos:])
		*pos += 4
		return v
	}

	opts := &engine.RunOptions{
		Entry:    int64(entry),
		SP:       int64(sp),
		HeapBase: int64(heapBase),
		MemSize:  int64(memSize),
		MaxSteps: int64(maxSteps),
		Seed:     int64(seed),
		Sandbox:  flags&1 != 0,
	}
	pos := 0
	bcLen := readU32(&pos)
	if int(bcLen) > len(req)-pos {
		return errResultV2("native: malformed v2 request (bytecode)",
			int64(sp), int64(heapBase))
	}
	if bcLen > 0 {
		opts.BC = req[pos : pos+int(bcLen)]
	}
	pos += int(bcLen)

	segCount := readU32(&pos)
	for i := uint32(0); i < segCount; i++ {
		if pos+12 > len(req) {
			return errResultV2("native: malformed v2 request (segment)",
				int64(sp), int64(heapBase))
		}
		addr := binary.LittleEndian.Uint64(req[pos:])
		dataLen := binary.LittleEndian.Uint32(req[pos+8:])
		pos += 12
		if int(dataLen) > len(req)-pos {
			return errResultV2("native: malformed v2 request (segment data)",
				int64(sp), int64(heapBase))
		}
		if dataLen > 0 {
			opts.Segments = append(opts.Segments, engine.MemSeg{
				Addr: addr, Data: req[pos : pos+int(dataLen)],
			})
		}
		pos += int(dataLen)
	}

	inLen := readU32(&pos)
	if int(inLen) > len(req)-pos {
		return errResultV2("native: malformed v2 request (input)",
			int64(sp), int64(heapBase))
	}
	if inLen > 0 {
		opts.Input = req[pos : pos+int(inLen)]
	}
	_ = pos

	res := engine.RunV2(opts)
	if res == nil {
		return errResultV2("native: no result", int64(sp), int64(heapBase))
	}
	return marshalResultV2(res)
}

//export codecin_free
func codecin_free(ptr unsafe.Pointer) {
	if ptr != nil {
		C.free(ptr)
	}
}

//export codecin_set_args
func codecin_set_args(argv **C.char, argc C.int) {
	defer func() {
		recover() // 参数注入失败不得拖垮宿主进程: 降级为无参数
	}()
	if argc < 0 {
		return
	}
	args := make([]string, 0, int(argc))
	if argc > 0 {
		for _, p := range unsafe.Slice(argv, int(argc)) {
			if p == nil {
				args = append(args, "")
			} else {
				args = append(args, C.GoString(p))
			}
		}
	}
	engine.SetProgramArgs(args)
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

// buildVersion 为空时使用生成器注入的 engine.BuildVersion
// (单一事实来源: codecin/__init__.py 的 __version__)。
func versionString() string {
	v := buildVersion
	if v == "" || v == "dev" {
		v = engine.BuildVersion
	}
	return "codecin-native " + v + " (Go)"
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
		versionCStr = C.CString(versionString())
	})
	return versionCStr
}

func main() {}
