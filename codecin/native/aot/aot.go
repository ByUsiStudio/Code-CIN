// Package aot 提供 AOT 构建: 把 CIN 程序编译成**独立静态可执行文件**。
//
// 产物不依赖 Python、不依赖 Go 工具链、不依赖任何动态库:
// 字节码与初始内存镜像被嵌入二进制, 由内置的 Go VM 执行
// (构建时 CGO_ENABLED=0, 因此 Windows / Linux / macOS 均为静态链接)。
//
// 构建流程 (Build):
//  1. 在 codecin-native 模块内创建临时包目录 .aotbuild-<rand>/
//     (Go 工具链会忽略以 '.' 开头的目录, 因此不影响 go build ./...);
//  2. 写入 main.go (由共享模板 stub_main.go.txt 提供) 与两个资源文件;
//  3. CGO_ENABLED=0 + GOOS/GOARCH 交叉编译出目标平台可执行文件;
//  4. 删除临时目录。
package aot

import (
	_ "embed"
	"fmt"
	"io"
	"os"

	"codecin-native/engine"
)

// StubSource 是生成的 main.go 模板, 与 Python 侧 codecin/aot.py 共用同一份
// 文件 (codecin/native/aot/stub_main.go.txt), 避免两处模板漂移。
//
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
