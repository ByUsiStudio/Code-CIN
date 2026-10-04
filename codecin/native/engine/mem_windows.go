//go:build windows

package engine

import (
	"syscall"
	"unsafe"
)

// memoryStatusEx 对应 Windows MEMORYSTATUSEX 结构。
type memoryStatusEx struct {
	Length               uint32
	MemoryLoad           uint32
	TotalPhys            uint64
	AvailPhys            uint64
	TotalPageFile        uint64
	AvailPageFile        uint64
	TotalVirtual         uint64
	AvailVirtual         uint64
	AvailExtendedVirtual uint64
}

func SystemMemoryKB() (uint64, uint64, bool) {
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	proc := kernel32.NewProc("GlobalMemoryStatusEx")
	var status memoryStatusEx
	status.Length = uint32(unsafe.Sizeof(status))
	ret, _, _ := proc.Call(uintptr(unsafe.Pointer(&status)))
	if ret == 0 {
		return 0, 0, false
	}
	return status.TotalPhys / 1024, status.AvailPhys / 1024, true
}
