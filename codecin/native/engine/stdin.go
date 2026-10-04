package engine

// 跨平台标准输入行读取 (SYS 131 input_str 与 IN 指令共用)。
//
// 读取顺序与解释器路径一致:
//  1. 预读缓冲 vm.inData (管道/重定向输入由宿主一次性读入);
//  2. 真实标准输入 (交互 TTY / AOT 产物 / 缓冲耗尽)。
//
// 平台差异收敛在 stdinReadLinePlatform:
//   - Windows 真实控制台: ReadConsoleW (UTF-16 → UTF-8)。os.Stdin 直读在
//     Windows 控制台拿到的是 OEM/ANSI 代码页字节 (中文 Windows 下 GBK),
//     非 ASCII 输入必然乱码; ReadConsoleW 由控制台负责行编辑/回显,
//     与 Linux/macOS 终端同样支持退格、方向键历史 (conhost 自带)。
//   - 其余情况 (Unix 终端 / 全平台管道重定向): 字节流按行读,
//     Unix 终端输入本就是 UTF-8, 管道字节原样透传。

import (
	"bufio"
	"os"
	"strings"
	"sync"
)

var (
	stdinMu    sync.Mutex
	stdinBufRd *bufio.Reader // 通用字节流读取器 (惰性创建)
)

// stdinReadLine 阻塞读取一行标准输入, 返回 (行, 是否成功)。
// 行尾 \n 与 Windows 风格 \r\n 已剥离; EOF / 读失败返回 ("", false)。
// 键盘监听 (raw 模式) 激活时临时恢复行缓冲, 读完后重新启用。
func stdinReadLine() (string, bool) {
	stdinMu.Lock()
	defer stdinMu.Unlock()
	lineInputBegin()
	defer lineInputEnd()
	if line, ok, handled := stdinReadLinePlatform(); handled {
		return line, ok
	}
	if stdinBufRd == nil {
		stdinBufRd = bufio.NewReader(os.Stdin)
	}
	line, err := stdinBufRd.ReadString('\n')
	if err != nil && line == "" {
		return "", false
	}
	return line, true
}

// trimEOL 剥离行尾换行 (\n 与 \r\n)。
func trimEOL(s string) string {
	s = strings.TrimSuffix(s, "\n")
	s = strings.TrimSuffix(s, "\r")
	return s
}

// stdoutIsTerminal 判断 stdout 是否为真实终端 (管道/文件/测试捕获为 false)。
// 只有真实终端才允许把缓冲输出提前落盘 (交互提示可见), 管道/重定向保持
// "程序结束一次性回传" 的纯缓冲语义, 输出字节与旧版本完全一致。
func stdoutIsTerminal() bool {
	fi, err := os.Stdout.Stat()
	if err != nil {
		return false
	}
	return fi.Mode()&os.ModeCharDevice != 0
}
