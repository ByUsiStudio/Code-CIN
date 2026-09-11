// Package compiler 是 Code CIN 语言的 Go 版编译器 (Python codecin/cin.py 的忠实移植)。
// 它将 CIN 源码编译为 ir.Program, 与 Python CINCompiler 的产物逐字节一致。
package compiler

import "codecin-native/ir"

// ---------------- 类型系统 ----------------

// TypeKind 类型类别。
type TypeKind uint8

const (
	kInt TypeKind = iota
	kFloat
	kBool
	kString
	kVoid
	kStruct
	kArray
	kPtrArray
)

// Type 表示一个 CIN 类型。等价于 Python 侧的元组表示:
//
//	int/float/bool/string/void  -> Kind 对应标量
//	('struct', Name)            -> kStruct
//	('array', elem, size)       -> kArray
//	('ptrarray', elem)          -> kPtrArray
type Type struct {
	Kind TypeKind
	Elem *Type
	Size int
	Name string
}

func scalarT(k TypeKind) *Type { return &Type{Kind: k} }

func isFixedArray(t *Type) bool { return t != nil && t.Kind == kArray }
func isPtrArray(t *Type) bool   { return t != nil && t.Kind == kPtrArray }
func isStruct(t *Type) bool     { return t != nil && t.Kind == kStruct }
func isScalar(t *Type) bool {
	return t != nil && (t.Kind == kInt || t.Kind == kFloat || t.Kind == kBool ||
		t.Kind == kString || t.Kind == kVoid)
}

func typeSlots(t *Type) int {
	if isFixedArray(t) {
		return t.Size * typeSlots(t.Elem)
	}
	return 1
}

func typeName(t *Type) string {
	if t == nil {
		return "nil"
	}
	switch t.Kind {
	case kInt:
		return "int"
	case kFloat:
		return "float"
	case kBool:
		return "bool"
	case kString:
		return "string"
	case kVoid:
		return "void"
	case kStruct:
		return t.Name
	case kArray:
		return typeName(t.Elem) + "[]"
	case kPtrArray:
		return typeName(t.Elem) + "[]"
	}
	return "?"
}

// ---------------- AST ----------------

// Node 是 AST 节点 (等价于 Python 侧的位置元组)。
type Node struct {
	Kind    string // num/bool/str/var/call/binop/not/bitnot/neg/preinc/predec/postinc/postdec/cond/member/index
	//        block/return/if/while/for/dowhile/switch/break/continue/decl/cpu/assert/expr
	//        declitem/case/arraylit
	Num     float64
	IsFloat bool
	Bool    bool
	Str     string
	Name    string
	Op      string
	A, B, C, D *Node
	List    []*Node
	Int     int
	Type    *Type
	Filename string
	// 数组字面量: Is2D=false 时 ArrayLit[0] 为元素; Is2D=true 时 ArrayLit 为行。
	ArrayLit [][]*Node
	Is2D     bool
	// cpu 语句操作数 (kind, value) 对。
	Operands [][2]string
}

// DeclItem 一条变量声明 (name, type, init, arrayLit)。
type DeclItem struct {
	Name      string
	Type      *Type
	Init      *Node
	ArrayLit  *Node
}

// SwitchBranch 一个 switch 分支 ('case', constExpr|nil, stmts)。
type SwitchBranch struct {
	Const *Node // nil = default
	Stmts []*Node
}

// ---------------- 编译上下文 ----------------

// compiler 持有整个编译过程的可变状态。
type compiler struct {
	// 词法/语法产物
	structs   map[string]*StructDef
	functions map[string]*FuncDef
	globals   []*GlobalVar

	// 代码生成状态
	res        *ir.Program
	dataPtr    int
	heapStr    map[string]int
	filename   string
	bounds     bool

	// 函数生成上下文
	funcDef       *FuncDef
	locals        map[string]localVar
	frameBytes    int
	breakLbls     []string
	continueLbls  []string
	labelCounter  int
	globalsSym    map[string]globalVar
}

type localVar struct {
	t       *Type
	off     int
	isBlock bool
}

type globalVar struct {
	t       *Type
	addr    int
	isBlock bool
}

// StructDef struct 定义。
type StructDef struct {
	name      string
	fields    []fieldDef
	offsets   map[string]int
	sizeSlots int
}

type fieldDef struct {
	name string
	t    *Type
}

// FuncDef 函数定义。
type FuncDef struct {
	name    string
	params  []paramDef
	retType *Type
	body    []*Node
	line    int
}

type paramDef struct {
	name string
	t    *Type
}

// GlobalVar 全局变量。
type GlobalVar struct {
	name     string
	t        *Type
	addr     int
	init     *Node
	arrayLit *Node
}

// newCompiler 构建编译器。
func newCompiler(filename string, bounds bool) *compiler {
	return &compiler{
		structs:   map[string]*StructDef{},
		functions: map[string]*FuncDef{},
		globals:   []*GlobalVar{},
		res: &ir.Program{
			Labels:     map[string]int{},
			DataLabels: map[string]int{},
		},
		heapStr:     map[string]int{},
		filename:    filename,
		bounds:      bounds,
		locals:      map[string]localVar{},
		globalsSym:  map[string]globalVar{},
	}
}
