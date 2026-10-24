//go:build !windows

package engine

import (
	"os"
	"syscall"
)

// raiseLineInterrupt raw 行编辑收到 Ctrl+C 时调用: 先恢复终端 (关 raw),
// 再把 SIGINT 重新投递给自身, 与行规程 (ISIG) 下的 Ctrl+C 语义一致。
// Go 运行时对 SIGINT 的默认动作是终止进程; 若宿主已接管信号 (自定义
// handler), 信号被处理后会返回, 调用方按 EOF 继续。
func raiseLineInterrupt() {
	keyboardRestore()
	_ = syscall.Kill(os.Getpid(), syscall.SIGINT)
}
