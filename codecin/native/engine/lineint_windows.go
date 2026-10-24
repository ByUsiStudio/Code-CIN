//go:build windows

package engine

import "time"

var procGenerateCtrlEvent = kernel32.NewProc("GenerateConsoleCtrlEvent")

const ctrlCEvent = 0

// raiseLineInterrupt raw 行编辑收到 Ctrl+C 时调用: 先恢复控制台模式,
// 再投递 CTRL_C_EVENT, 与行规程下 Ctrl+C 的行为一致。
// 无控制台 (GUI 子系统) 时投递失败, 调用方按 EOF 继续。
func raiseLineInterrupt() {
	keyboardRestore()
	_, _, _ = procGenerateCtrlEvent.Call(ctrlCEvent, 0)
	// 事件由控制台线程分发; 留出时间让默认处理生效
	time.Sleep(50 * time.Millisecond)
}
