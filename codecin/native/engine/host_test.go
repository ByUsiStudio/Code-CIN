package engine

import (
	"path/filepath"
	"strings"
	"testing"
)

// 宿主能力 (SYS 80..115) 的引擎侧单元测试: 只覆盖确定性、无副作用或副作用
// 可完全控制的调用 (哈希/编码/路径/内存信息)。涉及真实系统交互 (剪贴板、
// 通知、网络、Termux) 的调用在 Python 侧测试中以门禁方式覆盖。

// newHostVM 构造一个可分配堆字符串的最小 vmState。
func newHostVM(size int) *vmState {
	mem := make([]byte, size)
	return &vmState{
		mem:     mem,
		sp:      uint64(size - 8),
		heapPtr: uint64(size / 2),
	}
}

// readHostString 读取 x0 指向的 NUL 结尾字符串。
func readHostString(vm *vmState, ptr uint64) string {
	return vm.readCString(ptr)
}

func TestHostSHA256AndBase64(t *testing.T) {
	vm := newHostVM(4096)

	got := readHostString(vm, vm.sha256Hex("abc"))
	want := "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
	if got != want {
		t.Fatalf("sha256(abc) = %q, 期望 %q", got, want)
	}

	enc := readHostString(vm, vm.base64Encode("hello"))
	if enc != "aGVsbG8=" {
		t.Fatalf("base64_encode(hello) = %q", enc)
	}
	dec := readHostString(vm, vm.base64Decode("aGVsbG8="))
	if dec != "hello" {
		t.Fatalf("base64_decode 往返失败: %q", dec)
	}

	// 非法 Base64 -> 空串 (不报错、不 panic)
	if bad := readHostString(vm, vm.base64Decode("!!!")); bad != "" {
		t.Fatalf("非法 Base64 应返回空串, 实际 %q", bad)
	}

	// UTF-8 字节参与哈希 (中文 3 字节)
	if n := len(readHostString(vm, vm.sha256Hex("中"))); n != 64 {
		t.Fatalf("sha256 十六进制长度应为 64, 实际 %d", n)
	}
}

func TestHostPathHelpers(t *testing.T) {
	vm := newHostVM(4096)

	joined := readHostString(vm, vm.pathJoin("a", "b.txt"))
	if joined != filepath.Join("a", "b.txt") {
		t.Fatalf("path_join = %q", joined)
	}
	if base := readHostString(vm, vm.pathBasename(joined)); base != "b.txt" {
		t.Fatalf("path_basename = %q", base)
	}
	if dir := readHostString(vm, vm.pathDirname(joined)); dir != "a" {
		t.Fatalf("path_dirname = %q", dir)
	}
	if abs := readHostString(vm, vm.pathAbs(".")); abs == "" {
		t.Fatal("path_abs 返回空串")
	}
	if tmp := readHostString(vm, vm.tempDir()); tmp == "" {
		t.Fatal("temp_dir 返回空串")
	}
}

func TestHostSystemInfo(t *testing.T) {
	vm := newHostVM(4096)

	if n := vm.cpuCount(); n == 0 {
		t.Fatal("cpu_count 返回 0")
	}
	if arch := readHostString(vm, vm.archName()); arch == "" {
		t.Fatal("arch_name 返回空串")
	}
	info := readHostString(vm, vm.memInfo())
	if !strings.Contains(info, `"total_kb":`) ||
		!strings.Contains(info, `"free_kb":`) {
		t.Fatalf("mem_info 结构不符: %q", info)
	}

	// 时间: 毫秒时间戳应为正数
	if ms := vm.timeMs(); ms < 1_600_000_000_000 {
		t.Fatalf("time_ms 异常: %d", ms)
	}
	// is_android 只允许 0/1
	if a := vm.isAndroid(); a != 0 && a != 1 {
		t.Fatalf("is_android 应返回 0/1, 实际 %d", a)
	}
	// sleep_ms(0) 不得报错
	if code := vm.sleepMs(0); code != 0 {
		t.Fatalf("sleep_ms(0) = %d", code)
	}
}

func TestHostFileHelpers(t *testing.T) {
	dir := t.TempDir()
	vm := newHostVM(8192)

	src := filepath.Join(dir, "a.txt")
	dst := filepath.Join(dir, "b.txt")
	moved := filepath.Join(dir, "c.txt")
	if code := vm.fileWrite(src, "hello"); code != 0 {
		t.Fatalf("file_write = %d", code)
	}
	if code := vm.fileCopy(src, dst); code != 0 {
		t.Fatalf("file_copy = %d", code)
	}
	if code := vm.fileMove(dst, moved); code != 0 {
		t.Fatalf("file_move = %d", code)
	}
	if code := vm.fileSize(moved); code != 5 {
		t.Fatalf("file_size = %d, 期望 5", code)
	}
	if code := vm.fileMtime(moved); code <= 0 {
		t.Fatalf("file_mtime = %d, 期望正数", code)
	}
	if code := vm.isDir(dir); code != 1 {
		t.Fatalf("is_dir(目录) = %d", code)
	}
	if code := vm.isDir(moved); code != 0 {
		t.Fatalf("is_dir(文件) = %d", code)
	}
	// 失败路径: 不存在的文件
	if code := vm.fileMtime(filepath.Join(dir, "nope")); code != mask64 {
		t.Fatalf("file_mtime(不存在) = %d, 期望 -1", code)
	}
}
