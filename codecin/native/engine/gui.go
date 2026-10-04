package engine

import (
	"image"
	"sync"
)

var (
	guiMu        sync.Mutex
	guiOpen      bool
	guiClosedF   bool // 用户已请求关闭 (点 X / Alt+F4 / WM_DELETE_WINDOW)
	guiW, guiH   int
	guiMouseX    int
	guiMouseY    int
	guiMouseBtn  int // bit0 左 / bit1 右 / bit2 中 (当前按住)
	guiBuffer    *image.RGBA
	guiForcedOff bool // 测试钩子: 禁止真实窗口 (不触碰显示服务)
)

const guiMaxDim = 4096 // 单边尺寸上限 (防一个 gui_new 吃掉全部内存)

// guiNew SYS 119: gui_new(w, h, title) -> 0 / -1。
func (vm *vmState) guiNew(w, h uint64, title string) uint64 {
	if guiForcedOff {
		return mask64
	}
	cw, ch := clampCoord(w), clampCoord(h)
	if cw < 1 {
		cw = 1
	}
	if ch < 1 {
		ch = 1
	}
	if cw > guiMaxDim {
		cw = guiMaxDim
	}
	if ch > guiMaxDim {
		ch = guiMaxDim
	}
	buf := image.NewRGBA(image.Rect(0, 0, cw, ch))
	for i := range buf.Pix { // 白色背景 (与 canvas() 一致)
		buf.Pix[i] = 255
	}
	if !guiPlatformOpen(cw, ch, title, buf) {
		return mask64
	}
	guiMu.Lock()
	guiOpen = true
	guiClosedF = false
	guiW, guiH = cw, ch
	guiMouseX, guiMouseY, guiMouseBtn = 0, 0, 0
	guiBuffer = buf
	guiMu.Unlock()
	curCanvas = buf // 画布内建直接画进窗口后备缓冲
	return 0
}

// guiUpdate SYS 120: gui_update() -> 0 (处理事件 + 呈现) / -1 (无窗口)。
func (vm *vmState) guiUpdate() uint64 {
	guiMu.Lock()
	open, buf := guiOpen, guiBuffer
	guiMu.Unlock()
	if !open || buf == nil {
		return mask64
	}
	if !guiPlatformUpdate(buf) {
		return mask64
	}
	return 0
}

// guiClose SYS 121: gui_close() -> 0 (销毁窗口; 画布保留可 save_png)。
func (vm *vmState) guiClose() uint64 {
	guiMu.Lock()
	open := guiOpen
	guiMu.Unlock()
	if open {
		guiPlatformClose()
	}
	guiMu.Lock()
	guiOpen = false
	guiMu.Unlock()
	return 0
}

// guiClosedQ SYS 122: gui_closed() -> 1 用户请求关闭 / 0。
func (vm *vmState) guiClosedQ() uint64 {
	guiMu.Lock()
	defer guiMu.Unlock()
	if guiOpen && guiClosedF {
		return 1
	}
	return 0
}

// guiActiveQ SYS 128: gui_active() -> 1 窗口已打开 / 0。
func (vm *vmState) guiActiveQ() uint64 {
	guiMu.Lock()
	defer guiMu.Unlock()
	if guiOpen {
		return 1
	}
	return 0
}

func (vm *vmState) mouseX() uint64 {
	guiMu.Lock()
	defer guiMu.Unlock()
	if !guiOpen {
		return mask64
	}
	return uint64(guiMouseX) & mask31
}

func (vm *vmState) mouseY() uint64 {
	guiMu.Lock()
	defer guiMu.Unlock()
	if !guiOpen {
		return mask64
	}
	return uint64(guiMouseY) & mask31
}

func (vm *vmState) mouseBtn() uint64 {
	guiMu.Lock()
	defer guiMu.Unlock()
	if !guiOpen {
		return mask64
	}
	return uint64(guiMouseBtn)
}

func guiMarkClosed() {
	guiMu.Lock()
	guiClosedF = true
	guiMu.Unlock()
}

func guiInjectKey(code uint64) {
	keyMu.Lock()
	keyQueue = append(keyQueue, code)
	keyMu.Unlock()
}

func guiSetMouse(x, y int, btnMask int) {
	guiMu.Lock()
	guiMouseX, guiMouseY = x, y
	if btnMask >= 0 {
		guiMouseBtn = btnMask
	}
	guiMu.Unlock()
}

func guiMouseButtonBit(bit int, down bool) int {
	guiMu.Lock()
	defer guiMu.Unlock()
	if down {
		guiMouseBtn |= bit
	} else {
		guiMouseBtn &^= bit
	}
	return guiMouseBtn
}

// guiSnapshot 复制后备缓冲像素快照 (呈现线程读, VM 线程写, 防撕裂)。
func guiSnapshot(buf *image.RGBA) []byte {
	guiMu.Lock()
	defer guiMu.Unlock()
	if !guiOpen || buf != guiBuffer || buf == nil {
		return nil
	}
	pix := make([]byte, len(buf.Pix))
	copy(pix, buf.Pix)
	return pix
}

// guiMask31 限制负坐标按补码回绕后仍为非负 (mouse_x 返回值)。
const mask31 = uint64(1<<31) - 1
