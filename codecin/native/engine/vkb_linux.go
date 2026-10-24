//go:build linux

package engine

import (
	"os"
	"os/exec"
	"sync"
)

// Linux 虚拟键盘 (屏幕键盘): 依次尝试桌面常见实现, 以子进程方式启动
// onboard (GNOME/多数发行版) / matchbox-keyboard / xvkbd。
// 收起时结束该子进程, 并对已由桌面启动的实例做兜底退出 (onboard -x /
// pkill)。无图形会话 (DISPLAY 与 WAYLAND_DISPLAY 都未设置) 或均未安装
// 时优雅失败 (-1), 不影响调用方。
var (
	vkbMu   sync.Mutex
	vkbProc *exec.Cmd
)

type vkbLaunch struct {
	name string
	args []string
}

func vkbLinuxLaunches() []vkbLaunch {
	return []vkbLaunch{
		{name: "onboard"},
		{name: "matchbox-keyboard"},
		{name: "xvkbd", args: []string{"-geometry", "480x160+0-0"}},
	}
}

func guiVkbShow() bool {
	vkbMu.Lock()
	defer vkbMu.Unlock()
	if vkbProc != nil {
		if vkbProc.Process != nil {
			return true // 本进程启动的实例仍在运行
		}
		vkbProc = nil
	}
	if os.Getenv("DISPLAY") == "" && os.Getenv("WAYLAND_DISPLAY") == "" {
		return false // 无图形会话
	}
	for _, l := range vkbLinuxLaunches() {
		path, err := exec.LookPath(l.name)
		if err != nil {
			continue
		}
		cmd := exec.Command(path, l.args...)
		if err := cmd.Start(); err != nil {
			continue
		}
		vkbProc = cmd
		return true
	}
	return false
}

func guiVkbHide() bool {
	vkbMu.Lock()
	cmd := vkbProc
	vkbProc = nil
	vkbMu.Unlock()
	hidden := false
	if cmd != nil && cmd.Process != nil {
		_ = cmd.Process.Kill()
		_, _ = cmd.Process.Wait()
		hidden = true
	}
	// 桌面会话里可能已有常驻实例 (单实例程序): 用各工具自身的方式收起
	if path, err := exec.LookPath("onboard"); err == nil {
		if exec.Command(path, "-x").Run() == nil {
			hidden = true
		}
	}
	for _, name := range []string{"matchbox-keyboard", "xvkbd"} {
		if exec.Command("pkill", "-x", name).Run() == nil {
			hidden = true
		}
	}
	return hidden
}

// vkbProcessCleanup 程序退出时确保不遗留本进程启动的屏幕键盘。
func vkbProcessCleanup() {
	vkbMu.Lock()
	defer vkbMu.Unlock()
	if vkbProc != nil && vkbProc.Process != nil {
		_ = vkbProc.Process.Kill()
		_, _ = vkbProc.Process.Wait()
	}
	vkbProc = nil
}
