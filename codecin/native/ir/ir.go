// Package ir 定义 Code CIN 字节码中间表示 (IR)。
//
// 该 IR 与 Python codecin 侧 CINCompiler 产出的结构完全等价:
//
//	指令 = (opcode 名, 操作数列表); 操作数用 Kind 区分 (reg/imm/mem/label/cond/float/str)。
//
// 编译器 (compiler 包) 产出 *Program, engine 包将其编码为 UCBC 字节码后交由 VM 执行。
package ir

// Operand 表示一条指令的操作数。
type Operand struct {
	Kind string  // reg / imm / mem / label / cond / float / str / vec / veclane
	V    int64   // reg 号 / imm 值 / mem base(-1=绝对地址) / str 地址 / vec 号
	Off  int64   // mem 偏移 / veclane
	Name string  // label 名 / cond 名
	F    float64 // float 值
}

// Instr 是一条 IR 指令。
type Instr struct {
	Op   string
	Args []Operand
}

// DataWrite 是一次数据段写入 (地址 + 字节)。
type DataWrite struct {
	Addr int
	Data []byte
}

// Program 是编译产物: 指令流 + 标签 + 数据段写入。
type Program struct {
	Instructions []Instr
	Labels       map[string]int
	DataLabels   map[string]int
	DataWrites   []DataWrite
}

// 操作数构造辅助 (与编译器共享书写习惯)。
func Reg(n int) Operand   { return Operand{Kind: "reg", V: int64(n)} }
func Imm(v int64) Operand { return Operand{Kind: "imm", V: v} }
func Mem(base, off int) Operand {
	return Operand{Kind: "mem", V: int64(base), Off: int64(off)}
}
func Label(name string) Operand { return Operand{Kind: "label", Name: name} }
func Cond(name string) Operand  { return Operand{Kind: "cond", Name: name} }
func Float(f float64) Operand   { return Operand{Kind: "float", F: f} }
func Str(addr int) Operand      { return Operand{Kind: "str", V: int64(addr)} }
