//go:build windows

package engine

import (
	"syscall"
)

// Windows 键盘后端: msvcrt 的 _kbhit/_getch。
// _getch 直读控制台输入缓冲 (不回显、不经行缓冲, 无需 raw mode);
// 管道/重定向下 _kbhit 恒为 0, 天然优雅失败, 也不会阻塞 VM。

var (
	msvcrt    = syscall.NewLazyDLL("msvcrt.dll")
	procKbhit = msvcrt.NewProc("_kbhit")
	procGetch = msvcrt.NewProc("_getch")
)

func keyEnablePlatform() bool { return true }

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
