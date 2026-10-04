//go:build windows

package engine

import (
	"strings"
	"unicode/utf16"
	"unsafe"
)

// Windows 控制台行输入: ReadConsoleW (Unicode)。
// stdin 是真实控制台时接管行读取 (handled=true); 管道/重定向返回
// handled=false, 由 stdin.go 的通用字节流路径处理。
// 行编辑与回显由控制台默认模式 (ENABLE_LINE_INPUT|ENABLE_ECHO_INPUT)
// 提供; 键盘监听 raw 化过的话 lineInputBegin 已先恢复保存的模式。

var procReadConsoleW = kernel32.NewProc("ReadConsoleW")

// stdinReadLinePlatform Windows 实现。
// 返回 (行, 是否成功, 是否接管); 接管后不再走通用路径。
func stdinReadLinePlatform() (string, bool, bool) {
	h, _, _ := procGetStdHandle.Call(stdInputHandle)
	if h == 0 {
		return "", false, false
	}
	var mode uint32
	if r, _, _ := procGetConsoleMode.Call(h, uintptr(unsafe.Pointer(&mode))); r == 0 {
		return "", false, false // 管道 / 重定向: 交还通用路径
	}

	const chunk = 512 // UTF-16 单元; 行模式下单次调用最多读满一块
	var acc []uint16
	var wbuf [chunk]uint16
	for {
		var n uint32
		r, _, _ := procReadConsoleW.Call(h,
			uintptr(unsafe.Pointer(&wbuf[0])), uintptr(chunk),
			uintptr(unsafe.Pointer(&n)), 0)
		if r == 0 {
			if len(acc) == 0 {
				return "", false, true
			}
			break
		}
		acc = append(acc, wbuf[:n]...)
		// 一行结束: 已读到回车, 或读不满一块 (EOF / 无行尾的最后一行)
		if n == 0 || n < chunk || (len(acc) > 0 && acc[len(acc)-1] == '\n') {
			break
		}
	}
	if len(acc) == 0 {
		return "", false, true // Ctrl+Z EOF 等: 视为 EOF
	}
	// UTF-16 → UTF-8 (代理对由 utf16.Decode 组合, 与键码解码一致)
	line := string(utf16.Decode(acc))
	// 控制台行模式附带 CRLF; 无行尾输入可能带裸 CR
	line = strings.TrimSuffix(line, "\n")
	line = strings.TrimSuffix(line, "\r")
	return line, true, true
}
