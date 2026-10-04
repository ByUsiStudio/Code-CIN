//go:build windows

package engine

import "testing"

// Windows 键盘后端 (ReadConsoleInputW) 的纯逻辑测试:
// KEY_EVENT_RECORD 解码 / VK 映射 / 修饰变体 / UTF-16 代理对组合。

func TestDecodeKeyEventBasic(t *testing.T) {
	resetKeyState()
	defer resetKeyState()
	// 普通字符 'A': keyDown=1 repeat=1 vk=0x41 char='A' state=0
	rec := make([]byte, 16)
	putLE32(rec[0:4], 1)
	putLE16(rec[4:6], 1)
	putLE16(rec[6:8], 0x41)
	putLE16(rec[10:12], 'A')
	code, ok := decodeKeyEvent(rec)
	if !ok || code != 'A' {
		t.Fatalf("普通字符应产出 'A', 实际 %d ok=%v", code, ok)
	}
	// key-up 事件丢弃
	putLE32(rec[0:4], 0)
	if _, ok := decodeKeyEvent(rec); ok {
		t.Fatal("key-up 事件应被丢弃")
	}
	// Ctrl+C: char=3 (控制字节, 与 Unix 一致)
	putLE32(rec[0:4], 1)
	putLE16(rec[6:8], 0x43)
	putLE16(rec[10:12], 3)
	putLE32(rec[12:16], ksLeftCtrl)
	code, ok = decodeKeyEvent(rec)
	if !ok || code != 3 {
		t.Fatalf("Ctrl+C 应产出 3, 实际 %d ok=%v", code, ok)
	}
}

func TestDecodeKeyEventVKAndModifiers(t *testing.T) {
	resetKeyState()
	defer resetKeyState()
	rec := make([]byte, 16)
	putLE32(rec[0:4], 1)
	putLE16(rec[4:6], 1)
	// 方向键: VK_LEFT -> keyLeft
	putLE16(rec[6:8], 0x25)
	if code, ok := decodeKeyEvent(rec); !ok || code != keyLeft {
		t.Fatalf("VK_LEFT 应产出 keyLeft, 实际 %d ok=%v", code, ok)
	}
	// Ctrl+Left -> keyCtrlLeft
	putLE32(rec[12:16], ksLeftCtrl)
	if code, ok := decodeKeyEvent(rec); !ok || code != keyCtrlLeft {
		t.Fatalf("Ctrl+VK_LEFT 应产出 keyCtrlLeft, 实际 %d ok=%v", code, ok)
	}
	// Shift+Left -> keyShiftLeft
	putLE32(rec[12:16], ksShift)
	if code, ok := decodeKeyEvent(rec); !ok || code != keyShiftLeft {
		t.Fatalf("Shift+VK_LEFT 应产出 keyShiftLeft, 实际 %d ok=%v", code, ok)
	}
	// F11 (VK 0x7A): msvcrt 扫描码表缺失, 现由 VK 表补齐
	putLE32(rec[12:16], 0)
	putLE16(rec[6:8], 0x7A)
	if code, ok := decodeKeyEvent(rec); !ok || code != keyF11 {
		t.Fatalf("VK_F11 应产出 keyF11, 实际 %d ok=%v", code, ok)
	}
	// 按键重复: repeat=3 -> 1 个直接产出 + 2 个进 keyPending
	putLE16(rec[4:6], 3)
	putLE16(rec[6:8], 0x25)
	if _, ok := decodeKeyEvent(rec); !ok {
		t.Fatal("重复按键应产出")
	}
	if len(keyPending) != 2 {
		t.Fatalf("repeat=3 应溢出 2 个到 keyPending, 实际 %d", len(keyPending))
	}
}

func TestConsoleKeyCodeSurrogatePair(t *testing.T) {
	resetKeyState()
	defer resetKeyState()
	// U+1D11E = D834 + DD1E ( surrogate pair)
	if code, ok := consoleKeyCode(0, 0xD834, 0); ok || code != 0 {
		t.Fatalf("高代理应缓存等待, 实际 %d ok=%v", code, ok)
	}
	code, ok := consoleKeyCode(0, 0xDD1E, 0)
	if !ok || code != 0x1D11E {
		t.Fatalf("代理对应组合为 0x1D11E, 实际 %d ok=%v", code, ok)
	}
}

func putLE16(b []byte, v uint16) { b[0] = byte(v); b[1] = byte(v >> 8) }

func putLE32(b []byte, v uint32) {
	b[0] = byte(v)
	b[1] = byte(v >> 8)
	b[2] = byte(v >> 16)
	b[3] = byte(v >> 24)
}
