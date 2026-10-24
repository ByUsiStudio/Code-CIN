//go:build !windows && !linux && !darwin

package engine

// 其余平台 (BSD 等): 虚拟键盘优雅失败 (vkb_show / vkb_hide 返回 -1)。

func guiVkbShow() bool { return false }

func guiVkbHide() bool { return false }

func vkbProcessCleanup() {}
