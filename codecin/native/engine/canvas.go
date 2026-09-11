package engine

import (
	"fmt"
	"image"
	"image/color"
	"image/png"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// 2D 绘图画布 (Go 标准库 image/png 实现): 新建画布 → 着色 → 绘制形状/文本 → 导出 PNG。
// 宿主全局状态: 同一时刻一个「当前画布」与「当前颜色」。

var (
	curCanvas *image.RGBA
	curColor  = color.RGBA{R: 0, G: 0, B: 0, A: 255} // 默认黑色画笔
)

func clampCoord(v uint64) int {
	if v > 100000 {
		return 100000
	}
	return int(v)
}

func (vm *vmState) canvasNew(w, h uint64) {
	cw, ch := clampCoord(w), clampCoord(h)
	if cw < 1 {
		cw = 1
	}
	if ch < 1 {
		ch = 1
	}
	curCanvas = image.NewRGBA(image.Rect(0, 0, cw, ch))
	// 白色背景
	for i := range curCanvas.Pix {
		curCanvas.Pix[i] = 255
	}
	curColor = color.RGBA{R: 0, G: 0, B: 0, A: 255}
}

func (vm *vmState) canvasSetColor(rgb uint64) {
	curColor = color.RGBA{
		R: uint8((rgb >> 16) & 0xFF),
		G: uint8((rgb >> 8) & 0xFF),
		B: uint8(rgb & 0xFF),
		A: 255,
	}
}

func (vm *vmState) canvasRect(x, y, w, h uint64) {
	if curCanvas == nil {
		return
	}
	x0, y0 := clampCoord(x), clampCoord(y)
	x1, y1 := x0+clampCoord(w), y0+clampCoord(h)
	b := curCanvas.Bounds()
	if x0 < b.Min.X {
		x0 = b.Min.X
	}
	if y0 < b.Min.Y {
		y0 = b.Min.Y
	}
	if x1 > b.Max.X {
		x1 = b.Max.X
	}
	if y1 > b.Max.Y {
		y1 = b.Max.Y
	}
	for py := y0; py < y1; py++ {
		for px := x0; px < x1; px++ {
			curCanvas.SetRGBA(px, py, curColor)
		}
	}
}

func (vm *vmState) canvasCircle(cx, cy, r uint64) {
	if curCanvas == nil {
		return
	}
	ccx, ccy := clampCoord(cx), clampCoord(cy)
	rr := clampCoord(r)
	rr2 := rr * rr
	b := curCanvas.Bounds()
	for py := ccy - rr; py <= ccy+rr; py++ {
		for px := ccx - rr; px <= ccx+rr; px++ {
			dx := px - ccx
			dy := py - ccy
			if dx*dx+dy*dy <= rr2 {
				if px >= b.Min.X && px < b.Max.X && py >= b.Min.Y && py < b.Max.Y {
					curCanvas.SetRGBA(px, py, curColor)
				}
			}
		}
	}
}

func (vm *vmState) canvasLine(x0, y0, x1, y1 uint64) {
	if curCanvas == nil {
		return
	}
	ax, ay := clampCoord(x0), clampCoord(y0)
	bx, by := clampCoord(x1), clampCoord(y1)
	b := curCanvas.Bounds()
	// Bresenham
	dx := bx - ax
	if dx < 0 {
		dx = -dx
	}
	sx := 1
	if ax > bx {
		sx = -1
	}
	dy := by - ay
	if dy < 0 {
		dy = -dy
	}
	sy := 1
	if ay > by {
		sy = -1
	}
	err := dx - dy
	for {
		if ax >= b.Min.X && ax < b.Max.X && ay >= b.Min.Y && ay < b.Max.Y {
			curCanvas.SetRGBA(ax, ay, curColor)
		}
		if ax == bx && ay == by {
			break
		}
		e2 := 2 * err
		if e2 > -dy {
			err -= dy
			ax += sx
		}
		if e2 < dx {
			err += dx
			ay += sy
		}
	}
}

// font5x7: 内置 5x7 点阵字库 (ASCII 数字/大写/常用标点; 小写映射为大写)。
type glyph [7]string

var font5x7 = map[rune]glyph{
	' ': {"     ", "     ", "     ", "     ", "     ", "     ", "     "},
	'0': {" ### ", "#   #", "#  ##", "# # #", "##  #", "#   #", " ### "},
	'1': {"  #  ", " ##  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "},
	'2': {" ### ", "#   #", "    #", "   # ", "  #  ", " #   ", "#####"},
	'3': {"#####", "    #", "   # ", "  ## ", "    #", "#   #", " ### "},
	'4': {"   # ", "  ## ", " # # ", "#  # ", "#####", "   # ", "   # "},
	'5': {"#####", "#    ", "#### ", "    #", "    #", "#   #", " ### "},
	'6': {"  ## ", " #   ", "#    ", "#### ", "#   #", "#   #", " ### "},
	'7': {"#####", "    #", "   # ", "  #  ", " #   ", " #   ", " #   "},
	'8': {" ### ", "#   #", "#   #", " ### ", "#   #", "#   #", " ### "},
	'9': {" ### ", "#   #", "#   #", " ####", "    #", "   # ", " ##  "},
	'A': {" ### ", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"},
	'B': {"#### ", "#   #", "#   #", "#### ", "#   #", "#   #", "#### "},
	'C': {" ### ", "#   #", "#    ", "#    ", "#    ", "#   #", " ### "},
	'D': {"#### ", "#   #", "#   #", "#   #", "#   #", "#   #", "#### "},
	'E': {"#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#####"},
	'F': {"#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#    "},
	'G': {" ### ", "#   #", "#    ", "# ###", "#   #", "#   #", " ####"},
	'H': {"#   #", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"},
	'I': {" ### ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "},
	'J': {"    #", "    #", "    #", "    #", "#   #", "#   #", " ### "},
	'K': {"#   #", "#  # ", "# #  ", "##   ", "# #  ", "#  # ", "#   #"},
	'L': {"#    ", "#    ", "#    ", "#    ", "#    ", "#    ", "#####"},
	'M': {"#   #", "## ##", "# # #", "# # #", "#   #", "#   #", "#   #"},
	'N': {"#   #", "##  #", "# # #", "#  ##", "#   #", "#   #", "#   #"},
	'O': {" ### ", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "},
	'P': {"#### ", "#   #", "#   #", "#### ", "#    ", "#    ", "#    "},
	'Q': {" ### ", "#   #", "#   #", "#   #", "# # #", "#  # ", " ## #"},
	'R': {"#### ", "#   #", "#   #", "#### ", "# #  ", "#  # ", "#   #"},
	'S': {" ####", "#    ", "#    ", " ### ", "    #", "    #", "#### "},
	'T': {"#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  "},
	'U': {"#   #", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "},
	'V': {"#   #", "#   #", "#   #", "#   #", "#   #", " # # ", "  #  "},
	'W': {"#   #", "#   #", "#   #", "# # #", "# # #", "## ##", "#   #"},
	'X': {"#   #", "#   #", " # # ", "  #  ", " # # ", "#   #", "#   #"},
	'Y': {"#   #", "#   #", " # # ", "  #  ", "  #  ", "  #  ", "  #  "},
	'Z': {"#####", "    #", "   # ", "  #  ", " #   ", "#    ", "#####"},
	'.': {"     ", "     ", "     ", "     ", "     ", " ##  ", " ##  "},
	',': {"     ", "     ", "     ", "     ", "  ## ", "  #  ", " #   "},
	':': {"     ", "  ## ", "  ## ", "     ", "  ## ", "  ## ", "     "},
	'-': {"     ", "     ", "     ", "#####", "     ", "     ", "     "},
	'!': {"  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "     ", "  #  "},
	'?': {" ### ", "#   #", "    #", "   # ", "  #  ", "     ", "  #  "},
	'/': {"    #", "    #", "   # ", "  #  ", " #   ", "#    ", "#    "},
}

func (vm *vmState) canvasText(x, y uint64, s string) {
	if curCanvas == nil {
		return
	}
	ox, oy := clampCoord(x), clampCoord(y)
	s = strings.ToUpper(s)
	b := curCanvas.Bounds()
	px := ox
	for _, r := range s {
		g, ok := font5x7[r]
		if !ok {
			g = font5x7['?']
		}
		for row := 0; row < 7; row++ {
			for col := 0; col < 5; col++ {
				if g[row][col] == '#' {
					cx := px + col
					cy := oy + row
					if cx >= b.Min.X && cx < b.Max.X && cy >= b.Min.Y && cy < b.Max.Y {
						curCanvas.SetRGBA(cx, cy, curColor)
					}
				}
			}
		}
		px += 6
	}
}

func (vm *vmState) canvasSave(path string) uint64 {
	if curCanvas == nil {
		return mask64 // -1
	}
	f, err := os.Create(path)
	if err != nil {
		return mask64
	}
	defer f.Close()
	if err := png.Encode(f, curCanvas); err != nil {
		return mask64
	}
	return 0
}

// canvasShow 保存当前画布到临时 PNG 并用系统查看器打开 (跨平台 "窗口")。
func (vm *vmState) canvasShow() uint64 {
	if curCanvas == nil {
		return mask64 // -1
	}
	path := filepath.Join(os.TempDir(),
		fmt.Sprintf("codecin_canvas_%d.png", time.Now().UnixNano()))
	f, err := os.Create(path)
	if err != nil {
		return mask64
	}
	if err := png.Encode(f, curCanvas); err != nil {
		_ = f.Close()
		return mask64
	}
	_ = f.Close()
	openViewer(path)
	return 0
}

func openViewer(path string) {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.Command("cmd", "/c", "start", "", path)
	case "darwin":
		cmd = exec.Command("open", path)
	default:
		cmd = exec.Command("xdg-open", path)
	}
	_ = cmd.Start()
}
