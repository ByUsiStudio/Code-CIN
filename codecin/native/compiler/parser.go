package compiler

import (
	"fmt"
	"math/big"
	"strconv"
)

// ---------------- 语法分析 ----------------

type parser struct {
	toks     []Token
	pos      int
	filename string

	// 枚举与范围 for 状态
	enums     map[string]int64 // 枚举成员名 -> 编译期整数值 (可被后续成员引用)
	enumTypes map[string]bool  // 已声明的枚举类型名 (裸类型名等价于 int)
	rangeID   int              // 范围 for 隐藏局部名序号 ($rf<id>p / $rf<id>e)
}

func (p *parser) peek() Token {
	return p.toks[p.pos]
}

func (p *parser) peekN(k int) Token {
	idx := p.pos + k
	if idx >= len(p.toks) {
		idx = len(p.toks) - 1
	}
	return p.toks[idx]
}

func (p *parser) next() Token {
	t := p.toks[p.pos]
	p.pos++
	return t
}

func (p *parser) accept(kind string) bool {
	if p.peek().kind == kind {
		p.next()
		return true
	}
	return false
}

func (p *parser) expect(kind string) (Token, error) {
	t := p.peek()
	if t.kind != kind {
		return t, fmt.Errorf("Expected %s but got %s (%q) at %s", kind, t.kind, t.sval, p.loc())
	}
	return p.next(), nil
}

func (p *parser) skipNL() {
	for p.accept("NL") {
	}
}

func (p *parser) loc() string {
	return p.locTok(p.peek())
}

// locTok 返回指定 token 的 'file:line' 定位 (等价于 Python 侧 _loc(tok))。
func (p *parser) locTok(t Token) string {
	if t.filename != "" {
		return fmt.Sprintf("%s:%d", t.filename, t.line)
	}
	if p.filename != "" {
		return fmt.Sprintf("%s:%d", p.filename, t.line)
	}
	return fmt.Sprintf("<cin>:%d", t.line)
}

// ---------------- 类型 ----------------

func typeKindFromName(name string) TypeKind {
	switch name {
	case "float":
		return kFloat
	case "bool":
		return kBool
	case "string":
		return kString
	case "void":
		return kVoid
	default:
		return kInt
	}
}

func (p *parser) parseType() (*Type, error) {
	t, err := p.expect("IDENT")
	if err != nil {
		return nil, err
	}
	base := t.sval
	var ty *Type
	switch {
	case base == "unsigned":
		if p.peek().kind == "IDENT" {
			nv := p.peek().sval
			if nv == "char" || nv == "short" || nv == "int" || nv == "long" {
				p.next()
			}
		}
		ty = scalarT(kInt)
	case p.enumTypes[base]:
		// 枚举类型名等价于 int (成员是编译期整数常量)
		ty = scalarT(kInt)
	case base == "int" || base == "float" || base == "bool" || base == "string" || base == "void":
		ty = scalarT(typeKindFromName(base))
	case base == "char" || base == "short" || base == "long":
		ty = scalarT(kInt)
	default:
		ty = &Type{Kind: kStruct, Name: base}
	}
	for p.peek().kind == "LBRACKET" && p.peekN(1).kind == "RBRACKET" {
		p.next()
		p.next()
		ty = &Type{Kind: kPtrArray, Elem: ty}
	}
	return ty, nil
}

func (p *parser) isDeclStart() bool {
	t := p.peek()
	if t.kind != "IDENT" {
		return false
	}
	v := t.sval
	if baseTypeWords[v] {
		return true
	}
	return !keywords[v] && p.peekN(1).kind == "IDENT"
}

func (p *parser) parseDims() ([]int, error) {
	var dims []int
	for p.peek().kind == "LBRACKET" {
		p.next()
		num := 0
		if p.peek().kind == "NUMBER" {
			num = int(p.next().ival)
		}
		if _, err := p.expect("RBRACKET"); err != nil {
			return nil, err
		}
		dims = append(dims, num)
	}
	return dims, nil
}

// ---------------- 程序 ----------------

func (p *parser) parseProgram() (map[string]*StructDef, []*GlobalVar, map[string]*FuncDef, []string, map[string]int64, error) {
	structs := map[string]*StructDef{}
	var globals []*GlobalVar
	functions := map[string]*FuncDef{}
	var funcOrder []string

	p.skipNL()
	for p.peek().kind != "EOF" {
		if p.peek().kind == "IDENT" && p.peek().sval == "struct" {
			if err := p.parseStruct(structs); err != nil {
				return nil, nil, nil, nil, nil, err
			}
		} else if p.peek().kind == "IDENT" && p.peek().sval == "enum" {
			if err := p.parseEnum(); err != nil {
				return nil, nil, nil, nil, nil, err
			}
		} else if p.peek().kind == "IDENT" && p.peek().sval == "function" {
			f, err := p.parseFunction()
			if err != nil {
				return nil, nil, nil, nil, nil, err
			}
			functions[f.name] = f
			funcOrder = append(funcOrder, f.name)
		} else {
			gs, err := p.parseGlobal()
			if err != nil {
				return nil, nil, nil, nil, nil, err
			}
			globals = append(globals, gs...)
		}
		p.skipNL()
	}
	return structs, globals, functions, funcOrder, p.enums, nil
}

// ---------------- 枚举 ----------------

// parseEnum 解析 `enum Name { A, B = 3, C }` —— 成员为编译期整数常量 (自动递增)。
func (p *parser) parseEnum() error {
	tok := p.next() // enum
	nameT, err := p.expect("IDENT")
	if err != nil {
		return err
	}
	name := nameT.sval
	if keywords[name] || p.enumTypes[name] {
		return fmt.Errorf("Invalid enum name: %s at %s", name, p.locTok(tok))
	}
	if _, err := p.expect("LBRACE"); err != nil {
		return err
	}
	p.skipNL()
	// 与 Python 一致: 用任意精度整数累加, 越界在写入成员表前判定。
	value := big.NewInt(0)
	count := 0
	for p.peek().kind != "RBRACE" && p.peek().kind != "EOF" {
		p.skipNL()
		if p.peek().kind == "RBRACE" {
			break
		}
		mtok := p.peek()
		mnameT, err := p.expect("IDENT")
		if err != nil {
			return err
		}
		mname := mnameT.sval
		if _, dup := p.enums[mname]; dup {
			return fmt.Errorf("Duplicate enum member: %s at %s", mname, p.locTok(mtok))
		}
		if p.accept("ASSIGN") {
			node, err := p.parseExpr()
			if err != nil {
				return err
			}
			value, err = p.evalEnumConst(node, mname)
			if err != nil {
				return err
			}
		}
		if !value.IsInt64() {
			return fmt.Errorf("Enum member %s out of 64-bit range at %s",
				mname, p.locTok(mtok))
		}
		p.enums[mname] = value.Int64()
		value = new(big.Int).Add(value, big.NewInt(1))
		count++
		if !p.accept("COMMA") {
			break
		}
		p.skipNL()
	}
	if count == 0 {
		return fmt.Errorf("Empty enum %s at %s", name, p.locTok(tok))
	}
	p.skipNL() // 最后一个成员无尾逗号时, 先跳过换行再收 RBRACE
	if _, err := p.expect("RBRACE"); err != nil {
		return err
	}
	p.accept("SEMI")
	p.enumTypes[name] = true
	return nil
}

// evalEnumConst 求值枚举成员初始值: 整数常量表达式 (可引用已定义的枚举成员)。
// 使用 big.Int 以匹配 Python 侧任意精度算术 (溢出只在最后统一判定)。
func (p *parser) evalEnumConst(n *Node, mname string) (*big.Int, error) {
	switch n.Kind {
	case "num":
		if !n.IsFloat {
			return big.NewInt(n.Ival), nil
		}
	case "var":
		if v, ok := p.enums[n.Name]; ok {
			return big.NewInt(v), nil
		}
		return nil, fmt.Errorf("Enum member %s initializer must be an integer "+
			"constant (unknown name: %s) at %s", mname, n.Name, p.loc())
	case "neg":
		v, err := p.evalEnumConst(n.A, mname)
		if err != nil {
			return nil, err
		}
		return new(big.Int).Neg(v), nil
	case "bitnot":
		v, err := p.evalEnumConst(n.A, mname)
		if err != nil {
			return nil, err
		}
		return new(big.Int).Not(v), nil
	case "binop":
		switch n.Op {
		case "+", "-", "*", "/", "%", "<<", ">>", "&", "|", "^":
		default:
			return nil, fmt.Errorf("Enum member %s initializer must be an "+
				"integer constant expression at %s", mname, p.loc())
		}
		a, err := p.evalEnumConst(n.A, mname)
		if err != nil {
			return nil, err
		}
		b, err := p.evalEnumConst(n.B, mname)
		if err != nil {
			return nil, err
		}
		if (n.Op == "/" || n.Op == "%") && b.Sign() == 0 {
			return nil, fmt.Errorf("Enum member %s initializer divides by zero", mname)
		}
		switch n.Op {
		case "+":
			return new(big.Int).Add(a, b), nil
		case "-":
			return new(big.Int).Sub(a, b), nil
		case "*":
			return new(big.Int).Mul(a, b), nil
		case "/":
			// Quo 向零截断, 与 DIV 指令/Python _trunc_div 一致
			return new(big.Int).Quo(a, b), nil
		case "%":
			// Rem 取被除数符号, 与 Python a - _trunc_div(a,b)*b 一致
			return new(big.Int).Rem(a, b), nil
		case "&":
			return new(big.Int).And(a, b), nil
		case "|":
			return new(big.Int).Or(a, b), nil
		case "^":
			return new(big.Int).Xor(a, b), nil
		case "<<":
			if !b.IsInt64() || b.Int64() < 0 || b.Int64() > 4096 {
				// 左操作数非零时结果必然超出 64 位 (避免构造超大中间值)
				if a.Sign() == 0 {
					return big.NewInt(0), nil
				}
				return new(big.Int).Lsh(big.NewInt(1), 63), nil
			}
			return new(big.Int).Lsh(a, uint(b.Int64())), nil
		case ">>":
			if !b.IsInt64() || b.Int64() < 0 {
				return nil, fmt.Errorf("Enum member %s initializer must be an "+
					"integer constant expression at %s", mname, p.loc())
			}
			if b.Int64() > 4096 {
				if a.Sign() < 0 {
					return big.NewInt(-1), nil
				}
				return big.NewInt(0), nil
			}
			return new(big.Int).Rsh(a, uint(b.Int64())), nil
		}
	}
	return nil, fmt.Errorf("Enum member %s initializer must be an integer "+
		"constant expression at %s", mname, p.loc())
}

func (p *parser) parseStruct(structs map[string]*StructDef) error {
	p.next() // struct
	nameT, err := p.expect("IDENT")
	if err != nil {
		return err
	}
	if _, err := p.expect("LBRACE"); err != nil {
		return err
	}
	p.skipNL()
	sd := &StructDef{name: nameT.sval, offsets: map[string]int{}}
	off := 0
	for p.peek().kind != "RBRACE" {
		ftype, err := p.parseType()
		if err != nil {
			return err
		}
		fname, err := p.expect("IDENT")
		if err != nil {
			return err
		}
		dims, err := p.parseDims()
		if err != nil {
			return err
		}
		t := ftype
		for i := len(dims) - 1; i >= 0; i-- {
			d := dims[i]
			if d != 0 {
				t = &Type{Kind: kArray, Elem: t, Size: d}
			} else {
				t = &Type{Kind: kPtrArray, Elem: t}
			}
		}
		sd.fields = append(sd.fields, fieldDef{name: fname.sval, t: t})
		sd.offsets[fname.sval] = off
		off += typeSlots(t)
		p.accept("SEMI")
		p.skipNL()
	}
	if _, err := p.expect("RBRACE"); err != nil {
		return err
	}
	sd.sizeSlots = off
	structs[nameT.sval] = sd
	p.accept("SEMI")
	return nil
}

func (p *parser) parseFunction() (*FuncDef, error) {
	line := p.next().line // function
	nameT, err := p.expect("IDENT")
	if err != nil {
		return nil, err
	}
	if _, err := p.expect("LPAREN"); err != nil {
		return nil, err
	}
	var params []paramDef
	for p.peek().kind != "RPAREN" {
		ptype, err := p.parseType()
		if err != nil {
			return nil, err
		}
		pname, err := p.expect("IDENT")
		if err != nil {
			return nil, err
		}
		dims, err := p.parseDims()
		if err != nil {
			return nil, err
		}
		for i := len(dims) - 1; i >= 0; i-- {
			d := dims[i]
			if d != 0 {
				ptype = &Type{Kind: kArray, Elem: ptype, Size: d}
			} else {
				ptype = &Type{Kind: kPtrArray, Elem: ptype}
			}
		}
		params = append(params, paramDef{name: pname.sval, t: ptype})
		if !p.accept("COMMA") {
			break
		}
	}
	if _, err := p.expect("RPAREN"); err != nil {
		return nil, err
	}
	retType := scalarT(kVoid)
	if p.accept("ARROW") {
		retType, err = p.parseType()
		if err != nil {
			return nil, err
		}
	}
	if _, err := p.expect("LBRACE"); err != nil {
		return nil, err
	}
	body, err := p.parseBlockBody()
	if err != nil {
		return nil, err
	}
	if _, err := p.expect("RBRACE"); err != nil {
		return nil, err
	}
	return &FuncDef{name: nameT.sval, params: params, retType: retType, body: body, line: line}, nil
}

func (p *parser) parseGlobal() ([]*GlobalVar, error) {
	vtype, err := p.parseType()
	if err != nil {
		return nil, err
	}
	var gs []*GlobalVar
	for {
		name, err := p.expect("IDENT")
		if err != nil {
			return nil, err
		}
		if _, isEnum := p.enums[name.sval]; isEnum || keywords[name.sval] {
			return nil, fmt.Errorf("Name '%s' is already used as an enum member "+
				"or keyword at %s", name.sval, p.locTok(name))
		}
		dims, err := p.parseDims()
		if err != nil {
			return nil, err
		}
		t := vtype
		for i := len(dims) - 1; i >= 0; i-- {
			d := dims[i]
			if d != 0 {
				t = &Type{Kind: kArray, Elem: t, Size: d}
			} else {
				t = &Type{Kind: kPtrArray, Elem: t}
			}
		}
		gv := &GlobalVar{name: name.sval, t: t}
		if p.accept("ASSIGN") {
			if p.peek().kind == "LBRACE" {
				gv.arrayLit, err = p.parseArrayLiteral()
				if err != nil {
					return nil, err
				}
			} else {
				gv.init, err = p.parseExpr()
				if err != nil {
					return nil, err
				}
			}
		}
		gs = append(gs, gv)
		if !p.accept("COMMA") {
			break
		}
	}
	p.accept("SEMI")
	return gs, nil
}

func (p *parser) parseArrayLiteral() (*Node, error) {
	if _, err := p.expect("LBRACE"); err != nil {
		return nil, err
	}
	var rows [][]*Node
	var current []*Node
	for p.peek().kind != "RBRACE" {
		if p.peek().kind == "LBRACE" {
			p.next()
			var inner []*Node
			for p.peek().kind != "RBRACE" {
				e, err := p.parseExpr()
				if err != nil {
					return nil, err
				}
				inner = append(inner, e)
				if !p.accept("COMMA") {
					break
				}
			}
			if _, err := p.expect("RBRACE"); err != nil {
				return nil, err
			}
			rows = append(rows, inner)
		} else {
			e, err := p.parseExpr()
			if err != nil {
				return nil, err
			}
			current = append(current, e)
		}
		if !p.accept("COMMA") {
			break
		}
	}
	if _, err := p.expect("RBRACE"); err != nil {
		return nil, err
	}
	if len(rows) > 0 {
		return &Node{Kind: "arraylit", ArrayLit: rows, Is2D: true}, nil
	}
	return &Node{Kind: "arraylit", ArrayLit: [][]*Node{current}, Is2D: false}, nil
}

// ---------------- 语句 ----------------

func (p *parser) parseBlockBody() ([]*Node, error) {
	var stmts []*Node
	p.skipNL()
	for p.peek().kind != "RBRACE" && p.peek().kind != "EOF" {
		s, err := p.parseStmt()
		if err != nil {
			return nil, err
		}
		stmts = append(stmts, s)
		p.skipNL()
	}
	return stmts, nil
}

var cpuStmtWords = map[string]bool{
	"set": true, "add": true, "subtract": true, "multiply": true,
	"divide": true, "increment": true, "decrement": true,
}

func (p *parser) parseStmt() (*Node, error) {
	t := p.peek()
	switch {
	case t.kind == "LBRACE":
		p.next()
		body, err := p.parseBlockBody()
		if err != nil {
			return nil, err
		}
		if _, err := p.expect("RBRACE"); err != nil {
			return nil, err
		}
		return &Node{Kind: "block", List: body}, nil
	case t.kind == "IDENT" && t.sval == "assert":
		return p.parseAssert()
	case t.kind == "IDENT" && t.sval == "return":
		p.next()
		var expr *Node
		var err error
		if p.peek().kind != "NL" && p.peek().kind != "SEMI" && p.peek().kind != "RBRACE" {
			expr, err = p.parseExpr()
			if err != nil {
				return nil, err
			}
		}
		p.accept("SEMI")
		return &Node{Kind: "return", A: expr}, nil
	case t.kind == "IDENT" && t.sval == "if":
		return p.parseIf()
	case t.kind == "IDENT" && t.sval == "while":
		p.next()
		cond, err := p.parseParenExpr()
		if err != nil {
			return nil, err
		}
		body, err := p.parseStmt()
		if err != nil {
			return nil, err
		}
		return &Node{Kind: "while", A: cond, B: body}, nil
	case t.kind == "IDENT" && t.sval == "for":
		return p.parseFor()
	case t.kind == "IDENT" && t.sval == "do":
		return p.parseDo()
	case t.kind == "IDENT" && t.sval == "switch":
		return p.parseSwitch()
	case t.kind == "IDENT" && t.sval == "break":
		p.next()
		p.accept("SEMI")
		return &Node{Kind: "break"}, nil
	case t.kind == "IDENT" && t.sval == "continue":
		p.next()
		p.accept("SEMI")
		return &Node{Kind: "continue"}, nil
	case p.isDeclStart():
		return p.parseDecl(false)
	case t.kind == "IDENT" && cpuStmtWords[t.sval]:
		return p.parseCpuStmt()
	default:
		expr, err := p.parseAssign()
		if err != nil {
			return nil, err
		}
		p.accept("SEMI")
		return &Node{Kind: "expr", A: expr}, nil
	}
}

func (p *parser) parseAssign() (*Node, error) {
	target, err := p.parseExpr()
	if err != nil {
		return nil, err
	}
	t := p.peek()
	if t.kind == "ASSIGN" {
		p.next()
		value, err := p.parseAssign()
		if err != nil {
			return nil, err
		}
		return &Node{Kind: "binop", Op: "=", A: target, B: value}, nil
	}
	if op, ok := compoundAssign[t.kind]; ok {
		p.next()
		value, err := p.parseAssign()
		if err != nil {
			return nil, err
		}
		return &Node{Kind: "binop", Op: op, A: target, B: value}, nil
	}
	return target, nil
}

func (p *parser) parseDo() (*Node, error) {
	p.next() // do
	body, err := p.parseStmt()
	if err != nil {
		return nil, err
	}
	p.skipNL()
	if !(p.peek().kind == "IDENT" && p.peek().sval == "while") {
		return nil, fmt.Errorf("Expected 'while' after do body at %s", p.loc())
	}
	p.next()
	cond, err := p.parseParenExpr()
	if err != nil {
		return nil, err
	}
	p.accept("SEMI")
	return &Node{Kind: "dowhile", A: body, B: cond}, nil
}

func (p *parser) parseSwitch() (*Node, error) {
	p.next() // switch
	cond, err := p.parseParenExpr()
	if err != nil {
		return nil, err
	}
	if _, err := p.expect("LBRACE"); err != nil {
		return nil, err
	}
	p.skipNL()
	var branches []*Node
	var cur *Node
	for p.peek().kind != "RBRACE" && p.peek().kind != "EOF" {
		p.skipNL()
		t := p.peek()
		if t.kind == "IDENT" && t.sval == "case" {
			if cur != nil {
				branches = append(branches, cur)
			}
			p.next()
			alts, err := p.parseCaseAlts()
			if err != nil {
				return nil, err
			}
			if _, err := p.expect("COLON"); err != nil {
				return nil, err
			}
			cur = &Node{Kind: "case", Alts: alts}
		} else if t.kind == "IDENT" && t.sval == "default" {
			if cur != nil {
				branches = append(branches, cur)
			}
			p.next()
			if _, err := p.expect("COLON"); err != nil {
				return nil, err
			}
			cur = &Node{Kind: "case"}
		} else {
			if cur == nil {
				return nil, fmt.Errorf("Statement before first case in switch at %s", p.loc())
			}
			s, err := p.parseStmt()
			if err != nil {
				return nil, err
			}
			cur.List = append(cur.List, s)
		}
		p.skipNL()
	}
	if cur != nil {
		branches = append(branches, cur)
	}
	if _, err := p.expect("RBRACE"); err != nil {
		return nil, err
	}
	if len(branches) == 0 {
		return nil, fmt.Errorf("Empty switch at %s", p.loc())
	}
	return &Node{Kind: "switch", A: cond, List: branches}, nil
}

// parseCaseAlts 解析 case 值列表: 单值 / 范围 1..5 / 多值 1, 2, 7..9 (逗号分隔)。
func (p *parser) parseCaseAlts() ([]*Node, error) {
	var alts []*Node
	for {
		low, err := p.parseExpr()
		if err != nil {
			return nil, err
		}
		if p.accept("DOTDOT") {
			high, err := p.parseExpr()
			if err != nil {
				return nil, err
			}
			alts = append(alts, &Node{Kind: "caserange", A: low, B: high})
		} else {
			alts = append(alts, &Node{Kind: "caseval", A: low})
		}
		if !p.accept("COMMA") {
			break
		}
	}
	return alts, nil
}

func (p *parser) parseAssert() (*Node, error) {
	tok := p.peek()
	p.next() // assert
	if _, err := p.expect("LPAREN"); err != nil {
		return nil, err
	}
	cond, err := p.parseExpr()
	if err != nil {
		return nil, err
	}
	var msg *Node
	if p.accept("COMMA") {
		msg, err = p.parseExpr()
		if err != nil {
			return nil, err
		}
	}
	if _, err := p.expect("RPAREN"); err != nil {
		return nil, err
	}
	p.accept("SEMI")
	return &Node{Kind: "assert", A: cond, B: msg, Int: tok.line, Filename: tok.filename}, nil
}

func (p *parser) parseIf() (*Node, error) {
	p.next() // if
	cond, err := p.parseParenExpr()
	if err != nil {
		return nil, err
	}
	thenBody, err := p.parseStmt()
	if err != nil {
		return nil, err
	}
	var elseBody *Node
	p.skipNL()
	if p.peek().kind == "IDENT" && p.peek().sval == "else" {
		p.next()
		p.skipNL()
		elseBody, err = p.parseStmt()
		if err != nil {
			return nil, err
		}
	}
	return &Node{Kind: "if", A: cond, B: thenBody, C: elseBody}, nil
}

func (p *parser) parseFor() (*Node, error) {
	p.next() // for
	if _, err := p.expect("LPAREN"); err != nil {
		return nil, err
	}
	if p.isRangeFor() {
		return p.parseRangeFor()
	}
	var init *Node
	if p.peek().kind != "SEMI" {
		if p.isDeclStart() {
			init, _ = p.parseDecl(true)
		} else {
			// 允许赋值表达式作初始化子句: for (i = 0; ...)
			e, err := p.parseAssign()
			if err != nil {
				return nil, err
			}
			init = &Node{Kind: "expr", A: e}
		}
	}
	if _, err := p.expect("SEMI"); err != nil {
		return nil, err
	}
	var cond *Node
	if p.peek().kind != "SEMI" {
		var err error
		cond, err = p.parseExpr()
		if err != nil {
			return nil, err
		}
	}
	if _, err := p.expect("SEMI"); err != nil {
		return nil, err
	}
	var update *Node
	if p.peek().kind != "RPAREN" {
		e, err := p.parseAssign()
		if err != nil {
			return nil, err
		}
		update = &Node{Kind: "expr", A: e}
	}
	if _, err := p.expect("RPAREN"); err != nil {
		return nil, err
	}
	body, err := p.parseStmt()
	if err != nil {
		return nil, err
	}
	return &Node{Kind: "for", A: init, B: cond, C: update, D: body}, nil
}

// isRangeFor 判定 for (T v : arr) 形式 (LPAREN 已消费)。
func (p *parser) isRangeFor() bool {
	t := p.peek()
	if t.kind != "IDENT" {
		return false
	}
	v := t.sval
	if v == "unsigned" {
		n1, n2, n3 := p.peekN(1), p.peekN(2), p.peekN(3)
		return n1.kind == "IDENT" &&
			(n1.sval == "char" || n1.sval == "short" || n1.sval == "int" || n1.sval == "long") &&
			n2.kind == "IDENT" && n3.kind == "COLON"
	}
	if baseTypeWords[v] {
		return p.peekN(1).kind == "IDENT" && p.peekN(2).kind == "COLON"
	}
	if keywords[v] {
		return false
	}
	return p.peekN(1).kind == "IDENT" && p.peekN(2).kind == "COLON"
}

// parseRangeFor 解析 for (T v : arr) —— 遍历定长数组元素 (元素类型为标量/struct)。
func (p *parser) parseRangeFor() (*Node, error) {
	elemType, err := p.parseType()
	if err != nil {
		return nil, err
	}
	vname, err := p.expect("IDENT")
	if err != nil {
		return nil, err
	}
	if _, isEnum := p.enums[vname.sval]; isEnum {
		return nil, fmt.Errorf("Invalid range-for variable: %s at %s", vname.sval, p.loc())
	}
	if _, err := p.expect("COLON"); err != nil {
		return nil, err
	}
	arr, err := p.parseExpr()
	if err != nil {
		return nil, err
	}
	if _, err := p.expect("RPAREN"); err != nil {
		return nil, err
	}
	body, err := p.parseStmt()
	if err != nil {
		return nil, err
	}
	p.rangeID++
	return &Node{Kind: "rangefor", Name: vname.sval, Type: elemType, A: arr, D: body, RangeID: p.rangeID}, nil
}

func (p *parser) parseDecl(noSemi bool) (*Node, error) {
	vtype, err := p.parseType()
	if err != nil {
		return nil, err
	}
	var items []*Node
	for {
		name, err := p.expect("IDENT")
		if err != nil {
			return nil, err
		}
		dims, err := p.parseDims()
		if err != nil {
			return nil, err
		}
		t := vtype
		for i := len(dims) - 1; i >= 0; i-- {
			d := dims[i]
			if d != 0 {
				t = &Type{Kind: kArray, Elem: t, Size: d}
			} else {
				t = &Type{Kind: kPtrArray, Elem: t}
			}
		}
		var init, arrayLit *Node
		if p.accept("ASSIGN") {
			if p.peek().kind == "LBRACE" {
				arrayLit, err = p.parseArrayLiteral()
				if err != nil {
					return nil, err
				}
			} else {
				init, err = p.parseExpr()
				if err != nil {
					return nil, err
				}
			}
		}
		items = append(items, &Node{Kind: "declitem", Name: name.sval, Type: t, A: init, B: arrayLit})
		if !p.accept("COMMA") {
			break
		}
	}
	if !noSemi {
		p.accept("SEMI")
	}
	return &Node{Kind: "decl", List: items}, nil
}

func (p *parser) parseCpuStmt() (*Node, error) {
	op := p.next().sval
	var operands [][2]string
	for p.peek().kind != "NL" && p.peek().kind != "SEMI" && p.peek().kind != "EOF" {
		tok := p.next()
		val := tok.sval
		if tok.kind == "NUMBER" {
			val = strconv.FormatInt(tok.ival, 10)
		} else if tok.kind == "FLOAT" {
			val = strconv.FormatFloat(tok.fval, 'g', -1, 64)
		}
		operands = append(operands, [2]string{tok.kind, val})
	}
	p.accept("SEMI")
	return &Node{Kind: "cpu", Name: op, Operands: operands}, nil
}

func (p *parser) parseParenExpr() (*Node, error) {
	if _, err := p.expect("LPAREN"); err != nil {
		return nil, err
	}
	e, err := p.parseExpr()
	if err != nil {
		return nil, err
	}
	if _, err := p.expect("RPAREN"); err != nil {
		return nil, err
	}
	return e, nil
}

// ---------------- 表达式 ----------------

func (p *parser) parseExpr() (*Node, error) {
	e, err := p.parseOr()
	if err != nil {
		return nil, err
	}
	if p.accept("QUESTION") {
		a, err := p.parseAssign()
		if err != nil {
			return nil, err
		}
		if _, err := p.expect("COLON"); err != nil {
			return nil, err
		}
		b, err := p.parseAssign()
		if err != nil {
			return nil, err
		}
		return &Node{Kind: "cond", A: e, B: a, C: b}, nil
	}
	return e, nil
}

func (p *parser) parseOr() (*Node, error) {
	left, err := p.parseAnd()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "OR" {
		p.next()
		right, err := p.parseAnd()
		if err != nil {
			return nil, err
		}
		left = &Node{Kind: "binop", Op: "||", A: left, B: right}
	}
	return left, nil
}

func (p *parser) parseAnd() (*Node, error) {
	left, err := p.parseBitOr()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "AND" {
		p.next()
		right, err := p.parseBitOr()
		if err != nil {
			return nil, err
		}
		left = &Node{Kind: "binop", Op: "&&", A: left, B: right}
	}
	return left, nil
}

func (p *parser) parseBitOr() (*Node, error) {
	left, err := p.parseBitXor()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "PIPE" {
		p.next()
		right, err := p.parseBitXor()
		if err != nil {
			return nil, err
		}
		left = &Node{Kind: "binop", Op: "|", A: left, B: right}
	}
	return left, nil
}

func (p *parser) parseBitXor() (*Node, error) {
	left, err := p.parseBitAnd()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "CARET" {
		p.next()
		right, err := p.parseBitAnd()
		if err != nil {
			return nil, err
		}
		left = &Node{Kind: "binop", Op: "^", A: left, B: right}
	}
	return left, nil
}

func (p *parser) parseBitAnd() (*Node, error) {
	left, err := p.parseEquality()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "AMP" {
		p.next()
		right, err := p.parseEquality()
		if err != nil {
			return nil, err
		}
		left = &Node{Kind: "binop", Op: "&", A: left, B: right}
	}
	return left, nil
}

func (p *parser) parseEquality() (*Node, error) {
	left, err := p.parseRelational()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "EQ" || p.peek().kind == "NEQ" {
		op := "=="
		if p.next().kind == "NEQ" {
			op = "!="
		}
		right, err := p.parseRelational()
		if err != nil {
			return nil, err
		}
		left = &Node{Kind: "binop", Op: op, A: left, B: right}
	}
	return left, nil
}

func (p *parser) parseRelational() (*Node, error) {
	left, err := p.parseShift()
	if err != nil {
		return nil, err
	}
	opMap := map[string]string{"LT": "<", "GT": ">", "LE": "<=", "GE": ">="}
	for {
		op, ok := opMap[p.peek().kind]
		if !ok {
			break
		}
		p.next()
		right, err := p.parseShift()
		if err != nil {
			return nil, err
		}
		left = &Node{Kind: "binop", Op: op, A: left, B: right}
	}
	return left, nil
}

func (p *parser) parseShift() (*Node, error) {
	left, err := p.parseAdditive()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "SHL" || p.peek().kind == "SHR" {
		op := "<<"
		if p.next().kind == "SHR" {
			op = ">>"
		}
		right, err := p.parseAdditive()
		if err != nil {
			return nil, err
		}
		left = &Node{Kind: "binop", Op: op, A: left, B: right}
	}
	return left, nil
}

func (p *parser) parseAdditive() (*Node, error) {
	left, err := p.parseMultiplicative()
	if err != nil {
		return nil, err
	}
	for p.peek().kind == "PLUS" || p.peek().kind == "MINUS" {
		op := "+"
		if p.next().kind == "MINUS" {
			op = "-"
		}
		right, err := p.parseMultiplicative()
		if err != nil {
			return nil, err
		}
		left = &Node{Kind: "binop", Op: op, A: left, B: right}
	}
	return left, nil
}

func (p *parser) parseMultiplicative() (*Node, error) {
	left, err := p.parseUnary()
	if err != nil {
		return nil, err
	}
	opMap := map[string]string{"STAR": "*", "SLASH": "/", "PERCENT": "%"}
	for {
		op, ok := opMap[p.peek().kind]
		if !ok {
			break
		}
		p.next()
		right, err := p.parseUnary()
		if err != nil {
			return nil, err
		}
		left = &Node{Kind: "binop", Op: op, A: left, B: right}
	}
	return left, nil
}

func (p *parser) parseUnary() (*Node, error) {
	switch p.peek().kind {
	case "BANG":
		p.next()
		x, err := p.parseUnary()
		if err != nil {
			return nil, err
		}
		return &Node{Kind: "not", A: x}, nil
	case "TILDE":
		p.next()
		x, err := p.parseUnary()
		if err != nil {
			return nil, err
		}
		return &Node{Kind: "bitnot", A: x}, nil
	case "MINUS":
		p.next()
		x, err := p.parseUnary()
		if err != nil {
			return nil, err
		}
		return &Node{Kind: "neg", A: x}, nil
	case "INC":
		p.next()
		x, err := p.parseUnary()
		if err != nil {
			return nil, err
		}
		return &Node{Kind: "preinc", A: x}, nil
	case "DEC":
		p.next()
		x, err := p.parseUnary()
		if err != nil {
			return nil, err
		}
		return &Node{Kind: "predec", A: x}, nil
	}
	return p.parsePostfix()
}

func (p *parser) parsePostfix() (*Node, error) {
	e, err := p.parsePrimary()
	if err != nil {
		return nil, err
	}
	for {
		switch p.peek().kind {
		case "LBRACKET":
			p.next()
			idx, err := p.parseExpr()
			if err != nil {
				return nil, err
			}
			if _, err := p.expect("RBRACKET"); err != nil {
				return nil, err
			}
			e = &Node{Kind: "index", A: e, B: idx}
		case "DOT":
			p.next()
			name, err := p.expect("IDENT")
			if err != nil {
				return nil, err
			}
			e = &Node{Kind: "member", A: e, Name: name.sval}
		case "INC":
			p.next()
			e = &Node{Kind: "postinc", A: e}
		case "DEC":
			p.next()
			e = &Node{Kind: "postdec", A: e}
		default:
			return e, nil
		}
	}
}

func (p *parser) parsePrimary() (*Node, error) {
	t := p.peek()
	switch t.kind {
	case "NUMBER":
		p.next()
		return &Node{Kind: "num", Ival: t.ival}, nil
	case "FLOAT":
		p.next()
		return &Node{Kind: "num", Num: t.fval, IsFloat: true}, nil
	case "STRING":
		p.next()
		return &Node{Kind: "str", Str: t.sval}, nil
	case "IDENT":
		if t.sval == "true" {
			p.next()
			return &Node{Kind: "bool", Bool: true}, nil
		}
		if t.sval == "false" {
			p.next()
			return &Node{Kind: "bool", Bool: false}, nil
		}
	case "LPAREN":
		return p.parseParenExpr()
	}
	if t.kind == "IDENT" {
		name := p.next().sval
		if p.peek().kind == "LPAREN" {
			p.next()
			var args []*Node
			for p.peek().kind != "RPAREN" {
				a, err := p.parseExpr()
				if err != nil {
					return nil, err
				}
				args = append(args, a)
				if !p.accept("COMMA") {
					break
				}
			}
			if _, err := p.expect("RPAREN"); err != nil {
				return nil, err
			}
			return &Node{Kind: "call", Name: name, List: args}, nil
		}
		return &Node{Kind: "var", Name: name}, nil
	}
	return nil, fmt.Errorf("Unexpected token %s (%q) at %s", t.kind, t.sval, p.loc())
}
