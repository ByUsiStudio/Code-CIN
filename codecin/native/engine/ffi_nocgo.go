//go:build !cgo

package engine

// ffi_nocgo.go: CGO_ENABLED=0 (AOT 静态构建) 下的 FFI 桩。
// LoadLibrary/dlopen 依赖 cgo, 静态构建中不可用; 这里保持 engine 包
// 可编译 —— FFI 系统调用在运行时返回明确错误, 而非构建期链接失败。
// 其余能力 (VM/网络/音频/画布) 不受影响。

func (vm *vmState) ffiOpen(path string) uint64 { return 0 }

func (vm *vmState) ffiSym(handle uint64, name string) uint64 { return 0 }

func (vm *vmState) ffiCall(fnID, argbuf, nargs uint64,
	wantFloat bool) (uint64, string) {
	return 0, "FFI unavailable: built without cgo (static AOT build); " +
		"use the dynamic-library runtime for ffi_*"
}

func (vm *vmState) ffiClose(handle uint64) uint64 { return mask64 }
