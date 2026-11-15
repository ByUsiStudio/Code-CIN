package mc

import (
	"os"
	"strings"
	"testing"

	"codecin-native/compiler"
	"codecin-native/engine"
	"codecin-native/ir"
)

// runAArch64 把 IR 编成 AArch64 ELF, 再喂给测试用解释器执行。
func runAArch64(t *testing.T, prog *ir.Program) *a64CPU {
	t.Helper()
	code, err := EncodeAArch64(prog)
	if err != nil {
		t.Fatalf("EncodeAArch64: %v", err)
	}
	img, err := WrapAArch64ELF(prog, code)
	if err != nil {
		t.Fatalf("WrapAArch64ELF: %v", err)
	}
	if err := CheckAArch64ELF(img); err != nil {
		t.Fatalf("CheckAArch64ELF: %v", err)
	}
	cpu := &a64CPU{
		mem:   newEmuMem(),
		files: map[int]*emuFile{},
		base:  0x100000000,
		sp:    0x200000000,
	}
	cpu.mem.write(baseAddr, img)
	cpu.run(50_000_000)
	return cpu
}

// runEngine 用 Go 引擎执行同一份 IR, 作为语义基准。
func runEngine(t *testing.T, prog *ir.Program) (string, int64) {
	t.Helper()
	bc, err := engine.EncodeProgram(*prog, 0)
	if err != nil {
		t.Fatalf("EncodeProgram: %v", err)
	}
	mem := make([]byte, 1<<20)
	for _, dw := range prog.DataWrites {
		copy(mem[dw.Addr:], dw.Data)
	}
	res := engine.Run(bc, mem, 0, int64(len(mem)-8), int64(len(mem)/2), nil, 50_000_000)
	if res == nil {
		t.Fatal("engine.Run 返回 nil")
	}
	if res.ErrMsg != "" {
		t.Fatalf("引擎错误: %s", res.ErrMsg)
	}
	return res.Output, int64(res.Regs[0])
}

func mustEncode(t *testing.T, prog *ir.Program) []byte {
	t.Helper()
	code, err := EncodeAArch64(prog)
	if err != nil {
		t.Fatalf("EncodeAArch64: %v", err)
	}
	return code
}

func TestAArch64ELFHeader(t *testing.T) {
	prog := &ir.Program{Instructions: []ir.Instr{
		{Op: "MOV", Args: []ir.Operand{ir.Reg(0), ir.Imm(42)}},
		{Op: "HALT"},
	}}
	cpu := runAArch64(t, prog)
	if cpu.err != "" {
		t.Fatalf("执行失败: %s", cpu.err)
	}
	if cpu.code != 42 {
		t.Fatalf("退出码 = %d, 期望 42", cpu.code)
	}
	img, err := WrapAArch64ELF(prog, mustEncode(t, prog))
	if err != nil {
		t.Fatalf("WrapAArch64ELF: %v", err)
	}
	w := uint32(img[a64EntryOff]) | uint32(img[a64EntryOff+1])<<8 |
		uint32(img[a64EntryOff+2])<<16 | uint32(img[a64EntryOff+3])<<24
	if w&0xFFE0001F != 0xD2800000 {
		t.Fatalf("入口指令不是 MOVZ: %#x", w)
	}
}

// TestAArch64MatchesEngine 是后端的主回归: 同一份源码分别走引擎与 ARM64
// 机器码, 要求 stdout 与退出码逐字节一致。
func TestAArch64MatchesEngine(t *testing.T) {
	cases := []struct {
		name string
		src  string
	}{
		{"arith", `
function main() -> int {
    int a = 17;
    int b = 5;
    println(a + b);
    println(a - b);
    println(a * b);
    println(a / b);
    println(a % b);
    println(a & b);
    println(a | b);
    println(a ^ b);
    println(a << 2);
    println(a >> 1);
    println(-a);
    println(~a);
    println(a == b);
    println(a > b);
    println(a < b);
    return a % b;
}
`},
		{"loop_array", `
function main() -> int {
    int a[8];
    int i = 0;
    while (i < 8) { a[i] = i * i - 3; i = i + 1; }
    int sum = 0;
    i = 0;
    while (i < 8) { sum = sum + a[i]; i = i + 1; }
    println(sum);
    return sum;
}
`},
		{"recursion", `
function fib(int n) -> int {
    if (n < 2) { return n; }
    return fib(n - 1) + fib(n - 2);
}

function main() -> int {
    println(fib(0));
    println(fib(1));
    println(fib(10));
    println(fib(15));
    return fib(12);
}
`},
		{"strings", `
function main() -> int {
    string s = "hello";
    string t = "world";
    println(s);
    println(strlen(s));
    println(s + " " + t);
    println(s[1]);
    println(upper(s));
    println(lower("ABC"));
    println(substr("abcdef", 2, 3));
    println(indexof("hello world", "wor"));
    println(trim("  pad  "));
    println(strcmp("abc", "abc"));
    return 0;
}
`},
		{"floats", `
function main() -> int {
    println(0.5);
    println(1.25);
    println(3.0);
    println(-2.5);
    println(100.0);
    println(0.5 + 1.25);
    println(1.5 * 4.0);
    println(3.0 - 0.5);
    println(floor(2.7));
    println(ceil(2.1));
    println(round(2.5));
    println(sqrt(144.0));
    int k = sqrt(2.0) * 1000.0;
    println(k);
    return 0;
}
`},
		{"mathfn", `
function main() -> int {
    // 结果可直接表示的用例: 逐字节比对 FTOA 输出。
    println(pow(2.0, 10.0));
    println(pow(2.0, -3.0));
    println(pow(1.5, 4.0));
    println(pow(3.0, 0.0));
    println(sqrt(6.25));
    println(sin(0.0));
    println(cos(0.0));
    println(tan(0.0));
    println(floor(-2.3));
    println(ceil(-2.3));
    println(round(-2.5));
    println(round(2.5));
    // 非平凡值: 放大取整, 避开末位差异, 只校验前 6 位有效数字。
    int a = sin(1.0) * 1000000.0;
    int b = cos(1.0) * 1000000.0;
    int c = tan(1.0) * 1000000.0;
    int d = pow(4.0, 0.5) * 1000.0;
    int e = pow(2.0, 0.5) * 1000000.0;
    int f = sin(2.5) * 1000000.0;
    int g = cos(2.5) * 1000000.0;
    println(a);
    println(b);
    println(c);
    println(d);
    println(e);
    println(f);
    println(g);
    return a;
}
`},
		{"files", `
function main() -> int {
    string p = "t.txt";
    println(file_exists(p));
    file_write(p, "hello");
    println(file_exists(p));
    println(file_size(p));
    file_append(p, " world");
    println(file_read(p));
    println(file_size(p));
    println(file_copy(p, "t2.txt"));
    println(file_read("t2.txt"));
    file_move("t2.txt", "t3.txt");
    println(file_exists("t2.txt"));
    println(file_exists("t3.txt"));
    file_delete("t3.txt");
    println(file_exists("t3.txt"));
    file_delete(p);
    println(file_exists(p));
    return 0;
}
`},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			dir := t.TempDir()
			old, _ := os.Getwd()
			if err := os.Chdir(dir); err != nil {
				t.Fatalf("chdir: %v", err)
			}
			defer os.Chdir(old)

			prog, err := compiler.Compile(c.src, "t.cin", false)
			if err != nil {
				t.Fatalf("编译失败: %v", err)
			}
			want, wantRC := runEngine(t, prog)

			cpu := runAArch64(t, prog)
			if cpu.err != "" {
				t.Fatalf("解释器: %s\n已输出:\n%s", cpu.err, cpu.out.String())
			}
			if got := cpu.out.String(); got != want {
				t.Fatalf("stdout 不一致\n--- 引擎 ---\n%q\n--- 机器码 ---\n%q", want, got)
			}
			if cpu.code != wantRC {
				t.Fatalf("退出码 %d, 引擎 %d", cpu.code, wantRC)
			}
		})
	}
}

// TestAArch64DirList 单独验证目录列表: 条目顺序依赖文件系统, 所以只检查
// 集合与格式 (目录带尾部 '/', 条目间 '\n' 分隔)。
func TestAArch64DirList(t *testing.T) {
	dir := t.TempDir()
	old, _ := os.Getwd()
	if err := os.Chdir(dir); err != nil {
		t.Fatalf("chdir: %v", err)
	}
	defer os.Chdir(old)
	os.Mkdir("sub", 0o755)
	os.WriteFile("a.txt", []byte("a"), 0o644)
	os.WriteFile("b.txt", []byte("bb"), 0o644)

	prog, err := compiler.Compile(`
function main() -> int {
    println(dir_list("."));
    return 0;
}
`, "t.cin", false)
	if err != nil {
		t.Fatalf("编译失败: %v", err)
	}
	cpu := runAArch64(t, prog)
	if cpu.err != "" {
		t.Fatalf("解释器: %s", cpu.err)
	}
	lines := strings.Split(strings.TrimRight(cpu.out.String(), "\n"), "\n")
	set := map[string]bool{}
	for _, l := range lines {
		set[l] = true
	}
	for _, want := range []string{"a.txt", "b.txt", "sub/"} {
		if !set[want] {
			t.Fatalf("dir_list 缺少 %q, 实际: %v", want, lines)
		}
	}
}
