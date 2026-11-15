// cincc: CIN → 目标机器码 (不经 C / 不经 UCBC)。
//
// 用法: cincc -o out prog.cin [--target x86_64-linux]
package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"codecin-native/compiler"
	"codecin-native/mc"
)

// defaultLibDir 猜测内置标准库目录 (codecin/lib)。
//
// 顺序: 环境变量 CODECIN_LIB_DIR -> 可执行文件同级的 lib / 上一级的 lib。
// 从源码树直接编译时 cincc 位于 <pkg>/native/, 库在 <pkg>/lib。
// 猜不到时返回空串, 此时裸名字 import 只能报 Import file not found。
func defaultLibDir() string {
	if v := os.Getenv("CODECIN_LIB_DIR"); v != "" {
		return v
	}
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	dir := filepath.Dir(exe)
	for _, cand := range []string{
		filepath.Join(dir, "lib"),
		filepath.Join(dir, "..", "lib"),
	} {
		if fi, e := os.Stat(cand); e == nil && fi.IsDir() {
			return filepath.Clean(cand)
		}
	}
	return ""
}

func main() {
	out := flag.String("o", "", "输出可执行文件")
	target := flag.String("target", "x86_64-linux", "目标平台")
	libDir := flag.String("lib", defaultLibDir(), "内置标准库目录 (codecin/lib)")
	verbose := flag.Bool("v", false, "打印 IR")
	flag.Parse()
	if flag.NArg() != 1 || *out == "" {
		fmt.Fprintln(os.Stderr, "用法: cincc -o <out> <prog.cin>")
		os.Exit(2)
	}
	switch *target {
	case "x86_64-linux", "x86_64-windows", "aarch64-linux", "aarch64-darwin":
	default:
		fmt.Fprintf(os.Stderr, "cincc: 暂不支持目标 %s\n", *target)
		os.Exit(2)
	}
	src, err := os.ReadFile(flag.Arg(0))
	if err != nil {
		fmt.Fprintf(os.Stderr, "cincc: %v\n", err)
		os.Exit(1)
	}
	prog, err := compiler.CompileFile(flag.Arg(0), *libDir)
	if err != nil {
		// CompileFile 会展开 import; 源码含 import 时回退到 Compile 只会报
		// 与真实原因无关的解析错误, 直接把原始错误报告给用户。
		if strings.Contains(string(src), "import ") {
			fmt.Fprintf(os.Stderr, "cincc: 编译失败: %v\n", err)
			os.Exit(1)
		}
		prog, err = compiler.Compile(string(src), flag.Arg(0), false)
		if err != nil {
			fmt.Fprintf(os.Stderr, "cincc: 编译失败: %v\n", err)
			os.Exit(1)
		}
	}
	if *verbose {
		for i, ins := range prog.Instructions {
			fmt.Printf("%3d %-6s %v\n", i, ins.Op, ins.Args)
		}
	}
	var code []byte
	if *target == "x86_64-windows" {
		img, werr := mc.WrapPE(prog)
		if werr != nil {
			fmt.Fprintf(os.Stderr, "cincc: %v\n", werr)
			os.Exit(1)
		}
		if err := os.WriteFile(*out, img, 0o755); err != nil {
			fmt.Fprintf(os.Stderr, "cincc: 写出失败: %v\n", err)
			os.Exit(1)
		}
		fmt.Printf("cincc: %s -> %s (%d 字节, PE32+)\n", flag.Arg(0), *out, len(img))
		return
	}
	if *target == "aarch64-linux" {
		code, err = mc.EncodeAArch64(prog)
		if err != nil {
			fmt.Fprintf(os.Stderr, "cincc: %v\n", err)
			os.Exit(1)
		}
		img, werr := mc.WrapAArch64ELF(prog, code)
		if werr != nil {
			fmt.Fprintf(os.Stderr, "cincc: %v\n", werr)
			os.Exit(1)
		}
		if err := os.WriteFile(*out, img, 0o755); err != nil {
			fmt.Fprintf(os.Stderr, "cincc: 写出失败: %v\n", err)
			os.Exit(1)
		}
		fmt.Printf("cincc: %s -> %s (%d 字节, AArch64 ELF)\n", flag.Arg(0), *out, len(img))
		return
	}
	if *target == "aarch64-darwin" {
		img, werr := mc.WrapMachO(prog)
		if werr != nil {
			fmt.Fprintf(os.Stderr, "cincc: %v\n", werr)
			os.Exit(1)
		}
		if err := os.WriteFile(*out, img, 0o755); err != nil {
			fmt.Fprintf(os.Stderr, "cincc: 写出失败: %v\n", err)
			os.Exit(1)
		}
		fmt.Printf("cincc: %s -> %s (%d 字节, Mach-O arm64)\n", flag.Arg(0), *out, len(img))
		return
	}
	code, err = mc.Encode(prog)
	if err != nil {
		fmt.Fprintf(os.Stderr, "cincc: %v\n", err)
		os.Exit(1)
	}
	img := mc.WrapELF(prog, code)
	if err := os.WriteFile(*out, img, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "cincc: 写出失败: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("cincc: %s -> %s (%d 字节, %d 条 IR)\n",
		flag.Arg(0), *out, len(img), len(prog.Instructions))
}
