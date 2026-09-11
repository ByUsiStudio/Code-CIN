package engine

import (
	"os"
	"os/exec"
	"os/user"
	"runtime"
	"sort"
	"strings"
)

// 系统原生交互: 文件/目录/进程/环境变量/系统信息 (跨平台 Windows/Linux/macOS)。
// 全部经 SYS 宿主调用暴露给 CIN。

// empty 返回一个安全的空串地址 (惰性分配, 避免影响 heap_ptr 的三路径一致性)。
func (vm *vmState) empty() uint64 {
	if vm.emptyStr == 0 {
		p, _ := vm.heapDupString("")
		vm.emptyStr = p
	}
	cur := vm.emptyStr
	return cur
}

// hs 在堆上分配字符串; 失败时回退到预留空串 (安全无副作用)。
func (vm *vmState) hs(s string) uint64 {
	p, e := vm.heapDupString(s)
	if e != "" {
		return vm.empty()
	}
	return p
}

// ---------------- 文件系统 ----------------

func (vm *vmState) fileRead(path string) uint64 {
	data, err := os.ReadFile(path)
	if err != nil {
		return vm.empty()
	}
	return vm.hs(string(data))
}

func (vm *vmState) fileWrite(path, content string) uint64 {
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		return mask64
	}
	return 0
}

func (vm *vmState) fileAppend(path, content string) uint64 {
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return mask64
	}
	defer f.Close()
	if _, err := f.WriteString(content); err != nil {
		return mask64
	}
	return 0
}

func (vm *vmState) fileExists(path string) uint64 {
	if _, err := os.Stat(path); err == nil {
		return 1
	}
	return 0
}

func (vm *vmState) fileDelete(path string) uint64 {
	if err := os.Remove(path); err != nil {
		return mask64
	}
	return 0
}

func (vm *vmState) fileSize(path string) uint64 {
	fi, err := os.Stat(path)
	if err != nil {
		return mask64
	}
	return uint64(fi.Size())
}

func (vm *vmState) mkdir(path string) uint64 {
	if err := os.MkdirAll(path, 0o755); err != nil {
		return mask64
	}
	return 0
}

func (vm *vmState) dirList(path string) uint64 {
	entries, err := os.ReadDir(path)
	if err != nil {
		return vm.empty()
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() {
			name += "/"
		}
		names = append(names, name)
	}
	sort.Strings(names)
	return vm.hs(strings.Join(names, "\n"))
}

// ---------------- 进程执行 ----------------

func shellCommand(cmd string) *exec.Cmd {
	if runtime.GOOS == "windows" {
		return exec.Command("cmd", "/c", cmd)
	}
	return exec.Command("sh", "-c", cmd)
}

func (vm *vmState) execCmd(cmd string) uint64 {
	err := shellCommand(cmd).Run()
	if err == nil {
		return 0
	}
	if ee, ok := err.(*exec.ExitError); ok {
		return uint64(int64(ee.ExitCode())) & mask64
	}
	return mask64
}

func (vm *vmState) execOutput(cmd string) uint64 {
	out, err := shellCommand(cmd).Output()
	if err != nil && len(out) == 0 {
		return vm.empty()
	}
	return vm.hs(string(out))
}

// ---------------- 环境变量 / 系统信息 ----------------

func (vm *vmState) getenv(name string) uint64 {
	return vm.hs(os.Getenv(name))
}

func (vm *vmState) setenv(name, value string) uint64 {
	if err := os.Setenv(name, value); err != nil {
		return mask64
	}
	return 0
}

func (vm *vmState) osName() uint64 {
	return vm.hs(runtime.GOOS)
}

func (vm *vmState) hostname() uint64 {
	h, err := os.Hostname()
	if err != nil {
		return vm.empty()
	}
	return vm.hs(h)
}

func (vm *vmState) username() uint64 {
	if u, err := user.Current(); err == nil && u.Username != "" {
		return vm.hs(u.Username)
	}
	for _, key := range []string{"USER", "USERNAME", "LOGNAME"} {
		if v := os.Getenv(key); v != "" {
			return vm.hs(v)
		}
	}
	return vm.empty()
}

func (vm *vmState) cwd() uint64 {
	d, err := os.Getwd()
	if err != nil {
		return vm.empty()
	}
	return vm.hs(d)
}

func (vm *vmState) homeDir() uint64 {
	if h, err := os.UserHomeDir(); err == nil && h != "" {
		return vm.hs(h)
	}
	if h := os.Getenv("HOME"); h != "" {
		return vm.hs(h)
	}
	return vm.hs(os.Getenv("USERPROFILE"))
}
