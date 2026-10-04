//go:build !windows

package engine

// 非 Windows 平台: 终端与管道统一是 UTF-8 字节流 (term 行规程负责行编辑
// 与回显), 无需 ReadConsoleW 特化, 交还 stdin.go 的通用 bufio 路径。
func stdinReadLinePlatform() (string, bool, bool) {
	return "", false, false
}
