package compiler

import (
	"fmt"
	"sort"
	"strings"
	"testing"

	"codecin-native/engine"
	"codecin-native/ir"
)

// 这些测试锁定 docs/SUGGESTIONS.md 批次 B 修复的 Go 侧行为:
//   * switch 负 case 匹配 / 非常量 case 报错 / 标签与分支一一对应;
//   * 语义错误必须报编译错误, 而不是产出静默错误的字节码;
//   * 数值字面量后缀与 64 位范围;
//   * switch 内 continue 不泄漏选择器栈槽。

// dumpProgram 把 IR 渲染成稳定文本, 便于比较与断言 (避开对 engine 包的依赖)。
func dumpProgram(prog *ir.Program) string {
	var sb strings.Builder
	for _, ins := range prog.Instructions {
		sb.WriteString(ins.Op)
		for _, a := range ins.Args {
			fmt.Fprintf(&sb, " %s:%d:%d:%s:%g", a.Kind, a.V, a.Off, a.Name, a.F)
		}
		sb.WriteByte('\n')
	}
	return sb.String()
}

func compileOK(t *testing.T, src string) string {
	t.Helper()
	prog, err := Compile(src, "t.cin", false)
	if err != nil {
		t.Fatalf("期望编译通过, 实际报错: %v", err)
	}
	return dumpProgram(prog)
}

func compileErr(t *testing.T, src, wantSubstr string) {
	t.Helper()
	_, err := Compile(src, "t.cin", false)
	if err == nil {
		t.Fatalf("期望编译错误 (%s), 实际通过", wantSubstr)
	}
	if !strings.Contains(err.Error(), wantSubstr) {
		t.Fatalf("错误信息应包含 %q, 实际: %v", wantSubstr, err)
	}
}

func TestSwitchNegativeCaseIsComparable(t *testing.T) {
	// 旧实现用 -1 当 default 哨兵并跳过 raw < 0, `case -1` 永远匹配不上。
	// 这里检查分派比较链里确实出现了 0xFFFFFFFFFFFFFFFF。
	src := `
function main() -> int {
    int x = -1
    switch (x) {
        case -1: return 10
        default: return 30
    }
    return 0
}`
	prog, err := Compile(src, "t.cin", false)
	if err != nil {
		t.Fatalf("编译失败: %v", err)
	}
	found := false
	for _, ins := range prog.Instructions {
		if ins.Op != "MOV" || len(ins.Args) != 2 {
			continue
		}
		if ins.Args[1].Kind == "imm" && ins.Args[1].V == -1 {
			found = true
		}
	}
	if !found {
		t.Fatal("分派链中未找到 case -1 的比较常量")
	}
}

func TestSwitchNonConstantCaseIsCompileError(t *testing.T) {
	compileErr(t, `
function main() -> int {
    int y = 2
    int x = 1
    switch (x) {
        case y: return 10
        case 1: return 20
    }
    return 0
}`, "constant")
}

func TestSwitchFloatSelectorIsCompileError(t *testing.T) {
	compileErr(t, `
function main() -> int {
    float f = 1.5
    switch (f) {
        case 1: return 1
    }
    return 0
}`, "Switch expression must be integer")
}

func TestSwitchWithContinueCompiles(t *testing.T) {
	// 只需通过编译与编码: 栈平衡由运行时测试 (tests/test_switch_semantics.py) 覆盖
	compileOK(t, `
function main() -> int {
    int i = 0
    int n = 0
    while (i < 10) {
        i = i + 1
        switch (i) {
            case 1: continue
            default: n = n + 1
        }
    }
    return n
}`)
}

func TestSemanticErrorsAreReported(t *testing.T) {
	cases := []struct{ name, src, want string }{
		{"unknown_function", `function main() -> int { return foo() }`, "Unknown function"},
		{"undefined_var", `function main() -> int { zzz = 1 return 0 }`, "Undefined variable"},
		{"member_on_int", `function main() -> int { int x = 1 return x.foo }`, "non-struct"},
		{"bitnot_float", `function main() -> int { return ~1.5 }`, "Bitwise NOT"},
		{"float_mod", `function main() -> int { return 7.5 % 2.0 }`, "Float modulo"},
		{"sqrt_no_args", `function main() -> int { float f = sqrt() return 0 }`, "at least 1"},
		{"substr_too_few", `function main() -> int { string s = substr("a", 1) return 0 }`, "at least 3"},
		{"break_outside", `function main() -> int { break return 0 }`, "break outside loop"},
		{"continue_outside", `function main() -> int { continue return 0 }`, "continue outside loop"},
		{"int_out_of_range", `function main() -> int { int x = 9223372036854775808 return 0 }`, "Malformed numeric"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) { compileErr(t, c.src, c.want) })
	}
}

func TestValidProgramsStillCompile(t *testing.T) {
	// 防止错误检查做得过严
	srcs := []string{
		`function main() -> int { int x = 0xFFu return x }`,
		`function main() -> int { int x = 0b1010U return x }`,
		`function main() -> int { int x = 42u return x }`,
		`function main() -> int { float f = 1.5f return 0 }`,
		`function main() -> int { return min(3, 4) + max(1, 2) }`,
		`function main() -> int { return strlen("ab", 9) }`,
		`function main() -> int { int a = 6 int b = 3 return (a & b) | (a ^ b) }`,
	}
	for _, src := range srcs {
		if _, err := Compile(src, "t.cin", false); err != nil {
			t.Fatalf("合法程序被拒: %v\n%s", err, src)
		}
	}
}

func TestCompileIsDeterministic(t *testing.T) {
	src := `function main() -> int { int s = 0 for (int i = 0; i < 5; i++) { s += i } return s }`
	a := compileOK(t, src)
	b := compileOK(t, src)
	if a != b {
		t.Fatal("同一输入的编译产物不稳定")
	}
}

// runCompiled 编译 CIN 源码并在 Go VM 中执行, 返回 x0 (main 的返回值)。
func runCompiled(t *testing.T, src string) uint64 {
	t.Helper()
	prog, err := Compile(src, "t.cin", false)
	if err != nil {
		t.Fatalf("编译失败: %v", err)
	}
	bc, err := engine.EncodeProgram(*prog, 0)
	if err != nil {
		t.Fatalf("编码失败: %v", err)
	}
	mem := make([]byte, 64*1024)
	for _, dw := range prog.DataWrites {
		copy(mem[dw.Addr:], dw.Data)
	}
	res := engine.Run(bc, mem, 0, int64(len(mem)-8), int64(len(mem)/2),
		nil, 100_000_000)
	if res == nil {
		t.Fatal("Run 返回 nil")
	}
	if res.ErrMsg != "" {
		t.Fatalf("运行期错误: %s", res.ErrMsg)
	}
	if res.Status != engine.StatusOK {
		t.Fatalf("非正常结束: status=%d", res.Status)
	}
	return res.Regs[0]
}

// TestFloatSubscriptIsTruncatedToInt 锁定子集 issue #1 的 Go 侧修复。
//
// `/` 恒为浮点除法, 所以 `a[(lo + hi) / 2]` 的下标是 float。修复前 genIndex
// 把这个 float 的 IEEE-754 位模式直接当作字节偏移 (于是 249.5 变成
// 0x0379_8000_0000_0000), 标准 Hoare 快排第一次取枢轴就越界崩溃。
func TestFloatSubscriptIsTruncatedToInt(t *testing.T) {
	src := `
int G_C[500]

function qrec(int lo, int hi) -> void {
    if (lo >= hi) { return }
    int p = G_C[(lo + hi) / 2]
    int i = lo
    int j = hi
    while (i <= j) {
        while (i <= j && G_C[i] < p) { i = i + 1 }
        while (i <= j && G_C[j] > p) { j = j - 1 }
        if (i <= j) {
            int t = G_C[i]
            G_C[i] = G_C[j]
            G_C[j] = t
            i = i + 1
            j = j - 1
        }
    }
    qrec(lo, j)
    qrec(i, hi)
}

function main() -> int {
    for (int i = 0; i < 500; i++) {
        G_C[i] = (i * 7919 + 13) % 10007
    }
    qrec(0, 499)
    for (int i = 0; i < 499; i++) {
        if (G_C[i] > G_C[i + 1]) { return 1000000000 + i }
    }
    return G_C[0] * 10007 + G_C[499]
}`

	vals := make([]int, 500)
	for i := range vals {
		vals[i] = (i*7919 + 13) % 10007
	}
	sort.Ints(vals)
	want := uint64(vals[0]*10007 + vals[len(vals)-1])
	if got := runCompiled(t, src); got != want {
		t.Fatalf("快排结果 x0 = %d, 期望 %d (未排序或越界)", got, want)
	}
}

func TestFloatSubscriptTruncatesTowardZero(t *testing.T) {
	// float 下标与 `int x = 1.9` 一致: 向零截断; -0.5 -> 0。
	src := `
function main() -> int {
    int a[4]
    a[0] = 7
    a[1] = 20
    a[2] = 30
    a[3] = 40
    float f = 1.9
    int r = a[f]
    float g = 3.0
    a[g - 0.5] = 99
    float neg = -0.5
    string s = "ABC"
    int ch = s[1.9]
    return r + a[2] + a[neg] + ch
}`
	// 20 + 99 + 7 + 'B'(66) = 192
	if got := runCompiled(t, src); got != 192 {
		t.Fatalf("x0 = %d, 期望 192", got)
	}
}
