//go:build !windows && !linux

package engine

import "image"

func guiPlatformOpen(w, h int, title string, buf *image.RGBA) bool { return false }

func guiPlatformUpdate(buf *image.RGBA) bool { return false }

func guiPlatformClose() {}
