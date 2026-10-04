//go:build !windows && !linux && !darwin

package engine

// 其他平台: 键盘监听不可用, 一律优雅失败 (key_hit=0, get_key=-1)。

func keyEnablePlatform() bool { return false }

func keyRestorePlatform() {}

// keyResetPlatform 重置平台侧解码中间态 (key_flush 调用; stub 无)。
func keyResetPlatform() {}

func keyNext() (uint64, bool) { return 0, false }
