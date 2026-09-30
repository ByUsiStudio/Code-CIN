//go:build windows

package engine

import (
	"syscall"
	"unsafe"
)

// Windows 键盘后端: msvcrt 的 _kbhit/_getch。
// _getch 直读控制台输入缓冲 (不回显、不经行缓冲, 无需 raw mode);
// 管道/重定向下 GetConsoleMode 失败 -> 不启用, 与非 Windows 平台判定一致,
// _kbhit 恒为 0, 天然优雅失败, 也不会阻塞 VM。

var (
	msvcrt    = syscall.NewLazyDLL("msvcrt.dll")
	procKbhit = msvcrt.NewProc("_kbhit")
	procGetch = msvcrt.NewProc("_getch")

	kernel32           = syscall.NewLazyDLL("kernel32.dll")
	procGetStdHandle   = kernel32.NewProc("GetStdHandle")
	procGetConsoleMode = kernel32.NewProc("GetConsoleMode")
	procFlushInputBuf  = kernel32.NewProc("FlushConsoleInputBuffer")
)

// keyEnablePlatform 校验 stdin 为真实控制台后启用, 并清空启动前残留的
// 输入缓存 (如启动命令时敲下的回车), 避免程序一激活就吃到旧键。
func keyEnablePlatform() bool {
	const stdInputHandle = ^uintptr(9) // (DWORD)-10
	h, _, _ := procGetStdHandle.Call(stdInputHandle)
	if h == 0 {
		return false
	}
	var mode uint32
	if r, _, _ := procGetConsoleMode.Call(h, uintptr(unsafe.Pointer(&mode))); r == 0 {
		return false // 管道 / 重定向: 不启用
	}
	_, _, _ = procFlushInputBuf.Call(h)
	return true
}

func keyRestorePlatform() {}

// getchPrefixMap 0x00 / 0xE0 前缀后的扫描码 -> 扩展键码
var getchPrefixMap = map[byte]uint64{
	0x48: keyUp, 0x50: keyDown, 0x4B: keyLeft, 0x4D: keyRight,
	0x47: keyHome, 0x4F: keyEnd, 0x49: keyPgUp, 0x51: keyPgDn,
	0x52: keyIns, 0x53: keyDel,
	0x3B: keyF1, 0x3C: keyF2, 0x3D: keyF3, 0x3E: keyF4,
	0x3F: keyF5, 0x40: keyF6, 0x41: keyF7, 0x42: keyF8,
	0x43: keyF9, 0x44: keyF10,
}

// keyNext 非阻塞取下一个已解码键码 (_kbhit 先行探测, 不会阻塞)。
func keyNext() (uint64, bool) {
	if r, _, _ := procKbhit.Call(); r == 0 {
		return 0, false
	}
	r, _, _ := procGetch.Call()
	b := byte(r)
	if b != 0x00 && b != 0xE0 {
		return uint64(b), true
	}
	// 扩展键: 前缀字节后必随扫描码 (同一次按键入队, 不会阻塞)
	r2, _, _ := procGetch.Call()
	if code, ok := getchPrefixMap[byte(r2)]; ok {
		return code, true
	}
	return 0, false // 未知扫描码: 丢弃
}
