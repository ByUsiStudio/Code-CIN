//go:build !windows && !linux

package engine

import "image"

func guiPlatformOpen(w, h int, title string, buf *image.RGBA) bool { return false }

func guiPlatformUpdate(buf *image.RGBA) bool { return false }

func guiPlatformClose() {}

// 虚拟键盘实现见 vkb_stub.go (其余平台优雅失败)。
