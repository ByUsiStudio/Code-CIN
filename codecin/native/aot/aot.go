package aot

import (
	_ "embed"
	"fmt"
	"io"
	"os"

	"codecin-native/engine"
)

//go:embed stub_main.go.txt
var stubSource string

// StubSource 返回生成的 main.go 源码。
func StubSource() string { return stubSource }

const maxSteps = 100000000

// stackSlot 与解释器/CLI 一致: 栈从内存末尾向下增长。
const stackSlot = 8

// Main 运行内嵌的字节码并返回进程退出码 (0 = 正常结束, 1 = 运行期错误)。
//
// 标准输入只在被重定向 (管道/文件) 时读取, 交互式终端下不会挂起等待 EOF。
func Main(bytecode, memImage []byte) int {
	mem := make([]byte, len(memImage))
	copy(mem, memImage)

	var in []byte
	if fi, err := os.Stdin.Stat(); err == nil &&
		fi.Mode()&os.ModeCharDevice == 0 {
		if data, err := io.ReadAll(os.Stdin); err == nil {
			in = data
		}
	}

	sp := int64(len(mem) - stackSlot)
	res := engine.Run(bytecode, mem, 0, sp, int64(len(mem)/2), in, maxSteps)
	if res == nil {
		fmt.Fprintln(os.Stderr, "runtime error: no result")
		return 1
	}
	fmt.Print(res.Output)
	if res.Status == engine.StatusError ||
		res.Status == engine.StatusUnsupported {
		msg := res.ErrMsg
		if msg == "" {
			msg = "unsupported instruction (原生 VM 未实现该指令)"
		}
		fmt.Fprintf(os.Stderr, "runtime error: %s\n", msg)
		return 1
	}
	return 0
}
