package engine

import "testing"

// 键盘监听 (SYS 116..118) 的引擎侧单元测试: 只覆盖纯逻辑
// (转义序列解码 / 键码队列 / SYS 语义), 不依赖真实终端输入。

// resetKeyState 重置键盘全局状态并禁用平台后端 (测试不触碰真实控制台)。
func resetKeyState() {
	keyMu.Lock()
	defer keyMu.Unlock()
	keyQueue = keyQueue[:0]
	keyPending = keyPending[:0]
	keyEsc = 0
	keyEscNum = keyEscNum[:0]
	keyReady = false
	keyForcedOff = true
}

// feedAll 依次喂入字节, 返回全部完整键码。
func feedAll(t *testing.T, s string) []uint64 {
	t.Helper()
	var codes []uint64
	for i := 0; i < len(s); i++ {
		if code, done := keyFeed(s[i]); done {
			codes = append(codes, code)
		}
	}
	// 溢出的键码 (如 ESC 后随普通键) 补进结果
	for len(keyPending) > 0 {
		codes = append(codes, keyPending[0])
		keyPending = keyPending[1:]
	}
	return codes
}

func TestKeyFeedPlainBytes(t *testing.T) {
	resetKeyState()
	codes := feedAll(t, "A z9")
	if len(codes) != 4 || codes[0] != 'A' || codes[1] != ' ' ||
		codes[2] != 'z' || codes[3] != '9' {
		t.Fatalf("普通字节应直通, 实际 %v", codes)
	}
	// Ctrl 组合键即原始控制字节 (Ctrl+C = 3)
	codes = feedAll(t, "\x03")
	if len(codes) != 1 || codes[0] != 3 {
		t.Fatalf("Ctrl+C 应产出原始字节 3, 实际 %v", codes)
	}
}

func TestKeyFeedCSIArrowsAndNav(t *testing.T) {
	resetKeyState()
	cases := map[string]uint64{
		"\x1B[A": keyUp, "\x1B[B": keyDown,
		"\x1B[C": keyRight, "\x1B[D": keyLeft,
		"\x1B[H": keyHome, "\x1B[F": keyEnd,
		"\x1BOC":  keyRight, // SS3 应用模式方向键
		"\x1B[1~": keyHome, "\x1B[2~": keyIns, "\x1B[3~": keyDel,
		"\x1B[4~": keyEnd, "\x1B[5~": keyPgUp, "\x1B[6~": keyPgDn,
		"\x1B[15~": keyF5, "\x1B[17~": keyF6, "\x1B[21~": keyF10,
		"\x1B[1;5C": keyRight, // 带修饰参数 (Ctrl+Right): 忽略参数
	}
	for seq, want := range cases {
		resetKeyState()
		codes := feedAll(t, seq)
		if len(codes) != 1 || codes[0] != want {
			t.Fatalf("序列 %q 应产出键码 %d, 实际 %v", seq, want, codes)
		}
	}
}

func TestKeyFeedLoneEscapeAndUnknown(t *testing.T) {
	resetKeyState()
	// 单独按 ESC 后随普通键: ESC 立即产出, 后续字节正常
	codes := feedAll(t, "\x1Bx")
	if len(codes) != 2 || codes[0] != 0x1B || codes[1] != 'x' {
		t.Fatalf("ESC+普通键应产出 [27 x], 实际 %v", codes)
	}
	// 双击 ESC: 第一个产出, 第二个进入状态 (下一次产出或保持中间态)
	resetKeyState()
	codes = feedAll(t, "\x1B\x1B")
	if len(codes) != 1 || codes[0] != 0x1B {
		t.Fatalf("双击 ESC 应产出一次 27, 实际 %v", codes)
	}
	// 未知 CSI 最终字节: 整体丢弃, 不产出
	resetKeyState()
	if codes := feedAll(t, "\x1B[Z"); len(codes) != 0 {
		t.Fatalf("未知序列应整体丢弃, 实际 %v", codes)
	}
	// 状态应已复位: 后续普通字节正常
	if codes := feedAll(t, "q"); len(codes) != 1 || codes[0] != 'q' {
		t.Fatalf("未知序列后状态应复位, 实际 %v", codes)
	}
}

func TestKeyGetConsumesQueueInOrder(t *testing.T) {
	resetKeyState()
	vm := newHostVM(4096)
	keyMu.Lock()
	keyQueue = append(keyQueue[:0], 'A', keyDown)
	keyMu.Unlock()

	if got := vm.keyGet(); got != 'A' {
		t.Fatalf("keyGet 应按序弹出 'A', 实际 %d", got)
	}
	if got := vm.keyGet(); got != keyDown {
		t.Fatalf("keyGet 应按序弹出 keyDown, 实际 %d", got)
	}
	// 队列空后: 各平台在无输入时统一返回 -1 (mask64), 不阻塞
	if got := vm.keyGet(); got != mask64 {
		t.Fatalf("队列空且无输入应返回 -1 (mask64), 实际 %d", got)
	}
}

func TestKeyFlushClearsQueueAndDecoder(t *testing.T) {
	resetKeyState()
	vm := newHostVM(4096)
	keyMu.Lock()
	keyQueue = append(keyQueue[:0], keyUp, keyDel)
	keyEsc = 2 // 模拟转义序列解码中间态
	keyEscNum = append(keyEscNum[:0], '1', ';')
	keyMu.Unlock()

	if got := vm.keyFlush(); got != 0 {
		t.Fatalf("key_flush 应返回 0, 实际 %d", got)
	}
	keyMu.Lock()
	defer keyMu.Unlock()
	if len(keyQueue) != 0 || keyEsc != 0 || len(keyEscNum) != 0 {
		t.Fatalf("flush 后队列与解码状态应清空: queue=%v esc=%d num=%v",
			keyQueue, keyEsc, keyEscNum)
	}
}

func TestKeyHitAndDrainGuard(t *testing.T) {
	resetKeyState()
	vm := newHostVM(4096)
	// 后端未启用 (非终端) 时: drain 不触碰 stdin, key_hit 恒 0
	keyMu.Lock()
	keyReady = false
	keyMu.Unlock()
	if got := vm.keyHit(); got != 0 {
		t.Fatalf("未启用后端时 key_hit 应为 0, 实际 %d", got)
	}
	// 有缓存的键码时: key_hit 与启用状态无关
	keyMu.Lock()
	keyQueue = append(keyQueue[:0], 'z')
	keyMu.Unlock()
	if got := vm.keyHit(); got != 1 {
		t.Fatalf("队列有键码时 key_hit 应为 1, 实际 %d", got)
	}
}
