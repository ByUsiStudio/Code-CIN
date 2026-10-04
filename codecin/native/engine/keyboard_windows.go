//go:build windows

package engine

import (
	"syscall"
	"unsafe"
)

// Windows 键盘后端: ReadConsoleInputW (Unicode 控制台输入事件流)。
// 相比 msvcrt _getch 的升级:
//   - 完整 Unicode 字符 (UTF-16, 含代理对组合 / IME 提交), 与 Unix 的
//     UTF-8 解码产出同一套码点键码;
//   - 过滤 key-up 事件, 支持按键重复 (wRepeatCount);
//   - 修饰键状态 (Ctrl/Shift + 方向键 -> 1101..1108, 与 Unix CSI 修饰一致);
//   - F11/F12 (msvcrt 扫描码表缺失)。
// 管道/重定向下 GetConsoleMode 失败 -> 不启用, 与非 Windows 平台判定一致,
// PeekConsoleInputW 永不触发, 天然优雅失败, 也不会阻塞 VM。

var (
	kernel32           = syscall.NewLazyDLL("kernel32.dll")
	procGetStdHandle   = kernel32.NewProc("GetStdHandle")
	procGetConsoleMode = kernel32.NewProc("GetConsoleMode")
	procSetConsoleMode = kernel32.NewProc("SetConsoleMode")
	procPeekInput      = kernel32.NewProc("PeekConsoleInputW")
	procReadInput      = kernel32.NewProc("ReadConsoleInputW")
	procFlushInputBuf  = kernel32.NewProc("FlushConsoleInputBuffer")
)

const (
	stdInputHandle = ^uintptr(9) // (DWORD)-10

	// console mode 标志
	cmProcessedInput = 0x0001
	cmLineInput      = 0x0002
	cmEchoInput      = 0x0004
	cmMouseInput     = 0x0010
	cmQuickEdit      = 0x0040
	cmExtendedFlags  = 0x0080

	evtKeyEvent = 0x0001 // INPUT_RECORD.EventType

	// KEY_EVENT_RECORD.dwControlKeyState
	ksShift     = 0x0010
	ksLeftCtrl  = 0x0008
	ksRightCtrl = 0x0004
)

var (
	keySavedMode uint32 // 启用前的控制台模式 (恢复用)
	keySavedOK   bool
	keySurrogate rune // UTF-16 高代理缓存 (与下一事件低代理组合)
)

// keyEnablePlatform 校验 stdin 为真实控制台后启用, 并清空启动前残留的
// 输入缓存 (如启动命令时敲下的回车), 避免程序一激活就吃到旧键。
func keyEnablePlatform() bool {
	h := consoleHandle()
	if h == 0 {
		return false
	}
	var mode uint32
	if r, _, _ := procGetConsoleMode.Call(h, uintptr(unsafe.Pointer(&mode))); r == 0 {
		return false // 管道 / 重定向: 不启用
	}
	keySavedMode = mode
	keySavedOK = true
	// raw 化: 关行缓冲/回显/信号处理 (Ctrl+C 变键码 3, 与 Unix 关 ISIG 一致)、
	// 鼠标事件 (避免塞满队列) 与 QuickEdit (拖选会冻结输出)。
	raw := mode &^ (cmProcessedInput | cmLineInput | cmEchoInput | cmMouseInput)
	raw |= cmExtendedFlags
	raw &^= cmQuickEdit
	_, _, _ = procSetConsoleMode.Call(h, uintptr(raw))
	_, _, _ = procFlushInputBuf.Call(h)
	return true
}

func keyRestorePlatform() {
	if keySavedOK {
		if h := consoleHandle(); h != 0 {
			_, _, _ = procSetConsoleMode.Call(h, uintptr(keySavedMode))
		}
		keySavedOK = false
	}
}

// keyResetPlatform 重置平台侧解码中间态 (key_flush 调用)。
func keyResetPlatform() { keySurrogate = 0 }

func consoleHandle() uintptr {
	h, _, _ := procGetStdHandle.Call(stdInputHandle)
	return h
}

// vkKeycode Windows 虚拟键码 -> 基础扩展键码 (控制台与 Win32 GUI 窗口共用)。
var vkKeycode = map[uint16]uint64{
	0x21: keyPgUp, 0x22: keyPgDn, 0x23: keyEnd, 0x24: keyHome,
	0x25: keyLeft, 0x26: keyUp, 0x27: keyRight, 0x28: keyDown,
	0x2D: keyIns, 0x2E: keyDel,
	0x70: keyF1, 0x71: keyF2, 0x72: keyF3, 0x73: keyF4,
	0x74: keyF5, 0x75: keyF6, 0x76: keyF7, 0x77: keyF8,
	0x78: keyF9, 0x79: keyF10, 0x7A: keyF11, 0x7B: keyF12,
}

// keyNext 非阻塞取下一个已解码键码 (PeekConsoleInputW 先行探测, 不会阻塞)。
// INPUT_RECORD/KEY_EVENT_RECORD 按固定偏移手工解码
// (sizeof(INPUT_RECORD)=20; keyDown@4 repeat@8 vk@10 char@14 state@16)。
func keyNext() (uint64, bool) {
	h := consoleHandle()
	if h == 0 {
		return 0, false
	}
	var buf [20]byte
	var n uint32
	r, _, _ := procPeekInput.Call(h, uintptr(unsafe.Pointer(&buf[0])), 1,
		uintptr(unsafe.Pointer(&n)))
	if r == 0 || n == 0 {
		return 0, false
	}
	r, _, _ = procReadInput.Call(h, uintptr(unsafe.Pointer(&buf[0])), 1,
		uintptr(unsafe.Pointer(&n)))
	if r == 0 || n == 0 {
		return 0, false
	}
	if le16(buf[0:2]) != evtKeyEvent {
		return 0, false // 鼠标/窗口/焦点等事件: 已读出并丢弃
	}
	return decodeKeyEvent(buf[4:20])
}

func le16(b []byte) uint16 { return uint16(b[0]) | uint16(b[1])<<8 }

func le32(b []byte) uint32 {
	return uint32(b[0]) | uint32(b[1])<<8 | uint32(b[2])<<16 | uint32(b[3])<<24
}

// decodeKeyEvent 解码 KEY_EVENT_RECORD (rec[0:16])。
// 只处理按下事件; Ctrl+字母沿用控制字节 (Ctrl+C=3, 与 Unix 一致),
// 方向键携带修饰变体, 重复次数入队 keyPending。
func decodeKeyEvent(rec []byte) (uint64, bool) {
	if le32(rec[0:4]) == 0 {
		return 0, false // key-up 丢弃
	}
	repeat := int(le16(rec[4:6]))
	vk := le16(rec[6:8])
	ch := rune(le16(rec[10:12]))
	state := le32(rec[12:16])
	code, ok := consoleKeyCode(vk, ch, state)
	if !ok || repeat < 1 {
		return 0, false
	}
	// 防御: 单键重复最多入队 7 次 (溢出部分丢弃, 不放大队列)
	if repeat > 8 {
		repeat = 8
	}
	for i := 1; i < repeat; i++ {
		keyPending = append(keyPending, code)
	}
	return code, true
}

// consoleKeyCode (虚拟键码, UTF-16 字符, 修饰状态) -> 键码。
// Windows 控制台后端与 Win32 GUI 窗口后端共用。
// 返回 (0, false) 表示忽略 (修饰键自身 / 未知 VK / 纯 Alt 组合等)。
func consoleKeyCode(vk uint16, ch rune, state uint32) (uint64, bool) {
	ctrl := state&(ksLeftCtrl|ksRightCtrl) != 0
	shift := state&ksShift != 0
	if base, ok := vkKeycode[vk]; ok {
		if base >= keyUp && base <= keyRight {
			if ctrl {
				return base - keyUp + keyCtrlUp, true
			}
			if shift {
				return base - keyUp + keyShiftUp, true
			}
		}
		return base, true
	}
	if ch == 0 {
		return 0, false
	}
	switch {
	case ch >= 0xD800 && ch < 0xDC00: // 高代理: 等下一事件的低代理
		keySurrogate = ch
		return 0, false
	case ch >= 0xDC00 && ch < 0xE000: // 低代理: 与缓存组合
		if keySurrogate == 0 {
			return 0, false
		}
		ch = 0x10000 + (keySurrogate-0xD800)<<10 + (ch - 0xDC00)
		keySurrogate = 0
	default:
		keySurrogate = 0
	}
	return uint64(ch), true
}
