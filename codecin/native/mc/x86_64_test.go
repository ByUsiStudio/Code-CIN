package mc

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"

	"codecin-native/ir"
)

func TestWrapELFHeaders(t *testing.T) {
	code, err := Encode(&ir.Program{Instructions: []ir.Instr{
		{Op: "MOV", Args: []ir.Operand{ir.Reg(0), ir.Imm(7)}},
		{Op: "HALT"},
	}})
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	img := WrapELF(&ir.Program{}, code)
	if err := CheckELF(img); err != nil {
		t.Fatalf("CheckELF: %v", err)
	}
}

func TestUnsupportedOpcode(t *testing.T) {
	_, err := Encode(&ir.Program{Instructions: []ir.Instr{{Op: "DIV"}}})
	if err == nil {
		t.Fatal("期望不支持的指令报错")
	}
}

// TestRunUnderWSL 真实执行产物: exit(7)。
func TestRunUnderWSL(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("仅在 Windows + WSL 下运行")
	}
	if err := exec.Command("wsl", "-e", "echo", "ok").Run(); err != nil {
		t.Skip("WSL 不可用")
	}
	code, err := Encode(&ir.Program{Instructions: []ir.Instr{
		{Op: "MOV", Args: []ir.Operand{ir.Reg(0), ir.Imm(42)}},
		{Op: "HALT"},
	}})
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	runELF(t, code, "rc=42")
}

// TestLoopUnderWSL 验证 CMP/JNZ/INC 生成的循环: R0 从 0 增到 4 后退出。
func TestLoopUnderWSL(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("仅在 Windows + WSL 下运行")
	}
	if err := exec.Command("wsl", "-e", "echo", "ok").Run(); err != nil {
		t.Skip("WSL 不可用")
	}
	prog := &ir.Program{
		Instructions: []ir.Instr{
			{Op: "MOV", Args: []ir.Operand{ir.Reg(0), ir.Imm(0)}},
			{Op: "INC", Args: []ir.Operand{ir.Reg(0)}},
			{Op: "CMP", Args: []ir.Operand{ir.Reg(0), ir.Imm(4)}},
			{Op: "JNZ", Args: []ir.Operand{ir.Label("loop")}},
			{Op: "HALT"},
		},
		Labels: map[string]int{"loop": 1},
	}
	code, err := Encode(prog)
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	runELF(t, code, "rc=4")
}

func runELF(t *testing.T, code []byte, want string) {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "prog")
	if err := os.WriteFile(path, WrapELF(&ir.Program{}, code), 0o755); err != nil {
		t.Fatal(err)
	}
	wslPath := "/mnt/" + string(path[0]|0x20) + filepath.ToSlash(path[2:])
	script := `"` + wslPath + `"; echo rc=$?`
	out, err := exec.Command("wsl", "-e", "sh", "-c", script).CombinedOutput()
	if err != nil {
		t.Fatalf("运行失败: %v\n%s", err, out)
	}
	if !contains(string(out), want) {
		t.Fatalf("输出 %q 不含 %q", out, want)
	}
}

func contains(s, sub string) bool {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}
