// Package engine 是 Code CIN 的原生字节码 VM (Go 实现)。
//
// 语义与 codecin/cpu.py 解释器严格一致:
//   - PC 语义: 取指后先 pc++ 再执行; CALL/BL 压入的是已自增的 pc
//   - 栈: 8 字节 qword, 自顶向下; sp 初值由调用方传入
//   - 寄存器: 33 个槽, R[31]=XZR 只读, R[32]=SP
//   - 遇到不支持的指令立即返回 StatusUnsupported, pc 指向该指令, 供 Python 回退
//
// 字节码格式 (UCBC v1):
//
//	magic[4] version u8 entry u32 count u32
//	指令: opcode u8 argc u8 ; 操作数: kind u8 value i64 extra i64 (小端)
package engine

import (
	"encoding/binary"
	"math"
	"math/rand"
	"strconv"
	"strings"
	"time"
)

// 执行状态码 (与 c-shared 桥接层 / Python native.py 一致)。
const (
	StatusOK          = 0
	StatusDone        = 1
	StatusUnsupported = 2
	StatusError       = 3
)

// 操作数种类/操作码/SYS 功能号常量由 isa_gen.go 提供 (单一事实来源:
// python script/gen_native_isa.py 自 codecin/isa.py 生成, 请勿在此重复定义)。

const mask64 = uint64(0xFFFFFFFFFFFFFFFF)

type operand struct {
	kind  uint8
	value int64
	extra int64
}

type instruction struct {
	opcode uint8
	args   []operand
}

type vmState struct {
	prog     []instruction
	entry    int
	mem      []byte
	regs     [33]uint64
	sp       uint64
	pc       int
	heapPtr  uint64
	steps    uint64
	flags    struct{ N, Z, C, V bool }
	out      strings.Builder
	outOver  bool // 输出超过 maxOutputBytes (截断并置错误)
	sysIdx   int
	rng      *rand.Rand
	inData   []byte
	inPos    int
	emptyStr uint64 // 预留空串地址 (宿主调用返回失败时的安全空串)
}

// maxOutputBytes 限制单次运行的输出总量: 程序用无限打印不能把宿主 OOM。
const maxOutputBytes = 16 << 20 // 16 MiB

// outWrite 追加输出; 超过上限则截断并标记 outOver。
func (vm *vmState) outWrite(s string) {
	if vm.outOver {
		return
	}
	remaining := maxOutputBytes - vm.out.Len()
	if remaining <= 0 {
		vm.outOver = true
		return
	}
	if len(s) > remaining {
		s = s[:remaining]
		vm.outOver = true
	}
	vm.out.WriteString(s)
}

// outByte 追加单字节输出 (同上限)。
func (vm *vmState) outByte(b byte) {
	if vm.outOver {
		return
	}
	if vm.out.Len() >= maxOutputBytes {
		vm.outOver = true
		return
	}
	vm.out.WriteByte(b)
}

// Result 是 Run 的执行结果。
type Result struct {
	Status  int
	Pc      int
	Sp      uint64
	HeapPtr uint64
	Steps   uint64
	Regs    [33]uint64
	Output  string
	ErrMsg  string
}

func decodeBytecode(bc []byte) ([]instruction, int, bool) {
	if len(bc) < 13 || string(bc[:4]) != "UCBC" {
		return nil, 0, false
	}
	if bc[4] != bcVersion {
		// 版本字节此前从不校验: 未知格式会被按当前布局强行解释
		return nil, 0, false
	}
	entry := binary.LittleEndian.Uint32(bc[5:9])
	count := binary.LittleEndian.Uint32(bc[9:13])
	// count 来自不可信输入: 先按"每条指令至少 2 字节头部"夹取。
	// 旧实现直接把它当 make 容量 (count=0xFFFFFFFF → 137GB 预分配),
	// 一个 13 字节的文件就能让宿主 OOM。
	if maxCount := uint32((len(bc) - 13) / 2); count > maxCount {
		return nil, 0, false
	}
	pos := 13
	prog := make([]instruction, 0, count)
	for i := uint32(0); i < count; i++ {
		if pos+2 > len(bc) {
			return nil, 0, false
		}
		opcode := bc[pos]
		argc := int(bc[pos+1])
		pos += 2
		if int(opcode) >= len(argCounts) {
			return nil, 0, false
		}
		// 参数个数必须与操作码匹配: 否则 execute 中的 args[i] 会越界 panic。
		// -1 表示变长 (B / JALR / SYS), 只设一个合理上限。
		if want := int(argCounts[opcode]); want >= 0 {
			if argc != want {
				return nil, 0, false
			}
		} else if argc > 6 {
			return nil, 0, false
		}
		ins := instruction{opcode: opcode}
		for j := 0; j < argc; j++ {
			if pos+17 > len(bc) {
				return nil, 0, false
			}
			op := operand{
				kind:  bc[pos],
				value: int64(binary.LittleEndian.Uint64(bc[pos+1 : pos+9])),
				extra: int64(binary.LittleEndian.Uint64(bc[pos+9 : pos+17])),
			}
			pos += 17
			ins.args = append(ins.args, op)
		}
		prog = append(prog, ins)
	}
	return prog, int(entry), true
}

// Run 执行程序字节码, 返回结果快照。mem 会被原地修改 (数据段/堆/栈写入)。
func Run(bc []byte, mem []byte, entry, sp, heapBase int64, inData []byte,
	maxSteps int64) *Result {

	prog, ent, ok := decodeBytecode(bc)
	if !ok {
		return &Result{Status: StatusError, ErrMsg: "bad bytecode"}
	}
	if entry >= 0 {
		ent = int(entry)
	}
	vm := &vmState{
		prog:    prog,
		entry:   ent,
		mem:     mem,
		sp:      uint64(sp),
		pc:      ent,
		heapPtr: uint64(heapBase),
		rng:     rand.New(rand.NewSource(time.Now().UnixNano())),
		inData:  inData,
	}

	finish := func(status int, errMsg string) *Result {
		// 输出超限一并报错: 不能把"被截断的输出"当成正常结果
		if vm.outOver && status != StatusError {
			status = StatusError
			if errMsg == "" {
				errMsg = "output limit exceeded (16 MiB)"
			}
		}
		return &Result{
			Status:  status,
			Pc:      vm.pc,
			Sp:      vm.sp,
			HeapPtr: vm.heapPtr,
			Steps:   vm.steps,
			Regs:    vm.regs,
			Output:  vm.out.String(),
			ErrMsg:  errMsg,
		}
	}

	for {
		if maxSteps > 0 && int64(vm.steps) >= maxSteps {
			// 步数用尽不等于正常停机: 旧实现返回 StatusDone, 与 HALT 无法区分,
			// 于是被截断的程序在 Python 侧被当成 halted=true 正常结束。
			return finish(StatusError,
				fmt.Sprintf("instruction limit reached (%d steps)", maxSteps))
		}
		if vm.pc < 0 || vm.pc >= len(vm.prog) {
			return finish(StatusDone, "")
		}
		ins := vm.prog[vm.pc]
		if !opcodeSupported(ins.opcode) {
			return finish(StatusUnsupported, "")
		}
		// 未知 SYS 功能号: 交给解释器 (pc 不自增)
		if ins.opcode == opSYS {
			if len(ins.args) == 0 || ins.args[0].kind != kindImm ||
				!syscallSupported(uint64(ins.args[0].value)) {
				return finish(StatusUnsupported, "")
			}
		}
		// 与解释器一致: 先自增 pc
		vm.pc++
		vm.steps++
		halt, err := vm.execute(ins)
		if err != "" {
			return finish(StatusError, err)
		}
		if halt {
			return finish(StatusOK, "")
		}
	}
}

func opcodeSupported(op uint8) bool {
	switch op {
	case opMOV, opLOAD, opSTORE, opADD, opSUB, opMUL, opDIV, opAND, opOR, opXOR,
		opSHL, opSHR, opINC, opDEC, opCMP, opJMP, opJZ, opJNZ, opJE, opJL, opJG,
		opPUSH, opPOP, opCALL, opRET, opIN, opOUT, opHALT, opLSL, opLSR, opASR,
		opMVN, opB, opBL, opNOP, opLB, opLH, opLW, opLD, opSB, opSH, opSW, opSD,
		opADDI, opXORI, opORI, opANDI, opSYS:
		return true
	}
	return false
}

// syscallSupported: 原生 VM 已实现的 SYS 功能号。
// 现已在 Go 侧实现全部宿主调用 (0..sysTERMUXSMS), 供独立 Go CLI 使用。
func syscallSupported(id uint64) bool {
	return id <= sysTERMUXSMS
}

// ---------------- 操作数/寄存器/内存 ----------------

func (vm *vmState) reg(n int) uint64 {
	if n == 32 {
		return vm.sp
	}
	if n == 31 || n < 0 || n > 32 {
		return 0
	}
	return vm.regs[n]
}

func (vm *vmState) setReg(n int, v uint64) {
	if n == 32 {
		vm.sp = v
	} else if n == 31 || n < 0 || n > 32 {
		// XZR 只读
	} else {
		vm.regs[n] = v
	}
}

func (vm *vmState) memAddr(op operand) (uint64, bool) {
	base := op.value
	off := uint64(op.extra)
	if base >= 0 {
		return (vm.reg(int(base)) + off) & mask64, true
	}
	return off & mask64, true
}

func (vm *vmState) val(op operand) (uint64, bool) {
	switch op.kind {
	case kindReg:
		return vm.reg(int(op.value)), true
	case kindImm:
		return uint64(op.value), true
	case kindFloat:
		return uint64(op.value), true
	case kindStr:
		return uint64(op.value), true
	case kindMem:
		addr, ok := vm.memAddr(op)
		if !ok {
			return 0, false
		}
		v, e := vm.readQ(addr)
		return v, e == ""
	case kindCond:
		if vm.condition(uint8(op.value)) {
			return 1, true
		}
		return 0, true
	case kindVec, kindVecLane:
		// CIN 不生成向量操作数; 向量指令整体不支持
		return 0, false
	}
	return 0, false
}

func (vm *vmState) checkAddr(addr uint64, width int) string {
	if uint64(len(vm.mem)) < uint64(width) || addr > uint64(len(vm.mem))-uint64(width) {
		return "address " + strconv.FormatUint(addr, 16) + " out of bounds"
	}
	return ""
}

func (vm *vmState) readQ(addr uint64) (uint64, string) {
	if e := vm.checkAddr(addr, 8); e != "" {
		return 0, e
	}
	return binary.LittleEndian.Uint64(vm.mem[addr : addr+8]), ""
}

func (vm *vmState) writeQ(addr, v uint64) string {
	if e := vm.checkAddr(addr, 8); e != "" {
		return e
	}
	binary.LittleEndian.PutUint64(vm.mem[addr:addr+8], v)
	return ""
}

func toSigned(v uint64) int64 { return int64(v) }

// ---------------- 条件标志 ----------------

func (vm *vmState) condition(code uint8) bool {
	n, z, c, v := vm.flags.N, vm.flags.Z, vm.flags.C, vm.flags.V
	switch code {
	case 0: // EQ
		return z
	case 1: // NE
		return !z
	case 2: // CS
		return c
	case 3: // CC
		return !c
	case 4: // MI
		return n
	case 5: // PL
		return !n
	case 6: // VS
		return v
	case 7: // VC
		return !v
	case 8: // HI
		return c && !z
	case 9: // LS
		return !c || z
	case 10: // GE
		return n == v
	case 11: // LT
		return n != v
	case 12: // GT
		return !z && (n == v)
	case 13: // LE
		return z || (n != v)
	case 14: // AL
		return true
	default: // NV
		return false
	}
}

func (vm *vmState) setFlagsSub(a, b uint64) {
	res := (a - b) & mask64
	sa := int64(a)
	sb := int64(b)
	sr := sa - sb
	vm.flags.Z = res == 0
	vm.flags.N = res&(1<<63) != 0
	vm.flags.C = a >= b
	vm.flags.V = sr > math.MaxInt64 || sr < math.MinInt64
}

// ---------------- 栈 ----------------

func (vm *vmState) push(v uint64) string {
	vm.sp = (vm.sp - 8) & mask64
	if vm.sp < vm.heapPtr+4096 {
		return "Stack overflow (collides with heap)"
	}
	return vm.writeQ(vm.sp, v)
}

func (vm *vmState) pop() (uint64, string) {
	if vm.sp >= uint64(len(vm.mem))-8 {
		return 0, "Stack underflow"
	}
	v, e := vm.readQ(vm.sp)
	if e != "" {
		return 0, e
	}
	vm.sp = (vm.sp + 8) & mask64
	return v, ""
}

// ---------------- 浮点辅助 ----------------

func bitsToF(b uint64) float64 { return math.Float64frombits(b) }
func fToBits(f float64) uint64 { return math.Float64bits(f) }

// formatFloat 与 Python _format_float 对齐 (repr 最短表示 + 去尾零)
func formatFloat(f float64) string {
	if math.IsNaN(f) {
		return "NaN"
	}
	if math.IsInf(f, 1) {
		return "+Inf"
	}
	if math.IsInf(f, -1) {
		return "-Inf"
	}
	return strconv.FormatFloat(f, 'g', -1, 64)
}

func (vm *vmState) readCString(addr uint64) string {
	var sb strings.Builder
	for addr < uint64(len(vm.mem)) {
		ch := vm.mem[addr]
		if ch == 0 {
			break
		}
		sb.WriteByte(ch)
		addr++
	}
	return sb.String()
}

func (vm *vmState) writeString(addr uint64, s string) string {
	if e := vm.checkAddr(addr, len(s)+1); e != "" {
		return e
	}
	copy(vm.mem[addr:addr+uint64(len(s))], s)
	vm.mem[addr+uint64(len(s))] = 0
	return ""
}

// sysBuffer 轮转静态缓冲 (与 Python 一致: heap+2048+idx*64)
func (vm *vmState) sysBuffer() uint64 {
	idx := vm.sysIdx % 8
	vm.sysIdx++
	return vm.heapPtr + 2048 + uint64(idx)*64
}

// ---------------- 指令执行 ----------------

func (vm *vmState) execute(ins instruction) (bool, string) {
	args := ins.args
	switch ins.opcode {
	case opMOV:
		v, ok := vm.val(args[1])
		if !ok {
			return false, "bad MOV operand"
		}
		vm.setReg(int(args[0].value), v)
	case opADD:
		rd := int(args[0].value)
		v, ok := vm.val(args[1])
		if !ok {
			return false, "bad ADD operand"
		}
		vm.setReg(rd, (vm.reg(rd)+v)&mask64)
	case opSUB:
		rd := int(args[0].value)
		v, ok := vm.val(args[1])
		if !ok {
			return false, "bad SUB operand"
		}
		vm.setReg(rd, (vm.reg(rd)-v)&mask64)
	case opMUL:
		rd := int(args[0].value)
		v, ok := vm.val(args[1])
		if !ok {
			return false, "bad MUL operand"
		}
		vm.setReg(rd, (vm.reg(rd)*v)&mask64)
	case opDIV:
		rd := int(args[0].value)
		d, ok := vm.val(args[1])
		if !ok {
			return false, "bad DIV operand"
		}
		if d == 0 {
			return false, "Division by zero"
		}
		vm.setReg(rd, sdiv(vm.reg(rd), d))
	case opAND:
		rd := int(args[0].value)
		v, _ := vm.val(args[1])
		vm.setReg(rd, vm.reg(rd)&v)
	case opOR:
		rd := int(args[0].value)
		v, _ := vm.val(args[1])
		vm.setReg(rd, vm.reg(rd)|v)
	case opXOR:
		rd := int(args[0].value)
		v, _ := vm.val(args[1])
		vm.setReg(rd, vm.reg(rd)^v)
	case opSHL:
		rd := int(args[0].value)
		v, _ := vm.val(args[1])
		vm.setReg(rd, (vm.reg(rd)<<(v&63))&mask64)
	case opSHR:
		rd := int(args[0].value)
		v, _ := vm.val(args[1])
		vm.setReg(rd, vm.reg(rd)>>(v&63))
	case opINC:
		rd := int(args[0].value)
		vm.setReg(rd, (vm.reg(rd)+1)&mask64)
	case opDEC:
		rd := int(args[0].value)
		vm.setReg(rd, (vm.reg(rd)-1)&mask64)
	case opMVN:
		rd := int(args[0].value)
		v, _ := vm.val(args[1])
		vm.setReg(rd, ^v&mask64)
	case opCMP:
		a, _ := vm.val(args[0])
		b, _ := vm.val(args[1])
		vm.setFlagsSub(a, b)
	case opJMP:
		t, _ := vm.val(args[0])
		vm.pc = int(t)
	case opJZ, opJE:
		if vm.flags.Z {
			t, _ := vm.val(args[0])
			vm.pc = int(t)
		}
	case opJNZ:
		if !vm.flags.Z {
			t, _ := vm.val(args[0])
			vm.pc = int(t)
		}
	case opJL:
		if vm.flags.N != vm.flags.V {
			t, _ := vm.val(args[0])
			vm.pc = int(t)
		}
	case opJG:
		if !vm.flags.Z && vm.flags.N == vm.flags.V {
			t, _ := vm.val(args[0])
			vm.pc = int(t)
		}
	case opPUSH:
		v, _ := vm.val(args[0])
		if e := vm.push(v); e != "" {
			return false, e
		}
	case opPOP:
		v, e := vm.pop()
		if e != "" {
			return false, e
		}
		vm.setReg(int(args[0].value), v)
	case opCALL, opBL:
		if e := vm.push(uint64(vm.pc)); e != "" {
			return false, e
		}
		t, _ := vm.val(args[0])
		vm.pc = int(t)
	case opRET:
		v, e := vm.pop()
		if e != "" {
			return false, e
		}
		vm.pc = int(v)
	case opNOP:
		// nothing
	case opB:
		cond := -1
		target := uint64(0)
		for _, a := range args {
			if a.kind == kindCond {
				cond = int(a.value)
			} else {
				t, ok := vm.val(a)
				if ok {
					target = t
				}
			}
		}
		if cond >= 0 && !vm.condition(uint8(cond)) {
			break
		}
		vm.pc = int(target)
	case opIN:
		vm.setReg(int(args[0].value), vm.readLineInt())
	case opOUT:
		if e := vm.doOut(args[0]); e != "" {
			return false, e
		}
	case opHALT:
		return true, ""
	// load/store
	case opLOAD:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		if e := vm.checkAddr(addr, 4); e != "" {
			return false, e
		}
		v := uint64(binary.LittleEndian.Uint32(vm.mem[addr : addr+4]))
		vm.setReg(int(args[0].value), v)
	case opLW:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		if e := vm.checkAddr(addr, 4); e != "" {
			return false, e
		}
		v := int64(binary.LittleEndian.Uint32(vm.mem[addr : addr+4]))
		if v&0x80000000 != 0 {
			v -= 1 << 32
		}
		vm.setReg(int(args[0].value), uint64(v)&mask64)
	case opSTORE, opSW:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		if e := vm.checkAddr(addr, 4); e != "" {
			return false, e
		}
		rs := vm.reg(int(args[0].value))
		binary.LittleEndian.PutUint32(vm.mem[addr:addr+4], uint32(rs))
	case opLD:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		v, e := vm.readQ(addr)
		if e != "" {
			return false, e
		}
		vm.setReg(int(args[0].value), v)
	case opSD:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		rs := vm.reg(int(args[0].value))
		if e := vm.writeQ(addr, rs); e != "" {
			return false, e
		}
	case opLB:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		if e := vm.checkAddr(addr, 1); e != "" {
			return false, e
		}
		v := int64(vm.mem[addr])
		if v&0x80 != 0 {
			v -= 256
		}
		vm.setReg(int(args[0].value), uint64(v)&mask64)
	case opLH:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		if e := vm.checkAddr(addr, 2); e != "" {
			return false, e
		}
		v := int64(binary.LittleEndian.Uint16(vm.mem[addr : addr+2]))
		if v&0x8000 != 0 {
			v -= 65536
		}
		vm.setReg(int(args[0].value), uint64(v)&mask64)
	case opSB:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		if e := vm.checkAddr(addr, 1); e != "" {
			return false, e
		}
		vm.mem[addr] = byte(vm.reg(int(args[0].value)) & 0xFF)
	case opSH:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		if e := vm.checkAddr(addr, 2); e != "" {
			return false, e
		}
		binary.LittleEndian.PutUint16(vm.mem[addr:addr+2], uint16(vm.reg(int(args[0].value))&0xFFFF))
	// 立即数算术
	case opADDI:
		rd, rs := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		vm.setReg(rd, (vm.reg(rs)+v)&mask64)
	case opXORI:
		rd, rs := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		vm.setReg(rd, vm.reg(rs)^v)
	case opORI:
		rd, rs := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		vm.setReg(rd, vm.reg(rs)|v)
	case opANDI:
		rd, rs := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		vm.setReg(rd, vm.reg(rs)&v)
	case opLSL:
		rd, rn := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		vm.setReg(rd, (vm.reg(rn)<<(v&63))&mask64)
	case opLSR:
		rd, rn := int(args[0].value), int(args[1].value)
		v, _ := vm.val(args[2])
		vm.setReg(rd, vm.reg(rn)>>(v&63))
	case opASR:
		// 算术右移 (保留符号), 与 Python _op_asr 一致
		rd, rn := int(args[0].value), int(args[1].value)
		amt, _ := vm.val(args[2])
		sv := int64(vm.reg(rn))
		vm.setReg(rd, uint64(sv>>(amt&63))&mask64)
	case opSYS:
		if len(args) == 0 || args[0].kind != kindImm {
			return false, "SYS requires an immediate call id"
		}
		if e := vm.doSyscall(uint64(args[0].value)); e != "" {
			return false, e
		}
	}
	return false, ""
}

func (vm *vmState) loadStoreAddr(op operand) (uint64, string) {
	if op.kind == kindMem {
		addr, _ := vm.memAddr(op)
		return addr, ""
	}
	v, ok := vm.val(op)
	if !ok {
		return 0, "bad memory operand"
	}
	return v, ""
}

// sdiv: 有符号 64 位除法, 向零截断
func sdiv(a, b uint64) uint64 {
	sa := int64(a)
	sb := int64(b)
	q := sa / sb // Go 整数除法即向零截断
	return uint64(q) & mask64
}

func (vm *vmState) doOut(op operand) string {
	if op.kind == kindStr {
		vm.outWrite(vm.readCString(uint64(op.value)))
		return ""
	}
	v, ok := vm.val(op)
	if !ok {
		return "bad OUT operand"
	}
	if op.kind == kindFloat || op.kind == kindVec || op.kind == kindVecLane {
		vm.outWrite(formatFloat(bitsToF(v)))
		return ""
	}
	if v == 10 {
		vm.outByte('\n')
	} else {
		vm.outWrite(strconv.FormatUint(v, 10))
	}
	return ""
}

func (vm *vmState) readLineInt() uint64 {
	for vm.inPos < len(vm.inData) {
		end := vm.inPos
		for end < len(vm.inData) && vm.inData[end] != '\n' {
			end++
		}
		line := strings.TrimSpace(string(vm.inData[vm.inPos:end]))
		if end < len(vm.inData) {
			vm.inPos = end + 1
		} else {
			vm.inPos = end
		}
		n, err := strconv.ParseInt(line, 10, 64)
		if err == nil {
			return uint64(n) & mask64
		}
		return 0
	}
	return 0
}

// ---------------- SYS ----------------

func (vm *vmState) doSyscall(id uint64) string {
	x0 := vm.reg(0)
	x1 := vm.reg(1)
	x2 := vm.reg(2)

	switch id {
	case sysABS:
		vm.setReg(0, uint64(abs64(toSigned(x0)))&mask64)
	case sysSQRT:
		vm.setReg(0, fToBits(math.Sqrt(bitsToF(x0))))
	case sysPOW:
		vm.setReg(0, fToBits(math.Pow(bitsToF(x0), bitsToF(x1))))
	case sysSIN:
		vm.setReg(0, fToBits(math.Sin(bitsToF(x0))))
	case sysCOS:
		vm.setReg(0, fToBits(math.Cos(bitsToF(x0))))
	case sysTAN:
		vm.setReg(0, fToBits(math.Tan(bitsToF(x0))))
	case sysFADD:
		vm.setReg(0, fToBits(bitsToF(x0)+bitsToF(x1)))
	case sysFSUB:
		vm.setReg(0, fToBits(bitsToF(x0)-bitsToF(x1)))
	case sysFMUL:
		vm.setReg(0, fToBits(bitsToF(x0)*bitsToF(x1)))
	case sysFDIV:
		b := bitsToF(x1)
		if b == 0 {
			return "Float division by zero (SYS)"
		}
		vm.setReg(0, fToBits(bitsToF(x0)/b))
	case sysFCMP:
		a, b := bitsToF(x0), bitsToF(x1)
		r := uint64(0)
		if a < b {
			r = mask64 // -1 的无符号表示
		} else if a > b {
			r = 1
		}
		vm.setReg(0, r)
	case sysFTOI:
		vm.setReg(0, uint64(int64(bitsToF(x0)))&mask64)
	case sysITOF:
		vm.setReg(0, fToBits(float64(toSigned(x0))))
	case sysRAND:
		vm.setReg(0, uint64(vm.rng.Int63n(1<<31))&mask64)
	case sysSRAND:
		vm.rng.Seed(int64(x0))
	case sysTIME:
		vm.setReg(0, uint64(time.Now().Unix())&mask64)
	case sysSTRLEN:
		n := uint64(0)
		for x0+n < uint64(len(vm.mem)) && vm.mem[x0+n] != 0 {
			n++
		}
		vm.setReg(0, n)
	case sysSTRCMP:
		i := uint64(0)
		for {
			var ca, cb byte
			if x0+i < uint64(len(vm.mem)) {
				ca = vm.mem[x0+i]
			}
			if x1+i < uint64(len(vm.mem)) {
				cb = vm.mem[x1+i]
			}
			if ca != cb || ca == 0 {
				vm.setReg(0, uint64(int64(ca)-int64(cb))&mask64)
				break
			}
			i++
		}
	case sysSTRCPY, sysSTRCAT:
		dst := x0
		if id == sysSTRCAT {
			for dst < uint64(len(vm.mem)) && vm.mem[dst] != 0 {
				dst++
			}
		}
		src := x1
		i := uint64(0)
		for {
			var ch byte
			if src+i < uint64(len(vm.mem)) {
				ch = vm.mem[src+i]
			}
			if e := vm.checkAddr(dst+i, 1); e != "" {
				return e
			}
			vm.mem[dst+i] = ch
			i++
			if ch == 0 {
				break
			}
		}
		vm.setReg(0, x0)
	case sysMALLOC:
		size := (x0 + 15) &^ uint64(15)
		ptr := vm.heapPtr
		vm.heapPtr += size
		if vm.heapPtr > vm.sp {
			return "Heap exhausted"
		}
		vm.setReg(0, ptr)
	case sysPRINTFLO:
		vm.outWrite(formatFloat(bitsToF(x0)))
	case sysITOA:
		addr := vm.sysBuffer()
		s := strconv.FormatInt(toSigned(x0), 10)
		if e := vm.writeString(addr, s); e != "" {
			return e
		}
		vm.setReg(0, addr)
	case sysFTOA:
		addr := vm.sysBuffer()
		s := formatFloat(bitsToF(x0))
		if e := vm.writeString(addr, s); e != "" {
			return e
		}
		vm.setReg(0, addr)
	case sysPRINTSTR:
		vm.outWrite(vm.readCString(x0))
	case sysSTRCONCAT:
		sa := vm.readCString(x0)
		sb := vm.readCString(x1)
		data := []byte(sa + sb + "\x00")
		size := uint64(len(data)+15) &^ uint64(15)
		ptr := vm.heapPtr
		vm.heapPtr += size
		if vm.heapPtr > vm.sp {
			return "Heap exhausted (string concat)"
		}
		if e := vm.checkAddr(ptr, len(data)); e != "" {
			return e
		}
		copy(vm.mem[ptr:ptr+uint64(len(data))], data)
		vm.setReg(0, ptr)
	case sysBOOLSTR:
		addr := vm.sysBuffer()
		s := "false"
		if x0 != 0 {
			s = "true"
		}
		if e := vm.writeString(addr, s); e != "" {
			return e
		}
		vm.setReg(0, addr)
	case sysSUBSTR:
		// substr(s, start, len): 按字符索引, 越界自动裁剪
		runes := []rune(vm.readCString(x0))
		n := int64(len(runes))
		start := int64(0)
		if int64(x1) > n {
			start = n
		} else if int64(x1) > 0 {
			start = int64(x1)
		}
		length := int64(0)
		if int64(x2) > 0 {
			length = int64(x2)
		}
		end := start + length
		if end > n {
			end = n
		}
		p, e := vm.heapDupString(string(runes[start:end]))
		if e != "" {
			return e
		}
		vm.setReg(0, p)
	case sysINDEXOF:
		hay := vm.readCString(x0)
		needle := vm.readCString(x1)
		bi := strings.Index(hay, needle)
		idx := int64(-1)
		if bi >= 0 {
			idx = int64(len([]rune(hay[:bi])))
		}
		vm.setReg(0, uint64(idx)&mask64)
	case sysTOUPPER:
		p, e := vm.heapDupString(strings.ToUpper(vm.readCString(x0)))
		if e != "" {
			return e
		}
		vm.setReg(0, p)
	case sysTOLOWER:
		p, e := vm.heapDupString(strings.ToLower(vm.readCString(x0)))
		if e != "" {
			return e
		}
		vm.setReg(0, p)
	case sysABORT:
		msg := vm.readCString(x0)
		if msg != "" {
			return "Runtime abort: " + msg
		}
		return "Runtime abort"
	case sysFLOOR:
		vm.setReg(0, fToBits(math.Floor(bitsToF(x0))))
	case sysCEIL:
		vm.setReg(0, fToBits(math.Ceil(bitsToF(x0))))
	case sysROUND:
		vm.setReg(0, fToBits(math.Floor(bitsToF(x0)+0.5)))
	case sysATOI:
		s := strings.TrimSpace(vm.readCString(x0))
		n, err := strconv.ParseInt(s, 10, 64)
		if err != nil {
			vm.setReg(0, 0)
		} else {
			vm.setReg(0, uint64(n)&mask64)
		}
	case sysTRIM:
		p, e := vm.heapDupString(strings.TrimSpace(vm.readCString(x0)))
		if e != "" {
			return e
		}
		vm.setReg(0, p)
	case sysLTRIM:
		p, e := vm.heapDupString(strings.TrimLeft(vm.readCString(x0), " \t\n\r\v\f"))
		if e != "" {
			return e
		}
		vm.setReg(0, p)
	case sysRTRIM:
		p, e := vm.heapDupString(strings.TrimRight(vm.readCString(x0), " \t\n\r\v\f"))
		if e != "" {
			return e
		}
		vm.setReg(0, p)
	case sysAUDIOPLAY:
		vm.setReg(0, vm.audioPlay(vm.readCString(x0)))
	case sysAUDIOSTOP:
		vm.audioStop()
	case sysAUDIOVOL:
		vm.audioVolume(x0)
	case sysAUDIOWAIT:
		vm.audioWait()
	case sysCANVASNEW:
		vm.canvasNew(x0, x1)
	case sysCANVASSET:
		vm.canvasSetColor(x0)
	case sysCANVASRECT:
		vm.canvasRect(x0, x1, vm.reg(2), vm.reg(3))
	case sysCANVASCIRC:
		vm.canvasCircle(x0, x1, vm.reg(2))
	case sysCANVASTEXT:
		vm.canvasText(x0, x1, vm.readCString(vm.reg(2)))
	case sysCANVASLINE:
		vm.canvasLine(x0, x1, vm.reg(2), vm.reg(3))
	case sysCANVASSAVE:
		vm.setReg(0, vm.canvasSave(vm.readCString(x0)))
	case sysCANVASSHOW:
		vm.setReg(0, vm.canvasShow())
	// 系统原生交互 (文件/进程/环境/系统信息)
	case sysFILEREAD:
		vm.setReg(0, vm.fileRead(vm.readCString(x0)))
	case sysFILEWRITE:
		vm.setReg(0, vm.fileWrite(vm.readCString(x0), vm.readCString(x1)))
	case sysFILEAPPEND:
		vm.setReg(0, vm.fileAppend(vm.readCString(x0), vm.readCString(x1)))
	case sysFILEEXISTS:
		vm.setReg(0, vm.fileExists(vm.readCString(x0)))
	case sysFILEDELETE:
		vm.setReg(0, vm.fileDelete(vm.readCString(x0)))
	case sysFILESIZE:
		vm.setReg(0, vm.fileSize(vm.readCString(x0)))
	case sysMKDIR:
		vm.setReg(0, vm.mkdir(vm.readCString(x0)))
	case sysDIRLIST:
		vm.setReg(0, vm.dirList(vm.readCString(x0)))
	case sysEXEC:
		vm.setReg(0, vm.execCmd(vm.readCString(x0)))
	case sysEXECOUTPUT:
		vm.setReg(0, vm.execOutput(vm.readCString(x0)))
	case sysGETENV:
		vm.setReg(0, vm.getenv(vm.readCString(x0)))
	case sysSETENV:
		vm.setReg(0, vm.setenv(vm.readCString(x0), vm.readCString(x1)))
	case sysOSNAME:
		vm.setReg(0, vm.osName())
	case sysHOSTNAME:
		vm.setReg(0, vm.hostname())
	case sysUSERNAME:
		vm.setReg(0, vm.username())
	case sysCWD:
		vm.setReg(0, vm.cwd())
	case sysHOMEDIR:
		vm.setReg(0, vm.homeDir())
	// Termux API
	case sysTERMUXAVAIL:
		vm.setReg(0, vm.termuxAvailable())
	case sysTERMUXNOTIFY:
		vm.setReg(0, vm.termuxNotify(vm.readCString(x0), vm.readCString(x1)))
	case sysTERMUXTOAST:
		vm.setReg(0, vm.termuxToast(vm.readCString(x0)))
	case sysTERMUXCLIPGET:
		vm.setReg(0, vm.termuxClipboardGet())
	case sysTERMUXCLIPSET:
		vm.setReg(0, vm.termuxClipboardSet(vm.readCString(x0)))
	case sysTERMUXBATTERY:
		vm.setReg(0, vm.termuxBattery())
	case sysTERMUXVIBRATE:
		vm.setReg(0, vm.termuxVibrate(x0))
	case sysTERMUXTTS:
		vm.setReg(0, vm.termuxTTS(vm.readCString(x0)))
	case sysTERMUXLOCATION:
		vm.setReg(0, vm.termuxLocation())
	case sysTERMUXWIFI:
		vm.setReg(0, vm.termuxWifiInfo())
	case sysTERMUXDIALOG:
		vm.setReg(0, vm.termuxDialog(vm.readCString(x0)))
	case sysTERMUXSMS:
		vm.setReg(0, vm.termuxSmsSend(vm.readCString(x0), vm.readCString(x1)))
	default:
		return "Unknown SYS call id"
	}
	return ""
}

// heapDupString 在堆上分配 NUL 结尾字符串副本, 返回 (指针, 错误)。
func (vm *vmState) heapDupString(s string) (uint64, string) {
	data := []byte(s + "\x00")
	size := (uint64(len(data)) + 15) &^ uint64(15)
	ptr := vm.heapPtr
	vm.heapPtr += size
	if vm.heapPtr > vm.sp {
		return 0, "Heap exhausted (string operation)"
	}
	if e := vm.checkAddr(ptr, len(data)); e != "" {
		return 0, e
	}
	copy(vm.mem[ptr:ptr+uint64(len(data))], data)
	return ptr, ""
}

func abs64(v int64) int64 {
	if v < 0 {
		return -v
	}
	return v
}
