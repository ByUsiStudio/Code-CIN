package engine

import "sync"

var (
	argsMu      sync.RWMutex
	programArgs []string
)

// SetProgramArgs 设置 CIN 程序可见的命令行参数 (arg_count / arg 的数据源)。
// 传入 nil / 空切片表示无参数; 内部复制, 调用方随后修改无副作用。
func SetProgramArgs(args []string) {
	argsMu.Lock()
	defer argsMu.Unlock()
	programArgs = append([]string(nil), args...)
}

// currentProgramArgs 返回参数快照 (复制; VM 创建时取一次, 执行期间稳定)。
func currentProgramArgs() []string {
	argsMu.RLock()
	defer argsMu.RUnlock()
	return append([]string(nil), programArgs...)
}

// argCount SYS 129: arg_count() -> 参数个数。
func (vm *vmState) argCount() uint64 {
	return uint64(len(vm.args)) & mask64
}

// argAt SYS 130: arg(x0=i) -> 第 i 个参数 (堆字符串; 越界/负数为空串)。
func (vm *vmState) argAt(i uint64) uint64 {
	idx := toSigned(i)
	if idx < 0 || idx >= int64(len(vm.args)) {
		return vm.empty()
	}
	return vm.hs(vm.args[idx])
}