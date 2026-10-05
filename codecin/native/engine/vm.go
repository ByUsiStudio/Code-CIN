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
	"fmt"
	"math"
	"math/rand"
	"strconv"
	"strings"
	"time"
)

const (
	StatusOK          = 0
	StatusDone        = 1
	StatusUnsupported = 2
	StatusError       = 3
)

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
	prog      []instruction
	entry     int
	mem       []byte
	regs      [33]uint64
	vec       [32][4]float64
	sp        uint64
	pc        int
	heapPtr   uint64
	heapBase  uint64 // 堆起始 (heapPtr 初值; 报错信息用)
	steps     uint64
	flags     struct{ N, Z, C, V bool }
	out       strings.Builder
	outOver   bool // 输出超过 maxOutputBytes (截断并置错误)
	outCount  int  // 两种模式的累计输出字节 (16 MiB 限额依据)
	outDirect bool // 键盘监听激活 (真实终端): 输出直写 stdout, 不进缓冲
	sysIdx    int
	rng       *rand.Rand
	inData    []byte
	inPos     int
	args      []string // 传给 CIN 程序的命令行参数 (arg_count / arg)
	emptyStr  uint64   // 预留空串地址 (宿主调用返回失败时的安全空串)
	sandbox   bool     // 沙箱模式: 屏蔽宿主能力系统调用
	// 脏页位图 (稀疏内存回传用): 64KB 页, 每 bit 一页。
	dirtyBits  []uint64
	lastHTTPSt int64 // 最近一次 http_req 的状态码 (-1 无请求)
}

// dirtyPageBits 每页一个 bit; 1 GiB 内存 = 16384 页 = 2 KiB 位图。
const dirtyPageShift = 16 // 2^16 = 64 KiB

// touch 标记 [addr, addr+n) 覆盖的页为脏 (写内存必须经过这里)。
// 位图不足时自动扩容 (手工构造的 vmState / 高地址写入均安全)。
func (vm *vmState) touch(addr, n uint64) {
	if n == 0 {
		n = 1
	}
	first := addr >> dirtyPageShift
	last := (addr + n - 1) >> dirtyPageShift
	for p := first; p <= last; p++ {
		idx := p >> 6
		if idx >= uint64(len(vm.dirtyBits)) {
			grow := make([]uint64, idx+1)
			copy(grow, vm.dirtyBits)
			vm.dirtyBits = grow
		}
		vm.dirtyBits[idx] |= 1 << (p & 63)
	}
}

// dirtySegments 收集脏页并合并成相邻段 (按地址升序)。
func (vm *vmState) dirtySegments() []MemSeg {
	var segs []MemSeg
	n := uint64(len(vm.dirtyBits)) * 64
	memLen := uint64(len(vm.mem))
	start, end := uint64(0), uint64(0) // 当前合并段 [start, end) 页号
	flush := func() {
		if end > start {
			lo := start << dirtyPageShift
			if lo >= memLen {
				return
			}
			hi := end << dirtyPageShift
			if hi > memLen {
				hi = memLen // 末页可能超出实际内存大小
			}
			segs = append(segs, MemSeg{Addr: lo, Data: vm.mem[lo:hi:hi]})
		}
	}
	for p := uint64(0); p < n; p++ {
		if vm.dirtyBits[p>>6]&(1<<(p&63)) == 0 {
			continue
		}
		if end == p { // 连续: 扩段
			end = p + 1
			continue
		}
		flush()
		start, end = p, p+1
	}
	flush()
	return segs
}

// maxOutputBytes 限制单次运行的输出总量: 程序用无限打印不能把宿主 OOM。
const maxOutputBytes = 16 << 20 // 16 MiB

func (vm *vmState) outWrite(s string) {
	if vm.outOver {
		return
	}
	remaining := maxOutputBytes - vm.outCount
	if remaining <= 0 {
		vm.outOver = true
		return
	}
	if len(s) > remaining {
		s = s[:remaining]
		vm.outOver = true
	}
	vm.outCount += len(s)
	if vm.outDirect {
		keyOutSink(s)
		return
	}
	vm.out.WriteString(s)
}

// outByte 追加单字节输出 (同上限)。
func (vm *vmState) outByte(b byte) {
	if vm.outOver {
		return
	}
	if vm.outCount >= maxOutputBytes {
		vm.outOver = true
		return
	}
	vm.outCount++
	if vm.outDirect {
		keyOutSink(string(b))
		return
	}
	vm.out.WriteByte(b)
}

// enterDirectOut 键盘监听激活时切换直写模式: 先把已缓冲输出落到终端
// (激活前的提示先显示), 之后输出逐条直写 stdout, 不再等程序结束一次性回传。
// 非终端 (管道/测试捕获) 永远不会激活, 不会走到这里。
// 激活后 vm.out 恒空 -> Result.Output 为空 -> Python 侧不再打印 (不双打)。
func (vm *vmState) enterDirectOut() {
	if vm.outDirect {
		return
	}
	vm.outDirect = true
	if vm.out.Len() > 0 {
		keyOutSink(vm.out.String())
		vm.out.Reset()
	}
}

// MemSeg 是一段连续内存: [Addr, Addr+len(Data))。
type MemSeg struct {
	Addr uint64
	Data []byte
}

// Result 是 RunV2 的执行结果。
type Result struct {
	Status   int
	Pc       int
	Sp       uint64
	HeapPtr  uint64
	Steps    uint64
	Regs     [33]uint64
	Vec      [32][4]float64
	Flags    struct{ N, Z, C, V bool }
	Output   string
	ErrMsg   string
	Segments []MemSeg // 脏页合并段 (稀疏内存回传)
	MemSize  int64
}

// RunOptions 是 RunV2 的参数。
type RunOptions struct {
	BC       []byte   // UCBC 字节码
	Segments []MemSeg // 初始内存段 (数据段 / .crom 恢复内容)
	Entry    int64
	SP       int64
	HeapBase int64
	MemSize  int64 // 逻辑内存大小 (引擎按此分配, OS 懒提交)
	Input    []byte
	MaxSteps int64
	Sandbox  bool  // 沙箱: 屏蔽宿主能力系统调用
	Seed     int64 // 随机种子; 0 = 时间随机
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

// RunV2 执行程序字节码, 返回结果快照。内存按 MemSize 由引擎分配 (稀疏:
// OS 懒提交), 初始内容来自 Segments, 执行后按脏页回传 Segments。
func RunV2(opts *RunOptions) *Result {
	prog, ent, ok := decodeBytecode(opts.BC)
	if !ok {
		return &Result{Status: StatusError, ErrMsg: "bad bytecode"}
	}
	entry := int(opts.Entry)
	if entry >= 0 {
		ent = entry
	}
	memSize := opts.MemSize
	if memSize < 256 {
		memSize = 256
	}
	if memSize > 1<<40 {
		memSize = 1 << 40
	}
	mem := make([]byte, memSize)
	for _, seg := range opts.Segments {
		if seg.Addr >= uint64(memSize) || len(seg.Data) == 0 {
			continue
		}
		n := uint64(len(seg.Data))
		if seg.Addr+n > uint64(memSize) {
			n = uint64(memSize) - seg.Addr
		}
		copy(mem[seg.Addr:seg.Addr+n], seg.Data)
	}
	vm := &vmState{
		prog:     prog,
		entry:    ent,
		mem:      mem,
		sp:       uint64(opts.SP),
		pc:       ent,
		heapPtr:  uint64(opts.HeapBase),
		heapBase: uint64(opts.HeapBase),
		rng:      rand.New(rand.NewSource(time.Now().UnixNano())),
		inData:   opts.Input,
		args:     currentProgramArgs(),
		sandbox:  opts.Sandbox,
	}
	if opts.Seed != 0 {
		vm.rng.Seed(opts.Seed)
	}
	vm.dirtyBits = make([]uint64,
		(uint64(memSize)+(1<<(dirtyPageShift+6))-1)>>(dirtyPageShift+6))
	// 初始段视为已写内容: 数据段通常会被改写, 直接并入脏位图, 免去逐字节 touch。
	for _, seg := range opts.Segments {
		if seg.Addr < uint64(memSize) && len(seg.Data) > 0 {
			vm.touch(seg.Addr, uint64(len(seg.Data)))
		}
	}

	// 键盘监听可能切换终端 raw mode, 任何出口都必须恢复 (幂等)
	defer keyboardRestore()

	finish := func(status int, errMsg string) *Result {
		// 输出超限一并报错: 不能把"被截断的输出"当成正常结果
		if vm.outOver && status != StatusError {
			status = StatusError
			if errMsg == "" {
				errMsg = "output limit exceeded (16 MiB)"
			}
		}
		r := &Result{
			Status:  status,
			Pc:      vm.pc,
			Sp:      vm.sp,
			HeapPtr: vm.heapPtr,
			Steps:   vm.steps,
			Regs:    vm.regs,
			Vec:     vm.vec,
			Output:  vm.out.String(),
			ErrMsg:  errMsg,
			MemSize: memSize,
		}
		r.Flags.N, r.Flags.Z, r.Flags.C, r.Flags.V = vm.flags.N, vm.flags.Z, vm.flags.C, vm.flags.V
		r.Segments = vm.dirtySegments()
		return r
	}

	for {
		if opts.MaxSteps > 0 && int64(vm.steps) >= opts.MaxSteps {
			// 步数用尽不等于正常停机: 旧实现返回 StatusDone, 与 HALT 无法区分,
			// 于是被截断的程序在 Python 侧被当成 halted=true 正常结束。
			return finish(StatusError,
				fmt.Sprintf("instruction limit reached (%d steps)", opts.MaxSteps))
		}
		if vm.pc < 0 || vm.pc >= len(vm.prog) {
			return finish(StatusDone, "")
		}
		ins := vm.prog[vm.pc]
		if !opcodeSupported(ins.opcode) {
			return finish(StatusUnsupported, "")
		}
		// 未知 SYS 功能号: 直接报错 (解释器已删除, 无回退路径)
		if ins.opcode == opSYS {
			if len(ins.args) == 0 || ins.args[0].kind != kindImm ||
				!syscallSupported(uint64(ins.args[0].value)) {
				return finish(StatusError, fmt.Sprintf(
					"unsupported SYS call id: %d", ins.args[0].value))
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

// Run 是 v1 兼容入口: 调用方提供整块内存 (原地修改), 内部转成单段输入,
// 执行后把脏段写回。仅供旧 Go 测试与 AOT 旧路径使用。
func Run(bc []byte, mem []byte, entry, sp, heapBase int64, inData []byte,
	maxSteps int64) *Result {
	opts := &RunOptions{
		BC:       bc,
		Segments: []MemSeg{{Addr: 0, Data: mem}},
		Entry:    entry,
		SP:       sp,
		HeapBase: heapBase,
		MemSize:  int64(len(mem)),
		Input:    inData,
		MaxSteps: maxSteps,
	}
	res := RunV2(opts)
	if res == nil {
		return nil
	}
	for _, seg := range res.Segments {
		if seg.Addr < uint64(len(mem)) {
			n := uint64(len(seg.Data))
			if seg.Addr+n > uint64(len(mem)) {
				n = uint64(len(mem)) - seg.Addr
			}
			copy(mem[seg.Addr:seg.Addr+n], seg.Data)
		}
	}
	return res
}

// opcodeSupported: 全部 ISA 指令都在 Go 引擎内实现 (解释器已删除, 无回退)。
func opcodeSupported(op uint8) bool {
	return int(op) < len(argCounts)
}

func syscallSupported(id uint64) bool {
	return id <= sysDNSLOOKUP
}

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
	case kindVec:
		// 与 Python 解释器一致: 标量读 lane0 并截断为 int
		return uint64(int64(vm.vec[op.value&31][0])), true
	case kindVecLane:
		return uint64(int64(vm.vec[op.value&31][op.extra&3])), true
	}
	return 0, false
}

func (vm *vmState) checkAddr(addr uint64, width int) string {
	if uint64(len(vm.mem)) < uint64(width) || addr > uint64(len(vm.mem))-uint64(width) {
		hint := ""
		if addr >= 1<<63 {
			// 按位模 2^64 后为负数: 典型成因是栈溢出 (SP 被推到负地址)
			// 或野指针。可读性提示, 与 Python 侧 memory.py 文案一致。
			hint = " (negative address: stack overflow or bad pointer?)"
		}
		return "Address 0x" + strconv.FormatUint(addr, 16) + " out of bounds" + hint
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
	vm.touch(addr, 8)
	return ""
}

func toSigned(v uint64) int64 { return int64(v) }

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

func bitsToF(b uint64) float64 { return math.Float64frombits(b) }
func fToBits(f float64) uint64 { return math.Float64bits(f) }

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
	vm.touch(addr, uint64(len(s))+1)
	return ""
}

func (vm *vmState) sysBuffer() uint64 {
	idx := vm.sysIdx % 8
	vm.sysIdx++
	return vm.heapPtr + 2048 + uint64(idx)*64
}

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
		vm.touch(addr, 4)
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
		vm.touch(addr, 1)
	case opSH:
		addr, e := vm.loadStoreAddr(args[1])
		if e != "" {
			return false, e
		}
		if e := vm.checkAddr(addr, 2); e != "" {
			return false, e
		}
		binary.LittleEndian.PutUint16(vm.mem[addr:addr+2], uint16(vm.reg(int(args[0].value))&0xFFFF))
		vm.touch(addr, 2)
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
	default:
		// 其余 ISA 指令 (ARM64/RISC-V 扩展/向量/浮点) 在 ops_ext.go
		return vm.execExt(ins)
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
	if vm.inPos < len(vm.inData) {
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
	// 输入缓冲耗尽: 回退读标准输入 (交互 / AOT 产物)。
	// 阻塞前先冲刷输出缓冲, 让 "请输入..." 提示先出现在终端上。
	vm.flushOut()
	line, ok := stdinReadLine()
	if !ok {
		return 0
	}
	n, perr := strconv.ParseInt(strings.TrimSpace(line), 10, 64)
	if perr != nil {
		return 0
	}
	return uint64(n) & mask64
}

// readLineStr SYS 131: input_str() -> 读入一行 UTF-8 文本 (不含行尾)。
// 优先消费预读缓冲 (与解释器路径 / input() 的管道语义一致), 耗尽后回退
// 标准输入; EOF 返回空串。行尾 \n / \r\n 三平台统一剥离。
func (vm *vmState) readLineStr() uint64 {
	if vm.inPos < len(vm.inData) {
		end := vm.inPos
		for end < len(vm.inData) && vm.inData[end] != '\n' {
			end++
		}
		line := string(vm.inData[vm.inPos:end])
		if end < len(vm.inData) {
			vm.inPos = end + 1
		} else {
			vm.inPos = end
		}
		return vm.hs(trimEOL(line))
	}
	vm.flushOut()
	line, ok := stdinReadLine()
	if !ok {
		return vm.empty()
	}
	return vm.hs(trimEOL(line))
}

// flushOut 在阻塞读标准输入前, 把已缓冲输出冲刷到真实终端。
// 仅在 stdout 为终端时生效 (交互提示可见); 管道/重定向/测试捕获保持
// 纯缓冲语义 —— 输出仍随 Result.Output 一次性回传, 字节序列不变。
// 冲刷后 vm.out 清空, 回传结果里不再包含这部分 (不重复打印)。
func (vm *vmState) flushOut() {
	if vm.outDirect || vm.out.Len() == 0 || !stdoutIsTerminal() {
		return
	}
	consoleInit()
	keyOutSink(vm.out.String())
	vm.out.Reset()
}

// ---------------- SYS ----------------

// coreSyscalls 是沙箱模式下仍放行的核心 VM 机制 (非宿主能力)。
// 与解释器 cpu.py 的 _CORE_SYS_CALLS 保持一致。
var coreSyscalls = map[uint64]bool{
	sysALLOCFRAME: true,
	sysTIMEUS:     true,
	sysTIMENS:     true,
}

// syscallName 返回 SYS 功能号的可读名 (生成表, 与 isa.py Syscall 名一致)。
func syscallName(id int) string {
	if id >= 0 && id < len(syscallNames) && syscallNames[id] != "" {
		return syscallNames[id]
	}
	return "UNKNOWN"
}

func (vm *vmState) doSyscall(id uint64) string {
	// 沙箱模式: 拦截全部宿主能力 (音频/画布/GUI/文件/进程/环境/网络/FFI/
	// Termux/键盘...), 只放行核心 VM 机制 (ALLOCFRAME/TIMEUS/TIMENS) 与
	// AUDIOPLAY 以下的纯计算类内建。文本与原解释器 cpu.py 保持一致。
	if vm.sandbox && id >= sysAUDIOPLAY && !coreSyscalls[id] {
		return "Host capability disabled in sandbox mode (SYS " +
			intToString(int(id)) + ": " + syscallName(int(id)) + ")"
	}

	x0 := vm.reg(0)
	x1 := vm.reg(1)
	x2 := vm.reg(2)
	x3 := vm.reg(3)
	x4 := vm.reg(4)

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
		vm.touch(dst, i)
		vm.setReg(0, x0)
	case sysMALLOC:
		size := (x0 + 15) &^ uint64(15)
		ptr := vm.heapPtr
		vm.heapPtr += size
		if vm.heapPtr > vm.sp {
			free := uint64(0)
			if vm.sp > ptr {
				free = vm.sp - ptr
			}
			// 文本与 Python 解释器 (cpu.py) 保持一致
			return fmt.Sprintf("Heap exhausted: need %d bytes, free %d bytes "+
				"(heap 0x%x..0x%x). Try --mem-size (default 65536) or "+
				"reduce allocations", size, free, vm.heapBase, vm.sp)
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
			free := uint64(0)
			if vm.sp > ptr {
				free = vm.sp - ptr
			}
			return fmt.Sprintf("Heap exhausted (string concat): need %d bytes, "+
				"free %d bytes (heap 0x%x..0x%x). Try --mem-size "+
				"(default 65536) or reduce allocations",
				size, free, vm.heapBase, vm.sp)
		}
		if e := vm.checkAddr(ptr, len(data)); e != "" {
			return e
		}
		copy(vm.mem[ptr:ptr+uint64(len(data))], data)
		vm.touch(ptr, uint64(len(data)))
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
		// substr(s, start, len): 按字节索引 (与 strlen/s[i] 一致), 越界自动裁剪
		data := []byte(vm.readCString(x0))
		n := int64(len(data))
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
		p, e := vm.heapDupString(string(data[start:end]))
		if e != "" {
			return e
		}
		vm.setReg(0, p)
	case sysINDEXOF:
		hay := vm.readCString(x0)
		needle := vm.readCString(x1)
		// strings.Index 即字节索引 (与 strlen/s[i] 一致); 找不到为 -1
		vm.setReg(0, uint64(int64(strings.Index(hay, needle)))&mask64)
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
	// GUI 窗口 (Windows Win32 / Linux X11 / 其他平台优雅失败)
	case sysGUINEW:
		vm.setReg(0, vm.guiNew(x0, x1, vm.readCString(vm.reg(2))))
	case sysGUIUPDATE:
		vm.setReg(0, vm.guiUpdate())
	case sysGUICLOSE:
		vm.setReg(0, vm.guiClose())
	case sysGUICLOSED:
		vm.setReg(0, vm.guiClosedQ())
	case sysMOUSEX:
		vm.setReg(0, vm.mouseX())
	case sysMOUSEY:
		vm.setReg(0, vm.mouseY())
	case sysMOUSEBTN:
		vm.setReg(0, vm.mouseBtn())
	case sysGUIACTIVE:
		vm.setReg(0, vm.guiActiveQ())
	// 音频扩展 (播放进度与蜂鸣合成)
	case sysAUDIOPOS:
		vm.setReg(0, vm.audioPos())
	case sysAUDIOBEEP:
		vm.setReg(0, vm.audioBeep(x0, x1))
	// 音频控制增强 (查询 / 暂停恢复 / 音量读取)
	case sysAUDIODUR:
		vm.setReg(0, vm.audioDuration())
	case sysAUDIOPLAYING:
		if audioPlayingNow() {
			vm.setReg(0, 1)
		} else {
			vm.setReg(0, 0)
		}
	case sysAUDIOPAUSE:
		vm.setReg(0, vm.audioPause())
	case sysAUDIORESUME:
		vm.setReg(0, vm.audioResume())
	case sysAUDIOLEVEL:
		vm.setReg(0, uint64(audioLevelNow()))
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
	// 路径与文件系统扩展
	case sysPATHJOIN:
		vm.setReg(0, vm.pathJoin(vm.readCString(x0), vm.readCString(x1)))
	case sysPATHBASENAME:
		vm.setReg(0, vm.pathBasename(vm.readCString(x0)))
	case sysPATHDIRNAME:
		vm.setReg(0, vm.pathDirname(vm.readCString(x0)))
	case sysPATHABS:
		vm.setReg(0, vm.pathAbs(vm.readCString(x0)))
	case sysFILECOPY:
		vm.setReg(0, vm.fileCopy(vm.readCString(x0), vm.readCString(x1)))
	case sysFILEMOVE:
		vm.setReg(0, vm.fileMove(vm.readCString(x0), vm.readCString(x1)))
	case sysDIRREMOVE:
		vm.setReg(0, vm.dirRemove(vm.readCString(x0)))
	case sysISDIR:
		vm.setReg(0, vm.isDir(vm.readCString(x0)))
	case sysFILEMTIME:
		vm.setReg(0, vm.fileMtime(vm.readCString(x0)))
	case sysTEMPDIR:
		vm.setReg(0, vm.tempDir())
	case sysCHDIR:
		vm.setReg(0, vm.chdir(vm.readCString(x0)))
	// 时间与系统信息
	case sysTIMEMS:
		vm.setReg(0, vm.timeMs())
	case sysSLEEPMS:
		vm.setReg(0, vm.sleepMs(x0))
	case sysCPUCOUNT:
		vm.setReg(0, vm.cpuCount())
	case sysARCHNAME:
		vm.setReg(0, vm.archName())
	case sysMEMINFO:
		vm.setReg(0, vm.memInfo())
	case sysISANDROID:
		vm.setReg(0, vm.isAndroid())
	// 网络
	case sysHTTPGET:
		vm.setReg(0, vm.httpGet(vm.readCString(x0)))
	case sysHTTPPOST:
		vm.setReg(0, vm.httpPost(vm.readCString(x0), vm.readCString(x1)))
	case sysDOWNLOAD:
		vm.setReg(0, vm.download(vm.readCString(x0), vm.readCString(x1)))
	// 编码与哈希
	case sysSHA256:
		vm.setReg(0, vm.sha256Hex(vm.readCString(x0)))
	case sysBASE64ENC:
		vm.setReg(0, vm.base64Encode(vm.readCString(x0)))
	case sysBASE64DEC:
		vm.setReg(0, vm.base64Decode(vm.readCString(x0)))
	// 桌面集成
	case sysCLIPGET:
		vm.setReg(0, vm.clipboardGet())
	case sysCLIPSET:
		vm.setReg(0, vm.clipboardSet(vm.readCString(x0)))
	case sysNOTIFY:
		vm.setReg(0, vm.notify(vm.readCString(x0), vm.readCString(x1)))
	case sysOPENURL:
		vm.setReg(0, vm.openURL(vm.readCString(x0)))
	// Android / Termux 扩展
	case sysANDROIDINTENT:
		vm.setReg(0, vm.androidIntent(vm.readCString(x0), vm.readCString(x1)))
	case sysTERMUXCALL:
		vm.setReg(0, vm.termuxCall(vm.readCString(x0)))
	case sysTERMUXSHARE:
		vm.setReg(0, vm.termuxShare(vm.readCString(x0)))
	case sysTERMUXTORCH:
		vm.setReg(0, vm.termuxTorch(x0))
	case sysTERMUXVOLUME:
		vm.setReg(0, vm.termuxVolume(vm.readCString(x0), x1))
	case sysTERMUXBRIGHT:
		vm.setReg(0, vm.termuxBrightness(x0))
	case sysTERMUXCAMERA:
		vm.setReg(0, vm.termuxCameraPhoto(vm.readCString(x0)))
	case sysTERMUXFINGER:
		vm.setReg(0, vm.termuxFingerprint())
	case sysTERMUXSENSOR:
		vm.setReg(0, vm.termuxSensor(vm.readCString(x0)))
	// 键盘输入监听 (非阻塞轮询)
	case sysKEYHIT:
		vm.setReg(0, vm.keyHit())
	case sysKEYGET:
		vm.setReg(0, vm.keyGet())
	case sysKEYFLUSH:
		vm.setReg(0, vm.keyFlush())
	// 命令行参数与行输入 (跨平台一致)
	case sysARGC:
		vm.setReg(0, vm.argCount())
	case sysARGV:
		vm.setReg(0, vm.argAt(x0))
	case sysREADLINE:
		vm.setReg(0, vm.readLineStr())
	// 核心 VM 机制 (非宿主能力, 与 Python 解释器 cpu.py 语义逐一致)
	case sysALLOCFRAME:
		fb := x0
		guard := vm.heapPtr + 4096
		// 有符号比较: SP 绕回 (负地址) 必须判溢出, 不能先取模再比大小
		newSP := int64(vm.sp) - int64(fb)
		if newSP < int64(guard) {
			avail := int64(vm.sp) - int64(guard)
			if avail < 0 {
				avail = 0
			}
			return fmt.Sprintf("Stack overflow: frame needs %d bytes, stack "+
				"headroom only %d bytes (SP 0x%x, guard 0x%x, memory %d "+
				"bytes). Try --mem-size (default 1073741824) or smaller local "+
				"arrays", fb, avail, vm.sp, guard, len(vm.mem))
		}
		vm.sp = uint64(newSP)
		vm.setReg(0, vm.sp)
	case sysTIMEUS:
		vm.setReg(0, uint64(time.Now().UnixNano()/1000))
	case sysTIMENS:
		vm.setReg(0, uint64(time.Now().UnixNano()))
	// ---- FFI 动态库调用 ----
	case sysDLOPEN:
		vm.setReg(0, vm.ffiOpen(vm.readCString(x0)))
	case sysDLSYM:
		vm.setReg(0, vm.ffiSym(x0, vm.readCString(x1)))
	case sysFFICALL:
		r, e := vm.ffiCall(x0, x1, x2, false)
		if e != "" {
			return e
		}
		vm.setReg(0, r)
	case sysFFICALLF:
		r, e := vm.ffiCall(x0, x1, x2, true)
		if e != "" {
			return e
		}
		vm.setReg(0, r)
	case sysLIBCLOSE:
		vm.setReg(0, vm.ffiClose(x0))
	// ---- 网络: HTTP 扩展 / TCP / UDP / DNS ----
	// 传参约定: 与编译器 _gen_host_sys 一致, 参数按自然顺序放入 x0..x(n-1);
	// 字符串参数为 NUL 结尾堆串指针, 返回字符串为新堆串指针。
	case sysHTTPREQ:
		vm.setReg(0, vm.httpReq(vm.readCString(x0), vm.readCString(x1),
			vm.readCString(x2), vm.readCString(x3)))
	case sysHTTPCODE:
		if vm.lastHTTPSt < 0 {
			vm.setReg(0, mask64)
		} else {
			vm.setReg(0, uint64(vm.lastHTTPSt))
		}
	case sysTCPDIAL:
		vm.setReg(0, vm.tcpDial(vm.readCString(x0), int(x1)))
	case sysTCPSEND:
		vm.setReg(0, vm.tcpSend(x0, x1, int(x2)))
	case sysTCPRECV:
		n, e := vm.tcpRecv(x0, x1, int(x2))
		if e != "" {
			return e
		}
		vm.setReg(0, n)
	case sysTCPCLOSE:
		vm.setReg(0, vm.tcpClose(x0))
	case sysTCPLISTEN:
		vm.setReg(0, vm.tcpListen(x0))
	case sysTCPACCEPT:
		vm.setReg(0, vm.tcpAccept(x0))
	case sysUDPOPEN:
		vm.setReg(0, vm.udpOpen(x0))
	case sysUDPSENDTO:
		vm.setReg(0, vm.udpSendTo(x0, vm.readCString(x1), int(x2), x3, int(x4)))
	case sysUDPRECVFROM:
		n, e := vm.udpRecvFrom(x0, x1, int(x2), x3)
		if e != "" {
			return e
		}
		vm.setReg(0, n)
	case sysUDPCLOSE:
		vm.setReg(0, vm.udpClose(x0))
	case sysDNSLOOKUP:
		vm.setReg(0, vm.dnsLookup(vm.readCString(x0)))
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
		free := uint64(0)
		if vm.sp > ptr {
			free = vm.sp - ptr
		}
		return 0, fmt.Sprintf("Heap exhausted (string operation): need %d "+
			"bytes, free %d bytes (heap 0x%x..0x%x). Try --mem-size "+
			"(default 65536) or reduce allocations",
			size, free, vm.heapBase, vm.sp)
	}
	if e := vm.checkAddr(ptr, len(data)); e != "" {
		return 0, e
	}
	copy(vm.mem[ptr:ptr+uint64(len(data))], data)
	vm.touch(ptr, uint64(len(data)))
	return ptr, ""
}

func abs64(v int64) int64 {
	if v < 0 {
		return -v
	}
	return v
}
