package mc

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"

	"codecin-native/ir"
)

// TestPEPrintString 验证 Windows 后端能真的把字符串写到控制台并正常退出。
func TestPEPrintString(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("仅 Windows")
	}
	p := &ir.Program{
		Instructions: []ir.Instr{
			{Op: "OUT", Args: []ir.Operand{ir.Str(0x1000)}},
			{Op: "HALT"},
		},
		DataWrites: []ir.DataWrite{{Addr: 0x1000, Data: []byte("hi\n\x00")}},
	}
	img, err := WrapPE(p)
	if err != nil {
		t.Fatalf("WrapPE: %v", err)
	}
	dir := t.TempDir()
	exe := filepath.Join(dir, "p.exe")
	if err := os.WriteFile(exe, img, 0o755); err != nil {
		t.Fatal(err)
	}
	out, err := exec.Command(exe).CombinedOutput()
	t.Logf("out=%q err=%v", out, err)
	if string(out) != "hi\n" {
		t.Fatalf("输出 %q, 期望 %q", out, "hi\n")
	}
	if err != nil {
		t.Fatalf("运行返回错误: %v", err)
	}
}
