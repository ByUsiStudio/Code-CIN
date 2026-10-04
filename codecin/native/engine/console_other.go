//go:build !windows

package engine

// 非 Windows 平台终端原生支持 ANSI/VT 与 UTF-8, 无需初始化。
func consoleInit() {}
