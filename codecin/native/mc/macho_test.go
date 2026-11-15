package mc

import (
	"encoding/binary"
	"os"
	"testing"

	"codecin-native/compiler"
	"codecin-native/ir"
)

// machoEntryOff 从 Mach-O 映像里取出 LC_MAIN 的 entryoff (相对文件/__TEXT 起点)。
func machoEntryOff(t *testing.T, img []byte) uint64 {
	t.Helper()
	le := binary.LittleEndian
	if got := le.Uint32(img); got != 0xFEEDFACF {
		t.Fatalf("MH_MAGIC_64 = %#x", got)
	}
	ncmds := int(le.Uint32(img[16:]))
	pos := uint64(32)
	for i := 0; i < ncmds; i++ {
		cmd := le.Uint32(img[pos:])
		size := uint64(le.Uint32(img[pos+4:]))
		if size < 8 || pos+size > uint64(len(img)) {
			t.Fatalf("装载命令 %d 长度异常: cmd=%#x size=%d", i, cmd, size)
		}
		if cmd == 0x80000028 { // LC_MAIN
			return le.Uint64(img[pos+8:])
		}
		pos += size
	}
	t.Fatal("没有找到 LC_MAIN")
	return 0
}

// runAArch64Darwin 把 IR 编成 Mach-O, 映像放到 __TEXT 的虚拟地址上, 用解释器
// 的 Darwin 模式执行 (syscall 号走 x16, 出错靠进位标志)。
func runAArch64Darwin(t *testing.T, prog *ir.Program) *a64CPU {
	t.Helper()
	img, err := WrapMachO(prog)
	if err != nil {
		t.Fatalf("WrapMachO: %v", err)
	}
	if err := CheckMachO(img); err != nil {
		t.Fatalf("CheckMachO: %v", err)
	}
	cpu := &a64CPU{
		mem:        newEmuMem(),
		files:      map[int]*emuFile{},
		base:       0x1000000000, // mmap 返回的 VM 基址 (1 GiB, 不与映像重叠)
		sp:         0x200000000000,
		darwinMode: true,
	}
	cpu.mem.write(machTextVAddr, img)
	cpu.runFrom(machTextVAddr+machoEntryOff(t, img), 50_000_000)
	return cpu
}

// TestMachOStructure 校验 Mach-O 容器的固定字段, 这些字段错了 macOS 会直接
// 拒绝执行 ("bad CPU type" / "code signature invalid"), 而在本机测不出来。
func TestMachOStructure(t *testing.T) {
	prog, err := compiler.Compile(`
function main() -> int {
    println("hi");
    return 3;
}
`, "t.cin", false)
	if err != nil {
		t.Fatalf("编译: %v", err)
	}
	img, err := WrapMachO(prog)
	if err != nil {
		t.Fatalf("WrapMachO: %v", err)
	}
	le := binary.LittleEndian
	check := func(name string, got, want uint32) {
		t.Helper()
		if got != want {
			t.Fatalf("%s = %#x, 期望 %#x", name, got, want)
		}
	}
	check("magic", le.Uint32(img[0:]), 0xFEEDFACF)
	check("cputype", le.Uint32(img[4:]), 0x0100000C) // CPU_TYPE_ARM64
	check("cpusubtype", le.Uint32(img[8:]), 0)
	check("filetype", le.Uint32(img[12:]), 2) // MH_EXECUTE
	if n := le.Uint32(img[16:]); n != 8 {
		t.Fatalf("ncmds = %d, 期望 8", n)
	}
	if flags := le.Uint32(img[24:]); flags&0x00200000 == 0 || flags&0x1 == 0 {
		t.Fatalf("flags = %#x, 期望含 MH_PIE|MH_NOUNDEFS", flags)
	}

	// 装载命令必须首尾相接、总长与 header 一致
	ncmds := int(le.Uint32(img[16:]))
	cmdsSize := uint64(le.Uint32(img[20:]))
	pos := uint64(32)
	want := map[uint32]int{
		0x19: 3, // LC_SEGMENT_64: __PAGEZERO / __TEXT / __DATA / __LINKEDIT 里带节的算 3 条? 见下
	}
	_ = want
	seen := map[uint32]int{}
	for i := 0; i < ncmds; i++ {
		cmd := le.Uint32(img[pos:])
		size := uint64(le.Uint32(img[pos+4:]))
		seen[cmd]++
		pos += size
	}
	if pos != 32+cmdsSize {
		t.Fatalf("装载命令总长 %d, header 声明 %d", pos-32, cmdsSize)
	}
	for _, c := range []uint32{0x19, 0x80000028, 0x80000007, 0x32, 0x1D} {
		if seen[c] == 0 {
			t.Fatalf("缺少装载命令 %#x", c)
		}
	}
	if seen[0x19] != 4 { // __PAGEZERO/__TEXT/__DATA/__LINKEDIT
		t.Fatalf("LC_SEGMENT_64 数量 = %d, 期望 4", seen[0x19])
	}

	// LC_CODE_SIGNATURE 指向的区域必须是 SuperBlob + CodeDirectory
	sigOff, sigSize := uint32(0), uint32(0)
	pos = 32
	for i := 0; i < ncmds; i++ {
		cmd := le.Uint32(img[pos:])
		size := uint64(le.Uint32(img[pos+4:]))
		if cmd == 0x1D {
			sigOff = le.Uint32(img[pos+8:])
			sigSize = le.Uint32(img[pos+12:])
		}
		pos += size
	}
	if sigOff == 0 || uint64(sigOff)+uint64(sigSize) != uint64(len(img)) {
		t.Fatalf("签名段 sigOff=%d sigSize=%d len=%d", sigOff, sigSize, len(img))
	}
	if m := le.Uint32(img[sigOff:]); m != 0xFADE0CC0 {
		t.Fatalf("SuperBlob magic = %#x", m)
	}
	blobCount := le.Uint32(img[sigOff+8:])
	if blobCount == 0 {
		t.Fatal("SuperBlob 里没有子 blob")
	}
	cdOff := sigOff + le.Uint32(img[sigOff+16:]) // index[0].offset (magic/len/count 之后)
	if m := le.Uint32(img[cdOff:]); m != 0xFADE0C02 {
		t.Fatalf("CodeDirectory magic = %#x", m)
	}
	// 每 4 KiB 一页, 页数必须覆盖整个被签名区域
	codeLimit := le.Uint64(img[cdOff+32:])
	nCode := le.Uint32(img[cdOff+28:])
	if want := (uint32(codeLimit) + 4095) / 4096; nCode != want {
		t.Fatalf("CodeDirectory 页数 = %d, 期望 %d (codeLimit=%d)", nCode, want, codeLimit)
	}
}

// TestAArch64DarwinMatchesEngine 用 Darwin 模式的解释器跑与 Linux 相同的用例,
// 覆盖 x16 传号、进位判错、Darwin 的 O_* 标志与 stat64/getdirentries64 布局。
func TestAArch64DarwinMatchesEngine(t *testing.T) {
	cases := []struct{ name, src string }{
		{"arith", `
function main() -> int {
    int a = 17;
    int b = 5;
    println(a + b);
    println(a * b);
    println(a / b);
    println(a >> 1);
    println(-a);
    return a % b;
}
`},
		{"strings", `
function main() -> int {
    string s = "hello";
    println(s + " world");
    println(strlen(s));
    println(substr("abcdef", 2, 3));
    println(upper(s));
    println(trim("  pad  "));
    return 0;
}
`},
		{"floats", `
function main() -> int {
    println(1.25);
    println(3.4);
    println(sqrt(6.25));
    println(floor(2.7));
    return 0;
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
    println(file_copy(p, "t2.txt"));
    file_move("t2.txt", "t3.txt");
    println(file_exists("t2.txt"));
    println(file_exists("t3.txt"));
    file_delete("t3.txt");
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
			cpu := runAArch64Darwin(t, prog)
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
