//go:build windows

package engine

import (
	"fmt"
	"sync"
	"syscall"
	"unsafe"
	"time"
)

// Windows 音频后端: winmm waveOut 流式播放。
// 相比 PlaySoundW 的升级: 音量真实生效 (waveOutSetVolume)、stop 即时中止
// (waveOutReset)、audio_pos 可用 (配合时间基线)、无临时文件 (直接引用内存)。
// 回调传 0, 用 WHDR_DONE 轮询 (10ms) 判断自然结束。

var (
	winmm                 = syscall.NewLazyDLL("winmm.dll")
	procWaveOutOpen       = winmm.NewProc("waveOutOpen")
	procWaveOutPrepHdr    = winmm.NewProc("waveOutPrepareHeader")
	procWaveOutWrite      = winmm.NewProc("waveOutWrite")
	procWaveOutUnprepHdr  = winmm.NewProc("waveOutUnprepareHeader")
	procWaveOutReset      = winmm.NewProc("waveOutReset")
	procWaveOutClose      = winmm.NewProc("waveOutClose")
	procWaveOutSetVolume  = winmm.NewProc("waveOutSetVolume")
)

const (
	waveMapper = ^uintptr(0) // (UINT)-1
	whdrDone   = 0x1         // WHDR_DONE
)

// waveFormatEx 手工布局 (sizeof=18, 与 WAVEFORMATEX 一致)。
type waveFormatEx struct {
	wFormatTag      uint16 // 0: PCM=1
	nChannels       uint16 // 2
	nSamplesPerSec  uint32 // 4
	nAvgBytesPerSec uint32 // 8
	nBlockAlign     uint16 // 12
	wBitsPerSample  uint16 // 14
	cbSize          uint16 // 16
}

// waveHdr 手工布局 (x64 sizeof=48, 与 WAVEHDR 一致)。
type waveHdr struct {
	lpData          uintptr // 0
	dwBufferLength  uint32  // 8
	dwBytesRecorded uint32  // 12
	dwUser          uintptr // 16
	dwFlags         uint32  // 24
	dwLoops         uint32  // 28
	lpNext          uintptr // 32
	reserved        uintptr // 40
}

var (
	waveMu     sync.Mutex
	waveHandle uintptr
	waveHeader *waveHdr
)

// playPlatform 打开设备并整段写入 (waveOutWrite 异步播放), 失败同步上报。
// 调用前 audioPlayBytes 已确保上一段停止。
func playPlatform(data []byte) error {
	wi, ok := wavParse(data)
	if !ok {
		return fmt.Errorf("bad wav")
	}
	blockAlign := uint32(wi.channels * wi.bits / 8)
	if blockAlign == 0 || uint32(wi.sampleRate) == 0 {
		return fmt.Errorf("bad wav format")
	}
	wf := waveFormatEx{
		wFormatTag:      1, // PCM
		nChannels:       wi.channels,
		nSamplesPerSec:  uint32(wi.sampleRate),
		nAvgBytesPerSec: uint32(wi.sampleRate) * blockAlign,
		nBlockAlign:     uint16(blockAlign),
		wBitsPerSample:  wi.bits,
	}
	var h uintptr
	if r, _, _ := procWaveOutOpen.Call(uintptr(unsafe.Pointer(&h)), waveMapper,
		uintptr(unsafe.Pointer(&wf)), 0, 0, 0); r != 0 {
		return fmt.Errorf("waveOutOpen: %d", r)
	}
	waveMu.Lock()
	waveHandle = h
	waveMu.Unlock()
	setVolumePlatform(audioLevelNow())

	hdr := &waveHdr{
		lpData:         uintptr(unsafe.Pointer(&data[wi.dataOffset])),
		dwBufferLength: uint32(wi.dataLength),
	}
	if r, _, _ := procWaveOutPrepHdr.Call(h, uintptr(unsafe.Pointer(hdr)),
		unsafe.Sizeof(waveHdr{})); r != 0 {
		procWaveOutClose.Call(h)
		return fmt.Errorf("waveOutPrepareHeader: %d", r)
	}
	waveMu.Lock()
	waveHeader = hdr
	waveMu.Unlock()
	if r, _, _ := procWaveOutWrite.Call(h, uintptr(unsafe.Pointer(hdr)),
		unsafe.Sizeof(waveHdr{})); r != 0 {
		waveMu.Lock()
		waveHeader = nil
		waveHandle = 0
		waveMu.Unlock()
		procWaveOutUnprepHdr.Call(h, uintptr(unsafe.Pointer(hdr)), unsafe.Sizeof(waveHdr{}))
		procWaveOutClose.Call(h)
		return fmt.Errorf("waveOutWrite: %d", r)
	}
	return nil
}

// waitPlatform 等待当前播放自然结束 (WHDR_DONE 轮询) 并清理设备。
// stopPlatform 的 waveOutReset 会把缓冲立即置 done, 从而解除等待。
func waitPlatform() {
	for {
		waveMu.Lock()
		h, hdr := waveHandle, waveHeader
		waveMu.Unlock()
		if h == 0 {
			return // 已被 stop 清理
		}
		if hdr != nil && hdr.dwFlags&whdrDone != 0 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	waveMu.Lock()
	h, hdr := waveHandle, waveHeader
	waveHandle, waveHeader = 0, nil
	waveMu.Unlock()
	if h != 0 {
		procWaveOutUnprepHdr.Call(h, uintptr(unsafe.Pointer(hdr)), unsafe.Sizeof(waveHdr{}))
		procWaveOutClose.Call(h)
	}
}

// stopPlatform 中止播放 (waveOutReset 即时停止并标记缓冲 done)。
func stopPlatform() {
	waveMu.Lock()
	h := waveHandle
	waveMu.Unlock()
	if h != 0 {
		procWaveOutReset.Call(h)
	}
	cleanupTempWav()
}

// setVolumePlatform 实时设置音量 (0..100 -> 线性 0..0xFFFF 每声道)。
func setVolumePlatform(level int) {
	v := uint32(uint64(level) * 0xFFFF / 100)
	vol := uintptr(v) | uintptr(v)<<16
	waveMu.Lock()
	h := waveHandle
	waveMu.Unlock()
	if h != 0 {
		procWaveOutSetVolume.Call(h, vol)
	}
}
