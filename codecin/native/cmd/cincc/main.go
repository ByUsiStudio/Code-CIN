// cincc: CIN → 目标机器码 (不经 C / 不经 UCBC)。
//
// 用法: cincc -o out prog.cin [--target x86_64-linux]
package main

import (
	"flag"
	"fmt"
	"os"

	"codecin-native/compiler"
	"codecin-native/mc"
)

func main() {
	out := flag.String("o", "", "输出可执行文件")
	target := flag.String("target", "x86_64-linux", "目标平台")
	verbose := flag.Bool("v", false, "打印 IR")
	flag.Parse()
	if flag.NArg() != 1 || *out == "" {
		fmt.Fprintln(os.Stderr, "用法: cincc -o <out> <prog.cin>")
		os.Exit(2)
	}
	if *target != "x86_64-linux" {
		fmt.Fprintf(os.Stderr, "cincc: 暂不支持目标 %s\n", *target)
		os.Exit(2)
	}
	src, err := os.ReadFile(flag.Arg(0))
	if err != nil {
		fmt.Fprintf(os.Stderr, "cincc: %v\n", err)
		os.Exit(1)
	}
	prog, err := compiler.Compile(string(src), flag.Arg(0), false)
	if err != nil {
		fmt.Fprintf(os.Stderr, "cincc: 编译失败: %v\n", err)
		os.Exit(1)
	}
	if *verbose {
		for i, ins := range prog.Instructions {
			fmt.Printf("%3d %-6s %v\n", i, ins.Op, ins.Args)
		}
	}
	code, err := mc.Encode(prog)
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
