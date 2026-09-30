//go:build !windows && !linux && !darwin

package engine

// 其他平台: 键盘监听不可用, 一律优雅失败 (key_hit=0, get_key=-1)。

func keyEnablePlatform() bool { return false }

func keyRestorePlatform() {}

func keyNext() (uint64, bool) { return 0, false }
