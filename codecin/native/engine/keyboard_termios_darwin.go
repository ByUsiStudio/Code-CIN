//go:build darwin

package engine

// macOS termios 控制请求码 (TIOCGETA / TIOCSETA)。
const (
	ttyGetAttr = 0x40487413
	ttySetAttr = 0x80487414
)
