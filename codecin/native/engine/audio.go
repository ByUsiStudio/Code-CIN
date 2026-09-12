package engine

import (
	"encoding/binary"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"time"
)

// 联网音频: 下载 (http/https) 或本地文件 → 解析 WAV → 平台播放。
// 支持格式: WAV (PCM)。MP3/OGG 需外部解码器, 本实现不包含。
//
// 平台播放后端由构建约束拆分:
//   audio_windows.go (//go:build windows)   —— winmm PlaySoundW
//   audio_other.go   (//go:build !windows)  —— afplay / aplay / paplay / ffplay

// 宿主全局音频状态 (同一时刻一个音频流)。
var (
	audioActive  bool
	audioDur     time.Duration
	audioEnd     time.Time
	audioTemp    string // 当前播放的临时 WAV 文件路径 (异步播放时保留)
	audioCmd     *exec.Cmd
	audioStarted time.Time
)

// MaxResourceBytes 是 FetchResource 的下载/读取上限 (音频等宿主资源)。
const MaxResourceBytes = 64 << 20 // 64 MiB

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
		// 有上限读取: Content-Length 与实际读取量双重限制,
		// 防止恶意/异常 URL 用无限流把宿主 OOM。
		if resp.ContentLength > MaxResourceBytes {
			return nil, fmt.Errorf("resource too large: %d bytes", resp.ContentLength)
		}
		data, err := io.ReadAll(io.LimitReader(resp.Body, MaxResourceBytes+1))
		if err != nil {
			return nil, err
		}
		if len(data) > MaxResourceBytes {
			return nil, fmt.Errorf("resource too large: >%d bytes", MaxResourceBytes)
		}
		return data, nil
	}
	fi, err := os.Stat(loc)
	if err == nil && fi.Size() > MaxResourceBytes {
		return nil, fmt.Errorf("resource too large: %d bytes", fi.Size())
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

// writeTempWav 将 WAV 字节写入临时文件并记录路径。
func writeTempWav(data []byte) (string, error) {
	f, err := os.CreateTemp("", "codecin_*.wav")
	if err != nil {
		return "", err
	}
	if _, err := f.Write(data); err != nil {
		_ = f.Close()
		_ = os.Remove(f.Name())
		return "", err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(f.Name())
		return "", err
	}
	audioTemp = f.Name()
	return audioTemp, nil
}

// cleanupTempWav 删除临时 WAV 文件。
func cleanupTempWav() {
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
	// 音量控制依赖 OS 混音器, 本实现忽略 (保留参数以便后续扩展)。
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
