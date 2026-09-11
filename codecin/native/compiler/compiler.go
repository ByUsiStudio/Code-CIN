// Package compiler 是 CIN 高级语言编译器 (Go 实现)。
//
// 与 Python codecin/cin.py 的 CINCompiler 产出的 IR 严格等价:
//   指令流 + 标签 + 数据段写入 (地址 + 字节)。
// 编译器把 CIN 源码 (类 C 语法) 编译为 *ir.Program, engine 包将其编码为
// UCBC 字节码后交由 VM 执行。
package compiler

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"codecin-native/ir"
)

// cinError 是编译期错误 (等价于 Python CompilerError)。通过 panic 抛出,
// 由顶层 recoverHandler 捕获并转换为 error。
type cinError struct{ msg string }

func (e *cinError) Error() string { return e.msg }

// raise 抛出编译错误 (panic 内部, 顶层 recover 捕获)。
func raise(format string, args ...interface{}) {
	panic(&cinError{fmt.Sprintf(format, args...)})
}

func recoverHandler(err *error) {
	if r := recover(); r != nil {
		if ce, ok := r.(*cinError); ok {
			*err = errors.New("Compiler error: " + ce.msg)
			return
		}
		panic(r)
	}
}

// ---------------- 类型表示 ----------------
// 'int' / 'float' / 'bool' / 'string' / 'void' 为标量;
// ('struct', Name) 结构体; ('array', elem, size) 定长数组;
// ('ptrarray', elem) 动态/参数数组。

type TypeKind int

const (
	TScalar TypeKind = iota
	TStruct
	TArray    // 定长数组
	TPtrArray // 动态数组 / 指针数组
)

type CType struct {
	Kind TypeKind
	Name string // 标量名 / 结构体名
	Elem *CType // array/ptrarray 的元素类型
	Size int    // array 的元素个数
}

func scalarType(name string) *CType     { return &CType{Kind: TScalar, Name: name} }
func structType(name string) *CType     { return &CType{Kind: TStruct, Name: name} }
func arrayType(elem *CType, size int) *CType {
	return &CType{Kind: TArray, Elem: elem, Size: size}
}
func ptrArrayType(elem *CType) *CType { return &CType{Kind: TPtrArray, Elem: elem} }

func (t *CType) isScalar() bool     { return t != nil && t.Kind == TScalar }
func (t *CType) isStruct() bool     { return t != nil && t.Kind == TStruct }
func (t *CType) isFixedArray() bool { return t != nil && t.Kind == TArray }
func (t *CType) isPtrArray() bool   { return t != nil && t.Kind == TPtrArray }

func ctypeEqual(a, b *CType) bool {
	if a == nil || b == nil {
		return a == b
	}
	if a.Kind != b.Kind {
		return false
	}
	switch a.Kind {
	case TScalar, TStruct:
		return a.Name == b.Name
	case TArray:
		return a.Size == b.Size && ctypeEqual(a.Elem, b.Elem)
	case TPtrArray:
		return ctypeEqual(a.Elem, b.Elem)
	}
	return false
}

// typeSlots 返回类型占据的 qword 槽数。
func typeSlots(t *CType) int {
	if t.isFixedArray() {
		return t.Size * typeSlots(t.Elem)
	}
	return 1
}

// typeStr 用于错误消息中的类型显示 (近似 Python 元组 repr)。
func typeStr(t *CType) string {
	if t == nil {
		return "None"
	}
	switch t.Kind {
	case TScalar:
		return t.Name
	case TStruct:
		return fmt.Sprintf("('struct', '%s')", t.Name)
	case TArray:
		return fmt.Sprintf("('array', %s, %d)", typeStr(t.Elem), t.Size)
	case TPtrArray:
		return fmt.Sprintf("('ptrarray', %s)", typeStr(t.Elem))
	}
	return "?"
}

// ---------------- AST ----------------

// Node 是表达式 / 语句节点 (带标签的通用结构, 等价 Python 的元组 AST)。
type Node struct {
	Kind string

	// num
	IntV    int64
	FloatV  float64
	IsFloat bool

	// bool
	BoolV bool

	// str
	StrV string

	// var 名 / member 字段名 / call 函数名
	Name string

	// binop / cpu 操作名
	Op string

	// 子节点
	Kids []*Node

	// assert
	Line     int
	Filename string

	// decl 语句
	Decls []DeclEntry

	// switch 语句
	Branches []SwitchBranch

	// cpu 语句
	CpuOps []CpuOperand
}

type DeclEntry struct {
	Name     string
	Type     *CType
	Init     *Node    // 单表达式初始化 (nil 表示无)
	ArrayLit *ArrayLit // 数组字面量 (nil 表示无)
}

type ArrayLit struct {
	Is2D  bool
	Rows  [][]*Node // 2D 行
	Elems []*Node   // 1D 元素
}

type SwitchBranch struct {
	Const *Node    // nil 表示 default
	Stmts []*Node
}

type CpuOperand struct {
	IsIdent bool
	Name    string
	Num     int64
}

// StructDef / FuncDef / GlobalVar 与 Python 端对应。

type Field struct {
	Name string
	Type *CType
}

type StructDef struct {
	Name      string
	Fields    []Field
	Offsets   map[string]int
	SizeSlots int
}

type Param struct {
	Name string
	Type *CType
}

type FuncDef struct {
	Name    string
	Params  []Param
	RetType *CType
	Body    []*Node
	Line    int
}

type GlobalVar struct {
	Name     string
	Type     *CType
	Addr     int
	Init     *Node
	ArrayLit *ArrayLit
}

// ---------------- 行来源映射 ----------------

type fileLine struct {
	file string
	line int
}

type lineFile struct {
	file string
	line string
}

// ---------------- 顶层入口 ----------------

// Compile 编译一个 CIN 源码字符串 (不展开 import)。
func Compile(source string, filename string) (*ir.Program, error) {
	if filename == "" {
		filename = "<cin>"
	}
	return doCompile(source, filename, nil)
}

// CompileFile 读取 .cin 文件, 展开 import 后编译。
func CompileFile(path string, libDir string) (*ir.Program, error) {
	return doCompileFile(path, libDir)
}

func doCompileFile(path string, libDir string) (prog *ir.Program, err error) {
	defer recoverHandler(&err)
	source, origin := loadProgramSourceMapped(path, libDir)
	return doCompile(source, path, origin)
}

func doCompile(source string, filename string, origin []*fileLine) (prog *ir.Program, err error) {
	defer recoverHandler(&err)
	tokens := tokenize(source, origin)
	if origin != nil {
		remapTokens(tokens, origin)
	}
	p := newParser(tokens)
	structs, globals, funcOrder, funcs := p.parseProgram()
	gen := newCodeGen(structs, funcs, funcOrder, filename, false)
	gen.layoutGlobals(globals)
	return gen.generate(globals, funcs, funcOrder), nil
}

// ---------------- import 展开 ----------------

var importRe = regexp.MustCompile(`^import\s+["']([^"']+)["']\s*;?\s*$`)

func resolveImport(name, sourceDir, libDir, root string) string {
	candidates := []string{filepath.Join(sourceDir, name)}
	if libDir != "" {
		candidates = append(candidates, filepath.Join(libDir, name))
	}
	if root != "" {
		candidates = append(candidates, filepath.Join(root, name))
	}
	for _, cand := range candidates {
		if cand != "" {
			if st, err := os.Stat(cand); err == nil && !st.IsDir() {
				return cand
			}
		}
	}
	raise("Import file not found: '%s' (searched %s, %s, %s)", name, sourceDir, libDir, root)
	return ""
}

func collectModuleLines(path string, loaded, active map[string]bool, out *[]lineFile, libDir, root string) {
	abs, err := filepath.Abs(path)
	if err != nil {
		raise("cannot resolve path %s: %v", path, err)
	}
	real, err := filepath.EvalSymlinks(abs)
	if err != nil {
		raise("cannot open file %s", path)
	}
	if active[real] {
		raise("Circular import: %s (%s)", path, real)
	}
	if loaded[real] {
		return
	}
	active[real] = true
	data, err := os.ReadFile(real)
	if err != nil {
		raise("cannot read file %s: %v", real, err)
	}
	lines := strings.Split(string(data), "\n")
	for _, line := range lines {
		if m := importRe.FindStringSubmatch(line); m != nil {
			target := resolveImport(m[1], filepath.Dir(real), libDir, root)
			collectModuleLines(target, loaded, active, out, libDir, root)
			continue
		}
		*out = append(*out, lineFile{real, line})
	}
	delete(active, real)
	loaded[real] = true
}

// loadProgramSourceMapped 读取并展开 import, 返回 (合并源码, 行来源表)。
func loadProgramSourceMapped(path string, libDir string) (string, []*fileLine) {
	root := ""
	if libDir != "" {
		clean := filepath.Clean(libDir)
		if filepath.Base(clean) == "lib" {
			root = filepath.Dir(clean)
		}
	}
	out := []lineFile{}
	collectModuleLines(path, map[string]bool{}, map[string]bool{}, &out, libDir, root)
	counters := map[string]int{}
	origin := []*fileLine{nil}
	for _, lf := range out {
		counters[lf.file]++
		origin = append(origin, &fileLine{lf.file, counters[lf.file]})
	}
	lines := make([]string, len(out))
	for i, lf := range out {
		lines[i] = lf.line
	}
	return strings.Join(lines, "\n"), origin
}
