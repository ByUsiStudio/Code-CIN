package engine

import "testing"

func resetKeyState() {
	keyMu.Lock()
	defer keyMu.Unlock()
	keyQueue = keyQueue[:0]
	keyPending = keyPending[:0]
	keyEsc = 0
	keyEscNum = keyEscNum[:0]
	keyU8N = 0
	keyU8Acc = 0
	keyReady = false
	keyForcedOff = true
	keyResetPlatform()
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
		"\x1B[23~": keyF11, "\x1B[24~": keyF12,
		"\x1B[1;5C": keyCtrlRight, // Ctrl+Right (XTerm 修饰参数)
		"\x1B[1;5A": keyCtrlUp,
		"\x1B[1;2B": keyShiftDown,
		"\x1B[Z":    keyShiftTab,
	}
	for seq, want := range cases {
		resetKeyState()
		codes := feedAll(t, seq)
		if len(codes) != 1 || codes[0] != want {
			t.Fatalf("序列 %q 应产出键码 %d, 实际 %v", seq, want, codes)
		}
	}
}

func TestKeyFeedUTF8Codepoints(t *testing.T) {
	resetKeyState()
	// "中" U+4E2D (3 字节 UTF-8) → 码点 0x4E2D; ASCII 混排不受影响
	codes := feedAll(t, "a\xe4\xb8\xadz")
	if len(codes) != 3 || codes[0] != 'a' || codes[1] != 0x4E2D || codes[2] != 'z' {
		t.Fatalf("UTF-8 应解码为码点, 实际 %v", codes)
	}
	// 4 字节序列: "𝄞" U+1D11E
	resetKeyState()
	codes = feedAll(t, "\xF0\x9D\x84\x9E")
	if len(codes) != 1 || codes[0] != 0x1D11E {
		t.Fatalf("4 字节 UTF-8 应产出码点 0x1D11E, 实际 %v", codes)
	}
	// 非法续字节: 丢弃序列, 字节重新按普通路径处理
	resetKeyState()
	codes = feedAll(t, "\xe4q")
	if len(codes) != 1 || codes[0] != 'q' {
		t.Fatalf("非法序列应丢弃并重新解码, 实际 %v", codes)
	}
}

func TestKeyFlushClearsQueueAndDecoder(t *testing.T) {
	resetKeyState()
	vm := newHostVM(4096)
	keyMu.Lock()
	keyQueue = append(keyQueue[:0], keyUp, keyDel)
	keyEsc = 2 // 模拟转义序列解码中间态
	keyEscNum = append(keyEscNum[:0], '1', ';')
	keyU8N = 2 // 模拟未完成的 UTF-8 序列
	keyU8Acc = 0xE4
	keyMu.Unlock()

	if got := vm.keyFlush(); got != 0 {
		t.Fatalf("key_flush 应返回 0, 实际 %d", got)
	}
	keyMu.Lock()
	defer keyMu.Unlock()
	if len(keyQueue) != 0 || keyEsc != 0 || len(keyEscNum) != 0 ||
		keyU8N != 0 || keyU8Acc != 0 {
		t.Fatalf("flush 后队列与解码状态应清空: queue=%v esc=%d num=%v u8=%d/%d",
			keyQueue, keyEsc, keyEscNum, keyU8N, keyU8Acc)
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
	if codes := feedAll(t, "\x1B[Y"); len(codes) != 0 {
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

// TestEnterDirectOutStreamsBuffer 直写模式: 激活时先落盘已缓冲输出,
// 之后 outWrite 逐条直写且不进缓冲 (Result.Output 保持为空, 不双打)。
func TestEnterDirectOutStreamsBuffer(t *testing.T) {
	resetKeyState()
	defer resetKeyState()
	var got []string
	saved := keyOutSink
	keyOutSink = func(s string) { got = append(got, s) }
	defer func() { keyOutSink = saved }()

	vm := newHostVM(4096)
	vm.outWrite("A")
	if vm.out.String() != "A" {
		t.Fatalf("直写激活前输出应留在缓冲, 实际 %q", vm.out.String())
	}
	vm.enterDirectOut()
	if len(got) != 1 || got[0] != "A" {
		t.Fatalf("激活时应把已缓冲输出落盘, 实际 %v", got)
	}
	if vm.out.Len() != 0 || !vm.outDirect {
		t.Fatal("激活后缓冲应清空且置直写标志")
	}
	vm.outWrite("B")
	if len(got) != 2 || got[1] != "B" {
		t.Fatalf("激活后输出应逐条直写, 实际 %v", got)
	}
	if vm.out.Len() != 0 {
		t.Fatalf("直写模式下不应再进缓冲, 实际 %q", vm.out.String())
	}
	if vm.outCount != 2 {
		t.Fatalf("outCount 应累计 2, 实际 %d", vm.outCount)
	}
	// 重复进入: 幂等, 不重复落盘
	vm.enterDirectOut()
	if len(got) != 2 {
		t.Fatalf("enterDirectOut 应幂等, 实际 %v", got)
	}
}

// TestOutLimitDirectMode 直写模式同样受 16 MiB 限额: 超限截断置 outOver 并停止直写。
func TestOutLimitDirectMode(t *testing.T) {
	resetKeyState()
	defer resetKeyState()
	var writes int
	saved := keyOutSink
	keyOutSink = func(string) { writes++ }
	defer func() { keyOutSink = saved }()

	vm := newHostVM(4096)
	vm.outDirect = true
	vm.outCount = maxOutputBytes - 3
	vm.outWrite("abcd") // 只剩 3 字节额度: 截断到 3 并置 outOver
	if writes != 1 {
		t.Fatalf("截断部分应直写一次, 实际 %d", writes)
	}
	if !vm.outOver {
		t.Fatal("超限应置 outOver")
	}
	vm.outWrite("x") // outOver 后丢弃
	if writes != 1 {
		t.Fatalf("outOver 后不应再直写, 实际 %d", writes)
	}
	if vm.outCount != maxOutputBytes {
		t.Fatalf("outCount 应停在限额, 实际 %d", vm.outCount)
	}
}
