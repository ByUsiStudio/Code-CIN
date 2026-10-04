//go:build linux

package engine

import (
	"bufio"
	"encoding/binary"
	"image"
	"net"
	"os"
	"strconv"
	"strings"
	"time"
)

type x11Conn struct {
	conn      net.Conn
	order     binary.ByteOrder
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
	maxReqLen int // 请求最大字节数 (4 字节单位)
	nextID    uint32
}

// guiPlatformOpen 连接 X 服务并创建窗口 (同步, VM 线程)。
func guiPlatformOpen(w, h int, title string, buf *image.RGBA) bool {
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

// guiX11Dial 解析 $DISPLAY 建立连接 (unix socket 优先, 其次 TCP)。
func guiX11Dial() (*x11Conn, error) {
	spec := os.Getenv("DISPLAY")
	if spec == "" {
		return nil, errNoDisplay
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
			host+":"+strconv.Itoa(6000+atoiOr0(disp)), 2*time.Second)
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

var errNoDisplay = &net.OpError{Op: "dial", Err: errDisplayMissing}

type displayError struct{ msg string }

func (e *displayError) Error() string { return e.msg }

var errDisplayMissing = &displayError{msg: "DISPLAY not set"}

// parseDisplay 解析 "host:dpy[.screen]"。
func parseDisplay(spec string) (host, disp string, err error) {
	colon := strings.LastIndex(spec, ":")
	if colon < 0 {
		return "", "", &displayError{msg: "bad DISPLAY: " + spec}
	}
	host = spec[:colon]
	dpy := spec[colon+1:]
	if i := strings.Index(dpy, "."); i >= 0 {
		dpy = dpy[:i]
	}
	if _, e := strconv.Atoi(dpy); e != nil {
		return "", "", &displayError{msg: "bad DISPLAY number: " + spec}
	}
	return host, dpy, nil
}

func atoiOr0(s string) int {
	n, _ := strconv.Atoi(s)
	return n
}

// x11Handshake 发送连接握手并解析 setup 回复。
func x11Handshake(conn net.Conn, host, disp string) (*x11Conn, error) {
	// 认证: MIT-MAGIC-COOKIE-1 (尽力而为; 本地 socket 通常无需)
	name, data := xauthCookie(host, disp)
	hdr := make([]byte, 12)
	hdr[0] = 'l' // LSBFirst
	binary.BigEndian.PutUint16(hdr[2:4], 11) // protocol major
	nameLen := len(name)
	dataLen := len(data)
	binary.BigEndian.PutUint16(hdr[6:8], uint16(nameLen))
	binary.BigEndian.PutUint16(hdr[8:10], uint16(dataLen))
	req := append(hdr, name...)
	req = append(req, make([]byte, pad4(nameLen)-nameLen)...)
	req = append(req, data...)
	req = append(req, make([]byte, pad4(dataLen)-dataLen)...)
	if _, err := conn.Write(req); err != nil {
		return nil, err
	}
	r := bufio.NewReader(conn)
	first, err := r.ReadByte()
	if err != nil {
		return nil, err
	}
	if first != 1 { // 0=failed, 2=authenticate
		return nil, &displayError{msg: "X11 handshake rejected"}
	}
	setup := make([]byte, 7)
	if _, err := ioReadFull(r, setup); err != nil {
		return nil, err
	}
	extra := int(binary.BigEndian.Uint16(setup[5:7])) * 4
	body := make([]byte, extra)
	if _, err := ioReadFull(r, body); err != nil {
		return nil, err
	}
	// body: release(4) resBase(4) mask(4) motion(4) vendorLen(2) maxReq(2)
	//       screens(1) formats(1) order(1) bitorder(1) unit(1) pad(1)
	//       minKC(1) maxKC(1) ...
	if len(body) < 32 {
		return nil, &displayError{msg: "X11 setup truncated"}
	}
	vendorLen := int(binary.BigEndian.Uint16(body[16:18]))
	maxReq := int(binary.BigEndian.Uint16(body[18:20])) * 4
	numFormats := int(body[23])
	minKC, maxKC := body[30], body[31]
	pos := 32 + vendorLen
	pos = padTo(pos, 4)
	pos += numFormats * 8 // 深度格式
	if len(body) < pos+40 {
		return nil, &displayError{msg: "X11 setup truncated"}
	}
	sc := body[pos:]
	root := binary.BigEndian.Uint32(sc[0:4])
	sc2 := sc[32:] // 跳过到 root-visual(20)/depth(23)/allowed-depths(24)
	rootVisual := binary.BigEndian.Uint32(sc2[20:24])
	rootDepth := sc2[23]
	nDepths := int(sc2[24])
	pos2 := 40
	visual, depth := findTrueColor(sc2[pos2:], nDepths, rootVisual, rootDepth)
	xc := &x11Conn{
		conn:      conn,
		order:     binary.BigEndian,
		resBase:   binary.BigEndian.Uint32(body[4:8]),
		root:      root,
		visual:    visual,
		depth:     depth,
		minKC:     minKC,
		maxKC:     maxKC,
		maxReqLen: maxReq,
		nextID:    0,
	}
	if xc.visual == 0 {
		return nil, &displayError{msg: "no TrueColor visual found"}
	}
	if !xc.loadKeymap(r) {
		return nil, &displayError{msg: "X11 keymap query failed"}
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
			if class == 4 && depth >= 24 { // TrueColor
				return id, depth
			}
			pos += 24
		}
	}
	return fallbackVisual, fallbackDepth
}

// loadKeymap GetKeyboardMapping: keycode -> keysyms。
func (xc *x11Conn) loadKeymap(r *bufio.Reader) bool {
	count := int(xc.maxKC) - int(xc.minKC) + 1
	if count <= 0 {
		return false
	}
	req := make([]byte, 4)
	req[0] = 101 // GetKeyboardMapping
	req[1] = xc.minKC
	binary.BigEndian.PutUint16(req[2:4], uint16(count))
	if _, err := xc.conn.Write(req); err != nil {
		return false
	}
	head := make([]byte, 8)
	if _, err := ioReadFull(r, head); err != nil {
		return false
	}
	if head[0] != 1 {
		return false
	}
	n := int(binary.BigEndian.Uint32(head[4:8])) * 4
	body := make([]byte, n)
	if _, err := ioReadFull(r, body); err != nil {
		return false
	}
	xc.perKC = n / 4 / count
	if xc.perKC <= 0 {
		return false
	}
	xc.keysyms = make([]uint32, n/4)
	for i := range xc.keysyms {
		xc.keysyms[i] = binary.BigEndian.Uint32(body[i*4 : i*4+4])
	}
	return true
}

func (xc *x11Conn) allocID() uint32 {
	xc.nextID++
	return xc.resBase + xc.nextID
}

func (xc *x11Conn) openWindow(w, h int, title string) bool {
	win := xc.allocID()
	gc := xc.allocID()
	atomWMProtocols := xc.internAtom("WM_PROTOCOLS")
	atomWMDelete := xc.internAtom("WM_DELETE_WINDOW")

	const mask = 0x2 | 0x800
	req := make([]byte, 8+32)
	req[0] = 1 // CreateWindow
	putB16(req[2:4], uint32(8+len(req)-8)/4)
	putB16(req[4:8], win)
	putB16(req[8:12], xc.root)
	putB16(req[12:14], 0) // x
	putB16(req[14:16], 0) // y
	putB16(req[16:18], uint32(w))
	putB16(req[18:20], uint32(h))
	putB16(req[20:22], 1)                       // border width
	putB16(req[22:24], 1)                       // class: InputOutput
	putB16(req[24:28], xc.visual)               // visual
	putB16(req[28:32], mask)                    // value-mask
	putB16(req[32:36], 0xFFFFFF)                // background pixel (白)
	putB16(req[36:40], 0x20800|0x4|0x8|0x40|0x20000) // 曝光|按键|释放|指针|结构
	if err := xc.write(req); err != nil {
		return false
	}

	// CreateGC (graphics-exposures off)
	greq := make([]byte, 20)
	greq[0] = 55
	putB16(greq[2:4], 5)
	putB16(greq[4:8], gc)
	putB16(greq[8:12], win)
	putB16(greq[12:16], 1<<9) // GC_GRAPHICS_EXPOSURES
	// value 0 = off
	if err := xc.write(greq); err != nil {
		return false
	}
	// SetWMProtocols (ChangeProperty): 让点 X 触发 WM_DELETE_WINDOW
	if atomWMProtocols != 0 && atomWMDelete != 0 {
		preq := make([]byte, 28)
		preq[0] = 18 // ChangeProperty
		putB16(preq[2:4], 7)
		putB16(preq[4:8], win)
		putB16(preq[8:12], atomWMProtocols)
		preq[12] = 4 // format: 32
		putB16(preq[16:20], 1)
		putB16(preq[20:24], atomWMDelete)
		_ = xc.write(preq)
	}
	// MapWindow
	mreq := make([]byte, 8)
	mreq[0] = 8
	putB16(mreq[4:8], win)
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

// internAtom InternAtom (only-if-exists=0)。
func (xc *x11Conn) internAtom(name string) uint32 {
	b := []byte(name)
	req := make([]byte, 8+len(b))
	req[0] = 16
	putB16(req[2:4], uint32((8+len(b))/4))
	putB16(req[4:6], 0) // only-if-exists: false
	putB16(req[6:8], uint16(len(b)))
	copy(req[8:], b)
	if err := xc.write(req); err != nil {
		return 0
	}
	// 阻塞读回复 (连接仅此一处同步往返, 2s 超时由 dial 阶段覆盖)
	rep := make([]byte, 32)
	if err := xc.readEvent(rep); err != nil {
		return 0
	}
	if rep[0] != 1 {
		return 0
	}
	return binary.BigEndian.Uint32(rep[8:12])
}

// present 把 RGBA 像素呈现到窗口 (PutImage 分块; BGRX 依服务器字节序)。
func (xc *x11Conn) present(pix []byte, w, h int) bool {
	bpl := w * 4
	// 每请求行数受 max-request-length 限制 (头 24 字节)
	chunkRows := (xc.maxReqLen - 24) / bpl
	if chunkRows < 1 {
		chunkRows = 1
	}
	if chunkRows > h {
		chunkRows = h
	}
	row := 0
	for row < h {
		rows := chunkRows
		if rows > h-row {
			rows = h - row
		}
		n := rows * bpl
		req := make([]byte, 24+n)
		req[0] = 72 // PutImage
		putB16(req[2:4], uint32((24+n)/4))
		putB16(req[4:8], xc.win)
		putB16(req[8:12], xc.gc)
		putB16(req[12:14], uint32(w))
		putB16(req[14:16], uint32(rows))
		putB16(req[16:18], 0) // dst-x
		putB16(req[18:20], uint32(row))
		req[21] = xc.depth
		packPixels(pix[row*bpl:(row+rows)*bpl], req[24:], xc.order)
		if err := xc.write(req); err != nil {
			return false
		}
		row += rows
	}
	return true
}

// packPixels RGBA -> ZPixmap 像素 (LSB 服务器: B,G,R,x; MSB: x,R,G,B)。
func packPixels(src, dst []byte, order binary.ByteOrder) {
	lsb := order == binary.LittleEndian
	j := 0
	for i := 0; i+3 < len(src); i += 4 {
		if lsb {
			dst[j] = src[i+2]
			dst[j+1] = src[i+1]
			dst[j+2] = src[i]
		} else {
			dst[j] = 0
			dst[j+1] = src[i]
			dst[j+2] = src[i+1]
			dst[j+3] = src[i+2]
		}
		j += 4
	}
}

func (xc *x11Conn) destroy() {
	if xc.win != 0 {
		req := make([]byte, 8)
		req[0] = 4 // DestroyWindow
		putB16(req[4:8], xc.win)
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
		if _, err := ioReadFull(xc.conn, ev[:]); err != nil {
			if isTimeout(err) {
				return true // 输入暂尽: 正常
			}
			return false // 连接断开
		}
		xc.handleEvent(ev)
	}
}

// handleEvent 解析单个 32 字节事件。
func (xc *x11Conn) handleEvent(ev []byte) {
	switch ev[0] {
	case 0: // 错误: 消费
	case 2, 3: // KeyPress / KeyRelease
		if ev[0] != 2 {
			return
		}
		kc := ev[1]
		state := binary.BigEndian.Uint16(ev[28:30])
		if k, ok := xc.keycodeToKey(kc, state); ok {
			guiInjectKey(k)
		}
	case 4: // ButtonPress
		guiX11Button(int(ev[1]), true, binary.BigEndian.Uint16(ev[28:30]))
		guiX11Mouse(ev)
	case 5: // ButtonRelease
		guiX11Button(int(ev[1]), false, 0)
		guiX11Mouse(ev)
	case 6: // MotionNotify
		guiX11Mouse(ev)
	case 33: // ClientMessage (WM_DELETE_WINDOW)
		guiMarkClosed()
	default: // Expose / MapNotify / GraphicsExpose 等: 消费
	}
}

func guiX11Mouse(ev []byte) {
	x := int(int16(binary.BigEndian.Uint16(ev[24:26])))
	y := int(int16(binary.BigEndian.Uint16(ev[26:28])))
	guiSetMouse(x, y, -1)
}

// guiX11Button X11 按钮号 -> guiMouseBtn 位 (1 左 / 2 中 / 3 右)。
func guiX11Button(detail int, down bool, state uint16) {
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
	_ = state
}

// keycodeToKey 键码 -> 键码值 (与终端/Windows 三端一致)。
func (xc *x11Conn) keycodeToKey(kc uint8, state uint16) (uint64, bool) {
	shift := state&0x1 != 0
	ctrl := state&0x4 != 0
	k := xc.lookupKeysym(kc, shift)
	if k == 0 {
		return 0, false
	}
	// Ctrl+字母 -> 控制字节 (与 Windows/终端一致: Ctrl+C=3)
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
	case 0xFF63:
		return keyIns, true
	case 0xFFFF:
		return keyDel, true
	case 0xFF55:
		return keyPgUp, true
	case 0xFF56:
		return keyPgDn, true
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
	case 0xFF51:
		return arrowVariant(keyLeft, x11Mod(shift, ctrl)), true
	case 0xFF52:
		return arrowVariant(keyUp, x11Mod(shift, ctrl)), true
	case 0xFF53:
		return arrowVariant(keyRight, x11Mod(shift, ctrl)), true
	case 0xFF54:
		return arrowVariant(keyDown, x11Mod(shift, ctrl)), true
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

// lookupKeysym 取键盘映射: col0 = 基础, col1 = Shift 层。
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
	f, err := os.Open(path)
	if err != nil {
		return "", nil
	}
	defer f.Close()
	data, err := io.ReadAll(f)
	if err != nil {
		return "", nil
	}
	pos := 0
	for pos+4 <= len(data) {
		family := int(binary.BigEndian.Uint16(data[pos : pos+2]))
		pos += 2
		var addr, number, aname, adata []byte
		if pos+2 > len(data) {
			break
		}
		n := int(binary.BigEndian.Uint16(data[pos : pos+2]))
		pos += 2
		if pos+n > len(data) {
			break
		}
		addr = data[pos : pos+n]
		pos += n
		if pos+2 > len(data) {
			break
		}
		n = int(binary.BigEndian.Uint16(data[pos : pos+2]))
		pos += 2
		if pos+n > len(data) {
			break
		}
		number = data[pos : pos+n]
		pos += n
		if pos+2 > len(data) {
			break
		}
		n = int(binary.BigEndian.Uint16(data[pos : pos+2]))
		pos += 2
		if pos+n > len(data) {
			break
		}
		aname = data[pos : pos+n]
		pos += n
		if pos+2 > len(data) {
			break
		}
		n = int(binary.BigEndian.Uint16(data[pos : pos+2]))
		pos += 2
		if pos+n > len(data) {
			break
		}
		adata = data[pos : pos+n]
		pos += n
		if string(aname) != "MIT-MAGIC-COOKIE-1" {
			continue
		}
		if string(number) != disp {
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

func putB16(b []byte, v uint32) { binary.BigEndian.PutUint32(b, v) }

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

// readEvent 读一个 32 字节事件/回复 (阻塞, 需外部设置超时)。
func (xc *x11Conn) readEvent(b []byte) error {
	_, err := ioReadFull(bufio.NewReader(xc.conn), b)
	return err
}
