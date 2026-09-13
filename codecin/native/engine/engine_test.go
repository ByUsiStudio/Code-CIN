package engine

import (
	"bytes"
	"compress/zlib"
	"hash/crc32"
	"strings"
	"testing"

	"codecin-native/ir"
)

// 锁定 docs/SUGGESTIONS.md 批次 B 的引擎侧加固:
//   * decodeBytecode 不再信任不可信头部 (版本 / count / argc / opcode);
//   * 步数用尽报错误而不是伪装成正常停机;
//   * CROM 解压有上限 (zip bomb) 且 mem_size 必须自洽。

func tinyProgram() ir.Program {
	return ir.Program{
		Instructions: []ir.Instr{
			{Op: "MOV", Args: []ir.Operand{ir.Reg(0), ir.Imm(42)}},
			{Op: "HALT"},
		},
		Labels:     map[string]int{"main": 0},
		DataLabels: map[string]int{},
	}
}

func TestEncodeRunRoundTrip(t *testing.T) {
	prog := tinyProgram()
	bc, err := EncodeProgram(prog, 0)
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	mem := make([]byte, 4096)
	sp := int64(len(mem) - 8)
	res := Run(bc, mem, 0, sp, int64(len(mem)/2), nil, 1000)
	if res == nil {
		t.Fatal("Run 返回 nil")
	}
	if res.ErrMsg != "" {
		t.Fatalf("意外错误: %s", res.ErrMsg)
	}
	if res.Regs[0] != 42 {
		t.Fatalf("R0 = %d, 期望 42", res.Regs[0])
	}
}

func TestDecodeRejectsMalformedInput(t *testing.T) {
	valid, err := EncodeProgram(tinyProgram(), 0)
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	if _, _, ok := decodeBytecode(valid); !ok {
		t.Fatal("合法字节码被拒绝")
	}

	cases := []struct {
		name string
		bc   []byte
	}{
		{"empty", []byte{}},
		{"bad_magic", append([]byte("XXXX"), valid[4:]...)},
		{"short", valid[:6]},
	}
	for _, c := range cases {
		if _, _, ok := decodeBytecode(c.bc); ok {
			t.Errorf("%s: 畸形字节码被接受", c.name)
		}
	}

	// 版本字节
	badVer := append([]byte{}, valid...)
	badVer[4] = 99
	if _, _, ok := decodeBytecode(badVer); ok {
		t.Error("未知版本被接受")
	}

	// count 声明远超实际长度 (旧实现按 count 预分配 -> OOM)
	huge := []byte{'U', 'C', 'B', 'C', 1, 0, 0, 0, 0, 0xFF, 0xFF, 0xFF, 0xFF}
	if _, _, ok := decodeBytecode(huge); ok {
		t.Error("超大 count 被接受")
	}

	// argc 与操作码不匹配 (MOV 需要 2 个操作数, 这里声明 0 个)
	argcBad := []byte{'U', 'C', 'B', 'C', 1, 0, 0, 0, 0, 1, 0, 0, 0, opMOV, 0}
	if _, _, ok := decodeBytecode(argcBad); ok {
		t.Error("argc 不匹配的字节码被接受")
	}

	// 未定义 opcode
	opBad := []byte{'U', 'C', 'B', 'C', 1, 0, 0, 0, 0, 1, 0, 0, 0, 250, 0}
	if _, _, ok := decodeBytecode(opBad); ok {
		t.Error("未知操作码被接受")
	}
}

func TestStepLimitIsAnError(t *testing.T) {
	// JMP 自身 = 死循环
	prog := ir.Program{
		Instructions: []ir.Instr{
			{Op: "JMP", Args: []ir.Operand{ir.Label("loop")}},
			{Op: "HALT"},
		},
		Labels:     map[string]int{"loop": 0},
		DataLabels: map[string]int{},
	}
	bc, err := EncodeProgram(prog, 0)
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	mem := make([]byte, 4096)
	res := Run(bc, mem, 0, int64(len(mem)-8), int64(len(mem)/2), nil, 500)
	if res == nil {
		t.Fatal("Run 返回 nil")
	}
	if res.Status != StatusError {
		t.Fatalf("步数用尽应返回 StatusError, 实际 status=%d", res.Status)
	}
	if !strings.Contains(res.ErrMsg, "limit") {
		t.Fatalf("错误信息应提到 limit, 实际: %q", res.ErrMsg)
	}
}

func TestCromRoundTrip(t *testing.T) {
	data := []byte(strings.Repeat("codecin", 64))
	for _, compress := range []bool{false, true} {
		packed := CromPack(data, compress)
		raw, flags, ok := CromUnpack(packed)
		if !ok {
			t.Fatalf("compress=%v: 解包失败", compress)
		}
		if !bytes.Equal(raw, data) {
			t.Fatalf("compress=%v: 内容不一致", compress)
		}
		if compress && flags&0x01 == 0 {
			t.Fatalf("compress=%v: flags 未标记压缩", compress)
		}
	}
}

func TestCromRejectsBadInput(t *testing.T) {
	// magic / 版本
	if _, _, ok := CromUnpack([]byte("XXXX................")); ok {
		t.Error("坏 magic 被接受")
	}
	bad := CromPack([]byte("abc"), false)
	bad[4] = 99
	if _, _, ok := CromUnpack(bad); ok {
		t.Error("未知版本被接受")
	}
	// CRC 破坏
	bad2 := CromPack([]byte("abcdefgh"), true)
	bad2[len(bad2)-1] ^= 0xFF
	if _, _, ok := CromUnpack(bad2); ok {
		t.Error("CRC 损坏被接受")
	}
	// zip bomb: 头部 mem_size 远小于实际解压长度
	bomb := cromWithDeclaredSize(t, 1024, bytes.Repeat([]byte{0}, 8<<20))
	if _, _, ok := CromUnpack(bomb); ok {
		t.Error("zip bomb 被接受")
	}
	// mem_size 大于实际载荷
	mismatch := cromWithDeclaredSize(t, 1<<20, []byte("short"))
	if _, _, ok := CromUnpack(mismatch); ok {
		t.Error("mem_size 与载荷不符被接受")
	}
}

// cromWithDeclaredSize 手工构造头部 mem_size 与载荷不符的 CROM。
func cromWithDeclaredSize(t *testing.T, declared uint32, raw []byte) []byte {
	t.Helper()
	var buf bytes.Buffer
	w := zlib.NewWriter(&buf)
	if _, err := w.Write(raw); err != nil {
		t.Fatalf("zlib write: %v", err)
	}
	if err := w.Close(); err != nil {
		t.Fatalf("zlib close: %v", err)
	}
	payload := buf.Bytes()

	out := make([]byte, 16+len(payload))
	copy(out[0:4], "CROM")
	out[4] = cromVersion
	out[5] = byte(declared)
	out[6] = byte(declared >> 8)
	out[7] = byte(declared >> 16)
	out[8] = byte(declared >> 24)
	out[9] = 0x01 // 压缩
	crc := crc32.ChecksumIEEE(payload)
	out[10] = byte(crc)
	out[11] = byte(crc >> 8)
	out[12] = byte(crc >> 16)
	out[13] = byte(crc >> 24)
	copy(out[16:], payload)
	return out
}
