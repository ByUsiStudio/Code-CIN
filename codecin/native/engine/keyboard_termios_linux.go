//go:build linux

package engine

// Linux / Android(Termux) termios 控制请求码 (TCGETS / TCSETS)。
const (
	ttyGetAttr = 0x5401
	ttySetAttr = 0x5402
)
