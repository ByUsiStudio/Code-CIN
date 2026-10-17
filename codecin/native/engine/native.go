package engine

// NativeRuntime 是 native 代码生成后端使用的运行时。
//
// AOT 的 native 后端不再把 UCBC 字节码交给解释循环，而是把每条指令直接
// 生成成 Go 语句；本类型只暴露这些语句需要的原语 (寄存器/内存/标志/栈/
// SYS/输出)。整数/ARM64/RISC-V 扩展/FP/Vector 指令直接使用这里的原语；
// 少数动态控制流/无法静态求值的操作数通过 Exec 复用现有 VM 语义。
//
// 生成代码只依赖本文件的导出 API；不要把 vmState 暴露出去。

import (
	"encoding/binary"
	"fmt"
	"math"
	"math/bits"
	"math/rand"
	"os"
	"time"
)

// Arg 是生成代码向 Exec 传递的操作数 (与 VM 的 operand 同构)。
type Arg struct {
	Kind  uint8
	Value int64
	Extra int64
}

// 操作数种类 (与 engine 内部 kind* 常量一致)。
const (
	KindReg     = kindReg
	KindImm     = kindImm
	KindMem     = kindMem
	KindCond    = kindCond
	KindFloat   = kindFloat
	KindStr     = kindStr
	KindVec     = kindVec
	KindVecLane = kindVecLane
)

// NativeRuntime 持有 native 程序的全部可变状态。
type NativeRuntime struct {
	vm       *vmState
	maxSteps int64
	errMsg   string
}

// NewNativeRuntime 创建执行环境。
//
// memSize/sp/heapBase 与 VM 路径一致；segments 是初始化内存段。
// maxSteps <= 0 表示不限制。
func NewNativeRuntime(memSize, sp, heapBase int64, segments []MemSeg,
	maxSteps int64) *NativeRuntime {

	if memSize < 256 {
		memSize = 256
	}
	if memSize > 1<<40 {
		memSize = 1 << 40
	}
	mem := make([]byte, memSize)
	for _, seg := range segments {
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
		mem:      mem,
		sp:       uint64(sp),
		heapPtr:  uint64(heapBase),
		heapBase: uint64(heapBase),
		rng:      rand.New(rand.NewSource(time.Now().UnixNano())),
		args:     currentProgramArgs(),
	}
	vm.dirtyBits = make([]uint64,
		(uint64(memSize)+(1<<(dirtyPageShift+6))-1)>>(dirtyPageShift+6))
	return &NativeRuntime{vm: vm, maxSteps: maxSteps}
}

// SetInput 设置 stdin 重定向数据 (IN 指令使用)。
func (r *NativeRuntime) SetInput(data []byte) { r.vm.inData = data }

// SetSandbox 打开沙箱宿主调用限制。
func (r *NativeRuntime) SetSandbox(on bool) { r.vm.sandbox = on }

// Tick 推进一条指令的步数并检查上限 (与 VM 的 maxSteps 语义一致)。
func (r *NativeRuntime) Tick() string {
	if r.maxSteps > 0 && int64(r.vm.steps) >= r.maxSteps {
		return fmt.Sprintf("instruction limit reached (%d steps)", r.maxSteps)
	}
	r.vm.steps++
	return ""
}

// SetPC 记录当前指令索引 (少数回退指令如 AUIPC 需要)。
func (r *NativeRuntime) SetPC(pc int) { r.vm.pc = pc }

// PC 返回当前指令索引 (回退控制流指令执行后读取)。
func (r *NativeRuntime) PC() int { return r.vm.pc }

// ---------------- 寄存器 ----------------

// Reg 读取寄存器 (32 = SP, 31 = XZR)。
func (r *NativeRuntime) Reg(n int) uint64 { return r.vm.reg(n) }

// SetReg 写寄存器 (32 写 SP, 31 忽略)。
func (r *NativeRuntime) SetReg(n int, v uint64) { r.vm.setReg(n, v) }

// ---------------- 值/地址 ----------------

// Val 按操作数种类取值 (reg/imm/float/str/mem/cond/vec/veclane)。
func (r *NativeRuntime) Val(kind uint8, value, extra int64) (uint64, string) {
	v, ok := r.vm.val(operand{kind: kind, value: value, extra: extra})
	if !ok {
		return 0, "bad operand"
	}
	return v, ""
}

// Addr 计算内存地址: base >= 0 时为 reg(base)+off，否则为绝对地址 off。
func (r *NativeRuntime) Addr(base, off int64) uint64 {
	addr, _ := r.vm.memAddr(operand{kind: kindMem, value: base, extra: off})
	return addr
}

// ---------------- 整数运算 ----------------

func (r *NativeRuntime) Mov(rd int, v uint64) { r.vm.setReg(rd, v) }

func (r *NativeRuntime) Add(rd int, v uint64) {
	r.vm.setReg(rd, (r.vm.reg(rd)+v)&mask64)
}

func (r *NativeRuntime) AddTo(rd, rs int, v uint64) {
	r.vm.setReg(rd, (r.vm.reg(rs)+v)&mask64)
}

func (r *NativeRuntime) Sub(rd int, v uint64) {
	r.vm.setReg(rd, (r.vm.reg(rd)-v)&mask64)
}

func (r *NativeRuntime) SubFrom(rd, rs int, v uint64) {
	r.vm.setReg(rd, (r.vm.reg(rs)-v)&mask64)
}

func (r *NativeRuntime) Mul(rd int, v uint64) {
	r.vm.setReg(rd, (r.vm.reg(rd)*v)&mask64)
}

func (r *NativeRuntime) MulFrom(rd, rs int, v uint64) {
	r.vm.setReg(rd, (r.vm.reg(rs)*v)&mask64)
}

// Div 执行有符号 64 位除法 (向零截断)，除数为 0 返回错误。
func (r *NativeRuntime) Div(rd int, v uint64) string {
	if v == 0 {
		return "Division by zero"
	}
	r.vm.setReg(rd, sdiv(r.vm.reg(rd), v))
	return ""
}

func (r *NativeRuntime) DivFrom(rd, rs int, v uint64) string {
	if v == 0 {
		return "Division by zero"
	}
	r.vm.setReg(rd, sdiv(r.vm.reg(rs), v))
	return ""
}

func (r *NativeRuntime) And(rd int, v uint64) { r.vm.setReg(rd, r.vm.reg(rd)&v) }
func (r *NativeRuntime) Or(rd int, v uint64)  { r.vm.setReg(rd, r.vm.reg(rd)|v) }
func (r *NativeRuntime) Xor(rd int, v uint64) { r.vm.setReg(rd, r.vm.reg(rd)^v) }
func (r *NativeRuntime) Mvn(rd int, v uint64) { r.vm.setReg(rd, ^v&mask64) }
func (r *NativeRuntime) Inc(rd int)           { r.vm.setReg(rd, (r.vm.reg(rd)+1)&mask64) }
func (r *NativeRuntime) Dec(rd int)           { r.vm.setReg(rd, (r.vm.reg(rd)-1)&mask64) }
func (r *NativeRuntime) Shl(rd int, v uint64) { r.vm.setReg(rd, (r.vm.reg(rd)<<(v&63))&mask64) }
func (r *NativeRuntime) Shr(rd int, v uint64) { r.vm.setReg(rd, r.vm.reg(rd)>>(v&63)) }
func (r *NativeRuntime) Asr(rd int, v uint64) {
	r.vm.setReg(rd, uint64(int64(r.vm.reg(rd))>>(v&63))&mask64)
}

func (r *NativeRuntime) LslFrom(rd, rs int, v uint64) {
	r.vm.setReg(rd, (r.vm.reg(rs)<<(v&63))&mask64)
}

func (r *NativeRuntime) LsrFrom(rd, rs int, v uint64) {
	r.vm.setReg(rd, r.vm.reg(rs)>>(v&63))
}

func (r *NativeRuntime) AsrFrom(rd, rs int, v uint64) {
	r.vm.setReg(rd, uint64(int64(r.vm.reg(rs))>>(v&63))&mask64)
}

func (r *NativeRuntime) Cmp(a, b uint64) { r.vm.setFlagsSub(a, b) }

// ---------------- ARM64/RISC-V 扩展整数 ----------------

func (r *NativeRuntime) AddS(rd, rn int, v uint64) {
	a := r.vm.reg(rn)
	res := (a + v) & mask64
	r.vm.setFlagsAddGo(a, v, res)
	r.vm.setReg(rd, res)
}

func (r *NativeRuntime) SubS(rd, rn int, v uint64) {
	a := r.vm.reg(rn)
	r.vm.setFlagsSub(a, v)
	r.vm.setReg(rd, (a-v)&mask64)
}

func (r *NativeRuntime) AddC(rd, rn int, v uint64) {
	c := uint64(0)
	if r.vm.flags.C {
		c = 1
	}
	a := r.vm.reg(rn)
	res := (a + v + c) & mask64
	r.vm.setFlagsAddGo(a, v, res)
	r.vm.setReg(rd, res)
}

func (r *NativeRuntime) SubC(rd, rn int, v uint64) {
	c := uint64(1)
	if r.vm.flags.C {
		c = 0
	}
	a := r.vm.reg(rn)
	r.vm.setFlagsSub(a, v)
	r.vm.setReg(rd, (a-v-c)&mask64)
}

func (r *NativeRuntime) Ror(rd, rn int, v uint64) {
	amt := v & 63
	x := r.vm.reg(rn)
	if amt == 0 {
		r.vm.setReg(rd, x)
		return
	}
	r.vm.setReg(rd, ((x>>amt)|(x<<(64-amt)))&mask64)
}

func (r *NativeRuntime) Eor(rd, rn int, v uint64) {
	r.vm.setReg(rd, r.vm.reg(rn)^v)
}

func (r *NativeRuntime) Bic(rd, rn int, v uint64) {
	r.vm.setReg(rd, r.vm.reg(rn)&(^v&mask64))
}

func (r *NativeRuntime) Orn(rd, rn int, v uint64) {
	r.vm.setReg(rd, r.vm.reg(rn)|(^v&mask64))
}

// Ldp 读取两个 64 位值到 rt/rt2。
func (r *NativeRuntime) Ldp(rt, rt2 int, addr uint64) string {
	v1, e := r.vm.readQ(addr)
	if e != "" {
		return e
	}
	v2, e := r.vm.readQ(addr + 8)
	if e != "" {
		return e
	}
	r.vm.setReg(rt, v1)
	r.vm.setReg(rt2, v2)
	return ""
}

// Stp 把 rt/rt2 两个 64 位值写入 addr/addr+8。
func (r *NativeRuntime) Stp(rt, rt2 int, addr uint64) string {
	if e := r.vm.writeQ(addr, r.vm.reg(rt)); e != "" {
		return e
	}
	return r.vm.writeQ(addr+8, r.vm.reg(rt2))
}

// CondSelect 实现 CSEL/CSINC/CSINV/CSNEG。
func (r *NativeRuntime) CondSelect(kind string, rd, rn int, v uint64,
	cond uint8) {
	if r.vm.condition(cond) {
		r.vm.setReg(rd, r.vm.reg(rn))
		return
	}
	switch kind {
	case "CSEL":
		r.vm.setReg(rd, v)
	case "CSINC":
		r.vm.setReg(rd, v+1)
	case "CSINV":
		r.vm.setReg(rd, ^v&mask64)
	case "CSNEG":
		r.vm.setReg(rd, uint64(-int64(v))&mask64)
	}
}

func (r *NativeRuntime) SignExtend(rd int, v uint64, width int) {
	var out int64
	switch width {
	case 8:
		out = int64(int8(v))
	case 16:
		out = int64(int16(v))
	case 32:
		out = int64(int32(v))
	default:
		out = int64(v)
	}
	r.vm.setReg(rd, uint64(out)&mask64)
}

func (r *NativeRuntime) ZeroExtend(rd int, v uint64, width int) {
	switch width {
	case 8:
		v &= 0xFF
	case 16:
		v &= 0xFFFF
	case 32:
		v &= 0xFFFFFFFF
	}
	r.vm.setReg(rd, v)
}

func (r *NativeRuntime) Clz(rd int, v uint64)  { r.vm.setReg(rd, uint64(bits.LeadingZeros64(v))) }
func (r *NativeRuntime) Rbit(rd int, v uint64) { r.vm.setReg(rd, bits.Reverse64(v)) }
func (r *NativeRuntime) Rev(rd int, v uint64)  { r.vm.setReg(rd, bits.ReverseBytes64(v)) }

func (r *NativeRuntime) Cls(rd int, v uint64) {
	sv := int64(v)
	if sv >= 0 {
		if sv == 0 {
			r.vm.setReg(rd, 63)
		} else {
			r.vm.setReg(rd, uint64(63-bits.Len64(uint64(sv))))
		}
		return
	}
	r.vm.setReg(rd, uint64(63-bits.Len64(^uint64(sv))+1))
}

func (r *NativeRuntime) SetLessImm(rd, rs int, v uint64, signed bool) {
	var less bool
	if signed {
		less = int64(r.vm.reg(rs)) < int64(v)
	} else {
		less = r.vm.reg(rs) < v
	}
	if less {
		r.vm.setReg(rd, 1)
	} else {
		r.vm.setReg(rd, 0)
	}
}

func (r *NativeRuntime) Lui(rd int, v uint64) {
	r.vm.setReg(rd, (v&0xFFFFF)<<12)
}

// Auipc 的 pc 参数是当前指令索引 (内部按 pc+1 计算)。
func (r *NativeRuntime) Auipc(rd int, v uint64, pc int) {
	r.vm.setReg(rd, (uint64(pc+1)+((v&0xFFFFF)<<12))&mask64)
}

// ---------------- 浮点/向量 ----------------

func (r *NativeRuntime) FAdd(rd, rn, rm int) {
	r.vm.setVecScalar(rd, r.vm.vecScalar(rn)+r.vm.vecScalar(rm))
}

func (r *NativeRuntime) FSub(rd, rn, rm int) {
	r.vm.setVecScalar(rd, r.vm.vecScalar(rn)-r.vm.vecScalar(rm))
}

func (r *NativeRuntime) FMul(rd, rn, rm int) {
	r.vm.setVecScalar(rd, r.vm.vecScalar(rn)*r.vm.vecScalar(rm))
}

func (r *NativeRuntime) FDiv(rd, rn, rm int) string {
	b := r.vm.vecScalar(rm)
	if b == 0 {
		return "Float division by zero"
	}
	r.vm.setVecScalar(rd, r.vm.vecScalar(rn)/b)
	return ""
}

func (r *NativeRuntime) FCmp(rn, rm int) {
	a, b := r.vm.vecScalar(rn), r.vm.vecScalar(rm)
	r.vm.flags.Z = a == b
	r.vm.flags.N = a < b
	r.vm.flags.C = a >= b
	r.vm.flags.V = false
}

func (r *NativeRuntime) FCvtToInt(rd, rs int, lane int) {
	v := r.vm.vec[rs&31][lane&3]
	r.vm.setReg(rd, uint64(int64(v))&mask64)
}

func (r *NativeRuntime) FCvtToFloat(rd, rs int) {
	r.vm.setVecScalar(rd, float64(r.vm.reg(rs)))
}

func (r *NativeRuntime) FAbs(rd, rs int) {
	r.vm.setVecScalar(rd, math.Abs(r.vm.vecScalar(rs)))
}

func (r *NativeRuntime) FNeg(rd, rs int) {
	r.vm.setVecScalar(rd, -r.vm.vecScalar(rs))
}

func (r *NativeRuntime) LoadF32(rd int, addr uint64) string {
	if e := r.vm.checkAddr(addr, 4); e != "" {
		return e
	}
	f := math.Float32frombits(binary.LittleEndian.Uint32(r.vm.mem[addr : addr+4]))
	r.vm.setVecScalar(rd, float64(f))
	return ""
}

func (r *NativeRuntime) StoreF32(rs int, addr uint64) string {
	if e := r.vm.checkAddr(addr, 4); e != "" {
		return e
	}
	binary.LittleEndian.PutUint32(r.vm.mem[addr:addr+4],
		math.Float32bits(float32(r.vm.vecScalar(rs))))
	r.vm.touch(addr, 4)
	return ""
}

func (r *NativeRuntime) VOp(op string, rd, rn, rm int) string {
	a, b := r.vm.vec[rn&31], r.vm.vec[rm&31]
	var out [4]float64
	for i := 0; i < 4; i++ {
		switch op {
		case "VADD":
			out[i] = a[i] + b[i]
		case "VSUB":
			out[i] = a[i] - b[i]
		case "VMUL":
			out[i] = a[i] * b[i]
		case "VDIV":
			if b[i] == 0 {
				return "Vector division by zero"
			}
			out[i] = a[i] / b[i]
		}
	}
	r.vm.vec[rd&31] = out
	return ""
}

func (r *NativeRuntime) VLoad1(rd int, addr uint64) string {
	if e := r.vm.checkAddr(addr, 16); e != "" {
		return e
	}
	data := r.vm.mem[addr : addr+16 : addr+16]
	r.vm.vec[rd&31] = [4]float64{
		float64(math.Float32frombits(binary.LittleEndian.Uint32(data[0:4]))),
		float64(math.Float32frombits(binary.LittleEndian.Uint32(data[4:8]))),
		float64(math.Float32frombits(binary.LittleEndian.Uint32(data[8:12]))),
		float64(math.Float32frombits(binary.LittleEndian.Uint32(data[12:16]))),
	}
	return ""
}

func (r *NativeRuntime) VStore1(rs int, addr uint64) string {
	if e := r.vm.checkAddr(addr, 16); e != "" {
		return e
	}
	v := r.vm.vec[rs&31]
	for i := 0; i < 4; i++ {
		binary.LittleEndian.PutUint32(r.vm.mem[addr+uint64(i)*4:],
			math.Float32bits(float32(v[i])))
	}
	r.vm.touch(addr, 16)
	return ""
}

// ---------------- 标志位 ----------------

func (r *NativeRuntime) N() bool { return r.vm.flags.N }
func (r *NativeRuntime) Z() bool { return r.vm.flags.Z }
func (r *NativeRuntime) C() bool { return r.vm.flags.C }
func (r *NativeRuntime) V() bool { return r.vm.flags.V }

// Cond 按 ARM64 条件码求值 (0=EQ .. 14=AL)。
func (r *NativeRuntime) Cond(code uint8) bool { return r.vm.condition(code) }

// ---------------- 栈 ----------------

func (r *NativeRuntime) Push(v uint64) string  { return r.vm.push(v) }
func (r *NativeRuntime) Pop() (uint64, string) { return r.vm.pop() }

// PopRet 弹出返回地址并转成指令索引 (RET 使用)。
func (r *NativeRuntime) PopRet() (int, string) {
	v, e := r.vm.pop()
	return int(v), e
}

// ---------------- 内存 ----------------

// Load 从 addr 读取 width 字节；signed 为真时做符号扩展。
func (r *NativeRuntime) Load(rd int, addr uint64, width int, signed bool) string {
	if e := r.vm.checkAddr(addr, width); e != "" {
		return e
	}
	var v int64
	switch width {
	case 1:
		v = int64(r.vm.mem[addr])
		if signed && v&0x80 != 0 {
			v -= 256
		}
		if !signed {
			v &= 0xFF
		}
	case 2:
		v = int64(binary.LittleEndian.Uint16(r.vm.mem[addr : addr+2]))
		if signed && v&0x8000 != 0 {
			v -= 65536
		}
		if !signed {
			v &= 0xFFFF
		}
	case 4:
		v = int64(binary.LittleEndian.Uint32(r.vm.mem[addr : addr+4]))
		if signed && v&0x80000000 != 0 {
			v -= 1 << 32
		}
		if !signed {
			v &= 0xFFFFFFFF
		}
	default:
		v = int64(binary.LittleEndian.Uint64(r.vm.mem[addr : addr+8]))
	}
	r.vm.setReg(rd, uint64(v)&mask64)
	return ""
}

// Store 把 rs 的低 width 字节写入 addr。
func (r *NativeRuntime) Store(rs int, addr uint64, width int) string {
	if e := r.vm.checkAddr(addr, width); e != "" {
		return e
	}
	v := r.vm.reg(rs)
	switch width {
	case 1:
		r.vm.mem[addr] = byte(v)
	case 2:
		binary.LittleEndian.PutUint16(r.vm.mem[addr:addr+2], uint16(v))
	case 4:
		binary.LittleEndian.PutUint32(r.vm.mem[addr:addr+4], uint32(v))
	default:
		binary.LittleEndian.PutUint64(r.vm.mem[addr:addr+8], v)
	}
	r.vm.touch(addr, uint64(width))
	return ""
}

// LoadQ 供 mem 操作数取值 (64 位)。
func (r *NativeRuntime) LoadQ(addr uint64) (uint64, string) { return r.vm.readQ(addr) }

// ---------------- I/O / SYS ----------------

// In 读取一行整数到 rd (与 VM 的 IN 一致)。
func (r *NativeRuntime) In(rd int) { r.vm.setReg(rd, r.vm.readLineInt()) }

// Out 输出操作数 (字符串/整数/浮点)，kind 取 Kind* 常量。
func (r *NativeRuntime) Out(kind uint8, value, extra int64) string {
	return r.vm.doOut(operand{kind: kind, value: value, extra: extra})
}

// Sys 执行宿主/核心系统调用，参数在 X0..X4。
func (r *NativeRuntime) Sys(id uint64) string { return r.vm.doSyscall(id) }

// ---------------- 回退执行 ----------------

// Exec 执行一条未直接生成的指令，复用 VM 语义。
// pc 是当前指令索引；内部按 VM 主循环的约定使用 pc+1 (JAL/JALR/AUIPC/CALL
// 的链接地址因此是下一条指令)。动态控制流 (JALR/BR/动态 CALL) 也走这里。
func (r *NativeRuntime) Exec(op string, args []Arg, pc int) string {
	opv, ok := opcodeByName[op]
	if !ok {
		return "unsupported opcode: " + op
	}
	insArgs := make([]operand, len(args))
	for i, a := range args {
		insArgs[i] = operand{kind: a.Kind, value: a.Value, extra: a.Extra}
	}
	// 与 VM 主循环一致: execute 看到的是"已自增"的 pc (JAL/JALR/AUIPC/CALL
	// 的链接地址因此是下一条指令)。
	r.vm.pc = pc + 1
	_, e := r.vm.execute(instruction{opcode: opv, args: insArgs})
	return e
}

// ---------------- 收尾 ----------------

// Abort 记录运行期错误；返回 false 供生成代码直接 return。
func (r *NativeRuntime) Abort(msg string) bool {
	if r.errMsg == "" {
		r.errMsg = msg
	}
	return false
}

// Finish 输出程序结果并返回进程退出码 (0=正常, 1=错误)。
func (r *NativeRuntime) Finish(ok bool) int {
	// 键盘监听可能切换终端 raw mode, 任何出口都必须恢复 (幂等)。
	keyboardRestore()
	if r.vm.outOver && ok {
		ok = false
		if r.errMsg == "" {
			r.errMsg = "output limit exceeded (16 MiB)"
		}
	}
	if r.vm.out.Len() > 0 {
		fmt.Print(r.vm.out.String())
	}
	if ok {
		return 0
	}
	if r.errMsg == "" {
		r.errMsg = "runtime error"
	}
	fmt.Fprintf(os.Stderr, "runtime error: %s\n", r.errMsg)
	return 1
}
