//go:build linux

package engine

import (
	"bufio"
	"encoding/binary"
	"image"
	_ "io"
	"net"
	"os"
	"strconv"
	"strings"
	"time"
)

// Linux GUI 后端: 纯 Go X11 客户端 (标准库 net, 零第三方依赖)。
// 支持 X11 与 XWayland (绝大多数发行版的默认显示路径); 无 DISPLAY 或
// 认证失败时 gui_new 优雅返回 -1。
//
// 事件在同一 goroutine (调用 SYS 的 VM 线程) 内处理: X11 无线程亲和性
// 限制, 连接/呈现/泵事件全部同步完成 —— 与 VM 单线程确定性模型天然契合。
// 窗口间的间隙事件缓存在内核 socket 缓冲, gui_update 时统一泵出。
// 协议编码按 X11 核心协议 (请求 4 字节对齐, 大端字段)。

const (
	x11OpcodeCreateWindow  = 1
	x11OpcodeDestroyWindow = 4
	x11OpcodeMapWindow     = 8
	x11OpcodeInternAtom    = 16
	x11OpcodeChangeProp    = 18
	x11OpcodeCreateGC      = 55
	x11OpcodePutImage      = 72
	x11OpcodeKeymap        = 101

	evKeyPress       = 2
	evButtonPress    = 4
	evButtonRelease  = 5
	evMotion         = 6
	evClientMessage  = 33
	x11ZPixmap       = 2
	x11VisualTrueCol = 4
)

type x11Conn struct {
	conn      net.Conn
	br        *bufio.Reader
	resBase   uint32
	root      uint32
	visual    uint32
	depth     uint8
	minKC     uint8
	maxKC     uint8
	perKC     int
	keysyms   []uint32
	win       uint32
	gc        uint32
	w, h      int
	maxReqLen int // 单请求最大字节数 (握手值×4)
	nextID    uint32
	atomWMPro uint32
	atomWMDel uint32
}

// ---------------- 平台接口 ----------------

func guiPlatformOpen(w, h int, title string, buf *image.RGBA) bool {
	_ = buf
	xc, err := guiX11Dial()
	if err != nil {
		return false
	}
	if !xc.openWindow(w, h, title) {
		_ = xc.conn.Close()
		return false
	}
	return true
}

func guiPlatformUpdate(buf *image.RGBA) bool {
	guiMu.Lock()
	xc := guiX11
	guiMu.Unlock()
	if xc == nil {
		return false
	}
	if !xc.pump() { // 连接断开: 视为窗口关闭
		guiMarkClosed()
		return false
	}
	pix := guiSnapshot(buf)
	if pix == nil {
		return true
	}
	return xc.present(pix, buf.Rect.Dx(), buf.Rect.Dy())
}

func guiPlatformClose() {
	guiMu.Lock()
	xc := guiX11
	guiX11 = nil
	guiMu.Unlock()
	if xc != nil {
		xc.destroy()
	}
}

// guiX11 当前连接 (gui.go 状态之外的平台侧句柄)。
var guiX11 *x11Conn

// ---------------- 连接与握手 ----------------

// guiX11Dial 解析 $DISPLAY 建立连接 (unix socket 优先, 其次 TCP)。
func guiX11Dial() (*x11Conn, error) {
	spec := os.Getenv("DISPLAY")
	if spec == "" {
		return nil, &displayError{"DISPLAY not set"}
	}
	host, disp, err := parseDisplay(spec)
	if err != nil {
		return nil, err
	}
	var conn net.Conn
	if host == "" {
		conn, err = net.DialTimeout("unix",
			"/tmp/.X11-unix/X"+disp, 2*time.Second)
	} else {
		conn, err = net.DialTimeout("tcp",
			host+":"+strconv.Itoa(atoiOr0(disp)+6000), 2*time.Second)
	}
	if err != nil {
		return nil, err
	}
	_ = conn.SetDeadline(time.Now().Add(2 * time.Second))
	xc, err := x11Handshake(conn, host, disp)
	_ = conn.SetDeadline(time.Time{})
	if err != nil {
		_ = conn.Close()
		return nil, err
	}
	return xc, nil
}

type displayError struct{ msg string }

func (e *displayError) Error() string { return e.msg }

// parseDisplay 解析 "host:dpy[.screen]"。
func parseDisplay(spec string) (host, disp string, err error) {
	colon := strings.LastIndex(spec, ":")
	if colon < 0 {
		return "", "", &displayError{"bad DISPLAY: " + spec}
	}
	host = spec[:colon]
	dpy := spec[colon+1:]
	if i := strings.Index(dpy, "."); i >= 0 {
		dpy = dpy[:i]
	}
	if _, e := strconv.Atoi(dpy); e != nil {
		return "", "", &displayError{"bad DISPLAY number: " + spec}
	}
	return host, dpy, nil
}

func atoiOr0(s string) int {
	n, _ := strconv.Atoi(s)
	return n
}

// x11Handshake 发送连接握手并解析 setup 回复。
func x11Handshake(conn net.Conn, host, disp string) (*x11Conn, error) {
	br := bufio.NewReader(conn)
	// 认证: MIT-MAGIC-COOKIE-1 (尽力而为; 本地 socket 通常无需)
	name, cookie := xauthCookie(host, disp)
	hdr := make([]byte, 12)
	hdr[0] = 'l'                             // LSBFirst
	binary.BigEndian.PutUint16(hdr[2:4], 11) // protocol major
	binary.BigEndian.PutUint16(hdr[4:6], 0)  // minor
	binary.BigEndian.PutUint16(hdr[6:8], uint16(len(name)))
	binary.BigEndian.PutUint16(hdr[8:10], uint16(len(cookie)))
	req := append(hdr, name...)
	req = append(req, make([]byte, pad4(len(name))-len(name))...)
	req = append(req, cookie...)
	req = append(req, make([]byte, pad4(len(cookie))-len(cookie))...)
	if _, err := conn.Write(req); err != nil {
		return nil, err
	}
	first, err := br.ReadByte()
	if err != nil {
		return nil, err
	}
	if first != 1 { // 0=failed, 2=further-auth
		return nil, &displayError{"X11 handshake rejected"}
	}
	head := make([]byte, 7)
	if _, err := ioReadFull(br, head); err != nil {
		return nil, err
	}
	extra := int(binary.BigEndian.Uint16(head[5:7])) * 4
	body := make([]byte, extra)
	if _, err := ioReadFull(br, body); err != nil {
		return nil, err
	}
	// additional data: release(4) resBase(4) mask(4) motion(4) vendorLen(2)
	// maxReq(2) screens(1) formats(1) order(1) bitOrder(1) unit(1) pad(1)
	// minKC(1) maxKC(1) unused(4) vendor.. formats.. screens..
	if len(body) < 32 {
		return nil, &displayError{"X11 setup truncated"}
	}
	vendorLen := int(binary.BigEndian.Uint16(body[16:18]))
	maxReqBytes := int(binary.BigEndian.Uint16(body[18:20])) * 4
	numFormats := int(body[21])
	minKC, maxKC := body[26], body[27]
	pos := padTo(32+vendorLen, 4) + numFormats*8
	if len(body) < pos+40 {
		return nil, &displayError{"X11 setup truncated"}
	}
	sc := body[pos:]
	root := binary.BigEndian.Uint32(sc[0:4])
	rootVisual := binary.BigEndian.Uint32(sc[32:36])
	rootDepth := sc[38]
	nDepths := int(sc[39])
	visual, depth := findTrueColor(sc[40:], nDepths, rootVisual, rootDepth)
	if visual == 0 {
		return nil, &displayError{"no TrueColor visual found"}
	}
	xc := &x11Conn{
		conn:      conn,
		br:        br,
		resBase:   binary.BigEndian.Uint32(body[4:8]),
		root:      root,
		visual:    visual,
		depth:     depth,
		minKC:     minKC,
		maxKC:     maxKC,
		maxReqLen: maxReqBytes,
	}
	if !xc.loadKeymap() {
		return nil, &displayError{"X11 keymap query failed"}
	}
	return xc, nil
}

// findTrueColor 找 depth>=24 的 TrueColor visual (缺省回落根 visual)。
func findTrueColor(depths []byte, n int, fallbackVisual uint32, fallbackDepth uint8) (uint32, uint8) {
	pos := 0
	for d := 0; d < n && pos+8 <= len(depths); d++ {
		depth := depths[pos]
		nVis := int(binary.BigEndian.Uint16(depths[pos+2 : pos+4]))
		pos += 8
		for v := 0; v < nVis && pos+24 <= len(depths); v++ {
			id := binary.BigEndian.Uint32(depths[pos : pos+4])
			class := depths[pos+4]
			if class == x11VisualTrueCol && depth >= 24 {
				return id, depth
			}
			pos += 24
		}
	}
	return fallbackVisual, fallbackDepth
}

// loadKeymap GetKeyboardMapping: keycode -> keysyms 表。
func (xc *x11Conn) loadKeymap() bool {
	count := int(xc.maxKC) - int(xc.minKC) + 1
	if count <= 0 {
		return false
	}
	req := make([]byte, 8)
	req[0] = x11OpcodeKeymap
	req[1] = xc.minKC
	binary.BigEndian.PutUint16(req[2:4], 2) // 8 字节请求
	req[4] = uint8(count)
	if _, err := xc.conn.Write(req); err != nil {
		return false
	}
	head := make([]byte, 32)
	if _, err := ioReadFull(xc.br, head); err != nil || head[0] != 1 {
		return false
	}
	xc.perKC = int(head[1])
	n := int(binary.BigEndian.Uint32(head[4:8])) * 4
	if xc.perKC <= 0 || n <= 0 {
		return false
	}
	body := make([]byte, n)
	if _, err := ioReadFull(xc.br, body); err != nil {
		return false
	}
	xc.keysyms = make([]uint32, n/4)
	for i := range xc.keysyms {
		xc.keysyms[i] = binary.BigEndian.Uint32(body[i*4 : i*4+4])
	}
	return true
}

// ---------------- 窗口创建 / 呈现 / 销毁 ----------------

func (xc *x11Conn) allocID() uint32 {
	xc.nextID++
	return xc.resBase + xc.nextID
}

func (xc *x11Conn) openWindow(w, h int, title string) bool {
	xc.atomWMPro = xc.internAtom("WM_PROTOCOLS")
	xc.atomWMDel = xc.internAtom("WM_DELETE_WINDOW")
	win := xc.allocID()
	gc := xc.allocID()

	// CreateWindow (值顺序按 value-mask 位序: back-pixel -> event-mask)
	// CW_BACK_PIXEL(0x2) | CW_EVENT_MASK(0x800)
	const evMask = 0x1 | 0x2 | 0x4 | 0x8 | 0x40 | 0x8000 | 0x20000
	req := make([]byte, 44)
	req[0] = x11OpcodeCreateWindow
	req[1] = xc.depth
	binary.BigEndian.PutUint16(req[2:4], 11) // 44/4
	binary.BigEndian.PutUint32(req[4:8], win)
	binary.BigEndian.PutUint32(req[8:12], xc.root)
	binary.BigEndian.PutUint16(req[12:14], 0) // x
	binary.BigEndian.PutUint16(req[14:16], 0) // y
	binary.BigEndian.PutUint16(req[16:18], uint16(w))
	binary.BigEndian.PutUint16(req[18:20], uint16(h))
	binary.BigEndian.PutUint16(req[20:22], 1) // border
	binary.BigEndian.PutUint16(req[22:24], 1) // InputOutput
	binary.BigEndian.PutUint32(req[26:30], xc.visual)
	binary.BigEndian.PutUint32(req[30:34], 0x2|0x800)
	binary.BigEndian.PutUint32(req[34:38], 0xFFFFFF) // 白背景
	binary.BigEndian.PutUint32(req[38:42], evMask)
	if err := xc.write(req); err != nil {
		return false
	}

	// CreateGC (graphics-exposures = 0)
	greq := make([]byte, 20)
	greq[0] = x11OpcodeCreateGC
	binary.BigEndian.PutUint16(greq[2:4], 5) // 20/4
	binary.BigEndian.PutUint32(greq[4:8], gc)
	binary.BigEndian.PutUint32(greq[8:12], win)
	binary.BigEndian.PutUint32(greq[12:16], 1<<9) // GC_GRAPHICS_EXPOSURES
	if err := xc.write(greq); err != nil {
		return false
	}

	// ChangeProperty: WM_PROTOCOLS = [WM_DELETE_WINDOW] (点 X 触发 ClientMessage)
	if xc.atomWMPro != 0 && xc.atomWMDel != 0 {
		preq := make([]byte, 28)
		preq[0] = x11OpcodeChangeProp
		binary.BigEndian.PutUint16(preq[2:4], 7) // 28/4
		binary.BigEndian.PutUint32(preq[4:8], win)
		binary.BigEndian.PutUint32(preq[8:12], xc.atomWMPro)
		binary.BigEndian.PutUint32(preq[12:16], xc.atomWMPro) // type: ATOM
		preq[16] = 32                                         // format
		binary.BigEndian.PutUint32(preq[20:24], 1)            // n items
		binary.BigEndian.PutUint32(preq[24:28], xc.atomWMDel)
		_ = xc.write(preq)
	}

	// MapWindow
	mreq := make([]byte, 8)
	mreq[0] = x11OpcodeMapWindow
	binary.BigEndian.PutUint16(mreq[2:4], 2)
	binary.BigEndian.PutUint32(mreq[4:8], win)
	if err := xc.write(mreq); err != nil {
		return false
	}
	xc.win = win
	xc.gc = gc
	xc.w, xc.h = w, h
	guiMu.Lock()
	guiX11 = xc
	guiMu.Unlock()
	return true
}

// internAtom 同步查询 atom。
func (xc *x11Conn) internAtom(name string) uint32 {
	b := []byte(name)
	req := make([]byte, 8+pad4(len(b)))
	req[0] = x11OpcodeInternAtom
	req[1] = 0 // only-if-exists = false
	binary.BigEndian.PutUint16(req[2:4], uint16(len(req)/4))
	binary.BigEndian.PutUint16(req[4:6], uint16(len(b)))
	copy(req[8:], b)
	if err := xc.write(req); err != nil {
		return 0
	}
	rep := make([]byte, 32)
	if err := xc.readReply(rep); err != nil || rep[0] != 1 {
		return 0
	}
	return binary.BigEndian.Uint32(rep[8:12])
}

// present 把 RGBA 像素呈现到窗口 (PutImage 分块)。
func (xc *x11Conn) present(pix []byte, w, h int) bool {
	bpl := w * 4
	chunkRows := (xc.maxReqLen - 24) / bpl
	if chunkRows < 1 {
		chunkRows = 1
	}
	if chunkRows > h {
		chunkRows = h
	}
	for row := 0; row < h; row += chunkRows {
		rows := chunkRows
		if rows > h-row {
			rows = h - row
		}
		n := rows * bpl
		req := make([]byte, 24+n)
		req[0] = x11OpcodePutImage
		req[1] = x11ZPixmap
		binary.BigEndian.PutUint16(req[2:4], uint16((24+n)/4))
		binary.BigEndian.PutUint32(req[4:8], xc.win)
		binary.BigEndian.PutUint32(req[8:12], xc.gc)
		binary.BigEndian.PutUint16(req[12:14], uint16(w))
		binary.BigEndian.PutUint16(req[14:16], uint16(rows))
		binary.BigEndian.PutUint16(req[16:18], 0) // dst-x
		binary.BigEndian.PutUint16(req[18:20], uint16(row))
		req[21] = xc.depth
		packPixels(pix[row*bpl:(row+rows)*bpl], req[24:])
		if err := xc.write(req); err != nil {
			return false
		}
	}
	return true
}

// packPixels RGBA -> ZPixmap 像素单元 (LSB 服务器: B,G,R,x; MSB: x,R,G,B)。
// x11Handshake 默认本地服务器为 LSBFirst ('l' 握手序即客户端序),
// 这里按客户端一律 LSB 编码 (请求 hdr[0]='l' 已声明)。
func packPixels(src, dst []byte) {
	j := 0
	for i := 0; i+3 < len(src); i += 4 {
		dst[j] = src[i+2]   // B
		dst[j+1] = src[i+1] // G
		dst[j+2] = src[i]   // R
		j += 4
	}
}

func (xc *x11Conn) destroy() {
	if xc.win != 0 {
		req := make([]byte, 8)
		req[0] = x11OpcodeDestroyWindow
		binary.BigEndian.PutUint16(req[2:4], 2)
		binary.BigEndian.PutUint32(req[4:8], xc.win)
		_ = xc.write(req)
	}
	_ = xc.conn.Close()
}

// ---------------- 事件泵 ----------------

// pump 非阻塞泵空全部待处理事件 (按键/鼠标注入状态, 其余消费掉)。
func (xc *x11Conn) pump() bool {
	_ = xc.conn.SetReadDeadline(time.Now())
	defer func() { _ = xc.conn.SetReadDeadline(time.Time{}) }()
	var ev [32]byte
	for {
		if _, err := ioReadFull(xc.br, ev[:]); err != nil {
			if isTimeout(err) {
				return true // 输入暂尽: 正常
			}
			return false // 连接断开
		}
		xc.handleEvent(ev[:])
	}
}

// handleEvent 解析单个 32 字节事件。
func (xc *x11Conn) handleEvent(ev []byte) {
	switch ev[0] {
	case 0: // 错误: 消费
	case evKeyPress:
		state := binary.BigEndian.Uint16(ev[28:30])
		if k, ok := xc.keycodeToKey(ev[1], state); ok {
			guiInjectKey(k)
		}
	case evButtonPress:
		guiX11Button(int(ev[1]), true)
		guiX11Mouse(ev)
	case evButtonRelease:
		guiX11Button(int(ev[1]), false)
		guiX11Mouse(ev)
	case evMotion:
		guiX11Mouse(ev)
	case evClientMessage:
		// WM_DELETE_WINDOW: format=32(字节1), type@8, data[0]@12
		if ev[1] == 32 && binary.BigEndian.Uint32(ev[8:12]) == xc.atomWMPro &&
			binary.BigEndian.Uint32(ev[12:16]) == xc.atomWMDel {
			guiMarkClosed()
		}
	default: // Expose / MapNotify / ConfigureNotify 等: 消费
	}
}

func guiX11Mouse(ev []byte) {
	x := int(int16(binary.BigEndian.Uint16(ev[24:26])))
	y := int(int16(binary.BigEndian.Uint16(ev[26:28])))
	guiSetMouse(x, y, -1)
}

// guiX11Button X11 按钮号 -> guiMouseBtn 位 (1 左 / 2 中 / 3 右)。
func guiX11Button(detail int, down bool) {
	var bit int
	switch detail {
	case 1:
		bit = 1
	case 2:
		bit = 4
	case 3:
		bit = 2
	default:
		return // 滚轮 4/5
	}
	guiMouseButtonBit(bit, down)
}

// keycodeToKey 键码 -> 统一键码值 (与终端 / Windows 三端一致)。
func (xc *x11Conn) keycodeToKey(kc uint8, state uint16) (uint64, bool) {
	shift := state&0x1 != 0
	ctrl := state&0x4 != 0
	k := xc.lookupKeysym(kc, shift)
	if k == 0 {
		return 0, false
	}
	// Ctrl+字母 -> 控制字节 (与 Windows / 终端一致: Ctrl+C=3)
	if ctrl {
		switch {
		case k >= 'a' && k <= 'z':
			return uint64(k - 96), true
		case k >= 'A' && k <= 'Z':
			return uint64(k - 64), true
		}
	}
	switch k {
	case 0xFF08:
		return 8, true // BackSpace
	case 0xFF09:
		return 9, true // Tab
	case 0xFE20:
		return keyShiftTab, true // ISO_Left_Tab (Shift+Tab)
	case 0xFF0D:
		return 13, true // Return
	case 0xFF1B:
		return 27, true // Escape
	case 0xFF50:
		return keyHome, true
	case 0xFF55:
		return keyPgUp, true
	case 0xFF56:
		return keyPgDn, true
	case 0xFF63:
		return keyIns, true
	case 0xFFFF:
		return keyDel, true
	case 0xFF51:
		return arrowVariant(keyLeft, x11Mod(shift, ctrl)), true
	case 0xFF52:
		return arrowVariant(keyUp, x11Mod(shift, ctrl)), true
	case 0xFF53:
		return arrowVariant(keyRight, x11Mod(shift, ctrl)), true
	case 0xFF54:
		return arrowVariant(keyDown, x11Mod(shift, ctrl)), true
	case 0xFFBE:
		return keyF1, true
	case 0xFFBF:
		return keyF2, true
	case 0xFFC0:
		return keyF3, true
	case 0xFFC1:
		return keyF4, true
	case 0xFFC2:
		return keyF5, true
	case 0xFFC3:
		return keyF6, true
	case 0xFFC4:
		return keyF7, true
	case 0xFFC5:
		return keyF8, true
	case 0xFFC6:
		return keyF9, true
	case 0xFFC7:
		return keyF10, true
	case 0xFFC8:
		return keyF11, true
	case 0xFFC9:
		return keyF12, true
	}
	switch {
	case k >= 0x20 && k <= 0xFF: // Latin-1
		return uint64(k), true
	case k >= 0x1000000: // Unicode keysym
		return uint64(k - 0x1000000), true
	}
	return 0, false
}

// x11Mod X11 state -> XTerm 修饰值 (1+shift|ctrl)。
func x11Mod(shift, ctrl bool) int {
	m := 1
	if shift {
		m += 1
	}
	if ctrl {
		m += 4
	}
	return m
}

// lookupKeysym 取键盘映射: col0 = 基础层, col1 = Shift 层。
func (xc *x11Conn) lookupKeysym(kc uint8, shift bool) uint32 {
	if xc.perKC <= 0 {
		return 0
	}
	idx := int(kc) - int(xc.minKC)
	rows := len(xc.keysyms) / xc.perKC
	if idx < 0 || idx >= rows {
		return 0
	}
	row := xc.keysyms[idx*xc.perKC : (idx+1)*xc.perKC]
	if shift && len(row) > 1 && row[1] != 0 {
		return row[1]
	}
	if row[0] != 0 {
		return row[0]
	}
	for _, k := range row {
		if k != 0 {
			return k
		}
	}
	return 0
}

// ---------------- Xauthority ----------------

// xauthCookie 读取 $XAUTHORITY / ~/.Xauthority 里匹配 display 的
// MIT-MAGIC-COOKIE-1 (尽力而为; 失败返回空认证)。
func xauthCookie(host, disp string) (string, []byte) {
	path := os.Getenv("XAUTHORITY")
	if path == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", nil
		}
		path = home + "/.Xauthority"
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return "", nil
	}
	pos := 0
	for pos+2 <= len(data) {
		family := int(binary.BigEndian.Uint16(data[pos : pos+2]))
		pos += 2
		fields := make([][]byte, 4)
		ok := true
		for fi := range fields {
			if pos+2 > len(data) {
				ok = false
				break
			}
			n := int(binary.BigEndian.Uint16(data[pos : pos+2]))
			pos += 2
			if pos+n > len(data) {
				ok = false
				break
			}
			fields[fi] = data[pos : pos+n]
			pos += n
		}
		if !ok {
			break
		}
		addr, number, aname, adata := fields[0], fields[1], fields[2], fields[3]
		if string(aname) != "MIT-MAGIC-COOKIE-1" || string(number) != disp {
			continue
		}
		// family 256 = LocalHost (unix socket); 其余匹配主机名
		if family == 256 || (host != "" && strings.EqualFold(string(addr), host)) {
			return "MIT-MAGIC-COOKIE-1", append([]byte(nil), adata...)
		}
	}
	return "", nil
}

// ---------------- 小工具 ----------------

func pad4(n int) int { return (n + 3) &^ 3 }

func padTo(pos, align int) int { return (pos + align - 1) &^ (align - 1) }

func ioReadFull(r *bufio.Reader, b []byte) (int, error) {
	total := 0
	for total < len(b) {
		n, err := r.Read(b[total:])
		total += n
		if err != nil {
			return total, err
		}
	}
	return total, nil
}

func isTimeout(err error) bool {
	ne, ok := err.(net.Error)
	return ok && ne.Timeout()
}

func (xc *x11Conn) write(b []byte) error {
	_, err := xc.conn.Write(b)
	return err
}

// readReply 读一个 32 字节回复/事件 (阻塞, 调用方负责超时)。
func (xc *x11Conn) readReply(b []byte) error {
	_, err := ioReadFull(xc.br, b)
	return err
}
