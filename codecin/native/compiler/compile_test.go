package compiler

import (
	"bytes"
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

// TestNestedSubscriptUsesCorrectBase 覆盖"临时值必须溢出到栈"这一类缺陷。
//
// 编译器没有寄存器分配器, 约定是任何跨另一个子表达式存活的临时值都要存栈
// (子表达式会随意覆盖 x1..x6)。genIndex 曾把数组/字符串基址暂存在 x3, 于是
// A[B[i]] 的内层下标把 x3 改成 B 的基址, 外层就用 &B[0] 当 A 的地址 ——
// 读错值、写坏内存, 且不报任何错。genAssign / genInit2dLiteral 有同一问题。
func TestNestedSubscriptUsesCorrectBase(t *testing.T) {
	cases := []struct {
		name string
		src  string
		want uint64
	}{
		{"nested_read", `
int A[8]
int B[8]
function main() -> int {
    for (int i = 0; i < 8; i++) { B[i] = 7 - i }
    A[0] = 100
    A[7] = 700
    return A[B[7]]
}`, 100},
		{"nested_write", `
int A[8]
int B[8]
function main() -> int {
    B[0] = 3
    A[3] = 0
    A[B[0]] = 77
    return A[3]
}`, 77},
		{"call_in_lvalue_index", `
int A[8]
function idx(int n) -> int { int t = n * 2
return t }
function main() -> int {
    A[6] = 0
    A[idx(3)] = 42
    return A[6]
}`, 42},
		{"nested_string_index", `
int B[4]
function main() -> int {
    string s = "ABC"
    B[0] = 1
    return s[B[0]]
}`, 66},
		{"three_levels", `
int A[8]
int B[8]
int C[8]
function main() -> int {
    C[0] = 2
    B[2] = 5
    A[5] = 91
    return A[B[C[0]]]
}`, 91},
		{"nested_in_2d_row", `
int B[4]
function main() -> int {
    int m[3][4]
    B[0] = 2
    m[2][3] = 55
    return m[B[0]][3]
}`, 55},
		{"compound_through_nested", `
int A[8]
int B[8]
function main() -> int {
    B[0] = 4
    A[4] = 10
    A[B[0]] += 5
    return A[4]
}`, 15},
		{"float_nested_index", `
int A[8]
int B[8]
function main() -> int {
    B[0] = 6
    A[3] = 44
    return A[B[0] / 2]
}`, 44},
		{"ptrarray_literal_with_calls", `
function g(int n) -> int { int t = n * 10
return t }
function main() -> int {
    int[][] m = {{g(1), g(2)}, {g(3), g(4)}}
    return m[0][0] * 1000 + m[0][1] * 100 + m[1][0] * 10 + m[1][1]
}`, 12340},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := runCompiled(t, c.src); got != c.want {
				t.Fatalf("x0 = %d, 期望 %d (嵌套下标算错基址)", got, c.want)
			}
		})
	}
}

// ==================== 枚举 / 范围 for / case 范围 / 多参数 print ====================
// 这些测试锁定 Python 编译器 (codecin/cin.py) 新增特性在 Go 侧的等价行为。

func countOp(prog *ir.Program, op string) int {
	n := 0
	for _, ins := range prog.Instructions {
		if ins.Op == op {
			n++
		}
	}
	return n
}

func countSys(prog *ir.Program, sysID int64) int {
	n := 0
	for _, ins := range prog.Instructions {
		if ins.Op == "SYS" && len(ins.Args) == 1 &&
			ins.Args[0].Kind == "imm" && ins.Args[0].V == sysID {
			n++
		}
	}
	return n
}

// hasImm 判定是否存在 op 指令带指定立即数操作数。
func hasImm(prog *ir.Program, op string, v int64) bool {
	for _, ins := range prog.Instructions {
		if ins.Op != op {
			continue
		}
		for _, a := range ins.Args {
			if a.Kind == "imm" && a.V == v {
				return true
			}
		}
	}
	return false
}

func hasCond(prog *ir.Program, cond string) bool {
	for _, ins := range prog.Instructions {
		if ins.Op != "B" {
			continue
		}
		for _, a := range ins.Args {
			if a.Kind == "cond" && a.Name == cond {
				return true
			}
		}
	}
	return false
}

func hasLabelPrefix(prog *ir.Program, prefix string) bool {
	for name := range prog.Labels {
		if strings.HasPrefix(name, prefix) {
			return true
		}
	}
	return false
}

func hasDataWrite(prog *ir.Program, want []byte) bool {
	for _, dw := range prog.DataWrites {
		if bytes.Equal(dw.Data, want) {
			return true
		}
	}
	return false
}

// ---- 枚举 ----

func TestEnumConstantsFoldIntoCodegen(t *testing.T) {
	src := `
enum E { A, B = 5, C }
function main() -> int {
    return C
}`
	prog, err := Compile(src, "t.cin", false)
	if err != nil {
		t.Fatalf("编译失败: %v", err)
	}
	// C 自动递增为 6: 值上下文必须折叠为立即数
	if !hasImm(prog, "MOV", 6) {
		t.Fatal("`return C` 未折叠为 MOV imm 6")
	}
	if got := runCompiled(t, src); got != 6 {
		t.Fatalf("x0 = %d, 期望 6", got)
	}
}

func TestEnumInGlobalInitializerAndCaseLabel(t *testing.T) {
	src := `
enum Color { RED = 3, GREEN, BLUE }
int G = BLUE

function main() -> int {
    int x = 4
    int r = 0
    switch (x) {
        case BLUE: r = 20 break
        default: r = 99
    }
    return G * 100 + r
}`
	// G = BLUE = 5, x = 4 命中 case BLUE -> 520
	if got := runCompiled(t, src); got != 520 {
		t.Fatalf("x0 = %d, 期望 520", got)
	}
}

func TestEnumMemberIsNotAnLvalue(t *testing.T) {
	compileErr(t, `
enum E { A, B }
function main() -> int {
    A = 3
    return 0
}`, "Cannot assign to enum member")
}

func TestEnumErrors(t *testing.T) {
	cases := []struct{ name, src, want string }{
		{"duplicate_member", `
enum E { A, B = 5, A }
function main() -> int { return 0 }`, "Duplicate enum member: A"},
		{"duplicate_across_enums", `
enum E { A }
enum F { A }
function main() -> int { return 0 }`, "Duplicate enum member: A"},
		{"empty_enum", `
enum E { }
function main() -> int { return 0 }`, "Empty enum E"},
		{"name_is_keyword", `
enum int { A }
function main() -> int { return 0 }`, "Invalid enum name: int"},
		{"name_is_taken", `
enum E { A }
enum E { B }
function main() -> int { return 0 }`, "Invalid enum name: E"},
		{"shift_out_of_range", `
enum E { A = 1 << 63 }
function main() -> int { return 0 }`, "Enum member A out of 64-bit range"},
		{"increment_out_of_range", `
enum E { A = 9223372036854775807, B }
function main() -> int { return 0 }`, "Enum member B out of 64-bit range"},
		{"negative_out_of_range", `
enum E { A = -9223372036854775807 - 2 }
function main() -> int { return 0 }`, "Enum member A out of 64-bit range"},
		{"divide_by_zero", `
enum E { A = 1 / 0, B }
function main() -> int { return 0 }`, "Enum member A initializer divides by zero"},
		{"modulo_by_zero", `
enum E { A = 1 % 0, B }
function main() -> int { return 0 }`, "Enum member A initializer divides by zero"},
		{"unknown_name", `
enum E { A = NOPE }
function main() -> int { return 0 }`, "must be an integer constant"},
		{"float_initializer", `
enum E { A = 1.5 }
function main() -> int { return 0 }`, "must be an integer constant expression"},
		{"global_name_collides", `
enum E { A }
int A = 1
function main() -> int { return 0 }`, "already used as an enum member or keyword"},
		{"global_name_is_keyword", `
enum E { A }
int enum = 1
function main() -> int { return 0 }`, "already used as an enum member or keyword"},
		{"range_for_var_is_member", `
enum E { A }
function main() -> int {
    int a[2]
    for (int A : a) { }
    return 0
}`, "Invalid range-for variable: A"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) { compileErr(t, c.src, c.want) })
	}
}

func TestEnumInitializerArithmetic(t *testing.T) {
	src := `
enum E {
    A = 1 + 2 * 3,
    B = 10 / 3,
    C = -10 / 3,
    D = -10 % 3,
    E2 = 1 << 4,
    F = 0xF0 | 0x0F,
    G = ~0,
    H = A + B,
    I = 255 >> 4,
    J = 7 & 3,
    K = 7 ^ 3,
}
function main() -> int {
    return A * 1000000 + B * 100000 + C * 10000 + D * 1000 + E2 * 100 + F
}`
	// A=7 B=3 C=-3 D=-1 E2=16 F=255 -> 7*1e6 + 3*1e5 - 3*1e4 - 1*1e3 + 16*100 + 255
	want := uint64(7*1000000 + 3*100000 + (-3)*10000 + (-1)*1000 + 16*100 + 255)
	if got := runCompiled(t, src); got != want {
		t.Fatalf("x0 = %d, 期望 %d (枚举常量表达式算错)", got, want)
	}
	prog, err := Compile(`
enum E { G = ~0, H = 255 >> 4 }
function main() -> int { return G + H }`, "t.cin", false)
	if err != nil {
		t.Fatalf("编译失败: %v", err)
	}
	// ~0 = -1, 255 >> 4 = 15
	if !hasImm(prog, "MOV", -1) || !hasImm(prog, "MOV", 15) {
		t.Fatal("~0 / >> 未折叠为预期立即数")
	}
}

// ---- 范围 for ----

func TestRangeForCompilesAndWalksPointer(t *testing.T) {
	src := `
function main() -> int {
    int a[4]
    a[0] = 1
    a[1] = 2
    a[2] = 3
    a[3] = 4
    int s = 0
    for (int v : a) { s = s + v }
    return s
}`
	prog, err := Compile(src, "t.cin", false)
	if err != nil {
		t.Fatalf("编译失败: %v", err)
	}
	// 指针游走: p += 8, 且 e = p + count*8
	if !hasImm(prog, "ADDI", 8) {
		t.Fatal("范围 for 未生成 ADDI +8 的指针递增")
	}
	if !hasImm(prog, "ADDI", 4*8) {
		t.Fatal("范围 for 未生成 e = p + count*8")
	}
	for _, l := range []string{"rfloop", "rfinc", "rfend"} {
		if !hasLabelPrefix(prog, l) {
			t.Fatalf("缺少标签前缀 %s", l)
		}
	}
	if got := runCompiled(t, src); got != 10 {
		t.Fatalf("x0 = %d, 期望 10", got)
	}
}

func TestRangeForBreakAndContinue(t *testing.T) {
	src := `
function main() -> int {
    int a[6]
    for (int i = 0; i < 6; i++) { a[i] = i }
    int s = 0
    for (int v : a) {
        if (v == 1) { continue }
        if (v == 4) { break }
        s = s + v
    }
    return s
}`
	// 0 + 2 + 3 = 5
	if got := runCompiled(t, src); got != 5 {
		t.Fatalf("x0 = %d, 期望 5 (break/continue 目标错误)", got)
	}
}

func TestRangeForGlobalArrayAndFloatElement(t *testing.T) {
	src := `
int G[3]
function main() -> int {
    G[0] = 1
    G[1] = 2
    G[2] = 3
    float s = 0.0
    for (float v : G) { s = s + v }
    return s * 10.0
}`
	// (1+2+3) * 10 = 60, 元素 int -> float 需插入 ITOF
	prog, err := Compile(src, "t.cin", false)
	if err != nil {
		t.Fatalf("编译失败: %v", err)
	}
	if countSys(prog, SysITOF) == 0 {
		t.Fatal("int 元素转 float 未生成 ITOF")
	}
	if got := runCompiled(t, src); got != 60 {
		t.Fatalf("x0 = %d, 期望 60", got)
	}
}

func TestRangeForErrors(t *testing.T) {
	cases := []struct{ name, src, want string }{
		{"ptrarray_param", `
function f(int[] a) -> int {
    int s = 0
    for (int v : a) { s = s + v }
    return s
}
function main() -> int { return f(0) }`, "range-for requires a fixed-size array"},
		{"ptrarray_local", `
function main() -> int {
    int[] a
    for (int v : a) { }
    return 0
}`, "range-for requires a fixed-size array"},
		{"multidimensional", `
function main() -> int {
    int m[2][3]
    for (int v : m) { }
    return 0
}`, "range-for over multi-dimensional arrays is not supported"},
		{"array_element_type_is_parse_error_not_range_for", `
function main() -> int {
    int m[2][3]
    for (int[] r : m) { }
    return 0
}`, "Expected"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) { compileErr(t, c.src, c.want) })
	}
}

// ---- case 范围 / 多值 ----

func TestCaseRangeProducesTwoComparisons(t *testing.T) {
	src := `
function main() -> int {
    int x = 5
    switch (x) {
        case 1..3: return 1
        case 7, 9..11: return 2
        default: return 3
    }
    return 0
}`
	prog, err := Compile(src, "t.cin", false)
	if err != nil {
		t.Fatalf("编译失败: %v", err)
	}
	if !hasCond(prog, "LT") || !hasCond(prog, "LE") {
		t.Fatal("范围 case 未生成 LT/LE 两段有符号比较")
	}
	if !hasCond(prog, "EQ") {
		t.Fatal("单值 case 未生成 EQ 比较")
	}
	for _, v := range []int64{1, 3, 7, 9, 11} {
		if !hasImm(prog, "MOV", v) {
			t.Fatalf("分派链缺少边界立即数 %d", v)
		}
	}
	if got := runCompiled(t, src); got != 3 {
		t.Fatalf("x0 = %d, 期望 3 (default)", got)
	}
}

func TestCaseRangeRuntime(t *testing.T) {
	src := `
function classify(int x) -> int {
    switch (x) {
        case 1..3: return 10
        case 7, 9..11: return 20
        default: return 30
    }
    return 0
}
function main() -> int {
    int s = 0
    s = s + classify(0)
    s = s + classify(1)
    s = s + classify(3)
    s = s + classify(4)
    s = s + classify(7)
    s = s + classify(9)
    s = s + classify(11)
    s = s + classify(12)
    return s
}`
	// 30 + 10 + 10 + 30 + 20 + 20 + 20 + 30 = 170
	if got := runCompiled(t, src); got != 170 {
		t.Fatalf("x0 = %d, 期望 170 (case 范围匹配错误)", got)
	}
}

func TestCaseRangeWithNegativeBounds(t *testing.T) {
	src := `
function main() -> int {
    int x = -2
    int r = 0
    switch (x) {
        case -3..-1: r = 1 break
        default: r = 2
    }
    return r
}`
	if got := runCompiled(t, src); got != 1 {
		t.Fatalf("x0 = %d, 期望 1", got)
	}
}

func TestEmptyCaseRangeIsCompileError(t *testing.T) {
	compileErr(t, `
function main() -> int {
    int x = 1
    switch (x) {
        case 5..1: return 1
    }
    return 0
}`, "Empty case range: 5..1")
}

func TestNonConstantCaseAltsAreCompileError(t *testing.T) {
	cases := []struct{ name, src string }{
		{"single", `
function main() -> int {
    int y = 2
    int x = 1
    switch (x) {
        case y: return 1
    }
    return 0
}`},
		{"range_high", `
function main() -> int {
    int y = 2
    int x = 1
    switch (x) {
        case 0..y: return 1
    }
    return 0
}`},
		{"range_low", `
function main() -> int {
    int y = 2
    int x = 1
    switch (x) {
        case y..9: return 1
    }
    return 0
}`},
		{"second_alt", `
function main() -> int {
    int y = 2
    int x = 1
    switch (x) {
        case 1, y: return 1
    }
    return 0
}`},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) { compileErr(t, c.src, "constant") })
	}
}

func TestCaseFloatValueStillReportsType(t *testing.T) {
	compileErr(t, `
function main() -> int {
    int x = 1
    switch (x) {
        case 1.5: return 1
    }
    return 0
}`, "case value must be an integer constant, got: float")
}

// ---- 多参数 print / println ----

func TestPrintSingleArgBytecodeUnchanged(t *testing.T) {
	// 单参数 println: 字符串化 + PRINT_STR + OUT 10 (与改动前逐字节一致)
	prog, err := Compile(`function main() -> int { println(7) return 0 }`, "t.cin", false)
	if err != nil {
		t.Fatalf("编译失败: %v", err)
	}
	if n := countSys(prog, SysITOA); n != 1 {
		t.Fatalf("println(7) 的 ITOA 次数 = %d, 期望 1", n)
	}
	if n := countSys(prog, SysPRINT_STR); n != 1 {
		t.Fatalf("println(7) 的 PRINT_STR 次数 = %d, 期望 1", n)
	}
	if n := countOp(prog, "OUT"); n != 1 {
		t.Fatalf("println(7) 的 OUT 次数 = %d, 期望 1", n)
	}

	// 单参数 print: 没有换行
	prog, err = Compile(`function main() -> int { print(7) return 0 }`, "t.cin", false)
	if err != nil {
		t.Fatalf("编译失败: %v", err)
	}
	if n := countSys(prog, SysPRINT_STR); n != 1 {
		t.Fatalf("print(7) 的 PRINT_STR 次数 = %d, 期望 1", n)
	}
	if n := countOp(prog, "OUT"); n != 0 {
		t.Fatalf("print(7) 不应输出换行, OUT 次数 = %d", n)
	}
}

func TestPrintMultipleArgs(t *testing.T) {
	prog, err := Compile(
		`function main() -> int { println(1, "a", 2.5) return 0 }`, "t.cin", false)
	if err != nil {
		t.Fatalf("多参数 println 编译失败: %v", err)
	}
	if n := countSys(prog, SysPRINT_STR); n != 3 {
		t.Fatalf("PRINT_STR 次数 = %d, 期望 3", n)
	}
	if n := countSys(prog, SysITOA); n != 1 {
		t.Fatalf("ITOA 次数 = %d, 期望 1", n)
	}
	if n := countSys(prog, SysFTOA); n != 1 {
		t.Fatalf("FTOA 次数 = %d, 期望 1", n)
	}
	if n := countOp(prog, "OUT"); n != 1 {
		t.Fatalf("OUT 次数 = %d, 期望 1 (只换行一次)", n)
	}

	// print 多参数: 无换行
	prog, err = Compile(`function main() -> int { print(1, 2, 3) return 0 }`, "t.cin", false)
	if err != nil {
		t.Fatalf("多参数 print 编译失败: %v", err)
	}
	if n := countSys(prog, SysPRINT_STR); n != 3 {
		t.Fatalf("print 的 PRINT_STR 次数 = %d, 期望 3", n)
	}
	if n := countOp(prog, "OUT"); n != 0 {
		t.Fatalf("print 多参数不应输出换行, OUT 次数 = %d", n)
	}

	// 无参 println: 仅换行
	prog, err = Compile(`function main() -> int { println() return 0 }`, "t.cin", false)
	if err != nil {
		t.Fatalf("println() 编译失败: %v", err)
	}
	if n := countSys(prog, SysPRINT_STR); n != 0 {
		t.Fatalf("println() 的 PRINT_STR 次数 = %d, 期望 0", n)
	}
	if n := countOp(prog, "OUT"); n != 1 {
		t.Fatalf("println() 的 OUT 次数 = %d, 期望 1", n)
	}
}

// ---- 字符串转义 ----

func TestStringEscapeBytes(t *testing.T) {
	cases := []struct {
		name string
		lit  string // CIN 源码里的字面量 (含引号)
		want []byte // 期望的数据字节 (含结尾 NUL)
	}{
		{"hex_utf8", `"\xE4\xB8\xAD"`, []byte{0xE4, 0xB8, 0xAD, 0x00}},
		{"unicode_escape", `"\u4e2d"`, []byte{0xE4, 0xB8, 0xAD, 0x00}},
		{"unicode_long", `"\U0001F600"`, []byte{0xF0, 0x9F, 0x98, 0x80, 0x00}},
		{"hex_short", `"\x41"`, []byte{0x41, 0x00}},
		{"hex_single_low", `"\x9"`, []byte{0x09, 0x00}},
		{"control_chars", `"\a\b\f\v"`, []byte{7, 8, 12, 11, 0x00}},
		{"quote_and_backslash", `"\'\\"`, []byte{'\'', '\\', 0x00}},
		{"unknown_escape_verbatim", `"\q"`, []byte{'q', 0x00}},
		{"newline_tab", `"\n\t"`, []byte{'\n', '\t', 0x00}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			src := `function main() -> int { string s = ` + c.lit + ` return strlen(s) }`
			prog, err := Compile(src, "t.cin", false)
			if err != nil {
				t.Fatalf("编译失败: %v", err)
			}
			if !hasDataWrite(prog, c.want) {
				t.Fatalf("未找到期望的数据字节 %v", c.want)
			}
		})
	}
}

func TestHexAndUnicodeEscapesAreIdentical(t *testing.T) {
	hexProg, err := Compile(
		`function main() -> int { string s = "\xE4\xB8\xAD" return strlen(s) }`,
		"t.cin", false)
	if err != nil {
		t.Fatalf("编译失败: %v", err)
	}
	uniProg, err := Compile(
		`function main() -> int { string s = "\u4e2d" return strlen(s) }`,
		"t.cin", false)
	if err != nil {
		t.Fatalf("编译失败: %v", err)
	}
	if dumpProgram(hexProg) != dumpProgram(uniProg) {
		t.Fatal(`"\xE4\xB8\xAD" 与 "\u4e2d" 的编译产物不一致`)
	}
	// 两处都必须是 3 字节 + NUL
	if !hasDataWrite(hexProg, []byte{0xE4, 0xB8, 0xAD, 0x00}) {
		t.Fatal("\\x 转义未产生 3 个原始字节")
	}
}

func TestStringEscapeErrors(t *testing.T) {
	cases := []struct{ name, src, want string }{
		{"hex_empty", `function main() -> int { string s = "\x" return 0 }`,
			"\\x escape needs at least one hex digit"},
		{"hex_no_digits", `function main() -> int { string s = "\xZZ" return 0 }`,
			"\\x escape needs at least one hex digit"},
		{"u_too_short", `function main() -> int { string s = "\u12" return 0 }`,
			"\\u escape needs exactly 4 hex digits"},
		{"u_non_hex", `function main() -> int { string s = "\uZZZZ" return 0 }`,
			"\\u escape needs exactly 4 hex digits"},
		{"U_too_short", `function main() -> int { string s = "\U0001F60" return 0 }`,
			"\\U escape needs exactly 8 hex digits"},
		{"U_non_hex", `function main() -> int { string s = "\U0001F60Z" return 0 }`,
			"\\U escape needs exactly 8 hex digits"},
		{"u_out_of_range", `function main() -> int { string s = "\U00110000" return 0 }`,
			"\\U escape out of Unicode range"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) { compileErr(t, c.src, c.want) })
	}
}

// ---- 数字与 '..' 的词法 ----

func TestDotDotIsNotPartOfNumber(t *testing.T) {
	// 1..5 必须切成 NUMBER(1) DOTDOT NUMBER(5)
	prog, err := Compile(`
function main() -> int {
    int x = 4
    switch (x) {
        case 1..5: return 7
    }
    return 0
}`, "t.cin", false)
	if err != nil {
		t.Fatalf("编译失败: %v", err)
	}
	if !hasImm(prog, "MOV", 1) || !hasImm(prog, "MOV", 5) {
		t.Fatal("1..5 未切成两个数字 token")
	}
	if got := runCompiled(t, `
function main() -> int {
    int x = 4
    switch (x) {
        case 1..5: return 7
    }
    return 0
}`); got != 7 {
		t.Fatalf("x0 = %d, 期望 7", got)
	}
}

func TestFloatMembersAndSecondDotStillWork(t *testing.T) {
	// 浮点字面量与 struct 成员访问不受 '..' 支持的影响
	if _, err := Compile(`
struct P { int x }
function main() -> int {
    P p
    p.x = 3
    float f = 1.5
    int z[2]
    return p.x + f
}`, "t.cin", false); err != nil {
		t.Fatalf("浮点/成员访问被破坏: %v", err)
	}
}

// ---- for (i = 0; ...) 初始化子句 ----

func TestForInitAssignmentWithoutDeclaration(t *testing.T) {
	src := `
function main() -> int {
    int i = 0
    int s = 0
    for (i = 0; i < 5; i++) { s = s + i }
    return s * 100 + i
}`
	// s = 0+1+2+3+4 = 10, i = 5 -> 1005
	if got := runCompiled(t, src); got != 1005 {
		t.Fatalf("x0 = %d, 期望 1005", got)
	}
}

// ---- 枚举类型名当类型用 ----

func TestEnumTypeNameIsIntAlias(t *testing.T) {
	src := `
enum Color { RED, GREEN, BLUE }
function main() -> int {
    Color c = GREEN
    unsigned int u = BLUE
    Color d = c + u
    return d
}`
	// GREEN=1, BLUE=2 -> 3
	if got := runCompiled(t, src); got != 3 {
		t.Fatalf("x0 = %d, 期望 3", got)
	}
}
