package engine

// 跨平台标准输入行读取 (SYS 131 input_str 与 IN 指令共用)。
//
// 读取顺序与解释器路径一致:
//  1. 预读缓冲 vm.inData (管道/重定向输入由宿主一次性读入);
//  2. 真实标准输入 (交互 TTY / AOT 产物 / 缓冲耗尽)。
//
// 交互路径 (三平台统一): 真实终端或 GUI 窗口打开时, 走 stdinLineEditor;
// 行编辑由引擎基于统一键码队列完成 (与 get_key 同一种事件监听)。
//   - Windows: ReadConsoleInputW 键码 (raw 控制台, 不再用 ReadConsoleW
//     的 OS 行编辑), 非 ASCII 由 UTF-16 解码, 无代码页乱码;
//   - Unix/macOS: termios raw + 转义序列/UTF-8 解码;
//   - GUI 窗口: 窗口过程注入 keyQueue, 与虚拟键盘输入同通道。
//
// 非交互路径 (管道/重定向/无 GUI): stdinReadLinePlatform 之后的通用
// 字节流按行读, 管道字节原样透传, 输出字节与旧版本完全一致。

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
// 真实终端 / GUI 窗口下由 stdinLineEditor 接管 (raw 事件监听 + 引擎行编辑);
// 其余情况临时恢复行缓冲后走平台/字节流路径。
func stdinReadLine() (string, bool) {
	stdinMu.Lock()
	defer stdinMu.Unlock()
	if line, ok, used := stdinLineEditor(); used {
		return line, ok
	}
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
