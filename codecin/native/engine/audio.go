package engine

import (
	"encoding/binary"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"
)

// 宿主全局音频状态 (同一时刻一个音频流)。
var (
	audioMu      sync.Mutex
	audioActive  bool
	audioDur     time.Duration
	audioStarted time.Time
	audioEnd     time.Time
	audioLevel   int = 100 // 0..100 (audio_volume; Windows 实时生效, Unix 传入播放器)
	audioDone    chan struct{}
	audioTemp    string  // 当前播放的临时 WAV 文件路径 (Unix 异步播放时保留)
	audioCmd     *exec.Cmd
	audioCmdMu   sync.Mutex
	audioData    []byte // 播放中的 WAV 字节 (waveOut 直接引用, 保活防 GC)
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

// wavInfo 是 WAV 解析结果 (fmt 与 data chunk)。
type wavInfo struct {
	channels   uint16
	sampleRate uint32
	bits       uint16
	dataOffset int
	dataLength int
}

// wavParse 遍历 RIFF chunk 提取 fmt 与 data (容忍 fact/LIST 等额外块与截断)。
func wavParse(data []byte) (wavInfo, bool) {
	var wi wavInfo
	if len(data) < 12 || string(data[0:4]) != "RIFF" || string(data[8:12]) != "WAVE" {
		return wi, false
	}
	pos := 12
	gotFmt := false
	for pos+8 <= len(data) {
		id := string(data[pos : pos+4])
		size := int(binary.LittleEndian.Uint32(data[pos+4 : pos+8]))
		body := pos + 8
		if body+size > len(data) {
			size = len(data) - body // 截断容错
		}
		switch id {
		case "fmt ":
			if size < 16 {
				return wi, false
			}
			wi.channels = binary.LittleEndian.Uint16(data[body+2 : body+4])
			wi.sampleRate = binary.LittleEndian.Uint32(data[body+4 : body+8])
			wi.bits = binary.LittleEndian.Uint16(data[body+14 : body+16])
			gotFmt = true
		case "data":
			wi.dataOffset = body
			wi.dataLength = size
		}
		pos = body + size
		if size%2 == 1 {
			pos++ // chunk 按字对齐
		}
	}
	if !gotFmt || wi.dataLength <= 0 || wi.channels == 0 ||
		wi.sampleRate == 0 || wi.bits == 0 || wi.bits%8 != 0 {
		return wi, false
	}
	return wi, true
}

// isWav 判断是否为 WAV (RIFF/WAVE)。
func isWav(data []byte) bool {
	_, ok := wavParse(data)
	return ok
}

// WavDuration 解析 WAV 头并返回时长 (0 表示无法识别)。
func WavDuration(data []byte) time.Duration {
	wi, ok := wavParse(data)
	if !ok {
		return 0
	}
	blockAlign := uint64(wi.channels) * uint64(wi.bits) / 8
	if blockAlign == 0 {
		return 0
	}
	return time.Duration(uint64(wi.dataLength) / blockAlign *
		uint64(time.Second) / uint64(wi.sampleRate))
}

// writeTempWav 将 WAV 字节写入临时文件并记录路径 (Unix 外部播放器用)。
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
	return vm.audioPlayBytes(data)
}

func (vm *vmState) audioPlayBytes(data []byte) uint64 {
	vm.audioStop() // 停掉上一段 (若在播)
	if err := playPlatform(data); err != nil {
		return mask64
	}
	audioData = data
	audioActive = true
	audioDur = WavDuration(data)
	audioStarted = time.Now()
	audioEnd = audioStarted.Add(audioDur)
	audioDone = make(chan struct{})
	if audioDur <= 0 {
		close(audioDone) // 无法确定时长: 立即视为结束
		return 0
	}
	go func(done chan struct{}) {
		waitPlatform()
		close(done)
	}(audioDone)
	return 0
}

func (vm *vmState) audioStop() {
	audioMu.Lock()
	done := audioDone
	audioActive = false
	audioMu.Unlock()
	stopPlatform()
	if done != nil {
		<-done // 等 wait goroutine 退出, 避免与下一次播放竞争
	}
}

func (vm *vmState) audioVolume(level uint64) {
	if level > 100 {
		level = 100
	}
	audioMu.Lock()
	audioLevel = int(level)
	audioMu.Unlock()
	setVolumePlatform(int(level))
}

func audioLevelNow() int {
	audioMu.Lock()
	defer audioMu.Unlock()
	return audioLevel
}

// audioPos SYS 126: audio_pos() -> 当前播放已进行毫秒 / -1 (无播放)。
func (vm *vmState) audioPos() uint64 {
	audioMu.Lock()
	defer audioMu.Unlock()
	if !audioActive || audioDur <= 0 {
		return mask64
	}
	el := time.Since(audioStarted)
	if el > audioDur {
		el = audioDur
	}
	return uint64(el.Milliseconds())
}

func (vm *vmState) audioBeep(freq, ms uint64) uint64 {
	if freq < 20 || freq > 20000 || ms == 0 {
		return mask64
	}
	if ms > 10000 {
		ms = 10000
	}
	return vm.audioPlayBytes(synthBeepWav(freq, ms))
}

func (vm *vmState) audioWait() {
	audioMu.Lock()
	done := audioDone
	audioMu.Unlock()
	if done == nil {
		return
	}
	<-done
	audioMu.Lock()
	audioActive = false
	audioMu.Unlock()
}

// synthBeepWav 合成 16-bit 单声道 22050Hz 正弦波 WAV (带 5ms 淡入淡出防爆音)。
func synthBeepWav(freq, ms uint64) []byte {
	const rate = 22050
	n := int(rate * ms / 1000)
	if n < 1 {
		n = 1
	}
	fade := n / 10
	if fade > rate/200 { // 最多 5ms
		fade = rate / 200
	}
	data := make([]byte, 44+n*2)
	copy(data[0:4], "RIFF")
	binary.LittleEndian.PutUint32(data[4:8], uint32(36+n*2))
	copy(data[8:12], "WAVE")
	copy(data[12:16], "fmt ")
	binary.LittleEndian.PutUint32(data[16:20], 16)
	binary.LittleEndian.PutUint16(data[20:22], 1)                    // PCM
	binary.LittleEndian.PutUint16(data[22:24], 1)                    // mono
	binary.LittleEndian.PutUint32(data[24:28], rate)                 // 采样率
	binary.LittleEndian.PutUint32(data[28:32], rate*2)               // byte rate
	binary.LittleEndian.PutUint16(data[32:34], 2)                    // block align
	binary.LittleEndian.PutUint16(data[34:36], 16)                   // bits
	copy(data[36:40], "data")
	binary.LittleEndian.PutUint32(data[40:44], uint32(n*2))
	w := 2 * math.Pi * float64(freq) / float64(rate)
	for i := 0; i < n; i++ {
		amp := 1.0
		if fade > 0 {
			if i < fade {
				amp = float64(i) / float64(fade)
			} else if i >= n-fade {
				amp = float64(n-1-i) / float64(fade)
			}
		}
		v := int16(28000 * amp * math.Sin(w*float64(i)))
		binary.LittleEndian.PutUint16(data[44+i*2:], uint16(v))
	}
	return data
}
