package engine

// ffi_registry.go: FFI 句柄注册表 (平台无关部分)。
//
// dlopen/dlsym 的原生指针用递增整数句柄暴露给 CIN 程序: 指针不在内存镜像
// 里搬运, go vet 的 unsafeptr 检查也保持安静。

import (
	"sync"
	"unsafe"
)

var ffiRegistry = struct {
	sync.Mutex
	next uint64
	libs map[uint64]unsafe.Pointer // dlopen 句柄
	fns  map[uint64]unsafe.Pointer // dlsym 函数指针
}{
	next: 1,
	libs: map[uint64]unsafe.Pointer{},
	fns:  map[uint64]unsafe.Pointer{},
}

// ffiLibGet 取出 dlopen 句柄 (不存在返回 false)。
func ffiLibGet(handle uint64) (unsafe.Pointer, bool) {
	ffiRegistry.Lock()
	defer ffiRegistry.Unlock()
	lib, ok := ffiRegistry.libs[handle]
	return lib, ok
}

// ffiLibPut 登记句柄, 返回暴露给 CIN 的整数 id。
func ffiLibPut(p unsafe.Pointer) uint64 {
	ffiRegistry.Lock()
	defer ffiRegistry.Unlock()
	id := ffiRegistry.next
	ffiRegistry.next++
	ffiRegistry.libs[id] = p
	return id
}

// ffiFnGet 取出函数指针。
func ffiFnGet(fnID uint64) (unsafe.Pointer, bool) {
	ffiRegistry.Lock()
	defer ffiRegistry.Unlock()
	fp, ok := ffiRegistry.fns[fnID]
	return fp, ok
}

// ffiFnPut 登记函数指针, 返回暴露给 CIN 的整数 id。
func ffiFnPut(p unsafe.Pointer) uint64 {
	ffiRegistry.Lock()
	defer ffiRegistry.Unlock()
	id := ffiRegistry.next
	ffiRegistry.next++
	ffiRegistry.fns[id] = p
	return id
}

// ffiLibDel 注销 dlopen 句柄 (返回是否存在)。
func ffiLibDel(handle uint64) (unsafe.Pointer, bool) {
	ffiRegistry.Lock()
	defer ffiRegistry.Unlock()
	lib, ok := ffiRegistry.libs[handle]
	if ok {
		delete(ffiRegistry.libs, handle)
	}
	return lib, ok
}
