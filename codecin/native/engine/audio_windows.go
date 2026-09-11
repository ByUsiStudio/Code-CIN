//go:build windows

package engine

import (
	"fmt"
	"syscall"
	"unsafe"
)

// Windows 音频后端: winmm.dll PlaySoundW (异步)。

var (
	winmm      = syscall.NewLazyDLL("winmm.dll")
	playSoundW = winmm.NewProc("PlaySoundW")
)

const (
	sndAsync    = 0x0001
	sndPurge    = 0x0040
	sndFilename = 0x00020000
)

func playPlatform(data []byte) error {
	path, err := writeTempWav(data)
	if err != nil {
		return err
	}
	p, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return err
	}
	r, _, _ := playSoundW.Call(uintptr(unsafe.Pointer(p)), 0, sndFilename|sndAsync)
	if r == 0 {
		return fmt.Errorf("PlaySoundW failed")
	}
	return nil
}

func stopPlatform() {
	playSoundW.Call(0, 0, sndPurge)
	cleanupTempWav()
}
