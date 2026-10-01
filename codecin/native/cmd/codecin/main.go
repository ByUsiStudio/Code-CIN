// Code CIN 的**纯 Go 命令行**(不依赖 cgo / Python)。
//
// 与 codecin/native/main.go 的区别:
//   - main.go 是给 Python ctypes 用的 c-shared 库 (buildmode=c-shared, 空 main);
//   - 本包是真正的可执行 CLI: 自己完成 词法/语法/编译 -> UCBC 编码 -> Go VM 执行。
//
// 用法:
//
//	codecin <program.cin> [--mem-size N] [--max-instructions N] [--lib-dir DIR]
//	codecin compile <program.cin> [-o out.ucbc] [--lib-dir DIR]
//	codecin version
//
// 退出码与 Python CLI 的约定一致: 0 正常结束, 1 编译/运行错误, 2 参数错误。
package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"codecin-native/compiler"
	"codecin-native/engine"
	"codecin-native/ir"
)

const (
	// 与 Python 侧 codecin/aot.py 的 DEFAULT_MEM_SIZE 一致
	defaultMemSize = 65536
	// 与 native/aot/aot.go 的 maxSteps 一致
	defaultMaxSteps = 100000000
	// 栈槽宽度 (栈从内存末尾向下增长)
	stackSlot = 8
)

const usageText = `Code CIN %s (纯 Go 实现)

用法:
  codecin <program.cin> [选项]         编译并运行
  codecin compile <program.cin> [选项]  只编译, 输出 UCBC 字节码
  codecin version                      打印版本
  codecin help                         显示本帮助

选项:
  --mem-size N           内存大小 (字节, 默认 %d, 最小 256)
  --max-instructions N   指令上限 (默认 %d)
  --lib-dir DIR          内置标准库目录 (默认自动查找 codecin/lib)
  -o, --output FILE      compile 子命令的输出路径 (默认 <program>.ucbc)
  -q, --quiet            运行时不回显程序输出
`

func main() { os.Exit(run(os.Args[1:])) }

func run(args []string) int {
	if len(args) == 0 {
		fmt.Printf(usageText, engine.BuildVersion, defaultMemSize, defaultMaxSteps)
		return 2
	}
	switch args[0] {
	case "version", "--version", "-V":
		fmt.Printf("Code CIN %s (Go)\n", engine.BuildVersion)
		return 0
	case "help", "--help", "-h":
		fmt.Printf(usageText, engine.BuildVersion, defaultMemSize, defaultMaxSteps)
		return 0
	case "compile":
		return cmdCompile(args[1:])
	case "run":
		return cmdRun(args[1:])
	}
	return cmdRun(args)
}

// options 是 CLI 的公共选项。
type options struct {
	program  string
	memSize  int
	maxSteps int64
	libDir   string
	output   string
	quiet    bool
	err      string
}

// parseOptions 解析位置参数与选项 (选项可出现在程序路径前后)。
// 返回值 code != 0 时, opts.err 一定是非空的用户可读原因。
func parseOptions(args []string, allowOutput bool) (*options, int) {
	o := &options{memSize: defaultMemSize, maxSteps: defaultMaxSteps}
	fail := func(format string, a ...any) (*options, int) {
		o.err = fmt.Sprintf(format, a...)
		return o, 2
	}
	for i := 0; i < len(args); i++ {
		a := args[i]
		value := func() (string, bool) {
			if i+1 >= len(args) {
				return "", false
			}
			i++
			return args[i], true
		}
		switch {
		case a == "--mem-size":
			v, ok := value()
			if !ok {
				return fail("选项 %s 需要一个值", a)
			}
			n, err := strconv.Atoi(v)
			if err != nil || n < 256 {
				return fail("--mem-size 需要 >= 256 的整数, 得到 %q", v)
			}
			o.memSize = n
		case a == "--max-instructions":
			v, ok := value()
			if !ok {
				return fail("选项 %s 需要一个值", a)
			}
			n, err := strconv.ParseInt(v, 10, 64)
			if err != nil || n <= 0 {
				return fail("--max-instructions 需要正整数, 得到 %q", v)
			}
			o.maxSteps = n
		case a == "--lib-dir":
			v, ok := value()
			if !ok {
				return fail("选项 %s 需要一个值", a)
			}
			o.libDir = v
		case a == "-q" || a == "--quiet":
			o.quiet = true
		case allowOutput && (a == "-o" || a == "--output"):
			v, ok := value()
			if !ok {
				return fail("选项 %s 需要一个值", a)
			}
			o.output = v
		case strings.HasPrefix(a, "-") && a != "-":
			return fail("未知选项: %s", a)
		default:
			if o.program != "" {
				return fail("只能指定一个程序文件 (多出: %s)", a)
			}
			o.program = a
		}
	}
	if o.program == "" {
		return fail("缺少程序文件")
	}
	return o, 0
}

// findLibDir 依次尝试: 显式指定 -> 可执行文件同级/上级的 codecin/lib -> 当前目录。
func findLibDir(explicit string) string {
	if explicit != "" {
		return explicit
	}
	var candidates []string
	if exe, err := os.Executable(); err == nil {
		dir := filepath.Dir(exe)
		candidates = append(candidates,
			filepath.Join(dir, "codecin", "lib"),
			filepath.Join(dir, "..", "codecin", "lib"))
	}
	candidates = append(candidates, filepath.Join("codecin", "lib"))
	for _, c := range candidates {
		if st, err := os.Stat(c); err == nil && st.IsDir() {
			return c
		}
	}
	return filepath.Join("codecin", "lib")
}

// makeMemory 构造初始内存镜像并应用数据段写入。
// 越界必须报错: 静默截断会让"数据段装不下"变成难以排查的错误结果。
func makeMemory(memSize int, prog *ir.Program) ([]byte, error) {
	mem := make([]byte, memSize)
	for _, dw := range prog.DataWrites {
		end := dw.Addr + len(dw.Data)
		if dw.Addr < 0 || end > len(mem) {
			return nil, fmt.Errorf(
				"数据段超出内存大小 %d 字节 (addr=0x%x size=%d); 用 --mem-size 增大后重试",
				memSize, dw.Addr, len(dw.Data))
		}
		copy(mem[dw.Addr:end], dw.Data)
	}
	return mem, nil
}

// readStdinIfPiped 只在标准输入**被重定向**时读取, 交互终端下不挂起等待 EOF。
func readStdinIfPiped() []byte {
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

func cmdRun(args []string) int {
	o, code := parseOptions(args, false)
	if code != 0 {
		fmt.Fprintln(os.Stderr, "参数错误: "+o.err)
		return code
	}
	prog, err := compiler.CompileFile(o.program, findLibDir(o.libDir))
	if err != nil {
		fmt.Fprintf(os.Stderr, "compile error: %v\n", err)
		return 1
	}
	bc, err := engine.EncodeProgram(*prog, 0)
	if err != nil {
		fmt.Fprintf(os.Stderr, "encode error: %v\n", err)
		return 1
	}
	mem, err := makeMemory(o.memSize, prog)
	if err != nil {
		fmt.Fprintln(os.Stderr, err.Error())
		return 1
	}
	sp := int64(len(mem) - stackSlot)
	res := engine.Run(bc, mem, 0, sp, int64(len(mem)/2), readStdinIfPiped(), o.maxSteps)
	if res == nil {
		fmt.Fprintln(os.Stderr, "runtime error: no result")
		return 1
	}
	if !o.quiet {
		fmt.Print(res.Output)
	}
	if res.Status == engine.StatusError || res.Status == engine.StatusUnsupported {
		msg := res.ErrMsg
		if msg == "" {
			msg = "unsupported instruction"
		}
		fmt.Fprintf(os.Stderr, "runtime error: %s\n", msg)
		return 1
	}
	return 0
}

func cmdCompile(args []string) int {
	o, code := parseOptions(args, true)
	if code != 0 {
		fmt.Fprintln(os.Stderr, "参数错误: "+o.err)
		return code
	}
	prog, err := compiler.CompileFile(o.program, findLibDir(o.libDir))
	if err != nil {
		fmt.Fprintf(os.Stderr, "compile error: %v\n", err)
		return 1
	}
	bc, err := engine.EncodeProgram(*prog, 0)
	if err != nil {
		fmt.Fprintf(os.Stderr, "encode error: %v\n", err)
		return 1
	}
	out := o.output
	if out == "" {
		out = strings.TrimSuffix(o.program, filepath.Ext(o.program)) + ".ucbc"
	}
	if err := os.WriteFile(out, bc, 0o644); err != nil {
		fmt.Fprintf(os.Stderr, "写入失败: %v\n", err)
		return 1
	}
	fmt.Printf("已写出 %s (%d 字节, %d 条指令)\n", out, len(bc), len(prog.Instructions))
	return 0
}
