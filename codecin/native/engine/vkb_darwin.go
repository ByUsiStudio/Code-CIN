//go:build darwin

package engine

import (
	"os"
	"os/exec"
	"sync"
)

// macOS 虚拟键盘: 打开系统自带的辅助功能键盘 (Accessibility Keyboard)。
// 系统未提供公开的编程接口, 只能以 App 方式启动/退出, 属 best-effort:
// 未启用辅助功能键盘或系统路径不存在时优雅失败 (-1)。
var (
	vkbDarwinMu  sync.Mutex
	vkbDarwinRun bool
)

// 不同 macOS 版本的位置 (按顺序探测)。
var vkbDarwinApps = []string{
	"/System/Library/Input Methods/Accessibility Keyboard.app",
	"/System/Library/CoreServices/Accessibility Keyboard.app",
	"/System/Library/Input Methods/Keyboard Viewer.app",
}

func guiVkbShow() bool {
	vkbDarwinMu.Lock()
	defer vkbDarwinMu.Unlock()
	if vkbDarwinRun {
		return true
	}
	for _, app := range vkbDarwinApps {
		if _, err := os.Stat(app); err != nil {
			continue
		}
		if err := exec.Command("open", app).Run(); err != nil {
			continue
		}
		vkbDarwinRun = true
		return true
	}
	return false
}

func guiVkbHide() bool {
	vkbDarwinMu.Lock()
	defer vkbDarwinMu.Unlock()
	if !vkbDarwinRun {
		return false
	}
	vkbDarwinRun = false
	hidden := false
	for _, app := range vkbDarwinApps {
		if exec.Command("pkill", "-f", app).Run() == nil {
			hidden = true
		}
	}
	return hidden
}

// vkbProcessCleanup macOS 无子进程可清理 (键盘由系统 App 托管)。
func vkbProcessCleanup() {}
