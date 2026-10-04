//go:build !windows

package engine

import (
	"errors"
	"os/exec"
	"runtime"
	"strconv"
	"syscall"
)

func playPlatform(data []byte) error {
	path, err := writeTempWav(data)
	if err != nil {
		return err
	}
	vol := audioLevelNow()

	if runtime.GOOS == "darwin" {
		audioCmdMu.Lock()
		audioCmd = exec.Command("afplay", "-v", strconv.Itoa(vol*255/100), path)
		err := audioCmd.Start()
		audioCmdMu.Unlock()
		return err
	}

	for _, p := range []string{"aplay", "paplay", "ffplay"} {
		if _, err := exec.LookPath(p); err != nil {
			continue
		}
		var args []string
		switch p {
		case "paplay":
			args = []string{"--volume=" + strconv.Itoa(vol*65536/100), path}
		case "ffplay":
			args = []string{"-nodisp", "-autoexit", "-loglevel", "quiet",
				"-volume", strconv.Itoa(vol), path}
		default:
			args = []string{path}
		}
		audioCmdMu.Lock()
		audioCmd = exec.Command(p, args...)
		err := audioCmd.Start()
		audioCmdMu.Unlock()
		return err
	}
	return errNoPlayer
}

var errNoPlayer = errors.New("no audio player found (aplay/paplay/ffplay)")

// waitPlatform 等待播放进程自然退出 (stopPlatform 的 Kill 会解除等待)。
// 不清空 audioCmd: 暂停/恢复 (audio_pause/audio_resume) 在整段播放期间
// 都需要通过它找到播放器进程的 pid。
func waitPlatform() {
	audioCmdMu.Lock()
	cmd := audioCmd
	audioCmdMu.Unlock()
	if cmd != nil && cmd.Process != nil {
		_ = cmd.Wait()
	}
}

func stopPlatform() {
	audioCmdMu.Lock()
	cmd := audioCmd
	audioCmd = nil
	audioCmdMu.Unlock()
	if cmd != nil && cmd.Process != nil {
		_ = cmd.Process.Kill()
		_ = cmd.Wait() // 回收僵尸进程 (Wait 已被调用时无害)
	}
	cleanupTempWav()
}

// pausePlatform 暂停播放: 向播放器进程投递 SIGSTOP。
// 进程已退出 (自然播完) 或无播放时失败, 与"暂停成功"可区分。
func pausePlatform() bool {
	audioCmdMu.Lock()
	cmd := audioCmd
	audioCmdMu.Unlock()
	if cmd == nil || cmd.Process == nil {
		return false
	}
	pid := cmd.Process.Pid
	if err := syscall.Kill(pid, 0); err != nil {
		return false // 进程已不存在
	}
	return syscall.Kill(pid, syscall.SIGSTOP) == nil
}

// resumePlatform 恢复播放: 向播放器进程投递 SIGCONT。
func resumePlatform() bool {
	audioCmdMu.Lock()
	cmd := audioCmd
	audioCmdMu.Unlock()
	if cmd == nil || cmd.Process == nil {
		return false
	}
	return syscall.Kill(cmd.Process.Pid, syscall.SIGCONT) == nil
}

// setVolumePlatform Unix 外部播放器无法实时改音量: 下次播放生效。
func setVolumePlatform(level int) {}
