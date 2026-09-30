//go:build !windows

package engine

import (
	"syscall"
	"unsafe"
)

// Unix / macOS / Termux 键盘后端: termios 原始输入 (非阻塞)。
// 只关 ICANON / ECHO / ISIG (行缓冲 / 回显 / Ctrl+C 信号), 保留输出标志
// (OPOST / ONLCR), 否则 get_key 轮询期间 println 的换行会错乱。
// 非 tty (管道 / 重定向): TCGETS 失败即不启用, 键盘 API 优雅失败。

var (
	keySavedTermios syscall.Termios
	keySavedOK      bool
)

func keyEnablePlatform() bool {
	var t syscall.Termios
	if err := ttyIoctl(ttyGetAttr, &t); err != nil {
		return false // 非 tty: 不启用
	}
	keySavedTermios = t
	keySavedOK = true
	raw := t
	raw.Lflag &^= syscall.ICANON | syscall.ECHO | syscall.ISIG
	raw.Cc[syscall.VMIN] = 0
	raw.Cc[syscall.VTIME] = 0
	return ttyIoctl(ttySetAttr, &raw) == nil
}

func keyRestorePlatform() {
	if keySavedOK {
		_ = ttyIoctl(ttySetAttr, &keySavedTermios)
		keySavedOK = false
	}
}

// keyNext 非阻塞读 stdin 并经转义序列状态机解码。
func keyNext() (uint64, bool) {
	for {
		var b [1]byte
		n, err := syscall.Read(0, b[:])
		if err != nil || n == 0 {
			// 输入暂尽: 若停在单独 ESC (序列未到齐) 则立即产出 ESC,
			// 避免单键被吞
			if keyEsc == 1 {
				keyEsc = 0
				return 0x1B, true
			}
			return 0, false
		}
		if code, done := keyFeed(b[0]); done {
			return code, true
		}
	}
}

// ttyIoctl 对 stdin (fd 0) 发 termios 控制请求。
func ttyIoctl(req uintptr, t *syscall.Termios) error {
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, 0, req,
		uintptr(unsafe.Pointer(t)))
	if errno != 0 {
		return errno
	}
	return nil
}
