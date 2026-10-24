//go:build !windows && !linux

package engine

import "image"

func guiPlatformOpen(w, h int, title string, buf *image.RGBA) bool { return false }

func guiPlatformUpdate(buf *image.RGBA) bool { return false }

func guiPlatformClose() {}

// 虚拟键盘: 仅 Windows 支持屏幕/触摸键盘, 其他平台优雅失败。
func guiVkbShow() bool { return false }

func guiVkbHide() bool { return false }
