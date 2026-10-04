//go:build windows

package engine

import (
	"sync"
	"unsafe"
)

// Windows 控制台输出兼容 (一次性的进程级初始化, 失败安全):
//   - ENABLE_VIRTUAL_TERMINAL_PROCESSING: 让 ANSI/VT 转义序列 (颜色/清屏/
//     光标) 在传统 conhost 上与 Linux/macOS 终端行为一致;
//   - SetConsoleOutputCP(65001): 输出代码页切 UTF-8。Go 恒以 UTF-8 直写
//     stdout, 中文 Windows 默认 GBK 代码页下中文/Emoji 会乱码。
// 两步都允许失败: 老 conhost 不支持 VT / 只读句柄时保持原样, 输出字节不变。

var (
	consoleInitOnce        sync.Once
	procSetConsoleOutputCP = kernel32.NewProc("SetConsoleOutputCP")
)

const (
	stdOutputHandle    = ^uintptr(10) // STD_OUTPUT_HANDLE = (DWORD)-11
	cmEnableVTOutput   = 0x0004       // ENABLE_VIRTUAL_TERMINAL_PROCESSING
	consoleCodePageUTF = 65001
)

// consoleInit 幂等初始化 Windows 控制台输出 (其他平台为空操作)。
func consoleInit() {
	consoleInitOnce.Do(func() {
		h, _, _ := procGetStdHandle.Call(stdOutputHandle)
		if h != 0 {
			var mode uint32
			if r, _, _ := procGetConsoleMode.Call(h, uintptr(unsafe.Pointer(&mode))); r != 0 {
				_, _, _ = procSetConsoleMode.Call(h, uintptr(mode|cmEnableVTOutput))
			}
		}
		_, _, _ = procSetConsoleOutputCP.Call(consoleCodePageUTF)
	})
}

// ConsoleInit 供 engine 包外 (aot 产物) 在打印输出前初始化终端。
func ConsoleInit() { consoleInit() }
