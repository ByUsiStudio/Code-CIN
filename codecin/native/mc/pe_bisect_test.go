package mc

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"

	"codecin-native/ir"
)

func runPEProg(t *testing.T, name string, p *ir.Program) int {
	t.Helper()
	img, err := WrapPE(p)
	if err != nil {
		t.Fatalf("%s: WrapPE: %v", name, err)
	}
	dir := t.TempDir()
	exe := filepath.Join(dir, "p.exe")
	if err := os.WriteFile(exe, img, 0o755); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(exe)
	err = cmd.Run()
	if cmd.ProcessState != nil {
		return cmd.ProcessState.ExitCode()
	}
	t.Fatalf("%s: 未启动: %v", name, err)
	return 0
}

// TestPEBisect 逐层定位 Windows 后端的问题所在。
func TestPEBisect(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("仅 Windows")
	}
	cases := []struct {
		name string
		p    *ir.Program
		want int
	}{
		{"mov", &ir.Program{Instructions: []ir.Instr{
			{Op: "MOV", Args: []ir.Operand{ir.Reg(0), ir.Imm(7)}},
			{Op: "HALT"},
		}}, 7},
		{"pushpop", &ir.Program{Instructions: []ir.Instr{
			{Op: "MOV", Args: []ir.Operand{ir.Reg(0), ir.Imm(3)}},
			{Op: "PUSH", Args: []ir.Operand{ir.Reg(0)}},
			{Op: "POP", Args: []ir.Operand{ir.Reg(1)}},
			{Op: "MOV", Args: []ir.Operand{ir.Reg(0), ir.Reg(1)}},
			{Op: "HALT"},
		}}, 3},
		{"callret", &ir.Program{
			Instructions: []ir.Instr{
				{Op: "CALL", Args: []ir.Operand{ir.Label("f")}},
				{Op: "HALT"},
				{Op: "RET"},
			},
			Labels: map[string]int{"f": 2},
		}, 0},
		{"call_mov_ret", &ir.Program{
			Instructions: []ir.Instr{
				{Op: "CALL", Args: []ir.Operand{ir.Label("f")}},
				{Op: "HALT"},
				{Op: "MOV", Args: []ir.Operand{ir.Reg(0), ir.Imm(11)}},
				{Op: "RET"},
			},
			Labels: map[string]int{"f": 2},
		}, 11},
		{"push_in_fn", &ir.Program{
			Instructions: []ir.Instr{
				{Op: "CALL", Args: []ir.Operand{ir.Label("f")}},
				{Op: "HALT"},
				{Op: "PUSH", Args: []ir.Operand{ir.Reg(0)}},
				{Op: "POP", Args: []ir.Operand{ir.Reg(0)}},
				{Op: "MOV", Args: []ir.Operand{ir.Reg(0), ir.Imm(5)}},
				{Op: "RET"},
			},
			Labels: map[string]int{"f": 2},
		}, 5},
		{"call_out", &ir.Program{
			Instructions: []ir.Instr{
				{Op: "CALL", Args: []ir.Operand{ir.Label("f")}},
				{Op: "HALT"},
				{Op: "OUT", Args: []ir.Operand{ir.Str(0x1000)}},
				{Op: "MOV", Args: []ir.Operand{ir.Reg(0), ir.Imm(9)}},
				{Op: "RET"},
			},
			Labels:     map[string]int{"f": 2},
			DataWrites: []ir.DataWrite{{Addr: 0x1000, Data: []byte("ok\n\x00")}},
		}, 9},
		{"call_out_halt", &ir.Program{
			Instructions: []ir.Instr{
				{Op: "CALL", Args: []ir.Operand{ir.Label("f")}},
				{Op: "HALT"},
				{Op: "OUT", Args: []ir.Operand{ir.Str(0x1000)}},
				{Op: "MOV", Args: []ir.Operand{ir.Reg(0), ir.Imm(8)}},
				{Op: "HALT"},
			},
			Labels:     map[string]int{"f": 2},
			DataWrites: []ir.DataWrite{{Addr: 0x1000, Data: []byte("ok\n\x00")}},
		}, 8},
	}
	for _, c := range cases {
		c := c
		t.Run(c.name, func(t *testing.T) {
			if got := runPEProg(t, c.name, c.p); got != c.want {
				t.Errorf("%s: 退出码 %d, 期望 %d", c.name, got, c.want)
			}
		})
	}
}
