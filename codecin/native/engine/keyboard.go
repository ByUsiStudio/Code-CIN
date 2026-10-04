package engine

// 跨平台键盘监听 (key_hit / get_key / key_flush, SYS 116..118)。
// 平台后端只负责启用/恢复终端原始输入与非阻塞取下一个**已解码**键码
// (keyEnablePlatform / keyRestorePlatform / keyNext);
// 键码队列与 SYS 语义统一在这里。非终端环境 (管道/重定向) 一律优雅失败:
// key_hit = 0, get_key = -1, 不阻塞 VM。

import (
	"os"
	"sync"
)

const (
	keyUp    = 1001
	keyDown  = 1002
	keyLeft  = 1003
	keyRight = 1004
	keyHome  = 1005
	keyEnd   = 1006
	keyPgUp  = 1007
	keyPgDn  = 1008
	keyIns   = 1009
	keyDel   = 1010
	keyF1    = 1021
	keyF2    = 1022
	keyF3    = 1023
	keyF4    = 1024
	keyF5    = 1025
	keyF6    = 1026
	keyF7    = 1027
	keyF8    = 1028
	keyF9    = 1029
	keyF10   = 1030
	keyF11   = 1031
	keyF12   = 1032
)

const (
	keyCtrlUp     = 1101
	keyCtrlDown   = 1102
	keyCtrlLeft   = 1103
	keyCtrlRight  = 1104
	keyShiftUp    = 1105
	keyShiftDown  = 1106
	keyShiftLeft  = 1107
	keyShiftRight = 1108
	keyShiftTab   = 1109
)

func arrowVariant(base uint64, mod int) uint64 {
	m := mod - 1
	if m&4 != 0 {
		return base - keyUp + keyCtrlUp
	}
	if m&1 != 0 {
		return base - keyUp + keyShiftUp
	}
	return base
}

var (
	keyMu        sync.Mutex
	keyReady     bool     // 后端是否已启用 (真实终端)
	keyQueue     []uint64 // 已解码待读键码
	keyEsc       int      // 转义序列状态 (仅 Unix 后端): 0 普通 / 1 见过 ESC / 2 CSI(ESC[) / 3 SS3(ESC O)
	keyEscNum    []byte   // CSI 参数字节
	keyPending   []uint64 // 一次喂入产出多个键码的缓冲 (如 ESC 后随普通键)
	keyU8N       int      // 未完成的 UTF-8 序列剩余续字节数 (0 = 无)
	keyU8Acc     uint64   // UTF-8 已累计码点
	keyForcedOff bool     // 测试钩子: 禁止启用平台后端 (不触碰真实控制台)
)

// keyOutSink 键盘监听激活后的直写输出通道。
// os.File.Write 无用户态缓冲, 每次直落 fd -> 天然实时; 测试可替换。
var keyOutSink = func(s string) { _, _ = os.Stdout.WriteString(s) }

// keyEnsure 惰性启用键盘监听; 可重复调用 (恢复后再次调用会重新启用)。
// 返回是否"本次调用刚激活" (真实终端首次启用), 供 VM 切换直写输出。
func keyEnsure() bool {
	keyMu.Lock()
	defer keyMu.Unlock()
	if !keyReady && !keyForcedOff {
		keyReady = keyEnablePlatform()
		return keyReady
	}
	return false
}

// keyboardRestore 恢复终端设置 (幂等; 引擎 Run 出口统一 defer)。
func keyboardRestore() {
	keyMu.Lock()
	defer keyMu.Unlock()
	if keyReady {
		keyRestorePlatform()
		keyReady = false
	}
}

// keyDrainLocked 从后端读键码填充队列 (须持 keyMu), 返回是否读到。
// 后端未启用 (非终端) 时直接跳过: keyNext 不得触碰 stdin, 否则
// 管道/重定向下非阻塞假设不成立, read 可能永久阻塞。
func keyDrainLocked() bool {
	if !keyReady {
		return false
	}
	produced := false
	for {
		code, ok := keyNext()
		if !ok {
			break
		}
		keyQueue = append(keyQueue, code)
		produced = true
	}
	return produced
}

// keyHit SYS 116: key_hit() -> 1 有待读按键 / 0 无
func (vm *vmState) keyHit() uint64 {
	if keyEnsure() {
		vm.enterDirectOut()
	}
	keyMu.Lock()
	defer keyMu.Unlock()
	if len(keyQueue) == 0 {
		keyDrainLocked()
	}
	if len(keyQueue) > 0 {
		return 1
	}
	return 0
}

// keyGet SYS 117: get_key() -> 键码 / -1 (mask64) 无按键
func (vm *vmState) keyGet() uint64 {
	if keyEnsure() {
		vm.enterDirectOut()
	}
	keyMu.Lock()
	defer keyMu.Unlock()
	if len(keyQueue) == 0 {
		keyDrainLocked()
	}
	if len(keyQueue) == 0 {
		return mask64
	}
	code := keyQueue[0]
	keyQueue = keyQueue[1:]
	return code
}

// keyFlush SYS 118: key_flush() 清空键码队列与解码中间态 -> 0
func (vm *vmState) keyFlush() uint64 {
	if keyEnsure() {
		vm.enterDirectOut()
	}
	keyMu.Lock()
	defer keyMu.Unlock()
	keyQueue = keyQueue[:0]
	keyPending = keyPending[:0]
	keyEsc = 0
	keyEscNum = keyEscNum[:0]
	keyU8N = 0
	keyU8Acc = 0
	keyResetPlatform()
	return 0
}

// ---------------- 转义序列解码 (Unix 后端逐字节喂入) ----------------

// csiFinalMap CSI 最终字节 (无参数或忽略修饰参数) -> 键码
var csiFinalMap = map[byte]uint64{
	'A': keyUp, 'B': keyDown, 'C': keyRight, 'D': keyLeft,
	'H': keyHome, 'F': keyEnd,
}

// keyFeed 喂一个原始字节, 返回 (键码, 是否构成完整键码)。
// 需要更多字节/未知序列返回 (0, false)。
// 单次喂入可能对应两个键码 (如先按 ESC 再按普通键), 溢出部分缓存在
// keyPending, 由下一次调用优先产出。
// 多字节 UTF-8 序列在普通状态解码为 Unicode 码点 (>255; 与 Windows
// ReadConsoleInputW 的 UTF-16 输出一致), 非法序列按原字节产出。
func keyFeed(b byte) (uint64, bool) {
	if len(keyPending) > 0 {
		code := keyPending[0]
		keyPending = keyPending[1:]
		return code, true
	}
	switch keyEsc {
	case 1: // 见过 ESC
		switch b {
		case '[':
			keyEsc = 2
			keyEscNum = keyEscNum[:0]
		case 'O':
			keyEsc = 3
		default:
			// 单独按下的 ESC: 立即产出; 当前字节重新解码
			keyEsc = 0
			if b == 0x1B {
				keyEsc = 1
				return 0x1B, true
			}
			keyPending = append(keyPending, uint64(b))
			return 0x1B, true
		}
		return 0, false
	case 2: // CSI: ESC [ <参数字节们> <最终字节>
		if (b >= '0' && b <= '9') || b == ';' {
			keyEscNum = append(keyEscNum, b)
			return 0, false
		}
		keyEsc = 0
		if b == '~' {
			return csiTildeCode(keyEscNum)
		}
		if b == 'Z' {
			return keyShiftTab, true // Shift+Tab
		}
		if base, ok := csiFinalMap[b]; ok {
			if mod, ok2 := csiModLast(keyEscNum); ok2 {
				if v := arrowVariant(base, mod); v != base {
					return v, true
				}
			}
			return base, true
		}
		return 0, false // 未知序列: 整体丢弃
	case 3: // SS3: ESC O <单字节>
		keyEsc = 0
		switch b {
		case 'A':
			return keyUp, true
		case 'B':
			return keyDown, true
		case 'C':
			return keyRight, true
		case 'D':
			return keyLeft, true
		case 'H':
			return keyHome, true
		case 'F':
			return keyEnd, true
		case 'P':
			return keyF1, true
		case 'Q':
			return keyF2, true
		case 'R':
			return keyF3, true
		case 'S':
			return keyF4, true
		}
		return 0, false
	default: // 普通字节 (含 UTF-8 多字节解码)
		return keyFeedPlain(b)
	}
}

// keyFeedPlain 普通字节解码: UTF-8 多字节序列 → 码点, 其余直通。
func keyFeedPlain(b byte) (uint64, bool) {
	if keyU8N > 0 {
		if b&0xC0 != 0x80 {
			// 非法续字节: 丢弃整个序列, 本字节按普通字节重新处理
			keyU8N = 0
			keyU8Acc = 0
			return keyFeedPlain(b)
		}
		keyU8Acc = keyU8Acc<<6 | uint64(b&0x3F)
		keyU8N--
		if keyU8N > 0 {
			return 0, false
		}
		cp := keyU8Acc
		keyU8Acc = 0
		// 过长编码 / 代理区 / 越界: 回退按原始字节产出
		if cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF) {
			return 0xFFFD, true
		}
		return cp, true
	}
	if b == 0x1B {
		keyEsc = 1
		return 0, false
	}
	switch {
	case b >= 0xC2 && b <= 0xDF:
		keyU8N = 1
		keyU8Acc = uint64(b & 0x1F)
		return 0, false
	case b >= 0xE0 && b <= 0xEF:
		keyU8N = 2
		keyU8Acc = uint64(b & 0x0F)
		return 0, false
	case b >= 0xF0 && b <= 0xF4:
		keyU8N = 3
		keyU8Acc = uint64(b & 0x07)
		return 0, false
	}
	return uint64(b), true
}

// csiModLast 取 CSI 参数最后一段分号后的修饰值 (XTerm 编码: 1+shift|meta|ctrl)。
// 无有效修饰参数返回 (1, false)。
func csiModLast(nums []byte) (int, bool) {
	start := -1
	for i, c := range nums {
		if c == ';' {
			start = i + 1
		} else if c < '0' || c > '9' {
			return 1, false // 带子参数 (':') 等复杂序列: 视为无修饰
		}
	}
	if start < 0 {
		return 1, false
	}
	n := 0
	if start >= len(nums) {
		return 1, false
	}
	for _, c := range nums[start:] {
		n = n*10 + int(c-'0')
	}
	if n < 2 {
		return 1, false
	}
	return n, true
}

// csiTildeCode 解析 ESC [ <n> ; <mod> ~ 系列
// (Home/Ins/Del/End/PgUp/PgDn/F1..F12; 修饰值仅作用于导航键时忽略)。
func csiTildeCode(nums []byte) (uint64, bool) {
	n := 0
	for _, c := range nums {
		if c == ';' {
			break // 修饰参数不影响 tilde 键
		}
		if c < '0' || c > '9' {
			return 0, false
		}
		n = n*10 + int(c-'0')
	}
	switch n {
	case 1:
		return keyHome, true
	case 2:
		return keyIns, true
	case 3:
		return keyDel, true
	case 4:
		return keyEnd, true
	case 5:
		return keyPgUp, true
	case 6:
		return keyPgDn, true
	case 11:
		return keyF1, true
	case 12:
		return keyF2, true
	case 13:
		return keyF3, true
	case 14:
		return keyF4, true
	case 15:
		return keyF5, true
	case 17:
		return keyF6, true
	case 18:
		return keyF7, true
	case 19:
		return keyF8, true
	case 20:
		return keyF9, true
	case 21:
		return keyF10, true
	case 23:
		return keyF11, true
	case 24:
		return keyF12, true
	}
	return 0, false
}
