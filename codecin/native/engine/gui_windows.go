//go:build windows

package engine

import (
	"image"
	"runtime"
	"sync"
	"syscall"
	"time"
	"unsafe"
)

var (
	user32                = syscall.NewLazyDLL("user32.dll")
	procGetModuleHandleW  = kernel32.NewProc("GetModuleHandleW")
	procRegisterClassExW  = user32.NewProc("RegisterClassExW")
	procCreateWindowExW   = user32.NewProc("CreateWindowExW")
	procDefWindowProcW    = user32.NewProc("DefWindowProcW")
	procPeekMessageW      = user32.NewProc("PeekMessageW")
	procTranslateMessage  = user32.NewProc("TranslateMessage")
	procDispatchMessageW  = user32.NewProc("DispatchMessageW")
	procDestroyWindow     = user32.NewProc("DestroyWindow")
	procInvalidateRect    = user32.NewProc("InvalidateRect")
	procUpdateWindow      = user32.NewProc("UpdateWindow")
	procBeginPaint        = user32.NewProc("BeginPaint")
	procEndPaint          = user32.NewProc("EndPaint")
	procAdjustWindowRect  = user32.NewProc("AdjustWindowRect")
	procLoadCursorW       = user32.NewProc("LoadCursorW")
	procGetKeyState       = user32.NewProc("GetKeyState")
	gdi32                 = syscall.NewLazyDLL("gdi32.dll")
	procStretchDIBits     = gdi32.NewProc("StretchDIBits")
	procSetStretchBltMode = gdi32.NewProc("SetStretchBltMode")
)

const (
	wmDestroy     = 0x0002
	wmPaint       = 0x000F
	wmClose       = 0x0010
	wmKeydown     = 0x0100
	wmChar        = 0x0102
	wmSyskeydown  = 0x0104
	wmUnichar     = 0x0109 // UTF-32 字符提交 (输入法/虚拟键盘)
	wmMousemove   = 0x0200
	wmLbuttondown = 0x0201
	wmLbuttonup   = 0x0202
	wmRbuttondown = 0x0204
	wmRbuttonup   = 0x0205
	wmMbuttondown = 0x0207
	wmMbuttonup   = 0x0208
	wmImeChar     = 0x0286 // IME/触摸键盘提交的 UTF-16 字符

	unicodeNoChar = 0xFFFF // WM_UNICHAR 探测: 应答复 1 表示支持

	wsOverlappedWindow = 0x00CF0000
	wsVisible          = 0x10000000
	cwUseDefault       = 0x80000000
	idcArrow           = 32512
	srccopy            = 0x00CC0020
	colorWindow        = 5

	vkShift   = 0x10
	vkControl = 0x11
)

// wndClassExW 手工布局 (x64, sizeof=80)。
type wndClassExW struct {
	cbSize        uint32
	style         uint32
	lpfnWndProc   uintptr
	cbClsExtra    int32
	cbWndExtra    int32
	hInstance     uintptr
	hIcon         uintptr
	hCursor       uintptr
	hbrBackground uintptr
	lpszMenuName  uintptr
	lpszClassName uintptr
	hIconSm       uintptr
}

// msgW 手工布局 (x64, sizeof=48)。
type msgW struct {
	hwnd     uintptr
	message  uint32
	wParam   uintptr
	lParam   uintptr
	time     uint32
	ptX, ptY int32
}

// rectL 手工布局 (sizeof=16)。
type rectL struct{ left, top, right, bottom int32 }

// paintStructW 手工布局 (sizeof=72): hdc + fErase + rcPaint + 其余。
type paintStructW struct {
	hdc        uintptr
	fErase     int32
	rcPaint    rectL
	fRestore   int32
	fIncUpdate int32
	reserved   [32]byte
}

// bmiHeader BITMAPINFOHEADER (sizeof=40)。
type bmiHeader struct {
	biSize          uint32
	biWidth         int32
	biHeight        int32 // 负值 = 自顶向下
	biPlanes        uint16
	biBitCount      uint16
	biCompression   uint32
	biSizeImage     uint32
	biXPelsPerMeter int32
	biYPelsPerMeter int32
	biClrUsed       uint32
	biClrImportant  uint32
}

// ---------------- 窗口线程 ----------------

type guiWinCmd struct {
	kind   int // 0 create / 1 update / 2 close
	w, h   int
	title  string
	result chan bool
}

var (
	guiWinCh    chan guiWinCmd
	guiWinOnce  sync.Once
	guiWinReady chan bool
	guiWinMu    sync.Mutex
	guiWinHwnd  uintptr
	winW, winH  int
	winScratch  []byte // 呈现用 BGRA 转换缓冲 (窗口线程私有访问)
	winBMI      bmiHeader
	winClassReg bool
)

func guiPlatformOpen(w, h int, title string, buf *image.RGBA) bool {
	guiWinOnce.Do(func() {
		guiWinCh = make(chan guiWinCmd, 8)
		guiWinReady = make(chan bool, 1)
		go guiWinThread()
	})
	if !<-guiWinReady {
		return false // 类注册失败 (极端环境)
	}
	res := make(chan bool, 1)
	guiWinCh <- guiWinCmd{kind: 0, w: w, h: h, title: title, result: res}
	if !<-res {
		return false
	}
	_ = buf
	return true
}

func guiPlatformUpdate(buf *image.RGBA) bool {
	guiWinMu.Lock()
	h := guiWinHwnd
	guiWinMu.Unlock()
	if h == 0 {
		return false
	}
	// 快照 + RGBA→BGRA 转换 (窗口线程消费)
	pix := guiSnapshot(buf)
	guiWinMu.Lock()
	if guiWinHwnd == 0 {
		guiWinMu.Unlock()
		return false
	}
	if pix != nil {
		rgbaToBgra(pix, buf.Rect.Dx(), buf.Rect.Dy())
	}
	guiWinMu.Unlock()
	procInvalidateRect.Call(h, 0, 0)
	r, _, _ := procUpdateWindow.Call(h) // 同步触发 WM_PAINT
	return r != 0
}

func guiPlatformClose() {
	guiWinMu.Lock()
	h := guiWinHwnd
	guiWinMu.Unlock()
	if h != 0 {
		res := make(chan bool, 1)
		guiWinCh <- guiWinCmd{kind: 2, result: res}
		<-res
	}
}

func guiWinThread() {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	hInst, _, _ := procGetModuleHandleW.Call(0)
	if hInst == 0 {
		guiWinReady <- false
		return
	}
	guiWinRegisterClassOnce(hInst)
	guiWinReady <- true

	quit := false
	var m msgW
	for !quit {
		// 泵空所有待处理消息 (WM_PAINT 由 UpdateWindow 同步触发)
		for {
			r, _, _ := procPeekMessageW.Call(uintptr(unsafe.Pointer(&m)),
				0, 0, 0, 1 /*PM_REMOVE*/)
			if r == 0 {
				break
			}
			if m.message == wmQuit {
				quit = true
				break
			}
			procTranslateMessage.Call(uintptr(unsafe.Pointer(&m)))
			procDispatchMessageW.Call(uintptr(unsafe.Pointer(&m)))
		}
		if quit {
			break
		}
		select {
		case cmd := <-guiWinCh:
			guiWinHandleCmd(cmd)
		case <-time.After(8 * time.Millisecond):
		}
	}
}

const wmQuit = 0x0012

func guiWinRegisterClassOnce(hInst uintptr) {
	if winClassReg {
		return
	}
	name, _ := syscall.UTF16PtrFromString("CodeCINWindow")
	wc := wndClassExW{
		cbSize:        uint32(unsafe.Sizeof(wndClassExW{})),
		style:         0x0003, // CS_HREDRAW | CS_VREDRAW
		lpfnWndProc:   syscall.NewCallback(guiWndProc),
		hInstance:     hInst,
		hCursor:       loadCursor(idcArrow),
		hbrBackground: colorWindow + 1, // COLOR_WINDOW+1
		lpszClassName: uintptr(unsafe.Pointer(name)),
	}
	procRegisterClassExW.Call(uintptr(unsafe.Pointer(&wc)))
	winClassReg = true
}

func loadCursor(id uintptr) uintptr {
	c, _, _ := procLoadCursorW.Call(0, id)
	return c
}

func guiWinHandleCmd(cmd guiWinCmd) {
	switch cmd.kind {
	case 0: // create
		title, _ := syscall.UTF16PtrFromString(cmd.title)
		rc := rectL{0, 0, int32(cmd.w), int32(cmd.h)}
		procAdjustWindowRect.Call(uintptr(unsafe.Pointer(&rc)), wsOverlappedWindow, 0)
		ww := uintptr(rc.right - rc.left)
		wh := uintptr(rc.bottom - rc.top)
		hInst, _, _ := procGetModuleHandleW.Call(0)
		h, _, _ := procCreateWindowExW.Call(0,
			uintptr(unsafe.Pointer(wcClassName())), // 类名 (确保已注册)
			uintptr(unsafe.Pointer(title)),
			wsOverlappedWindow|wsVisible,
			cwUseDefault, cwUseDefault, ww, wh,
			0, 0, hInst, 0)
		guiWinMu.Lock()
		guiWinHwnd = h
		winW, winH = cmd.w, cmd.h
		winBMI = bmiHeader{
			biSize:     uint32(unsafe.Sizeof(bmiHeader{})),
			biWidth:    int32(cmd.w),
			biHeight:   -int32(cmd.h),
			biPlanes:   1,
			biBitCount: 32,
		}
		guiWinMu.Unlock()
		cmd.result <- h != 0
	case 2: // close
		guiWinMu.Lock()
		h := guiWinHwnd
		guiWinHwnd = 0
		winScratch = nil
		guiWinMu.Unlock()
		if h != 0 {
			procDestroyWindow.Call(h)
		}
		cmd.result <- true
		// 线程保持存活 (消息泵常驻), 允许之后再次 gui_new
	}
}

var wcNamePtr, _ = syscall.UTF16PtrFromString("CodeCINWindow")

func wcClassName() *uint16 { return wcNamePtr }

func guiWndProc(hwnd uintptr, msg uintptr, wp, lp uintptr) uintptr {
	switch msg {
	case wmPaint:
		var ps paintStructW
		if r, _, _ := procBeginPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps))); r != 0 {
			guiWinMu.Lock()
			scratch, w, h, bmi := winScratch, winW, winH, winBMI
			guiWinMu.Unlock()
			if scratch != nil && w > 0 && h > 0 {
				procSetStretchBltMode.Call(ps.hdc, 3 /*COLORONCOLOR*/)
				procStretchDIBits.Call(ps.hdc,
					0, 0, uintptr(w), uintptr(h),
					0, 0, uintptr(w), uintptr(h),
					uintptr(unsafe.Pointer(&scratch[0])),
					uintptr(unsafe.Pointer(&bmi)),
					0 /*DIB_RGB_COLORS*/, srccopy)
			}
			procEndPaint.Call(hwnd, uintptr(unsafe.Pointer(&ps)))
		}
		return 0
	case wmClose:
		guiVkbHide()    // 程序窗口关闭时连带收起触摸键盘
		guiMarkClosed() // gui_closed() 置 1; 交给 DefWindowProc 销毁窗口
	case wmDestroy:
		// 窗口已销毁 (无论来自 gui_close 还是用户点 X): 清理句柄,
		// 避免残留的失效 hwnd 被后续 update/close 误用。
		// 不 PostQuitMessage: 窗口线程常驻, 允许之后再次 gui_new。
		guiWinMu.Lock()
		guiWinHwnd = 0
		winScratch = nil
		guiWinMu.Unlock()
		return 0
	case wmKeydown:
		guiWinKeyDown(wp, lp)
		return 0
	case wmChar:
		guiWinChar(wp, lp)
		return 0
	case wmImeChar:
		// IME / 屏幕键盘提交的字符可能绕过 WM_CHAR
		guiWinChar(wp, lp)
		return 0
	case wmUnichar:
		if wp == unicodeNoChar {
			return 1 // 宣告本窗口支持 WM_UNICHAR
		}
		guiWinUniChar(wp)
		return 0
	case wmSyskeydown:
		// Alt+F4 等交给 DefWindowProc (产生 WM_CLOSE)
	case wmMousemove:
		x, y := guiMouseXY(lp)
		guiSetMouse(x, y, -1)
		return 0
	case wmLbuttondown:
		x, y := guiMouseXY(lp)
		guiSetMouse(x, y, -1)
		guiMouseButtonBit(1, true)
	case wmLbuttonup:
		guiMouseButtonBit(1, false)
	case wmRbuttondown:
		guiMouseButtonBit(2, true)
	case wmRbuttonup:
		guiMouseButtonBit(2, false)
	case wmMbuttondown:
		guiMouseButtonBit(4, true)
	case wmMbuttonup:
		guiMouseButtonBit(4, false)
	}
	r, _, _ := procDefWindowProcW.Call(hwnd, uintptr(msg), wp, lp)
	return r
}

// guiWinKeyDown WM_KEYDOWN: VK 映射扩展键 (方向键/功能键, 含修饰变体)。
func guiWinKeyDown(vk, lp uintptr) {
	repeat := int(lp & 0xFFFF)
	if repeat < 1 {
		repeat = 1
	}
	if repeat > 8 {
		repeat = 8
	}
	if code, ok := consoleKeyCode(uint16(vk), 0, guiWinKeyState()); ok {
		guiInjectKey(code)
		for i := 1; i < repeat; i++ {
			guiInjectKey(code)
		}
	}
}

// guiWinChar WM_CHAR / WM_IME_CHAR: UTF-16 字符
// (含代理对组合 / Ctrl+字母控制字节)。
func guiWinChar(wp, lp uintptr) {
	repeat := int(lp & 0xFFFF)
	if repeat < 1 {
		repeat = 1
	}
	if repeat > 8 {
		repeat = 8
	}
	if code, ok := consoleKeyCode(0, rune(wp), guiWinKeyState()); ok {
		guiInjectKey(code)
		for i := 1; i < repeat; i++ {
			guiInjectKey(code)
		}
	}
}

// guiWinUniChar WM_UNICHAR: UTF-32 字符 (虚拟键盘/输入法的文本提交通道)。
func guiWinUniChar(wp uintptr) {
	if wp > 0x10FFFF || (wp >= 0xD800 && wp <= 0xDFFF) {
		return // 越界码点或裸代理项: 丢弃
	}
	if wp == 0 {
		return
	}
	if code, ok := consoleKeyCode(0, rune(wp), guiWinKeyState()); ok {
		guiInjectKey(code)
	}
}

// guiWinKeyState 取当前 Shift/Ctrl 状态位 (与控制台 dwControlKeyState 对齐)。
func guiWinKeyState() uint32 {
	state := uint32(0)
	if s, _, _ := procGetKeyState.Call(vkShift); s&0x8000 != 0 {
		state |= ksShift
	}
	if s, _, _ := procGetKeyState.Call(vkControl); s&0x8000 != 0 {
		state |= ksLeftCtrl
	}
	return state
}

// guiMouseXY lParam 拆坐标 (16 位有符号)。
func guiMouseXY(lp uintptr) (int, int) {
	return int(int16(lp)), int(int16(lp >> 16))
}

// rgbaToBgra RGBA 画布 → BGRA 呈现缓冲 (窗口线程, 每帧一次)。
func rgbaToBgra(pix []byte, w, h int) {
	n := w * h * 4
	if cap(winScratch) < n {
		winScratch = make([]byte, n)
	}
	winScratch = winScratch[:n]
	for i := 0; i < n; i += 4 {
		winScratch[i] = pix[i+2]   // B
		winScratch[i+1] = pix[i+1] // G
		winScratch[i+2] = pix[i]   // R
		winScratch[i+3] = 0
	}
}

// ---------------- 虚拟键盘 (Windows 屏幕/触摸键盘) ----------------
//
// 通过 WinRT InputPane 的桌面互操作接口为指定顶层窗口弹出/收起触摸键盘
// (TabTip / 输入体验)。这类键盘的按键由系统注入 (WM_KEYDOWN VK_PACKET +
// WM_CHAR / WM_IME_CHAR / WM_UNICHAR), 已由窗口过程统一转入按键队列,
// 因此 get_key 无需感知按键来源。

var (
	combaseDLL                 = syscall.NewLazyDLL("combase.dll")
	procRoInitialize           = combaseDLL.NewProc("RoInitialize")
	procRoGetActivationFactory = combaseDLL.NewProc("RoGetActivationFactory")
	procWindowsCreateString    = combaseDLL.NewProc("WindowsCreateString")
	procWindowsDeleteString    = combaseDLL.NewProc("WindowsDeleteString")
	procGetConsoleWindow       = kernel32.NewProc("GetConsoleWindow")
)

// winGUID GUID (Data1..Data4, sizeof=16)。
type winGUID struct {
	Data1 uint32
	Data2 uint16
	Data3 uint16
	Data4 [8]byte
}

var (
	// IInputPaneInterop (Win32 桌面互操作, inputpaneinterop.h)
	iidInputPaneInterop = winGUID{0x75CF2C57, 0x9195, 0x4931,
		[8]byte{0x83, 0x32, 0xF0, 0xB4, 0x09, 0xE9, 0x16, 0xAF}}
	// IInputPane2 提供 TryShow / TryHide
	iidInputPane2 = winGUID{0x8A6B3F26, 0x7090, 0x4793,
		[8]byte{0x94, 0x4C, 0xC3, 0xF2, 0xCD, 0xE2, 0x62, 0x76}}
)

const (
	vtRelease       = 2                 // IUnknown::Release
	vtFirstMethod   = 6                 // IUnknown(3) + IInspectable(3) 之后的第一个接口方法
	vtInputPaneShow = vtFirstMethod     // IInputPane2::TryShow
	vtInputPaneHide = vtFirstMethod + 1 // IInputPane2::TryHide
	rpcEChangedMode = 0x80010106
)

// winVtFn 读取 COM 对象虚表第 idx 项的函数指针。
func winVtFn(obj unsafe.Pointer, idx int) uintptr {
	vt := *(*unsafe.Pointer)(obj)
	return *(*uintptr)(unsafe.Add(vt, uintptr(idx)*unsafe.Sizeof(uintptr(0))))
}

// guiVkbTargetHwnd 虚拟键盘的绑定窗口: 优先 GUI 窗口, 无 GUI 时用控制台窗口
// (终端 raw 模式下同样可用)。
func guiVkbTargetHwnd() uintptr {
	guiWinMu.Lock()
	h := guiWinHwnd
	guiWinMu.Unlock()
	if h != 0 {
		return h
	}
	h, _, _ = procGetConsoleWindow.Call()
	return h
}

// guiVkbAction 在 STA 线程上取得目标窗口的 IInputPane2 并调用 TryShow/TryHide。
func guiVkbAction(show bool) bool {
	hwnd := guiVkbTargetHwnd()
	if hwnd == 0 {
		return false
	}
	// InputPane 只支持 STA: 固定 OS 线程并初始化单线程套间。
	// 已初始化时 RoInitialize 返回 S_FALSE (1), 仍可继续。
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	if hr, _, _ := procRoInitialize.Call(0 /*RO_INIT_SINGLETHREADED*/); int32(hr) < 0 &&
		uint32(hr) != rpcEChangedMode {
		return false
	}
	name := "Windows.UI.ViewManagement.InputPane"
	u16, _ := syscall.UTF16FromString(name)
	var hstr uintptr
	if hr, _, _ := procWindowsCreateString.Call(uintptr(unsafe.Pointer(&u16[0])),
		uintptr(len(u16)-1), uintptr(unsafe.Pointer(&hstr))); int32(hr) < 0 || hstr == 0 {
		return false
	}
	defer procWindowsDeleteString.Call(hstr)
	var factory unsafe.Pointer
	if hr, _, _ := procRoGetActivationFactory.Call(hstr,
		uintptr(unsafe.Pointer(&iidInputPaneInterop)),
		uintptr(unsafe.Pointer(&factory))); int32(hr) < 0 || factory == nil {
		return false
	}
	defer syscall.SyscallN(winVtFn(factory, vtRelease), uintptr(factory))
	var pane unsafe.Pointer
	if hr, _, _ := syscall.SyscallN(winVtFn(factory, vtFirstMethod), uintptr(factory), hwnd,
		uintptr(unsafe.Pointer(&iidInputPane2)),
		uintptr(unsafe.Pointer(&pane))); int32(hr) < 0 || pane == nil {
		return false
	}
	defer syscall.SyscallN(winVtFn(pane, vtRelease), uintptr(pane))
	idx := vtInputPaneShow
	if !show {
		idx = vtInputPaneHide
	}
	var ret byte // WinRT boolean (1 字节)
	hr, _, _ := syscall.SyscallN(winVtFn(pane, idx), uintptr(pane),
		uintptr(unsafe.Pointer(&ret)))
	return int32(hr) >= 0 && ret != 0
}

// guiVkbShow 弹出屏幕/触摸键盘。
func guiVkbShow() bool { return guiVkbAction(true) }

// guiVkbHide 收起屏幕/触摸键盘。
func guiVkbHide() bool { return guiVkbAction(false) }
