//go:build windows && cgo

package engine

// ffi_windows.go: 动态库加载与调用 (Windows: LoadLibrary / GetProcAddress)。
//
// 语义 (SYS 140-144):
//   - dlopen(path)       -> 句柄 (0 失败)
//   - dlsym(handle,name) -> 函数句柄 (0 失败)
//   - ffi_call(fn, argbuf, nargs)   -> long long (int64)
//   - ffi_callf(fn, argbuf, nargs)  -> double (按 IEEE754 位模式存 X0)
//   - lib_close(handle)  -> 0 成功 / -1
//
// 调用约定: 最多 8 个 int64 参数, 从 vm 内存 argbuf 顺序读取; 浮点参数由
// CIN 侧以位模式预先写入 argbuf。

import (
	"encoding/binary"
	"fmt"
	"unsafe"
)

/*
#include <windows.h>
#include <stdlib.h>
#include <string.h>
typedef long long (*ffi_fn8)(long long, long long, long long, long long,
                             long long, long long, long long, long long);
typedef double (*ffi_fnf8)(double, double, double, double,
                           double, double, double, double);
static void* ffi_open(const char* path) {
    return (void*)LoadLibraryA(path);
}
static void* ffi_sym(void* h, const char* name) {
    return (void*)GetProcAddress((HMODULE)h, name);
}
static void ffi_close(void* h) { FreeLibrary((HMODULE)h); }
static long long ffi_call8(void* f, long long* a) {
    return ((ffi_fn8)f)(a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7]);
}
// 浮点调用: a[] 存的是 IEEE754 位模式 (int64 槽), memcpy 到 double 数组
// 保持位模式不变, 再按 double 类型传参 —— Windows x64 ABI 下前 4 个浮点
// 参数走 XMM0-XMM3 (整型声明会走 RCX/RDX/..., 目标函数从 XMM 读到垃圾)。
static double ffi_callf8(void* f, long long* a) {
    double d[8];
    memcpy(d, a, sizeof d);
    return ((ffi_fnf8)f)(d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7]);
}
*/
import "C"

func (vm *vmState) ffiOpen(path string) uint64 {
	if path == "" {
		return 0
	}
	cpath := C.CString(path)
	defer C.free(unsafe.Pointer(cpath))
	h := C.ffi_open(cpath)
	if h == nil {
		return 0
	}
	return ffiLibPut(h)
}

func (vm *vmState) ffiSym(handle uint64, name string) uint64 {
	if name == "" {
		return 0
	}
	lib, ok := ffiLibGet(handle)
	if !ok {
		return 0
	}
	cname := C.CString(name)
	defer C.free(unsafe.Pointer(cname))
	fp := C.ffi_sym(lib, cname)
	if fp == nil {
		return 0
	}
	return ffiFnPut(fp)
}

// ffiCall 执行 ffi_call / ffi_callf。返回值: 整数调用为 int64;
// 浮点调用为 IEEE754 位模式。
func (vm *vmState) ffiCall(fnID, argbuf, nargs uint64, wantFloat bool) (uint64, string) {
	if nargs > 8 {
		return 0, fmt.Sprintf("FFI call: too many arguments (%d, max 8)", nargs)
	}
	fp, ok := ffiFnGet(fnID)
	if !ok {
		return 0, "FFI call: invalid function handle"
	}
	var args [8]C.longlong
	if nargs > 0 {
		if e := vm.checkAddr(argbuf, int(nargs)*8); e != "" {
			return 0, "FFI call: " + e
		}
		for i := uint64(0); i < nargs; i++ {
			args[i] = C.longlong(int64(binary.LittleEndian.Uint64(
				vm.mem[argbuf+i*8 : argbuf+i*8+8])))
		}
	}
	if wantFloat {
		r := C.ffi_callf8(fp, &args[0])
		return fToBits(float64(r)), ""
	}
	r := C.ffi_call8(fp, &args[0])
	return uint64(int64(r)) & mask64, ""
}

func (vm *vmState) ffiClose(handle uint64) uint64 {
	lib, ok := ffiLibDel(handle)
	if !ok {
		return mask64
	}
	C.ffi_close(lib)
	return 0
}
