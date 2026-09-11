package engine

import (
	"encoding/binary"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

// 联网音频: 下载 (http/https) 或本地文件 → 解析 WAV → 平台播放。
// 支持格式: WAV (PCM)。MP3/OGG 需外部解码器, 本实现不包含 (沙箱离线, 无法引入依赖)。

// 宿主全局音频状态 (同一时刻一个音频流)。
var (
	audioActive  bool
	audioDur     time.Duration
	audioEnd     time.Time
	audioTemp    string // 当前播放的临时 WAV 文件路径 (异步播放时保留)
	audioCmd     *exec.Cmd
	audioStarted time.Time
)

// FetchResource 下载 URL 或读取本地文件, 返回原始字节。
func FetchResource(loc string) ([]byte, error) {
	if strings.HasPrefix(loc, "http://") || strings.HasPrefix(loc, "https://") {
		client := &http.Client{Timeout: 30 * time.Second}
		resp, err := client.Get(loc)
		if err != nil {
			return nil, err
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			return nil, fmt.Errorf("http status %d", resp.StatusCode)
		}
		return io.ReadAll(resp.Body)
	}
	return os.ReadFile(loc)
}

// WavDuration 解析 WAV 头并返回时长 (0 表示无法识别)。
func WavDuration(data []byte) time.Duration {
	if len(data) < 44 || string(data[0:4]) != "RIFF" || string(data[8:12]) != "WAVE" {
		return 0
	}
	byteRate := binary.LittleEndian.Uint32(data[28:32])
	dataSize := binary.LittleEndian.Uint32(data[40:44])
	if byteRate == 0 {
		return 0
	}
	return time.Duration(float64(dataSize) / float64(byteRate) * float64(time.Second))
}

// isWav 判断是否为 WAV (RIFF/WAVE)。
func isWav(data []byte) bool {
	return len(data) >= 12 && string(data[0:4]) == "RIFF" && string(data[8:12]) == "WAVE"
}

// playPlatform 异步播放 WAV 字节; 返回 error。按平台选择后端。
func playPlatform(data []byte) error {
	f, err := os.CreateTemp("", "codecin_*.wav")
	if err != nil {
		return err
	}
	if _, err := f.Write(data); err != nil {
		_ = f.Close()
		_ = os.Remove(f.Name())
		return err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(f.Name())
		return err
	}
	audioTemp = f.Name()

	switch runtime.GOOS {
	case "windows":
		return playWindowsWav(audioTemp)
	case "darwin":
		audioCmd = exec.Command("afplay", audioTemp)
		return audioCmd.Start()
	default: // linux 等
		for _, p := range []string{"aplay", "paplay", "ffplay"} {
			if _, err := exec.LookPath(p); err == nil {
				if p == "ffplay" {
					audioCmd = exec.Command(p, "-nodisp", "-autoexit", "-loglevel", "quiet", audioTemp)
				} else {
					audioCmd = exec.Command(p, audioTemp)
				}
				return audioCmd.Start()
			}
		}
		return fmt.Errorf("no audio player found (aplay/paplay/ffplay)")
	}
}

// stopPlatform 停止平台播放。
func stopPlatform() {
	switch runtime.GOOS {
	case "windows":
		stopWindowsWav()
	default:
		if audioCmd != nil && audioCmd.Process != nil {
			_ = audioCmd.Process.Kill()
		}
		audioCmd = nil
	}
	if audioTemp != "" {
		_ = os.Remove(audioTemp)
		audioTemp = ""
	}
}

func (vm *vmState) audioPlay(loc string) uint64 {
	data, err := FetchResource(loc)
	if err != nil {
		return mask64 // -1
	}
	if !isWav(data) {
		return mask64 // 不支持的格式
	}
	if err := playPlatform(data); err != nil {
		return mask64
	}
	audioActive = true
	audioDur = WavDuration(data)
	audioStarted = time.Now()
	audioEnd = audioStarted.Add(audioDur)
	return 0
}

func (vm *vmState) audioStop() {
	audioActive = false
	stopPlatform()
}

func (vm *vmState) audioVolume(level uint64) {
	// 音量控制依赖 OS 混音器, 本实现忽略 (返回前记录, 便于后续扩展)。
	_ = level
}

func (vm *vmState) audioWait() {
	if !audioActive {
		return
	}
	if audioDur <= 0 {
		return
	}
	remain := time.Until(audioEnd)
	if remain > 0 {
		time.Sleep(remain)
	}
	audioActive = false
}

// ---- Windows: winmm PlaySound (异步) ----

var (
	winmm      = syscall.NewLazyDLL("winmm.dll")
	playSoundW = winmm.NewProc("PlaySoundW")
)

const (
	sndAsync   = 0x0001
	sndPurge   = 0x0040
	sndFilename = 0x00020000
)

func playWindowsWav(path string) error {
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

func stopWindowsWav() {
	playSoundW.Call(0, 0, sndPurge)
}
