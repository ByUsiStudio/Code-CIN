package mc

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"

	"codecin-native/ir"
)

func TestPECallRet(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("仅 Windows")
	}
	p := &ir.Program{
		Instructions: []ir.Instr{
			{Op: "CALL", Args: []ir.Operand{ir.Label("f")}},
			{Op: "HALT"},
			{Op: "MOV", Args: []ir.Operand{ir.Reg(0), ir.Imm(7)}},
			{Op: "RET"},
			{Op: "HALT"},
		},
		Labels: map[string]int{"f": 2},
	}
	img, err := WrapPE(p)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	exe := filepath.Join(dir, "cr.exe")
	os.WriteFile(exe, img, 0o755)
	err = exec.Command(exe).Run()
	if ee, ok := err.(*exec.ExitError); ok {
		if ee.ExitCode() != 7 {
			t.Fatalf("退出码 %d", ee.ExitCode())
		}
		t.Log("OK 7")
		return
	}
	t.Fatalf("err=%v", err)
}
