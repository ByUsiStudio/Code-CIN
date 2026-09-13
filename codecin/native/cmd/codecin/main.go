// Command codecin 是 Code CIN 的独立 Go CLI: 编译并运行 .cin 程序 (全 Go 链路)。
//
// 用法:
//
//	codecin <program.cin> [--dump-bytecode]
//	codecin build <program.cin> [-o out] [--target os/arch] [--keep-temp]
//
//	--dump-bytecode  只编译, 把 UCBC 字节码以十六进制打印到 stdout
//	                 (供 script/diff_go_python.py 做产物级等价比对)
//	build            编译成**独立静态可执行文件** (不依赖 Python/动态库),
//	                 默认目标为当前平台, 可用 --target 交叉编译。
package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"

	"codecin-native/aot"
	"codecin-native/compiler"
	"codecin-native/engine"
	"codecin-native/ir"
)

const (
	memSize   = aot.DefaultMemSize
	maxSteps  = 100000000
	stackSlot = 8
)

func main() {
	args := os.Args[1:]
	if len(args) > 0 && args[0] == "build" {
		os.Exit(runBuild(args[1:]))
	}
	if len(args) > 0 && (args[0] == "--help" || args[0] == "-h") {
		usage(os.Stdout)
		return
	}

	dumpBytecode := false
	path := ""
	for _, a := range args {
		if a == "--dump-bytecode" {
			dumpBytecode = true
			continue
		}
		if path == "" {
			path = a
		}
	}
	if path == "" {
		usage(os.Stderr)
		os.Exit(2)
	}

	if _, err := os.Stat(path); err != nil {
		fmt.Fprintf(os.Stderr, "codecin: cannot open %s: %v\n", path, err)
		os.Exit(1)
	}

	prog, err := compiler.CompileFile(path, findLibDir(path))
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

	mem := buildMemImage(prog)
	res := engine.Run(bc, mem, 0, int64(memSize-stackSlot),
		int64(memSize/2), readStdin(), maxSteps)
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

// runBuild 实现 `codecin build`: 编译成独立静态可执行文件。
func runBuild(args []string) int {
	out := ""
	target := ""
	keepTemp := false
	path := ""
	for i := 0; i < len(args); i++ {
		a := args[i]
		switch a {
		case "-o", "--out", "--output":
			if i+1 >= len(args) {
				fmt.Fprintln(os.Stderr, "codecin build: --out 需要一个路径")
				return 2
			}
			i++
			out = args[i]
		case "--target":
			if i+1 >= len(args) {
				fmt.Fprintln(os.Stderr, "codecin build: --target 需要 os/arch")
				return 2
			}
			i++
			target = args[i]
		case "--keep-temp":
			keepTemp = true
		case "--help", "-h":
			usage(os.Stdout)
			return 0
		default:
			if path == "" {
				path = a
			}
		}
	}
	if path == "" {
		fmt.Fprintln(os.Stderr, "codecin build: 缺少 <program.cin>")
		usage(os.Stderr)
		return 2
	}
	if _, err := os.Stat(path); err != nil {
		fmt.Fprintf(os.Stderr, "codecin: cannot open %s: %v\n", path, err)
		return 1
	}

	tgt := aot.Target{GOOS: runtime.GOOS, GOARCH: runtime.GOARCH}
	if target != "" {
		parts := splitTarget(target)
		if parts == nil {
			fmt.Fprintf(os.Stderr, "codecin build: --target 格式应为 os/arch, 例如 linux/amd64 (支持: %v)\n",
				aot.SupportedTargets)
			return 2
		}
		tgt = aot.Target{GOOS: parts[0], GOARCH: parts[1]}
	}
	if out == "" {
		base := path[:len(path)-len(filepath.Ext(path))]
		out = base + tgt.ExeSuffix()
	}

	moduleDir := aot.FindModuleDir(exeDir(), cwd(), path)
	if moduleDir == "" {
		fmt.Fprintln(os.Stderr, "codecin build: 找不到 codecin-native 模块目录 (go.mod); "+
			"AOT 构建需要 Go 工具链与仓库源码")
		return 1
	}

	prog, err := compiler.CompileFile(path, findLibDir(path))
	if err != nil {
		fmt.Fprintf(os.Stderr, "codecin: compile error: %v\n", err)
		return 1
	}
	bc, err := engine.EncodeProgram(*prog, 0)
	if err != nil {
		fmt.Fprintf(os.Stderr, "codecin: encode error: %v\n", err)
		return 1
	}

	fmt.Printf("codecin build: %s -> %s (静态链接, 目标 %s)\n", path, out, tgt)
	built, err := aot.Build(aot.BuildOptions{
		ModuleDir: moduleDir,
		Bytecode:  bc,
		MemImage:  buildMemImage(prog),
		Out:       out,
		Target:    tgt,
		KeepTemp:  keepTemp,
		Stdout:    os.Stdout,
		Stderr:    os.Stderr,
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "codecin build: %v\n", err)
		return 1
	}
	fmt.Printf("codecin build: 完成 -> %s\n", built)
	return 0
}

// buildMemImage 生成初始内存镜像 (数据段写入后的 64 KiB 内存)。
func buildMemImage(prog *ir.Program) []byte {
	mem := make([]byte, memSize)
	for _, dw := range prog.DataWrites {
		if dw.Addr >= 0 && dw.Addr+len(dw.Data) <= len(mem) {
			copy(mem[dw.Addr:], dw.Data)
		}
	}
	return mem
}

// readStdin 仅在标准输入被重定向时读取, 交互终端下不阻塞。
func readStdin() []byte {
	fi, err := os.Stdin.Stat()
	if err != nil || fi.Mode()&os.ModeCharDevice != 0 {
		return nil
	}
	data, err := io.ReadAll(os.Stdin)
	if err != nil {
		return nil
	}
	return data
}

func exeDir() string {
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	return filepath.Dir(exe)
}

func cwd() string {
	d, err := os.Getwd()
	if err != nil {
		return ""
	}
	return d
}

func splitTarget(t string) []string {
	for i := 0; i < len(t); i++ {
		if t[i] == '/' {
			if i == 0 || i == len(t)-1 {
				return nil
			}
			return []string{t[:i], t[i+1:]}
		}
	}
	return nil
}

func usage(w *os.File) {
	fmt.Fprint(w, `Code CIN Go CLI

用法:
  codecin <program.cin> [--dump-bytecode]      编译并运行 (全 Go 链路)
  codecin build <program.cin> [-o OUT] [--target OS/ARCH] [--keep-temp]
                                               编译成独立静态可执行文件

build 选项:
  -o, --out PATH    输出路径 (默认 <程序名> + 平台后缀)
  --target OS/ARCH  交叉编译目标, 支持: `+
		joinTargets(aot.SupportedTargets)+`
  --keep-temp       保留临时构建目录 (排查构建失败)

产物不依赖 Python、Go 工具链与动态库 (CGO_ENABLED=0 静态链接)。
`)
}

func joinTargets(list []string) string {
	out := ""
	for i, t := range list {
		if i > 0 {
			out += ", "
		}
		out += t
	}
	return out
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
