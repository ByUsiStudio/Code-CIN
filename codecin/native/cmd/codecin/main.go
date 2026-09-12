// Command codecin 是 Code CIN 的独立 Go CLI: 编译并运行 .cin 程序 (全 Go 链路)。
//
// 用法: codecin <program.cin> [--dump-bytecode]
//
//	--dump-bytecode  只编译, 把 UCBC 字节码以十六进制打印到 stdout
//	                 (供 script/diff_go_python.py 做产物级等价比对)
package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"

	"codecin-native/compiler"
	"codecin-native/engine"
)

const (
	memSize   = 65536
	maxSteps  = 100000000
	stackSlot = 8
)

func main() {
	dumpBytecode := false
	path := ""
	for _, a := range os.Args[1:] {
		if a == "--dump-bytecode" {
			dumpBytecode = true
			continue
		}
		if path == "" {
			path = a
		}
	}
	if path == "" {
		fmt.Fprintln(os.Stderr, "usage: codecin <program.cin> [--dump-bytecode]")
		os.Exit(2)
	}

	if _, err := os.Stat(path); err != nil {
		fmt.Fprintf(os.Stderr, "codecin: cannot open %s: %v\n", path, err)
		os.Exit(1)
	}

	libDir := findLibDir(path)
	prog, err := compiler.CompileFile(path, libDir)
	if err != nil {
		fmt.Fprintf(os.Stderr, "codecin: compile error: %v\n", err)
		os.Exit(1)
	}

	bc, err := engine.EncodeProgram(*prog, 0)
	if err != nil {
		fmt.Fprintf(os.Stderr, "codecin: encode error: %v\n", err)
		os.Exit(1)
	}
	if dumpBytecode {
		fmt.Printf("%x\n", bc)
		return
	}

	mem := make([]byte, memSize)
	for _, dw := range prog.DataWrites {
		if dw.Addr >= 0 && dw.Addr+len(dw.Data) <= len(mem) {
			copy(mem[dw.Addr:], dw.Data)
		}
	}

	in, _ := io.ReadAll(os.Stdin)
	sp := int64(memSize - stackSlot)
	heap := int64(memSize / 2)
	res := engine.Run(bc, mem, 0, sp, heap, in, maxSteps)
	if res == nil {
		fmt.Fprintln(os.Stderr, "codecin: runtime error: no result")
		os.Exit(1)
	}
	fmt.Print(res.Output)
	if res.Status == engine.StatusError {
		fmt.Fprintf(os.Stderr, "codecin: runtime error: %s\n", res.ErrMsg)
		os.Exit(1)
	}
}

// findLibDir 定位仓库 lib/ 目录 (相对源文件向上查找)。
func findLibDir(srcPath string) string {
	dir := filepath.Dir(srcPath)
	candidates := []string{
		filepath.Join(dir, "lib"),
		filepath.Join(dir, "..", "lib"),
		filepath.Join(dir, "..", "..", "lib"),
		"lib",
	}
	for _, c := range candidates {
		if fi, err := os.Stat(c); err == nil && fi.IsDir() {
			abs, _ := filepath.Abs(c)
			return abs
		}
	}
	return ""
}
