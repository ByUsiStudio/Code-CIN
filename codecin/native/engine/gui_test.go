package engine

import (
	"image"
	"testing"
)

// resetGuiState 恢复 GUI 全局状态到"从未打开" (隔离用例间影响)。
func resetGuiState() {
	guiMu.Lock()
	guiOpen = false
	guiClosedF = false
	guiW, guiH = 0, 0
	guiMouseX, guiMouseY, guiMouseBtn = 0, 0, 0
	guiBuffer = nil
	guiForcedOff = true // 默认禁真实窗口, 单测不触碰显示服务
	guiMu.Unlock()
}

func TestGuiNewForcedOffGraceful(t *testing.T) {
	resetGuiState()
	defer resetGuiState()
	vm := newHostVM(4096)
	// 非窗口环境 (管道 / 不支持平台 / 测试钩子): gui_new 返回 -1, 不报错
	if got := vm.guiNew(320, 240, "t"); got != mask64 {
		t.Fatalf("guiForcedOff 下 gui_new 应返回 -1, 实际 %d", got)
	}
	if got := vm.guiActiveQ(); got != 0 {
		t.Fatalf("gui_active 应为 0, 实际 %d", got)
	}
	// 后续操作全部优雅失败
	if got := vm.guiUpdate(); got != mask64 {
		t.Fatalf("无窗口 gui_update 应返回 -1, 实际 %d", got)
	}
	if got := vm.guiClose(); got != 0 {
		t.Fatalf("gui_close 恒返回 0, 实际 %d", got)
	}
	if got := vm.guiClosedQ(); got != 0 {
		t.Fatalf("gui_closed 应为 0, 实际 %d", got)
	}
	if got := vm.mouseX(); got != mask64 {
		t.Fatalf("无窗口 mouse_x 应返回 -1, 实际 %d", got)
	}
	if got := vm.mouseY(); got != mask64 {
		t.Fatalf("无窗口 mouse_y 应返回 -1, 实际 %d", got)
	}
	if got := vm.mouseBtn(); got != mask64 {
		t.Fatalf("无窗口 mouse_button 应返回 -1, 实际 %d", got)
	}
}

// openFakeWindow 直接置全局状态模拟"窗口已打开" (不依赖平台后端)。
func openFakeWindow(w, h int) *image.RGBA {
	buf := image.NewRGBA(image.Rect(0, 0, w, h))
	guiMu.Lock()
	guiOpen = true
	guiClosedF = false
	guiW, guiH = w, h
	guiMouseX, guiMouseY, guiMouseBtn = 0, 0, 0
	guiBuffer = buf
	guiMu.Unlock()
	return buf
}

func TestGuiMouseAndStateQueries(t *testing.T) {
	resetGuiState()
	defer resetGuiState()
	vm := newHostVM(4096)
	buf := openFakeWindow(80, 60)

	if got := vm.guiActiveQ(); got != 1 {
		t.Fatalf("gui_active 应为 1, 实际 %d", got)
	}
	if got := vm.guiClosedQ(); got != 0 {
		t.Fatalf("未请求关闭时 gui_closed 应为 0, 实际 %d", got)
	}
	guiSetMouse(10, 20, -1)
	guiMouseButtonBit(1, true) // 左键按下
	if got := vm.mouseX(); got != 10 {
		t.Fatalf("mouse_x 应为 10, 实际 %d", got)
	}
	if got := vm.mouseY(); got != 20 {
		t.Fatalf("mouse_y 应为 20, 实际 %d", got)
	}
	if got := vm.mouseBtn(); got != 1 {
		t.Fatalf("mouse_button 应为 1 (左键), 实际 %d", got)
	}
	guiMouseButtonBit(4, true) // 再按中键
	if got := vm.mouseBtn(); got != 5 {
		t.Fatalf("mouse_button 应为 5 (左|中), 实际 %d", got)
	}
	guiMouseButtonBit(1, false) // 左键抬起
	if got := vm.mouseBtn(); got != 4 {
		t.Fatalf("mouse_button 应为 4 (中键), 实际 %d", got)
	}
	// 用户请求关闭 (点 X / Alt+F4)
	guiMarkClosed()
	if got := vm.guiClosedQ(); got != 1 {
		t.Fatalf("guiMarkClosed 后 gui_closed 应为 1, 实际 %d", got)
	}
	_ = buf
}

func TestGuiSnapshotCopiesPixels(t *testing.T) {
	resetGuiState()
	defer resetGuiState()
	buf := openFakeWindow(2, 1)
	buf.Pix[0], buf.Pix[1], buf.Pix[2], buf.Pix[3] = 1, 2, 3, 4

	snap := guiSnapshot(buf)
	if snap == nil {
		t.Fatal("窗口打开时 guiSnapshot 应返回像素副本")
	}
	if len(snap) != len(buf.Pix) {
		t.Fatalf("快照长度应与缓冲一致, 实际 %d vs %d", len(snap), len(buf.Pix))
	}
	if snap[0] != 1 || snap[1] != 2 || snap[2] != 3 || snap[3] != 4 {
		t.Fatalf("快照内容不符: %v", snap[:4])
	}
	// 改原缓冲不影响已有快照 (防撕裂)
	buf.Pix[0] = 255
	if snap[0] != 1 {
		t.Fatal("快照应是独立副本, 不随后备缓冲变化")
	}
	// 未匹配的缓冲 / 关闭状态返回 nil
	if guiSnapshot(image.NewRGBA(image.Rect(0, 0, 1, 1))) != nil {
		t.Fatal("非当前窗口缓冲的快照应返回 nil")
	}
	guiMu.Lock()
	guiOpen = false
	guiMu.Unlock()
	if guiSnapshot(buf) != nil {
		t.Fatal("窗口关闭后快照应返回 nil")
	}
}

func TestGuiInjectKeySharesKeyboardQueue(t *testing.T) {
	resetGuiState()
	defer resetKeyState()
	// GUI 窗口按键注入与终端共用同一键码队列 (get_key 可读)
	guiInjectKey('A')
	guiInjectKey(1003) // keyLeft
	keyMu.Lock()
	defer keyMu.Unlock()
	if len(keyQueue) != 2 || keyQueue[0] != 'A' || keyQueue[1] != 1003 {
		t.Fatalf("guiInjectKey 应进入键盘队列, 实际 %v", keyQueue)
	}
}
