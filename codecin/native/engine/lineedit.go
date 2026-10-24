package engine

// 跨平台交互式行输入 (input / input_str 的终端路径)。
//
// 与 get_key 共用同一套键码队列与 raw 终端 (keyEnsure / keyOutSink):
// 行编辑由引擎自己完成, 不再依赖操作系统的行规程 (Windows ReadConsoleW /
// Unix canonical 模式 + 内核行编辑)。三条输入通道统一为一种事件监听:
//   - 真实终端: 逐字节/逐事件解码后的键码 (Windows ReadConsoleInputW,
//     Unix termios + 转义序列/UTF-8 解码);
//   - GUI 窗口: 窗口过程把按键注入 keyQueue (guiPressKey);
//   - 虚拟键盘: 系统输入注入的按键同样进入上面的通道。
//
// 支持: 字符输入 / 退格 / Delete / 左右 / Home / End / Ctrl+A / Ctrl+E /
// Ctrl+U / Ctrl+K / Ctrl+W / Ctrl+左/右 / Enter / Ctrl+D(EOF) /
// Ctrl+C(中断)。光标重绘用 VT/ANSI 相对移动 (Windows 控制台已开 VT 输出,
// Unix 终端原生支持)。
//
// 既没有真实终端也没有 GUI 窗口时返回 used=false, 由 stdin.go 回退到
// 字节流读取 (管道/重定向语义与旧版本完全一致)。

import (
	"strconv"
	"time"
)

const (
	lineCodeEnter     = 13
	lineCodeLF        = 10
	lineCodeTab       = 9
	lineCodeBackspace = 8
	lineCodeDeleteCh  = 127
	lineCodeCtrlA     = 1
	lineCodeCtrlC     = 3
	lineCodeCtrlD     = 4
	lineCodeCtrlE     = 5
	lineCodeCtrlK     = 11
	lineCodeCtrlU     = 21
	lineCodeCtrlW     = 23
	lineCodeCtrlZ     = 26
	linePollInterval  = 5 * time.Millisecond
)

// stdinLineEditor 用键码队列读取一行。返回 (行, 是否成功, 是否接管)。
// 接管时行编辑与回显已由本函数完成; 未接管 (非终端且无 GUI 窗口) 时
// 调用方走通用字节流路径。
func stdinLineEditor() (string, bool, bool) {
	if keyForcedOff {
		return "", false, false
	}
	// 真实终端: 启用 raw 模式, 之后所有按键统一走键码队列。
	keyEnsure()
	keyMu.Lock()
	raw := keyReady
	keyMu.Unlock()
	if !raw && !guiWindowOpen() {
		return "", false, false
	}

	var buf []rune
	pos := 0
	for {
		// 窗口已关闭: 不再等待按键, 按 EOF 返回 (避免永久阻塞)
		if guiUserClosed() {
			return "", false, true
		}
		code, ok := lineKeyPoll()
		if !ok {
			time.Sleep(linePollInterval)
			continue
		}
		switch code {
		case lineCodeEnter, lineCodeLF:
			keyOutSink("\r\n")
			return string(buf), true, true
		case lineCodeBackspace, lineCodeDeleteCh:
			if pos > 0 {
				w := runeWidth(buf[pos-1])
				buf = append(buf[:pos-1], buf[pos:]...)
				pos--
				cursorLeft(w)
				lineRewrite(buf, pos)
			}
		case keyDel:
			if pos < len(buf) {
				buf = append(buf[:pos], buf[pos+1:]...)
				lineRewrite(buf, pos)
			}
		case keyLeft:
			if pos > 0 {
				pos--
				cursorLeft(runeWidth(buf[pos]))
			}
		case keyRight:
			if pos < len(buf) {
				cursorRight(runeWidth(buf[pos]))
				pos++
			}
		case keyHome, lineCodeCtrlA:
			cursorLeft(totalWidth(buf[:pos]))
			pos = 0
		case keyEnd, lineCodeCtrlE:
			cursorRight(totalWidth(buf[pos:]))
			pos = len(buf)
		case keyCtrlLeft:
			np := wordLeftPos(buf, pos)
			cursorLeft(totalWidth(buf[np:pos]))
			pos = np
		case keyCtrlRight:
			np := wordRightPos(buf, pos)
			cursorRight(totalWidth(buf[pos:np]))
			pos = np
		case lineCodeCtrlU:
			if pos > 0 {
				cursorLeft(totalWidth(buf[:pos]))
				buf = append([]rune(nil), buf[pos:]...)
				pos = 0
				lineRewrite(buf, 0)
			}
		case lineCodeCtrlK:
			buf = buf[:pos]
			keyOutSink("\x1b[K")
		case lineCodeCtrlW:
			start := wordLeftPos(buf, pos)
			if start < pos {
				cursorLeft(totalWidth(buf[start:pos]))
				buf = append(buf[:start], buf[pos:]...)
				pos = start
				lineRewrite(buf, pos)
			}
		case lineCodeCtrlC:
			// 与行规程 (ISIG) 下 Ctrl+C 一致: 恢复终端后中断进程。
			keyOutSink("^C\r\n")
			raiseLineInterrupt()
			return "", false, true
		case lineCodeCtrlZ:
			keyOutSink("\r\n")
			return "", false, true
		case lineCodeCtrlD:
			if len(buf) == 0 {
				keyOutSink("\r\n")
				return "", false, true // EOF
			}
			if pos < len(buf) { // 非空行: 删除光标处字符 (与 readline 一致)
				buf = append(buf[:pos], buf[pos+1:]...)
				lineRewrite(buf, pos)
			}
		default:
			if code > 0x10FFFF {
				continue
			}
			r := rune(code)
			if r == lineCodeTab {
				r = '\t'
			} else if r < 0x20 || r == lineCodeDeleteCh {
				continue // 其余控制字符忽略 (功能键/修饰键等)
			}
			old := pos
			buf = append(buf, 0)
			copy(buf[old+1:], buf[old:])
			buf[old] = r
			pos = old + 1
			lineRewrite(buf, old) // 重画插入点之后的整段, 光标回到 old
			cursorRight(runeWidth(r))
		}
	}
}

// lineKeyPoll 非阻塞取一个键码 (与 get_key 相同的队列语义)。
func lineKeyPoll() (uint64, bool) {
	keyMu.Lock()
	defer keyMu.Unlock()
	if len(keyQueue) == 0 {
		keyDrainLocked()
	}
	if len(keyQueue) == 0 {
		return 0, false
	}
	code := keyQueue[0]
	keyQueue = keyQueue[1:]
	return code, true
}

// lineRewrite 从 from 处重画到行尾并清除残留, 结束时光标回到 from。
// 调用前光标必须已在 from 对应的列上。
func lineRewrite(buf []rune, from int) {
	tail := string(buf[from:])
	keyOutSink(tail)
	keyOutSink("\x1b[K")
	cursorLeft(totalWidth(buf[from:]))
}

func cursorLeft(n int) {
	if n > 0 {
		keyOutSink("\x1b[" + strconv.Itoa(n) + "D")
	}
}

func cursorRight(n int) {
	if n > 0 {
		keyOutSink("\x1b[" + strconv.Itoa(n) + "C")
	}
}

// totalWidth 一段文本在终端上的显示列数 (CJK 等宽字符按 2 列)。
func totalWidth(rs []rune) int {
	w := 0
	for _, r := range rs {
		w += runeWidth(r)
	}
	return w
}

// runeWidth 单字符显示宽度 (近似 East Asian Width: 宽字符 2 列)。
func runeWidth(r rune) int {
	switch {
	case r == '\t':
		return 4
	case r < 0x20 || (r >= 0x7F && r < 0xA0):
		return 0
	case r >= 0x1100 && (r <= 0x115F || r == 0x2329 || r == 0x232A ||
		(r >= 0x2E80 && r <= 0xA4CF && r != 0x303F) ||
		(r >= 0xAC00 && r <= 0xD7A3) ||
		(r >= 0xF900 && r <= 0xFAFF) ||
		(r >= 0xFE30 && r <= 0xFE6F) ||
		(r >= 0xFF00 && r <= 0xFF60) ||
		(r >= 0xFFE0 && r <= 0xFFE6) ||
		(r >= 0x1F300 && r <= 0x1F64F) ||
		(r >= 0x1F900 && r <= 0x1F9FF) ||
		(r >= 0x20000 && r <= 0x3FFFD)):
		return 2
	}
	return 1
}

// wordLeftPos 上一个词首 (Ctrl+Left / Ctrl+W)。
func wordLeftPos(buf []rune, pos int) int {
	i := pos
	for i > 0 && buf[i-1] == ' ' {
		i--
	}
	for i > 0 && buf[i-1] != ' ' {
		i--
	}
	return i
}

// wordRightPos 下一个词尾 (Ctrl+Right)。
func wordRightPos(buf []rune, pos int) int {
	i := pos
	for i < len(buf) && buf[i] != ' ' {
		i++
	}
	for i < len(buf) && buf[i] == ' ' {
		i++
	}
	return i
}
