package engine

// 跨平台键盘监听 (key_hit / get_key / key_flush, SYS 116..118)。
// 平台后端只负责启用/恢复终端原始输入与非阻塞取下一个**已解码**键码
// (keyEnablePlatform / keyRestorePlatform / keyNext);
// 键码队列与 SYS 语义统一在这里。非终端环境 (管道/重定向) 一律优雅失败:
// key_hit = 0, get_key = -1, 不阻塞 VM。

import (
	"sync"
)

// 扩展键码 (与 lib/key.cin 的 K_* 常量一致; 0..255 为原始字节)
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
)

var (
	keyMu        sync.Mutex
	keyReady     bool     // 后端是否已启用 (真实终端)
	keyQueue     []uint64 // 已解码待读键码
	keyEsc       int      // 转义序列状态 (仅 Unix 后端): 0 普通 / 1 见过 ESC / 2 CSI(ESC[) / 3 SS3(ESC O)
	keyEscNum    []byte   // CSI 参数字节
	keyPending   []uint64 // 一次喂入产出多个键码的缓冲 (如 ESC 后随普通键)
	keyForcedOff bool     // 测试钩子: 禁止启用平台后端 (不触碰真实控制台)
)

// keyEnsure 惰性启用键盘监听; 可重复调用 (恢复后再次调用会重新启用)。
func keyEnsure() {
	keyMu.Lock()
	defer keyMu.Unlock()
	if !keyReady && !keyForcedOff {
		keyReady = keyEnablePlatform()
	}
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
	keyEnsure()
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
	keyEnsure()
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
	keyEnsure()
	keyMu.Lock()
	defer keyMu.Unlock()
	keyQueue = keyQueue[:0]
	keyPending = keyPending[:0]
	keyEsc = 0
	keyEscNum = keyEscNum[:0]
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
		if code, ok := csiFinalMap[b]; ok {
			return code, true
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
	default: // 普通字节
		if b == 0x1B {
			keyEsc = 1
			return 0, false
		}
		return uint64(b), true
	}
}

// csiTildeCode 解析 ESC [ <n> ~ 系列 (Home/Ins/Del/End/PgUp/PgDn/F1..F10)。
func csiTildeCode(nums []byte) (uint64, bool) {
	n := 0
	for _, c := range nums {
		if c < '0' || c > '9' {
			return 0, false // 带修饰参数等: 丢弃
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
	}
	return 0, false
}
