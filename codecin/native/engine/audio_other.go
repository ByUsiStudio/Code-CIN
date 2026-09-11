//go:build !windows

package engine

import (
	"fmt"
	"os/exec"
	"runtime"
)

// Unix 音频后端 (Linux / macOS / Termux / BSD): 调用系统播放器。
//   macOS: afplay
//   Linux/Termux: aplay / paplay / ffplay (取第一个可用者)

func playPlatform(data []byte) error {
	path, err := writeTempWav(data)
	if err != nil {
		return err
	}

	if runtime.GOOS == "darwin" {
		audioCmd = exec.Command("afplay", path)
		return audioCmd.Start()
	}

	for _, p := range []string{"aplay", "paplay", "ffplay"} {
		if _, err := exec.LookPath(p); err != nil {
			continue
		}
		if p == "ffplay" {
			audioCmd = exec.Command(p, "-nodisp", "-autoexit", "-loglevel", "quiet", path)
		} else {
			audioCmd = exec.Command(p, path)
		}
		return audioCmd.Start()
	}
	return fmt.Errorf("no audio player found (aplay/paplay/ffplay)")
}

func stopPlatform() {
	if audioCmd != nil && audioCmd.Process != nil {
		_ = audioCmd.Process.Kill()
	}
	audioCmd = nil
	cleanupTempWav()
}
