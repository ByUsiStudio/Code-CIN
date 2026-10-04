package engine

import (
	"encoding/binary"
	"testing"
	"time"
)

// resetAudioState 恢复音频全局状态 (隔离用例间影响, 且不触碰真实设备)。
func resetAudioState() {
	audioMu.Lock()
	audioActive = false
	audioDur = 0
	audioStarted = time.Time{}
	audioEnd = time.Time{}
	audioLevel = 100
	audioDone = nil
	audioData = nil
	audioMu.Unlock()
	audioCmdMu.Lock()
	audioCmd = nil
	audioCmdMu.Unlock()
	audioTestOff = true
}

func TestWavParseSynthBeep(t *testing.T) {
	resetAudioState()
	defer resetAudioState()
	// 合成蜂鸣 WAV: 头部 + 数据完整, 时长与请求一致
	data := synthBeepWav(440, 100)
	info, ok := wavParse(data)
	if !ok {
		t.Fatal("synthBeepWav 产物应能被 wavParse 识别为合法 WAV")
	}
	if info.dataLength == 0 {
		t.Fatal("蜂鸣 WAV 数据块不应为空")
	}
	if got := WavDuration(data); got != 100*time.Millisecond {
		t.Fatalf("蜂鸣 WAV 时长应为 100ms, 实际 %v", got)
	}
}

func TestWavParseRejectsGarbage(t *testing.T) {
	cases := [][]byte{
		nil,
		[]byte("not a wav file at all"),
		[]byte("RIFF"),                 // 截断
		[]byte("RIFX\x24\x00\x00\x00"), // 非 RIFF
		[]byte("RIFF\x04\x00\x00\x00"), // 截断 (无 WAVE)
		[]byte("RIFF\x24\x00\x00\x00WAVEfmt \x10\x00"), // fmt 块截断
	}
	for i, data := range cases {
		if _, ok := wavParse(data); ok {
			t.Fatalf("用例 %d: 非法/截断数据不应通过 wavParse", i)
		}
		if len(data) >= 4 && isWav(data) {
			t.Fatalf("用例 %d: isWav 不应误判", i)
		}
	}
	// 完整最小 WAV 应通过 (44 字节头 + 4 字节数据)
	wav := make([]byte, 48)
	copy(wav[0:4], "RIFF")
	binary.LittleEndian.PutUint32(wav[4:8], 44)
	copy(wav[8:12], "WAVE")
	copy(wav[12:16], "fmt ")
	binary.LittleEndian.PutUint32(wav[16:20], 16)
	binary.LittleEndian.PutUint16(wav[20:22], 1) // PCM
	binary.LittleEndian.PutUint16(wav[22:24], 1) // mono
	binary.LittleEndian.PutUint32(wav[24:28], 8000)
	binary.LittleEndian.PutUint32(wav[28:32], 16000)
	binary.LittleEndian.PutUint16(wav[32:34], 2)
	binary.LittleEndian.PutUint16(wav[34:36], 16)
	copy(wav[36:40], "data")
	binary.LittleEndian.PutUint32(wav[40:44], 4)
	if _, ok := wavParse(wav); !ok {
		t.Fatal("最小合法 WAV 应通过 wavParse")
	}
	if got := WavDuration(wav); got != time.Millisecond/2 {
		// 4 字节 / 16000 B/s = 0.25ms
		t.Fatalf("时长应为 0.25ms, 实际 %v", got)
	}
}

func TestAudioVolumeClamp(t *testing.T) {
	resetAudioState()
	defer resetAudioState()
	vm := newHostVM(4096)
	vm.audioVolume(50)
	if got := audioLevelNow(); got != 50 {
		t.Fatalf("audio_volume(50) 后电平应为 50, 实际 %d", got)
	}
	vm.audioVolume(150) // 越界钳到 100
	if got := audioLevelNow(); got != 100 {
		t.Fatalf("audio_volume(150) 应钳到 100, 实际 %d", got)
	}
}

func TestAudioPosInactive(t *testing.T) {
	resetAudioState()
	defer resetAudioState()
	vm := newHostVM(4096)
	// 无播放 / 时长未知: audio_pos 返回 -1
	if got := vm.audioPos(); got != mask64 {
		t.Fatalf("无播放 audio_pos 应返回 -1, 实际 %d", got)
	}
}

func TestAudioPosElapsedClamped(t *testing.T) {
	resetAudioState()
	defer resetAudioState()
	vm := newHostVM(4096)
	// 手工构造"播放中"状态: 验证 audio_pos 的流逝与钳制
	audioMu.Lock()
	audioActive = true
	audioDur = 200 * time.Millisecond
	audioStarted = time.Now().Add(-50 * time.Millisecond)
	audioMu.Unlock()
	if got := vm.audioPos(); got < 40 || got > 60 {
		t.Fatalf("audio_pos 应在 40..60ms 附近, 实际 %d", got)
	}
	// 已超过时长: 钳到总时长
	audioMu.Lock()
	audioStarted = time.Now().Add(-1 * time.Second)
	audioMu.Unlock()
	if got := vm.audioPos(); got != 200 {
		t.Fatalf("超时后 audio_pos 应钳到 200, 实际 %d", got)
	}
}

func TestAudioBeepValidation(t *testing.T) {
	resetAudioState()
	defer resetAudioState()
	vm := newHostVM(4096)
	// 参数校验在触碰设备之前完成, 三平台行为一致
	cases := [][2]uint64{
		{19, 100},    // 频率过低
		{20001, 100}, // 频率过高
		{440, 0},     // 时长为 0
	}
	for _, c := range cases {
		if got := vm.audioBeep(c[0], c[1]); got != mask64 {
			t.Fatalf("beep(%d,%d) 应返回 -1, 实际 %d", c[0], c[1], got)
		}
	}
	// 合法参数 + 测试钩子 (播放视为失败): 返回 -1 但不崩溃、不残留状态
	if got := vm.audioBeep(440, 10); got != mask64 {
		t.Fatalf("audioTestOff 下合法 beep 应返回 -1, 实际 %d", got)
	}
	if audioLevelNow() != 100 {
		t.Fatal("失败的播放不应改变音量状态")
	}
}

func TestAudioWaitDoneAndInactive(t *testing.T) {
	resetAudioState()
	defer resetAudioState()
	vm := newHostVM(4096)
	vm.audioWait() // 无播放: 立即返回, 不阻塞

	// 已结束的播放: audio_wait 立即返回并把 audio_active 清零
	audioMu.Lock()
	audioDone = make(chan struct{})
	audioActive = true
	close(audioDone)
	audioMu.Unlock()
	vm.audioWait()
	audioMu.Lock()
	defer audioMu.Unlock()
	if audioActive {
		t.Fatal("audio_wait 后 audio_active 应清零")
	}
}
