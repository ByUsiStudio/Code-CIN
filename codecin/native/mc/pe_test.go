package mc

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"

	"codecin-native/ir"
)

func TestPEExitCode(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("仅 Windows")
	}
	p := &ir.Program{Instructions: []ir.Instr{
		{Op: "MOV", Args: []ir.Operand{ir.Reg(0), ir.Imm(42)}},
		{Op: "HALT"},
	}}
	img, err := WrapPE(p)
	if err != nil {
		t.Fatalf("WrapPE: %v", err)
	}
	dir := t.TempDir()
	exe := filepath.Join(dir, "t.exe")
	if err := os.WriteFile(exe, img, 0o755); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(exe)
	err = cmd.Run()
	if ee, ok := err.(*exec.ExitError); ok {
		if ee.ExitCode() != 42 {
			t.Fatalf("退出码 %d, 期望 42", ee.ExitCode())
		}
		return
	}
	if err != nil {
		t.Fatalf("运行失败: %v", err)
	}
	t.Fatalf("退出码 0, 期望 42")
}
