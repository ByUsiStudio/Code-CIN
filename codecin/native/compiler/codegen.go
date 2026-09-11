package compiler

import (
	"encoding/binary"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"codecin-native/ir"
)

// ---------------- 代码生成 ----------------

func (c *compiler) emit(op string, args ...ir.Operand) {
	c.res.Instructions = append(c.res.Instructions, ir.Instr{Op: op, Args: args})
}

func (c *compiler) label(name string) {
	c.res.Labels[name] = len(c.res.Instructions)
}

func (c *compiler) newLabel(hint string) string {
	c.labelCounter++
	return fmt.Sprintf("_%s_%d", hint, c.labelCounter)
}

func (c *compiler) reg(n int) ir.Operand { return ir.Reg(n) }
func (c *compiler) imm(v int64) ir.Operand { return ir.Imm(v) }
func (c *compiler) lab(name string) ir.Operand { return ir.Label(name) }

// ---------------- 数据段 ----------------

func (c *compiler) allocData(nbytes, align int) int {
	if align > 1 {
		c.dataPtr = (c.dataPtr + align - 1) &^ (align - 1)
	}
	addr := c.dataPtr
	c.dataPtr += nbytes
	return addr
}

func (c *compiler) dataString(text string) int {
	if addr, ok := c.heapStr[text]; ok {
		return addr
	}
	data := append([]byte(text), 0)
	addr := c.allocData(len(data), 1)
	c.res.DataWrites = append(c.res.DataWrites, ir.DataWrite{Addr: addr, Data: data})
	c.res.DataLabels[fmt.Sprintf("str_%x", addr)] = addr
	c.heapStr[text] = addr
	return addr
}

func (c *compiler) dataQword(value int64) int {
	addr := c.allocData(8, 8)
	var b [8]byte
	binary.LittleEndian.PutUint64(b[:], uint64(value))
	c.res.DataWrites = append(c.res.DataWrites, ir.DataWrite{Addr: addr, Data: b[:]})
	return addr
}

func (c *compiler) layoutGlobals(globals []*GlobalVar) {
	for _, gv := range globals {
		t := gv.t
		if isFixedArray(t) {
			addr := c.allocData(typeSlots(t)*8, 8)
			c.globalsSym[gv.name] = globalVar{t: t, addr: addr, isBlock: true}
			gv.addr = addr
		} else {
			addr := c.allocData(8, 8)
			c.globalsSym[gv.name] = globalVar{t: t, addr: addr, isBlock: false}
			gv.addr = addr
			if isStruct(t) {
				sd := c.structs[t.Name]
				block := c.allocData(sd.sizeSlots*8, 8)
				var b [8]byte
				binary.LittleEndian.PutUint64(b[:], uint64(block))
				c.res.DataWrites = append(c.res.DataWrites, ir.DataWrite{Addr: addr, Data: b[:]})
			}
		}
	}
}

// constValue 编译期常量求值, 返回 (类型, 原始位/地址)。
func (c *compiler) constValue(n *Node) (*Type, int64, error) {
	switch n.Kind {
	case "num":
		if n.IsFloat {
			return scalarT(kFloat), int64(math.Float64bits(n.Num)), nil
		}
		return scalarT(kInt), n.Ival, nil
	case "bool":
		v := int64(0)
		if n.Bool {
			v = 1
		}
		return scalarT(kBool), v, nil
	case "str":
		return scalarT(kString), int64(c.dataString(n.Str)), nil
	case "neg":
		inner := n.A
		if inner.Kind == "num" {
			if inner.IsFloat {
				return scalarT(kFloat), int64(math.Float64bits(-inner.Num)), nil
			}
			return scalarT(kInt), -inner.Ival, nil
		}
	case "bitnot":
		ct, raw, err := c.constValue(n.A)
		if err != nil {
			return nil, 0, err
		}
		if ct.Kind != kInt && ct.Kind != kBool {
			return nil, 0, fmt.Errorf("Bitwise NOT requires integer, got: %s", typeName(ct))
		}
		return scalarT(kInt), ^raw, nil
	}
	return nil, 0, fmt.Errorf("Non-constant global initializer: %s", n.Kind)
}

func (c *compiler) emitGlobalsInit(globals []*GlobalVar) {
	for _, gv := range globals {
		addr := c.globalsSym[gv.name].addr
		if gv.init != nil {
			_, raw, err := c.constValue(gv.init)
			if err == nil {
				var b [8]byte
				binary.LittleEndian.PutUint64(b[:], uint64(raw))
				c.res.DataWrites = append(c.res.DataWrites, ir.DataWrite{Addr: addr, Data: b[:]})
			}
		} else if gv.arrayLit != nil {
			lit := gv.arrayLit
			if lit.Is2D {
				for i, row := range lit.ArrayLit {
					for j, elem := range row {
						_, raw, err := c.constValue(elem)
						if err != nil {
							continue
						}
						var b [8]byte
						binary.LittleEndian.PutUint64(b[:], uint64(raw))
						off := addr + (i*len(row)+j)*8
						c.res.DataWrites = append(c.res.DataWrites, ir.DataWrite{Addr: off, Data: b[:]})
					}
				}
			} else {
				for i, elem := range lit.ArrayLit[0] {
					_, raw, err := c.constValue(elem)
					if err != nil {
						continue
					}
					var b [8]byte
					binary.LittleEndian.PutUint64(b[:], uint64(raw))
					c.res.DataWrites = append(c.res.DataWrites, ir.DataWrite{Addr: addr + i*8, Data: b[:]})
				}
			}
		}
	}
}

// ---------------- 函数生成 ----------------

// genFunctionBody 生成函数体 (locals 带名字)。
func (c *compiler) genFunctionBody(f *FuncDef) {
	c.funcDef = f
	c.locals = map[string]localVar{}
	c.breakLbls = nil
	c.continueLbls = nil

	localsInfo := c.prescanLocalsWithNames(f.body)
	off := 0
	for _, lv := range localsInfo {
		slots := typeSlots(lv.t)
		c.locals[lv.name] = localVar{t: lv.t, off: -(off + slots*8), isBlock: isFixedArray(lv.t)}
		off += slots * 8
	}
	c.frameBytes = off

	nargs := len(f.params)
	for k, prm := range f.params {
		poff := 16 + (nargs-1-k)*8
		c.locals[prm.name] = localVar{t: prm.t, off: poff, isBlock: false}
	}

	c.label(f.name)
	c.emit("PUSH", c.reg(29))
	c.emit("MOV", c.reg(29), c.reg(32))
	if c.frameBytes != 0 {
		c.emit("ADDI", c.reg(0), c.reg(32), c.imm(int64(-c.frameBytes)))
		c.emit("MOV", c.reg(32), c.reg(0))
	}

	// struct 局部变量: 堆分配对象
	for _, lv := range c.locals {
		if isStruct(lv.t) && lv.off < 0 {
			sd := c.structs[lv.t.Name]
			c.emit("MOV", c.reg(0), c.imm(int64(sd.sizeSlots*8)))
			c.emit("SYS", c.imm(SysMALLOC))
			c.emit("MOV", c.reg(2), c.reg(0))
			c.addrLocal(lv.off)
			c.emit("SD", c.reg(2), ir.Mem(0, 0))
		}
	}

	c.genStmts(f.body)
	c.epilogue()
}

type namedLocal struct {
	name string
	t    *Type
}

func (c *compiler) prescanLocalsWithNames(body []*Node) []namedLocal {
	var found []namedLocal
	seen := map[string]bool{}
	var walk func([]*Node)
	walk = func(stmts []*Node) {
		for _, s := range stmts {
			switch s.Kind {
			case "decl":
				for _, item := range s.List {
					if seen[item.Name] {
						continue
					}
					seen[item.Name] = true
					found = append(found, namedLocal{name: item.Name, t: item.Type})
				}
			case "block":
				walk(s.List)
			case "if":
				walk([]*Node{s.B})
				if s.C != nil {
					walk([]*Node{s.C})
				}
			case "while":
				walk([]*Node{s.B})
			case "dowhile":
				walk([]*Node{s.A})
			case "switch":
				for _, br := range s.List {
					walk(br.List)
				}
			case "for":
				if s.A != nil && s.A.Kind == "decl" {
					for _, item := range s.A.List {
						if !seen[item.Name] {
							seen[item.Name] = true
							found = append(found, namedLocal{name: item.Name, t: item.Type})
						}
					}
				}
				walk([]*Node{s.D})
			}
		}
	}
	walk(body)
	return found
}

func (c *compiler) epilogue() {
	c.emit("MOV", c.reg(6), c.reg(29))
	c.emit("MOV", c.reg(32), c.reg(6))
	c.emit("POP", c.reg(29))
	c.emit("RET")
}

func (c *compiler) addrLocal(off int) {
	if off == 0 {
		c.emit("MOV", c.reg(0), c.reg(29))
	} else {
		c.emit("ADDI", c.reg(0), c.reg(29), c.imm(int64(off)))
	}
}

func (c *compiler) addrVar(name string) error {
	if lv, ok := c.locals[name]; ok {
		c.addrLocal(lv.off)
		return nil
	}
	if gv, ok := c.globalsSym[name]; ok {
		c.emit("MOV", c.reg(0), c.imm(int64(gv.addr)))
		return nil
	}
	return fmt.Errorf("Undefined variable: %s", name)
}

func (c *compiler) varType(name string) *Type {
	if lv, ok := c.locals[name]; ok {
		return lv.t
	}
	if gv, ok := c.globalsSym[name]; ok {
		return gv.t
	}
	return nil
}

// ---------------- 语句 ----------------

func (c *compiler) genStmts(stmts []*Node) {
	for _, s := range stmts {
		c.genStmt(s)
	}
}

func (c *compiler) genStmt(s *Node) {
	switch s.Kind {
	case "block":
		c.genStmts(s.List)
	case "return":
		if s.A != nil {
			t := c.genValue(s.A)
			c.convert(t, c.funcDef.retType)
		}
		c.epilogue()
	case "if":
		c.genIf(s.A, s.B, s.C)
	case "while":
		c.genWhile(s.A, s.B)
	case "for":
		c.genFor(s.A, s.B, s.C, s.D)
	case "dowhile":
		c.genDowhile(s.A, s.B)
	case "switch":
		c.genSwitch(s.A, s.List)
	case "break":
		if len(c.breakLbls) == 0 {
			// 编译期错误 (parser 未拦), 运行时忽略
			return
		}
		c.emit("JMP", c.lab(c.breakLbls[len(c.breakLbls)-1]))
	case "continue":
		if len(c.continueLbls) == 0 {
			return
		}
		c.emit("JMP", c.lab(c.continueLbls[len(c.continueLbls)-1]))
	case "decl":
		c.genDecl(s.List)
	case "cpu":
		c.genCpuStmt(s.Name, s.Operands)
	case "assert":
		c.genAssert(s.A, s.B, s.Int, s.Filename)
	case "expr":
		c.genExprStmt(s.A)
	}
}

func (c *compiler) genIf(cond, thenBody, elseBody *Node) {
	lElse := c.newLabel("else")
	lEnd := c.newLabel("endif")
	target := lElse
	if elseBody == nil {
		target = lEnd
	}
	c.genCondJumpFalse(cond, target)
	c.genStmt(thenBody)
	if elseBody != nil {
		c.emit("JMP", c.lab(lEnd))
		c.label(lElse)
		c.genStmt(elseBody)
	}
	c.label(lEnd)
}

func (c *compiler) genWhile(cond, body *Node) {
	lStart := c.newLabel("while")
	lEnd := c.newLabel("wend")
	c.label(lStart)
	c.genCondJumpFalse(cond, lEnd)
	c.breakLbls = append(c.breakLbls, lEnd)
	c.continueLbls = append(c.continueLbls, lStart)
	c.genStmt(body)
	c.emit("JMP", c.lab(lStart))
	c.breakLbls = c.breakLbls[:len(c.breakLbls)-1]
	c.continueLbls = c.continueLbls[:len(c.continueLbls)-1]
	c.label(lEnd)
}

func (c *compiler) genFor(init, cond, update, body *Node) {
	if init != nil {
		c.genStmt(init)
	}
	lCond := c.newLabel("forc")
	lUpdate := c.newLabel("foru")
	lEnd := c.newLabel("fore")
	c.label(lCond)
	if cond != nil {
		c.genCondJumpFalse(cond, lEnd)
	}
	c.breakLbls = append(c.breakLbls, lEnd)
	c.continueLbls = append(c.continueLbls, lUpdate)
	c.genStmt(body)
	c.label(lUpdate)
	if update != nil {
		c.genStmt(update)
	}
	c.emit("JMP", c.lab(lCond))
	c.breakLbls = c.breakLbls[:len(c.breakLbls)-1]
	c.continueLbls = c.continueLbls[:len(c.continueLbls)-1]
	c.label(lEnd)
}

func (c *compiler) genDowhile(body, cond *Node) {
	lBody := c.newLabel("dbody")
	lCond := c.newLabel("dcond")
	lEnd := c.newLabel("dend")
	c.label(lBody)
	c.breakLbls = append(c.breakLbls, lEnd)
	c.continueLbls = append(c.continueLbls, lCond)
	c.genStmt(body)
	c.label(lCond)
	c.genCondJumpFalse(cond, lEnd)
	c.emit("JMP", c.lab(lBody))
	c.breakLbls = c.breakLbls[:len(c.breakLbls)-1]
	c.continueLbls = c.continueLbls[:len(c.continueLbls)-1]
	c.label(lEnd)
}

func (c *compiler) genSwitch(cond *Node, branches []*Node) {
	selT := c.exprType(cond)
	if selT == nil || (selT.Kind != kInt && selT.Kind != kBool) {
		// 编译错误: switch 表达式必须为整数
		return
	}
	lEnd := c.newLabel("swend")
	c.breakLbls = append(c.breakLbls, lEnd)
	c.genValue(cond)
	c.emit("PUSH", c.reg(0))

	var labels []struct {
		raw int64
		lbl string
	}
	defaultLbl := ""
	for _, br := range branches {
		if br.A == nil {
			lbl := c.newLabel("swdef")
			defaultLbl = lbl
			labels = append(labels, struct {
				raw int64
				lbl string
			}{-1, lbl})
			continue
		}
		ct, raw, err := c.constValue(br.A)
		if err != nil || (ct.Kind != kInt && ct.Kind != kBool) {
			continue
		}
		labels = append(labels, struct {
			raw int64
			lbl string
		}{raw, c.newLabel("swcase")})
	}

	for _, l := range labels {
		if l.raw < 0 {
			continue
		}
		c.emit("LD", c.reg(0), ir.Mem(32, 0))
		c.emit("MOV", c.reg(1), c.imm(l.raw))
		c.emit("CMP", c.reg(0), c.reg(1))
		c.emit("B", c.lab(l.lbl), ir.Cond("EQ"))
	}
	target := defaultLbl
	if target == "" {
		target = lEnd
	}
	c.emit("JMP", c.lab(target))

	for _, br := range branches {
		c.label(labels[len(labels)-1].lbl) // placeholder, corrected below
		_ = br
		break
	}
	// 修正: 逐分支 label (labels 与 branches 一一对应)
	for i, br := range branches {
		if i < len(labels) {
			c.label(labels[i].lbl)
		}
		c.genStmts(br.List)
	}

	c.label(lEnd)
	c.breakLbls = c.breakLbls[:len(c.breakLbls)-1]
	c.emit("ADDI", c.reg(32), c.reg(32), c.imm(8))
}

func (c *compiler) genDecl(items []*Node) {
	for _, item := range items {
		if item.B != nil { // arrayLit
			if item.B.Is2D {
				c.genInit2dLiteral(item.Name, item.Type, item.B)
			} else {
				c.genInit1dLiteral(item.Name, item.Type, item.B)
			}
			continue
		}
		if item.A != nil { // init
			vt := c.genValue(item.A)
			c.convert(vt, item.Type)
			c.emit("MOV", c.reg(2), c.reg(0))
			_ = c.addrVar(item.Name)
			c.emit("SD", c.reg(2), ir.Mem(0, 0))
		} else if isPtrArray(item.Type) && item.Type.Elem != nil && isPtrArray(item.Type.Elem) {
			c.emit("MOV", c.reg(0), c.imm(64*8))
			c.emit("SYS", c.imm(SysMALLOC))
			c.emit("MOV", c.reg(2), c.reg(0))
			_ = c.addrVar(item.Name)
			c.emit("SD", c.reg(2), ir.Mem(0, 0))
		}
	}
}

func (c *compiler) genInit1dLiteral(name string, t *Type, lit *Node) {
	elemT := scalarT(kInt)
	if isFixedArray(t) {
		elemT = t.Elem
	}
	for i, elem := range lit.ArrayLit[0] {
		vt := c.genValue(elem)
		c.convert(vt, elemT)
		c.emit("MOV", c.reg(2), c.reg(0))
		_ = c.addrVar(name)
		if i != 0 {
			c.emit("ADDI", c.reg(0), c.reg(0), c.imm(int64(i*8)))
		}
		c.emit("SD", c.reg(2), ir.Mem(0, 0))
	}
}

func (c *compiler) genInit2dLiteral(name string, t *Type, lit *Node) {
	_ = t
	nrows := len(lit.ArrayLit)
	ncols := 0
	for _, r := range lit.ArrayLit {
		if len(r) > ncols {
			ncols = len(r)
		}
	}
	c.emit("MOV", c.reg(0), c.imm(int64(nrows*8)))
	c.emit("SYS", c.imm(SysMALLOC))
	c.emit("MOV", c.reg(4), c.reg(0))
	for i, row := range lit.ArrayLit {
		colCnt := ncols
		if colCnt < 1 {
			colCnt = 1
		}
		c.emit("MOV", c.reg(0), c.imm(int64(colCnt*8)))
		c.emit("SYS", c.imm(SysMALLOC))
		c.emit("MOV", c.reg(5), c.reg(0))
		c.emit("MOV", c.reg(0), c.reg(4))
		c.emit("ADDI", c.reg(0), c.reg(0), c.imm(int64(i*8)))
		c.emit("SD", c.reg(5), ir.Mem(0, 0))
		for j, elem := range row {
			vt := c.genValue(elem)
			c.convert(vt, scalarT(kInt))
			c.emit("MOV", c.reg(2), c.reg(0))
			c.emit("MOV", c.reg(0), c.reg(5))
			if j != 0 {
				c.emit("ADDI", c.reg(0), c.reg(0), c.imm(int64(j*8)))
			}
			c.emit("SD", c.reg(2), ir.Mem(0, 0))
		}
	}
	c.emit("MOV", c.reg(2), c.reg(4))
	_ = c.addrVar(name)
	c.emit("SD", c.reg(2), ir.Mem(0, 0))
}

func (c *compiler) genCpuStmt(op string, operands [][2]string) {
	operandValue := func(kind, val string) (int64, string, bool) {
		if kind == "NUMBER" {
			var n int64
			fmt.Sscanf(val, "%d", &n)
			return n, "", true
		}
		if kind == "IDENT" {
			return 0, val, false
		}
		return 0, "", false
	}

	if op == "increment" || op == "decrement" {
		varname := operands[0][1]
		_ = c.addrVar(varname)
		c.emit("LD", c.reg(0), ir.Mem(0, 0))
		if op == "increment" {
			c.emit("INC", c.reg(0))
		} else {
			c.emit("DEC", c.reg(0))
		}
		c.emit("MOV", c.reg(2), c.reg(0))
		_ = c.addrVar(varname)
		c.emit("SD", c.reg(2), ir.Mem(0, 0))
		return
	}

	varname := operands[0][1]
	var rhsVal int64
	rhsIsVar := false
	var rhsVarName string
	if len(operands) > 1 {
		v, name, isNum := operandValue(operands[1][0], operands[1][1])
		if isNum {
			rhsVal = v
		} else {
			rhsIsVar = true
			rhsVarName = name
		}
	}

	if op == "set" {
		if rhsIsVar {
			c.genValue(&Node{Kind: "var", Name: rhsVarName})
		} else {
			c.emit("MOV", c.reg(0), c.imm(rhsVal))
		}
	} else {
		_ = c.addrVar(varname)
		c.emit("LD", c.reg(0), ir.Mem(0, 0))
		if rhsIsVar {
			c.emit("MOV", c.reg(2), c.reg(0))
			c.genValue(&Node{Kind: "var", Name: rhsVarName})
			c.emit("MOV", c.reg(1), c.reg(0))
			c.emit("MOV", c.reg(0), c.reg(2))
		} else {
			c.emit("MOV", c.reg(1), c.imm(rhsVal))
		}
		opMap := map[string]string{"add": "ADD", "subtract": "SUB",
			"multiply": "MUL", "divide": "DIV"}
		if m, ok := opMap[op]; ok {
			c.emit(m, c.reg(0), c.reg(1))
		}
	}
	c.emit("MOV", c.reg(2), c.reg(0))
	_ = c.addrVar(varname)
	c.emit("SD", c.reg(2), ir.Mem(0, 0))
}

func (c *compiler) genExprStmt(node *Node) {
	c.genValue(node)
}

func (c *compiler) runtimeAbort(text string) {
	addr := c.dataString(text)
	c.emit("MOV", c.reg(0), c.imm(int64(addr)))
	c.emit("SYS", c.imm(SysABORT))
}

func (c *compiler) genAssert(cond, msg *Node, line int, filename string) {
	lOk := c.newLabel("assertok")
	c.genCondJumpTrue(cond, lOk)
	if msg == nil {
		fn := filename
		if fn == "" {
			fn = c.filename
		}
		c.runtimeAbort(fmt.Sprintf("%s:%d: assertion failed", fn, line))
	} else {
		fn := filename
		if fn == "" {
			fn = c.filename
		}
		prefix := c.dataString(fmt.Sprintf("[assert %s:%d] ", fn, line))
		c.emit("MOV", c.reg(0), c.imm(int64(prefix)))
		c.emit("PUSH", c.reg(0))
		c.genStringValue(msg)
		c.emit("MOV", c.reg(1), c.reg(0))
		c.emit("POP", c.reg(0))
		c.emit("SYS", c.imm(SysSTR_CONCAT))
		c.emit("SYS", c.imm(SysABORT))
	}
	c.label(lOk)
}

// ---------------- 类型转换 ----------------

func sameType(a, b *Type) bool {
	if a == nil || b == nil {
		return a == b
	}
	if a.Kind != b.Kind {
		return false
	}
	switch a.Kind {
	case kArray:
		return a.Size == b.Size && sameType(a.Elem, b.Elem)
	case kPtrArray:
		return sameType(a.Elem, b.Elem)
	case kStruct:
		return a.Name == b.Name
	}
	return true
}

func (c *compiler) convert(from, to *Type) {
	if to == nil || from == nil || sameType(from, to) {
		return
	}
	if from.Kind == kInt && to.Kind == kFloat {
		c.emit("SYS", c.imm(SysITOF))
	} else if from.Kind == kFloat && to.Kind == kInt {
		c.emit("SYS", c.imm(SysFTOI))
	} else if from.Kind == kBool && (to.Kind == kInt || to.Kind == kFloat) {
		if to.Kind == kFloat {
			c.emit("SYS", c.imm(SysITOF))
		}
	}
	// int->bool, string/struct/array 指针无需转换
}

// ---------------- 表达式 ----------------

func (c *compiler) genValue(n *Node) *Type {
	switch n.Kind {
	case "num":
		if n.IsFloat {
			bits := int64(math.Float64bits(n.Num))
			c.emit("MOV", c.reg(0), c.imm(bits))
			return scalarT(kFloat)
		}
		c.emit("MOV", c.reg(0), c.imm(n.Ival))
		return scalarT(kInt)
	case "bool":
		v := int64(0)
		if n.Bool {
			v = 1
		}
		c.emit("MOV", c.reg(0), c.imm(v))
		return scalarT(kBool)
	case "str":
		c.emit("MOV", c.reg(0), c.imm(int64(c.dataString(n.Str))))
		return scalarT(kString)
	case "var":
		return c.genVarValue(n.Name)
	case "member":
		return c.genMember(n.A, n.Name, false)
	case "index":
		return c.genIndex(n.A, n.B, false)
	case "call":
		return c.genCall(n.Name, n.List)
	case "neg":
		t := c.genValue(n.A)
		c.emit("MOV", c.reg(1), c.reg(0))
		if t.Kind == kFloat {
			c.emit("MOV", c.reg(0), c.imm(0))
			c.emit("SYS", c.imm(SysITOF))
			c.emit("SYS", c.imm(SysFSUB))
		} else {
			c.emit("MOV", c.reg(0), c.imm(0))
			c.emit("SUB", c.reg(0), c.reg(1))
		}
		return t
	case "not":
		c.genValue(n.A)
		c.emit("XORI", c.reg(0), c.reg(0), c.imm(1))
		return scalarT(kBool)
	case "bitnot":
		t := c.genValue(n.A)
		if t.Kind == kFloat || t.Kind == kString {
			// 编译错误
			return scalarT(kInt)
		}
		c.emit("MVN", c.reg(0), c.reg(0))
		return scalarT(kInt)
	case "preinc", "predec", "postinc", "postdec":
		return c.genIncdec(n.Kind, n.A)
	case "cond":
		return c.genTernary(n.A, n.B, n.C)
	case "binop":
		return c.genBinop(n.Op, n.A, n.B)
	}
	return nil
}

func (c *compiler) genVarValue(name string) *Type {
	t := c.varType(name)
	if t == nil {
		return nil
	}
	isBlock := false
	if lv, ok := c.locals[name]; ok {
		isBlock = lv.isBlock
	} else if gv, ok := c.globalsSym[name]; ok {
		isBlock = gv.isBlock
	}
	if isBlock {
		_ = c.addrVar(name)
		return t
	}
	_ = c.addrVar(name)
	c.emit("LD", c.reg(0), ir.Mem(0, 0))
	return t
}

func (c *compiler) genLvalueAddr(n *Node) {
	switch n.Kind {
	case "var":
		_ = c.addrVar(n.Name)
	case "member":
		c.genMember(n.A, n.Name, true)
	case "index":
		c.genIndex(n.A, n.B, true)
	}
}

func (c *compiler) structField(structT *Type, fname string) (*Type, int, bool) {
	sd := c.structs[structT.Name]
	off, ok := sd.offsets[fname]
	if !ok {
		return nil, 0, false
	}
	for _, f := range sd.fields {
		if f.name == fname {
			return f.t, off, true
		}
	}
	return nil, 0, false
}

func (c *compiler) decay(t *Type) *Type {
	if isFixedArray(t) {
		elem := t.Elem
		if isFixedArray(elem) {
			return &Type{Kind: kArray, Elem: elem, Size: t.Size}
		}
		return &Type{Kind: kPtrArray, Elem: elem}
	}
	return t
}

func (c *compiler) genMember(objNode *Node, fname string, lvalue bool) *Type {
	objT := c.genValue(objNode)
	if !isStruct(objT) {
		return nil
	}
	ftype, foff, ok := c.structField(objT, fname)
	if !ok {
		return nil
	}
	if lvalue {
		if foff != 0 {
			c.emit("ADDI", c.reg(0), c.reg(0), c.imm(int64(foff*8)))
		}
		return nil
	}
	if isFixedArray(ftype) {
		if foff != 0 {
			c.emit("ADDI", c.reg(0), c.reg(0), c.imm(int64(foff*8)))
		}
		return c.decay(ftype)
	}
	c.emit("MOV", c.reg(1), c.reg(0))
	if foff != 0 {
		c.emit("ADDI", c.reg(1), c.reg(1), c.imm(int64(foff*8)))
	}
	c.emit("LD", c.reg(0), ir.Mem(1, 0))
	return ftype
}

func (c *compiler) genIndex(baseNode, idxNode *Node, lvalue bool) *Type {
	baseT := c.genValue(baseNode)
	if baseT != nil && baseT.Kind == kString {
		if lvalue {
			return nil // 编译错误: 字符串不可赋值
		}
		c.emit("MOV", c.reg(3), c.reg(0))
		c.genValue(idxNode)
		c.emit("ADD", c.reg(0), c.reg(3))
		c.emit("LB", c.reg(0), ir.Mem(0, 0))
		c.emit("ANDI", c.reg(0), c.reg(0), c.imm(0xFF))
		return scalarT(kInt)
	}
	if baseT == nil || (!isFixedArray(baseT) && !isPtrArray(baseT)) {
		return nil
	}
	elemT := baseT.Elem

	c.emit("MOV", c.reg(3), c.reg(0))
	c.genValue(idxNode)

	if c.bounds && isFixedArray(baseT) {
		size := baseT.Size
		lGe := c.newLabel("bndok")
		c.emit("CMP", c.reg(0), c.imm(0))
		c.emit("B", c.lab(lGe), ir.Cond("GE"))
		c.runtimeAbort("bounds-check: negative array index")
		c.label(lGe)
		lLt := c.newLabel("bndok")
		c.emit("CMP", c.reg(0), c.imm(int64(size)))
		c.emit("B", c.lab(lLt), ir.Cond("LT"))
		c.runtimeAbort(fmt.Sprintf("bounds-check: index >= length (%d)", size))
		c.label(lLt)
	}

	scale := typeSlots(elemT) * 8
	c.emit("MOV", c.reg(1), c.imm(int64(scale)))
	c.emit("MUL", c.reg(0), c.reg(1))
	c.emit("ADD", c.reg(0), c.reg(3))

	if lvalue {
		return nil
	}
	if isFixedArray(elemT) {
		return elemT
	}
	if isPtrArray(elemT) {
		c.emit("MOV", c.reg(5), c.reg(0))
		c.emit("LD", c.reg(1), ir.Mem(5, 0))
		lDone := c.newLabel("rowdone")
		c.emit("CMP", c.reg(1), c.imm(0))
		c.emit("B", c.lab(lDone), ir.Cond("NE"))
		c.emit("MOV", c.reg(0), c.imm(64*8))
		c.emit("SYS", c.imm(SysMALLOC))
		c.emit("MOV", c.reg(1), c.reg(0))
		c.emit("SD", c.reg(1), ir.Mem(5, 0))
		c.label(lDone)
		c.emit("MOV", c.reg(0), c.reg(1))
		return elemT
	}
	c.emit("LD", c.reg(0), ir.Mem(0, 0))
	return elemT
}

func (c *compiler) genAssign(target, valueNode *Node) *Type {
	vt := c.genValue(valueNode)
	tt := c.exprType(target)
	c.convert(vt, tt)
	c.emit("MOV", c.reg(2), c.reg(0))
	c.genLvalueAddr(target)
	c.emit("SD", c.reg(2), ir.Mem(0, 0))
	return tt
}

func (c *compiler) exprType(n *Node) *Type {
	switch n.Kind {
	case "var":
		return c.varType(n.Name)
	case "member":
		objT := c.exprType(n.A)
		if isStruct(objT) {
			ft, _, _ := c.structField(objT, n.Name)
			return ft
		}
	case "index":
		baseT := c.exprType(n.A)
		if baseT != nil && baseT.Kind == kString {
			return scalarT(kInt)
		}
		if isFixedArray(baseT) || isPtrArray(baseT) {
			return baseT.Elem
		}
	case "call":
		if f, ok := c.functions[n.Name]; ok {
			return f.retType
		}
		if n.Name == "min" || n.Name == "max" {
			at := c.exprType(n.List[0])
			bt := c.exprType(n.List[1])
			if at != nil && at.Kind == kFloat || bt != nil && bt.Kind == kFloat {
				return scalarT(kFloat)
			}
			return scalarT(kInt)
		}
		return c.builtinRetType(n.Name)
	case "num":
		if n.IsFloat {
			return scalarT(kFloat)
		}
		return scalarT(kInt)
	case "bool":
		return scalarT(kBool)
	case "str":
		return scalarT(kString)
	case "binop":
		switch n.Op {
		case "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=":
			return c.exprType(n.A)
		case "&", "|", "^", "<<", ">>":
			return scalarT(kInt)
		case "+", "-", "*", "%":
			lt := c.exprType(n.A)
			rt := c.exprType(n.B)
			if lt != nil && lt.Kind == kString || rt != nil && rt.Kind == kString {
				return scalarT(kString)
			}
			if lt != nil && lt.Kind == kFloat || rt != nil && rt.Kind == kFloat {
				return scalarT(kFloat)
			}
			return scalarT(kInt)
		case "/":
			return scalarT(kFloat)
		case "==", "!=", "<", ">", "<=", ">=", "&&", "||":
			return scalarT(kBool)
		}
	case "cond":
		lt := c.exprType(n.B)
		rt := c.exprType(n.C)
		ls := lt != nil && lt.Kind == kString
		rs := rt != nil && rt.Kind == kString
		if ls != rs {
			return scalarT(kInt)
		}
		if ls {
			return scalarT(kString)
		}
		if lt != nil && lt.Kind == kFloat || rt != nil && rt.Kind == kFloat {
			return scalarT(kFloat)
		}
		return scalarT(kInt)
	case "preinc", "predec", "postinc", "postdec":
		return c.exprType(n.A)
	case "neg":
		it := c.exprType(n.A)
		if it != nil && it.Kind == kFloat {
			return scalarT(kFloat)
		}
		return scalarT(kInt)
	case "not":
		return scalarT(kBool)
	case "bitnot":
		return scalarT(kInt)
	}
	return nil
}

// ---------------- 二元运算 ----------------

func (c *compiler) genBinop(op string, left, right *Node) *Type {
	if op == "=" {
		return c.genAssign(left, right)
	}
	if base, ok := compoundToBase[op]; ok {
		return c.genCompound(left, base, right)
	}
	if op == "&&" || op == "||" {
		return c.genLogical(op, left, right)
	}
	if op == "&" || op == "|" || op == "^" || op == "<<" || op == ">>" {
		return c.genBitwise(op, left, right)
	}

	lt := c.exprType(left)
	rt := c.exprType(right)
	if op == "+" && (lt != nil && lt.Kind == kString || rt != nil && rt.Kind == kString) {
		c.genStringValue(left)
		c.emit("PUSH", c.reg(0))
		c.genStringValue(right)
		c.emit("MOV", c.reg(1), c.reg(0))
		c.emit("POP", c.reg(0))
		c.emit("SYS", c.imm(SysSTR_CONCAT))
		return scalarT(kString)
	}

	floatMode := lt != nil && lt.Kind == kFloat || rt != nil && rt.Kind == kFloat || op == "/"
	c.genValue(left)
	if floatMode && lt != nil && lt.Kind == kInt {
		c.emit("SYS", c.imm(SysITOF))
	}
	if floatMode && lt != nil && lt.Kind == kBool {
		c.emit("SYS", c.imm(SysITOF))
	}
	c.emit("PUSH", c.reg(0))
	c.genValue(right)
	if floatMode && rt != nil && (rt.Kind == kInt || rt.Kind == kBool) {
		c.emit("SYS", c.imm(SysITOF))
	}
	c.emit("MOV", c.reg(1), c.reg(0))
	c.emit("POP", c.reg(0))

	if floatMode {
		sysMap := map[string]int64{"+": SysFADD, "-": SysFSUB, "*": SysFMUL, "/": SysFDIV}
		if op == "%" {
			return scalarT(kFloat) // 编译错误: 浮点取模
		}
		c.emit("SYS", c.imm(sysMap[op]))
		return scalarT(kFloat)
	}

	if op == "%" {
		c.emit("MOV", c.reg(2), c.reg(1))
		c.emit("PUSH", c.reg(0))
		c.emit("DIV", c.reg(0), c.reg(2))
		c.emit("MUL", c.reg(0), c.reg(2))
		c.emit("MOV", c.reg(1), c.reg(0))
		c.emit("POP", c.reg(0))
		c.emit("SUB", c.reg(0), c.reg(1))
		return scalarT(kInt)
	}

	opMap := map[string]string{"+": "ADD", "-": "SUB", "*": "MUL"}
	if m, ok := opMap[op]; ok {
		c.emit(m, c.reg(0), c.reg(1))
		return scalarT(kInt)
	}
	return scalarT(kInt)
}

var compoundToBase = map[string]string{
	"+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%",
	"&=": "&", "|=": "|", "^=": "^", "<<=": "<<", ">>=": ">>",
}

func (c *compiler) genLogical(op string, left, right *Node) *Type {
	lTrue := c.newLabel("lt")
	lFalse := c.newLabel("lf")
	lEnd := c.newLabel("le")
	if op == "&&" {
		c.genCondJumpFalse(left, lFalse)
		c.genCondJumpFalse(right, lFalse)
	} else {
		c.genCondJumpTrue(left, lTrue)
		c.genCondJumpTrue(right, lTrue)
		c.emit("JMP", c.lab(lFalse))
	}
	c.label(lTrue)
	c.emit("MOV", c.reg(0), c.imm(1))
	c.emit("JMP", c.lab(lEnd))
	c.label(lFalse)
	c.emit("MOV", c.reg(0), c.imm(0))
	c.label(lEnd)
	return scalarT(kBool)
}

func (c *compiler) genBitwise(op string, left, right *Node) *Type {
	lt := c.exprType(left)
	rt := c.exprType(right)
	if lt != nil && (lt.Kind == kFloat || lt.Kind == kString) ||
		rt != nil && (rt.Kind == kFloat || rt.Kind == kString) {
		return scalarT(kInt) // 编译错误
	}
	c.genValue(left)
	c.emit("PUSH", c.reg(0))
	c.genValue(right)
	c.emit("MOV", c.reg(1), c.reg(0))
	c.emit("POP", c.reg(0))
	switch op {
	case "&":
		c.emit("AND", c.reg(0), c.reg(1))
	case "|":
		c.emit("OR", c.reg(0), c.reg(1))
	case "^":
		c.emit("XOR", c.reg(0), c.reg(1))
	case "<<":
		c.emit("SHL", c.reg(0), c.reg(1))
	case ">>":
		c.emit("ASR", c.reg(0), c.reg(0), c.reg(1))
	}
	return scalarT(kInt)
}

func (c *compiler) genCompound(target *Node, op string, valueNode *Node) *Type {
	tt := c.exprType(target)
	if tt == nil || (tt.Kind != kInt && tt.Kind != kBool && tt.Kind != kFloat) {
		return nil
	}
	if tt.Kind == kFloat && op != "+" && op != "-" && op != "*" && op != "/" {
		return tt
	}
	floatMode := tt.Kind == kFloat

	c.genLvalueAddr(target)
	c.emit("PUSH", c.reg(0))
	vt := c.genValue(valueNode)
	c.convert(vt, tt)
	c.emit("MOV", c.reg(1), c.reg(0))
	c.emit("LD", c.reg(2), ir.Mem(32, 0))
	c.emit("LD", c.reg(0), ir.Mem(2, 0))

	if floatMode {
		fmap := map[string]int64{"+": SysFADD, "-": SysFSUB, "*": SysFMUL, "/": SysFDIV}
		c.emit("SYS", c.imm(fmap[op]))
	} else {
		switch op {
		case "+":
			c.emit("ADD", c.reg(0), c.reg(1))
		case "-":
			c.emit("SUB", c.reg(0), c.reg(1))
		case "*":
			c.emit("MUL", c.reg(0), c.reg(1))
		case "/":
			c.emit("DIV", c.reg(0), c.reg(1))
		case "%":
			c.emit("MOV", c.reg(3), c.reg(1))
			c.emit("PUSH", c.reg(0))
			c.emit("DIV", c.reg(0), c.reg(3))
			c.emit("MUL", c.reg(0), c.reg(3))
			c.emit("MOV", c.reg(1), c.reg(0))
			c.emit("POP", c.reg(0))
			c.emit("SUB", c.reg(0), c.reg(1))
		case "&":
			c.emit("AND", c.reg(0), c.reg(1))
		case "|":
			c.emit("OR", c.reg(0), c.reg(1))
		case "^":
			c.emit("XOR", c.reg(0), c.reg(1))
		case "<<":
			c.emit("SHL", c.reg(0), c.reg(1))
		case ">>":
			c.emit("ASR", c.reg(0), c.reg(0), c.reg(1))
		}
	}

	c.emit("SD", c.reg(0), ir.Mem(2, 0))
	c.emit("ADDI", c.reg(32), c.reg(32), c.imm(8))
	return tt
}

func (c *compiler) genIncdec(kind string, target *Node) *Type {
	tt := c.exprType(target)
	if tt == nil || (tt.Kind != kInt && tt.Kind != kBool && tt.Kind != kFloat) {
		return nil
	}
	op := "+"
	if kind == "predec" || kind == "postdec" {
		op = "-"
	}
	postfix := kind == "postinc" || kind == "postdec"
	floatMode := tt.Kind == kFloat

	c.genLvalueAddr(target)
	c.emit("PUSH", c.reg(0))
	c.emit("LD", c.reg(2), ir.Mem(32, 0))
	c.emit("LD", c.reg(0), ir.Mem(2, 0))
	if postfix {
		c.emit("MOV", c.reg(5), c.reg(0))
	}
	if floatMode {
		c.emit("MOV", c.reg(0), c.imm(1))
		c.emit("SYS", c.imm(SysITOF))
		c.emit("MOV", c.reg(1), c.reg(0))
		c.emit("LD", c.reg(0), ir.Mem(2, 0))
		if op == "+" {
			c.emit("SYS", c.imm(SysFADD))
		} else {
			c.emit("SYS", c.imm(SysFSUB))
		}
	} else {
		c.emit("MOV", c.reg(1), c.imm(1))
		if op == "+" {
			c.emit("ADD", c.reg(0), c.reg(1))
		} else {
			c.emit("SUB", c.reg(0), c.reg(1))
		}
	}
	c.emit("SD", c.reg(0), ir.Mem(2, 0))
	c.emit("ADDI", c.reg(32), c.reg(32), c.imm(8))
	if postfix {
		c.emit("MOV", c.reg(0), c.reg(5))
	}
	return tt
}

func (c *compiler) genMinmax(name string, args []*Node) *Type {
	at := c.exprType(args[0])
	bt := c.exprType(args[1])
	if at != nil && at.Kind == kString || bt != nil && bt.Kind == kString {
		return scalarT(kInt)
	}
	tt := scalarT(kInt)
	if at != nil && at.Kind == kFloat || bt != nil && bt.Kind == kFloat {
		tt = scalarT(kFloat)
	}

	c.genValue(args[0])
	if tt.Kind == kFloat && at != nil && (at.Kind == kInt || at.Kind == kBool) {
		c.emit("SYS", c.imm(SysITOF))
	}
	c.emit("PUSH", c.reg(0))
	c.genValue(args[1])
	if tt.Kind == kFloat && bt != nil && (bt.Kind == kInt || bt.Kind == kBool) {
		c.emit("SYS", c.imm(SysITOF))
	}
	c.emit("MOV", c.reg(1), c.reg(0))
	c.emit("POP", c.reg(0))

	cond := "LE"
	if name == "max" {
		cond = "GE"
	}
	if tt.Kind == kFloat {
		c.emit("MOV", c.reg(2), c.reg(0))
		c.emit("MOV", c.reg(3), c.reg(1))
		c.emit("SYS", c.imm(SysFCMP))
		c.emit("CMP", c.reg(0), c.imm(0))
		lKeep := c.newLabel("mmkeep")
		lDone := c.newLabel("mmdone")
		c.emit("B", c.lab(lKeep), ir.Cond(cond))
		c.emit("MOV", c.reg(0), c.reg(3))
		c.emit("JMP", c.lab(lDone))
		c.label(lKeep)
		c.emit("MOV", c.reg(0), c.reg(2))
		c.label(lDone)
	} else {
		c.emit("CMP", c.reg(0), c.reg(1))
		lKeep := c.newLabel("mmkeep")
		c.emit("B", c.lab(lKeep), ir.Cond(cond))
		c.emit("MOV", c.reg(0), c.reg(1))
		c.label(lKeep)
	}
	return tt
}

func (c *compiler) genTernary(cond, a, b *Node) *Type {
	lt := c.exprType(a)
	rt := c.exprType(b)
	ls := lt != nil && lt.Kind == kString
	rs := rt != nil && rt.Kind == kString
	if ls != rs {
		return scalarT(kInt)
	}
	tt := scalarT(kInt)
	if ls {
		tt = scalarT(kString)
	} else if lt != nil && lt.Kind == kFloat || rt != nil && rt.Kind == kFloat {
		tt = scalarT(kFloat)
	}
	lFalse := c.newLabel("cndf")
	lEnd := c.newLabel("cnde")
	c.genCondJumpFalse(cond, lFalse)
	t1 := c.genValue(a)
	c.convert(t1, tt)
	c.emit("MOV", c.reg(6), c.reg(0))
	c.emit("JMP", c.lab(lEnd))
	c.label(lFalse)
	t2 := c.genValue(b)
	c.convert(t2, tt)
	c.emit("MOV", c.reg(6), c.reg(0))
	c.label(lEnd)
	c.emit("MOV", c.reg(0), c.reg(6))
	return tt
}

// ---------------- 字符串化 ----------------

func (c *compiler) genStringValue(n *Node) {
	t := c.genValue(n)
	switch {
	case t != nil && t.Kind == kString:
		return
	case t != nil && t.Kind == kFloat:
		c.emit("SYS", c.imm(SysFTOA))
	case t != nil && t.Kind == kBool:
		c.emit("SYS", c.imm(SysBOOL_STR))
	default:
		c.emit("SYS", c.imm(SysITOA))
	}
}

func (c *compiler) genPrint(n *Node, newline bool) {
	t := c.genValue(n)
	switch {
	case t != nil && t.Kind == kString:
	case t != nil && t.Kind == kFloat:
		c.emit("SYS", c.imm(SysFTOA))
	case t != nil && t.Kind == kBool:
		c.emit("SYS", c.imm(SysBOOL_STR))
	default:
		c.emit("SYS", c.imm(SysITOA))
	}
	c.emit("SYS", c.imm(SysPRINT_STR))
	if newline {
		c.emit("OUT", c.imm(10))
	}
}

// ---------------- 函数调用 ----------------

func (c *compiler) builtinRetType(name string) *Type {
	switch name {
	case "sin", "cos", "tan", "sqrt", "pow", "floor", "ceil", "round":
		return scalarT(kFloat)
	case "strlen", "strcmp", "rand", "time", "abs", "input", "idiv", "atoi",
		"audio_play", "save_png", "show_canvas":
		return scalarT(kInt)
	case "strcpy", "int_to_str", "itoa", "float_to_str", "ftoa", "bool_to_str",
		"substr", "upper", "lower", "trim", "ltrim", "rtrim":
		return scalarT(kString)
	}
	return nil
}

func (c *compiler) genCall(name string, args []*Node) *Type {
	if name == "println" || name == "print" {
		if len(args) > 0 {
			c.genPrint(args[0], name == "println")
		} else {
			c.emit("OUT", c.imm(10))
		}
		return scalarT(kVoid)
	}

	mathUnary := map[string]int64{"sqrt": SysSQRT, "sin": SysSIN, "cos": SysCOS, "tan": SysTAN}
	if id, ok := mathUnary[name]; ok {
		at := c.genValue(args[0])
		if at.Kind == kInt {
			c.emit("SYS", c.imm(SysITOF))
		}
		c.emit("SYS", c.imm(id))
		return scalarT(kFloat)
	}

	roundUnary := map[string]int64{"floor": SysFLOOR, "ceil": SysCEIL, "round": SysROUND}
	if id, ok := roundUnary[name]; ok {
		at := c.genValue(args[0])
		if at.Kind == kInt || at.Kind == kBool {
			c.emit("SYS", c.imm(SysITOF))
		}
		c.emit("SYS", c.imm(id))
		return scalarT(kFloat)
	}

	if name == "min" || name == "max" {
		return c.genMinmax(name, args)
	}

	if name == "idiv" {
		c.genValue(args[0])
		c.emit("PUSH", c.reg(0))
		c.genValue(args[1])
		c.emit("MOV", c.reg(1), c.reg(0))
		c.emit("POP", c.reg(0))
		c.emit("DIV", c.reg(0), c.reg(1))
		return scalarT(kInt)
	}

	if name == "pow" {
		c.argFloat(args[0])
		c.emit("PUSH", c.reg(0))
		c.argFloat(args[1])
		c.emit("MOV", c.reg(1), c.reg(0))
		c.emit("POP", c.reg(0))
		c.emit("SYS", c.imm(SysPOW))
		return scalarT(kFloat)
	}
	if name == "abs" {
		c.genValue(args[0])
		c.emit("SYS", c.imm(SysABS))
		return scalarT(kInt)
	}
	if name == "strlen" {
		c.genValue(args[0])
		c.emit("SYS", c.imm(SysSTRLEN))
		return scalarT(kInt)
	}
	if name == "strcmp" {
		c.genValue(args[0])
		c.emit("PUSH", c.reg(0))
		c.genValue(args[1])
		c.emit("MOV", c.reg(1), c.reg(0))
		c.emit("POP", c.reg(0))
		c.emit("SYS", c.imm(SysSTRCMP))
		return scalarT(kInt)
	}
	if name == "strcpy" {
		c.genValue(args[0])
		c.emit("PUSH", c.reg(0))
		c.emit("MOV", c.reg(0), c.imm(int64(c.dataString(""))))
		c.emit("MOV", c.reg(1), c.reg(0))
		c.emit("POP", c.reg(0))
		c.emit("SYS", c.imm(SysSTR_CONCAT))
		return scalarT(kString)
	}
	if name == "rand" {
		c.emit("SYS", c.imm(SysRAND))
		return scalarT(kInt)
	}
	if name == "srand" {
		c.genValue(args[0])
		c.emit("SYS", c.imm(SysSRAND))
		return scalarT(kVoid)
	}
	if name == "int_to_str" || name == "itoa" {
		c.genValue(args[0])
		c.emit("SYS", c.imm(SysITOA))
		return scalarT(kString)
	}
	if name == "float_to_str" || name == "ftoa" {
		t := c.genValue(args[0])
		if t.Kind == kInt || t.Kind == kBool {
			c.emit("SYS", c.imm(SysITOF))
		}
		c.emit("SYS", c.imm(SysFTOA))
		return scalarT(kString)
	}
	if name == "bool_to_str" {
		c.genValue(args[0])
		c.emit("SYS", c.imm(SysBOOL_STR))
		return scalarT(kString)
	}
	if name == "substr" {
		c.genStringValue(args[0])
		c.emit("PUSH", c.reg(0))
		c.genValue(args[1])
		c.emit("PUSH", c.reg(0))
		c.genValue(args[2])
		c.emit("MOV", c.reg(2), c.reg(0))
		c.emit("POP", c.reg(1))
		c.emit("POP", c.reg(0))
		c.emit("SYS", c.imm(SysSUBSTR))
		return scalarT(kString)
	}
	if name == "indexof" {
		c.genStringValue(args[0])
		c.emit("PUSH", c.reg(0))
		c.genStringValue(args[1])
		c.emit("MOV", c.reg(1), c.reg(0))
		c.emit("POP", c.reg(0))
		c.emit("SYS", c.imm(SysINDEXOF))
		return scalarT(kInt)
	}
	if name == "upper" || name == "lower" {
		c.genStringValue(args[0])
		id := int64(SysTOUPPER)
		if name == "lower" {
			id = SysTOLOWER
		}
		c.emit("SYS", c.imm(id))
		return scalarT(kString)
	}
	if name == "trim" || name == "ltrim" || name == "rtrim" {
		c.genStringValue(args[0])
		id := map[string]int64{"trim": SysTRIM, "ltrim": SysLTRIM, "rtrim": SysRTRIM}[name]
		c.emit("SYS", c.imm(id))
		return scalarT(kString)
	}
	if name == "atoi" {
		c.genValue(args[0])
		c.emit("SYS", c.imm(SysATOI))
		return scalarT(kInt)
	}

	// 宿主能力: 联网音频 / 2D 绘图画布
	if name == "audio_play" {
		c.genHostSys(SysAUDIOPLAY, []*Node{args[0]})
		return scalarT(kInt)
	}
	if name == "audio_stop" {
		c.genHostSys(SysAUDIOSTOP, nil)
		return scalarT(kVoid)
	}
	if name == "audio_volume" {
		c.genHostSys(SysAUDIOVOL, []*Node{args[0]})
		return scalarT(kVoid)
	}
	if name == "audio_wait" {
		c.genHostSys(SysAUDIOWAIT, nil)
		return scalarT(kVoid)
	}
	if name == "canvas" {
		c.genHostSys(SysCANVASNEW, []*Node{args[0], args[1]})
		return scalarT(kVoid)
	}
	if name == "set_color" {
		c.genHostSys(SysCANVASSET, []*Node{args[0]})
		return scalarT(kVoid)
	}
	if name == "fill_rect" {
		c.genHostSys(SysCANVASRECT, args)
		return scalarT(kVoid)
	}
	if name == "fill_circle" {
		c.genHostSys(SysCANVASCIRC, args)
		return scalarT(kVoid)
	}
	if name == "draw_line" {
		c.genHostSys(SysCANVASLINE, args)
		return scalarT(kVoid)
	}
	if name == "draw_text" {
		c.genHostSys(SysCANVASTEXT, args)
		return scalarT(kVoid)
	}
	if name == "save_png" {
		c.genHostSys(SysCANVASSAVE, []*Node{args[0]})
		return scalarT(kInt)
	}
	if name == "show_canvas" {
		c.genHostSys(SysCANVASSHOW, nil)
		return scalarT(kInt)
	}

	if name == "time" {
		c.emit("SYS", c.imm(SysTIME))
		return scalarT(kInt)
	}
	if name == "input" {
		c.emit("MOV", c.reg(0), c.imm(0))
		return scalarT(kInt)
	}

	// 用户函数
	fdef := c.functions[name]
	if fdef == nil {
		return nil // Unknown function
	}
	for k, arg := range args {
		at := c.genValue(arg)
		var ptype *Type
		if k < len(fdef.params) {
			ptype = paramPromote(fdef.params[k].t)
		}
		c.convert(at, ptype)
		c.emit("PUSH", c.reg(0))
	}
	c.emit("CALL", c.lab(name))
	nargs := len(args)
	if nargs > 0 {
		c.emit("ADDI", c.reg(6), c.reg(32), c.imm(int64(nargs*8)))
		c.emit("MOV", c.reg(32), c.reg(6))
	}
	return fdef.retType
}

func paramPromote(t *Type) *Type {
	if isFixedArray(t) {
		return &Type{Kind: kPtrArray, Elem: t.Elem}
	}
	return t
}

func (c *compiler) argFloat(n *Node) {
	t := c.genValue(n)
	if t.Kind == kInt || t.Kind == kBool {
		c.emit("SYS", c.imm(SysITOF))
	}
}

func (c *compiler) genHostSys(sysID int64, args []*Node) {
	for _, a := range args {
		c.genValue(a)
		c.emit("PUSH", c.reg(0))
	}
	for i := len(args) - 1; i >= 0; i-- {
		c.emit("POP", c.reg(i))
	}
	c.emit("SYS", c.imm(sysID))
}

// ---------------- 条件跳转 ----------------

var condFalseJump = map[string]string{
	"==": "NE", "!=": "EQ", "<": "GE", ">": "LE", "<=": "GT", ">=": "LT",
}

func (c *compiler) genCondJumpFalse(n *Node, labelFalse string) {
	switch {
	case n.Kind == "bool":
		if !n.Bool {
			c.emit("JMP", c.lab(labelFalse))
		}
		return
	case n.Kind == "binop" && n.Op == "&&":
		c.genCondJumpFalse(n.A, labelFalse)
		c.genCondJumpFalse(n.B, labelFalse)
		return
	case n.Kind == "binop" && n.Op == "||":
		lTrue := c.newLabel("ortrue")
		c.genCondJumpTrue(n.A, lTrue)
		c.genCondJumpTrue(n.B, lTrue)
		c.emit("JMP", c.lab(labelFalse))
		c.label(lTrue)
		return
	case n.Kind == "not":
		c.genCondJumpTrue(n.A, labelFalse)
		return
	case n.Kind == "binop" && condFalseJump[n.Op] != "":
		op := n.Op
		lt := c.exprType(n.A)
		rt := c.exprType(n.B)
		floatMode := lt != nil && lt.Kind == kFloat || rt != nil && rt.Kind == kFloat
		c.genValue(n.A)
		if floatMode && lt != nil && (lt.Kind == kInt || lt.Kind == kBool) {
			c.emit("SYS", c.imm(SysITOF))
		}
		c.emit("PUSH", c.reg(0))
		c.genValue(n.B)
		if floatMode && rt != nil && (rt.Kind == kInt || rt.Kind == kBool) {
			c.emit("SYS", c.imm(SysITOF))
		}
		c.emit("MOV", c.reg(1), c.reg(0))
		c.emit("POP", c.reg(0))
		if floatMode {
			c.emit("SYS", c.imm(SysFCMP))
			c.emit("CMP", c.reg(0), c.imm(0))
		} else {
			c.emit("CMP", c.reg(0), c.reg(1))
		}
		c.emit("B", c.lab(labelFalse), ir.Cond(condFalseJump[op]))
		return
	}
	c.genValue(n)
	c.emit("CMP", c.reg(0), c.imm(0))
	c.emit("JZ", c.lab(labelFalse))
}

func (c *compiler) genCondJumpTrue(n *Node, labelTrue string) {
	lFalse := c.newLabel("cf")
	c.genCondJumpFalse(n, lFalse)
	c.emit("JMP", c.lab(labelTrue))
	c.label(lFalse)
}

// ---------------- 入口 ----------------

// Compile 编译 CIN 源码字符串, 返回 IR 程序。
func Compile(source, filename string, bounds bool) (*ir.Program, error) {
	c := newCompiler(filename, bounds)
	toks, err := tokenize(source, filename)
	if err != nil {
		return nil, err
	}
	p := &parser{toks: toks, filename: filename}
	structs, globals, functions, funcOrder, err := p.parseProgram()
	if err != nil {
		return nil, err
	}
	c.structs = structs
	c.globals = globals
	c.functions = functions

	c.layoutGlobals(globals)
	c.emitGlobalsInit(globals)
	c.emit("CALL", c.lab("main"))
	c.emit("HALT")
	for _, name := range funcOrder {
		c.genFunctionBody(functions[name])
	}
	return c.res, nil
}

var importRe = regexp.MustCompile(`^import\s+["']([^"']+)["']\s*;?\s*$`)

// CompileFile 读取 .cin 文件并展开 import, 然后编译。
func CompileFile(path, libDir string) (*ir.Program, error) {
	source, err := loadProgramSource(path, libDir)
	if err != nil {
		return nil, err
	}
	return Compile(source, path, false)
}

func loadProgramSource(path, libDir string) (string, error) {
	real, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	real, err = filepath.EvalSymlinks(real)
	if err != nil {
		real, _ = filepath.Abs(path)
	}
	var out []string
	loaded := map[string]bool{}
	active := map[string]bool{}
	var collect func(string) error
	collect = func(rp string) error {
		key := rp
		if active[key] {
			return fmt.Errorf("Circular import: %s", rp)
		}
		if loaded[key] {
			return nil
		}
		active[key] = true
		defer delete(active, key)
		data, err := os.ReadFile(rp)
		if err != nil {
			return err
		}
		for _, line := range strings.Split(strings.ReplaceAll(string(data), "\r\n", "\n"), "\n") {
			m := importRe.FindStringSubmatch(line)
			if m != nil {
				target := m[1]
				var candidates []string
				candidates = append(candidates, filepath.Join(filepath.Dir(rp), target))
				if libDir != "" {
					candidates = append(candidates, filepath.Join(libDir, target))
				}
				if filepath.Base(libDir) == "lib" {
					candidates = append(candidates, filepath.Join(filepath.Dir(libDir), target))
				}
				resolved := ""
				for _, cand := range candidates {
					if fi, e := os.Stat(cand); e == nil && !fi.IsDir() {
						resolved = cand
						break
					}
				}
				if resolved == "" {
					return fmt.Errorf("Import file not found: %q", target)
				}
				rr, e := filepath.Abs(resolved)
				if e == nil {
					if r2, e2 := filepath.EvalSymlinks(rr); e2 == nil {
						rr = r2
					}
				}
				if err := collect(rr); err != nil {
					return err
				}
				continue
			}
			out = append(out, line)
		}
		loaded[key] = true
		return nil
	}
	if err := collect(real); err != nil {
		return "", err
	}
	return strings.Join(out, "\n"), nil
}

// 让 unused import 不报错 (math 等)。
var _ = math.Float64bits
var _ = os.Stat
