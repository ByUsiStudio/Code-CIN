package mc

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"

	"codecin-native/ir"
)

func runRC(t *testing.T, code []byte, p *ir.Program) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "prog")
	if err := os.WriteFile(path, WrapELF(p, code), 0o755); err != nil {
		t.Fatal(err)
	}
	wslPath := "/mnt/" + string(path[0]|0x20) + filepath.ToSlash(path[2:])
	script := `"` + wslPath + `"; echo rc=$?`
	out, err := exec.Command("wsl", "-e", "sh", "-c", script).CombinedOutput()
	if err != nil {
		t.Fatalf("运行失败: %v\n%s", err, out)
	}
	return string(out)
}

// TestPrintString 验证 OUT str 生成的 write 系统调用真的输出了字符串。
func TestPrintString(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("仅在 Windows + WSL 下运行")
	}
	if err := exec.Command("wsl", "-e", "echo", "ok").Run(); err != nil {
		t.Skip("WSL 不可用")
	}
	p := &ir.Program{
		Instructions: []ir.Instr{
			{Op: "OUT", Args: []ir.Operand{ir.Str(0x1000)}},
			{Op: "HALT"},
		},
		DataWrites: []ir.DataWrite{{Addr: 0x1000, Data: []byte("hi\n\x00")}},
	}
	code, err := Encode(p)
	if err != nil {
		t.Fatalf("Encode: %v", err)
	}
	out := runRC(t, code, p)
	if !contains(out, "hi") {
		t.Fatalf("输出 %q 不含 hi", out)
	}
}
