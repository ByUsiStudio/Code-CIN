package engine

import (
	"os"
	"os/exec"
	"strconv"
	"strings"
)

// Termux API 交互 (Android / Termux)。经 termux-* 命令调用 (需 pkg install termux-api
// 并安装 Termux:API 应用)。非 Termux 环境下所有调用优雅失败 (返回 -1 或空串)。

// TermuxAvailable 检测 Termux API 是否可用。
func TermuxAvailable() bool {
	if os.Getenv("TERMUX_VERSION") != "" {
		return true
	}
	if strings.Contains(os.Getenv("PREFIX"), "com.termux") {
		return true
	}
	_, err := exec.LookPath("termux-notification")
	return err == nil
}

// termuxRun 运行 termux-* 命令, 返回 (stdout, 0/-1)。
func termuxRun(name string, args ...string) (string, uint64) {
	path, err := exec.LookPath(name)
	if err != nil {
		return "", mask64
	}
	out, err := exec.Command(path, args...).Output()
	if err != nil {
		return string(out), mask64
	}
	return string(out), 0
}

func (vm *vmState) termuxAvailable() uint64 {
	if TermuxAvailable() {
		return 1
	}
	return 0
}

func (vm *vmState) termuxNotify(title, content string) uint64 {
	_, code := termuxRun("termux-notification", "--title", title, "--content", content)
	return code
}

func (vm *vmState) termuxToast(msg string) uint64 {
	_, code := termuxRun("termux-toast", msg)
	return code
}

func (vm *vmState) termuxClipboardGet() uint64 {
	out, code := termuxRun("termux-clipboard-get")
	if code != 0 {
		return vm.empty()
	}
	return vm.hs(strings.TrimRight(out, "\r\n"))
}

func (vm *vmState) termuxClipboardSet(text string) uint64 {
	_, code := termuxRun("termux-clipboard-set", text)
	return code
}

func (vm *vmState) termuxBattery() uint64 {
	out, code := termuxRun("termux-battery-status")
	if code != 0 {
		return vm.empty()
	}
	return vm.hs(strings.TrimSpace(out))
}

func (vm *vmState) termuxVibrate(ms uint64) uint64 {
	_, code := termuxRun("termux-vibrate", "-d", strconv.FormatUint(ms, 10))
	return code
}

func (vm *vmState) termuxTTS(text string) uint64 {
	_, code := termuxRun("termux-tts-speak", text)
	return code
}

func (vm *vmState) termuxLocation() uint64 {
	out, code := termuxRun("termux-location")
	if code != 0 {
		return vm.empty()
	}
	return vm.hs(strings.TrimSpace(out))
}

func (vm *vmState) termuxWifiInfo() uint64 {
	out, code := termuxRun("termux-wifi-connectioninfo")
	if code != 0 {
		return vm.empty()
	}
	return vm.hs(strings.TrimSpace(out))
}

func (vm *vmState) termuxDialog(title string) uint64 {
	out, code := termuxRun("termux-dialog", "-t", title)
	if code != 0 {
		return vm.empty()
	}
	return vm.hs(strings.TrimSpace(out))
}

func (vm *vmState) termuxSmsSend(number, text string) uint64 {
	_, code := termuxRun("termux-sms-send", "-n", number, text)
	return code
}

// ---------------- Android / Termux 扩展 ----------------

func (vm *vmState) termuxCall(number string) uint64 {
	_, code := termuxRun("termux-telephony-call", number)
	return code
}

func (vm *vmState) termuxShare(file string) uint64 {
	_, code := termuxRun("termux-share", "-a", "send", file)
	return code
}

func (vm *vmState) termuxTorch(on uint64) uint64 {
	arg := "off"
	if on != 0 {
		arg = "on"
	}
	_, code := termuxRun("termux-torch", arg)
	return code
}

func (vm *vmState) termuxVolume(stream string, volume uint64) uint64 {
	_, code := termuxRun("termux-volume", stream,
		strconv.FormatUint(volume, 10))
	return code
}

func (vm *vmState) termuxBrightness(level uint64) uint64 {
	_, code := termuxRun("termux-brightness", strconv.FormatUint(level, 10))
	return code
}

func (vm *vmState) termuxCameraPhoto(path string) uint64 {
	_, code := termuxRun("termux-camera-photo", "-c", "0", path)
	return code
}

func (vm *vmState) termuxFingerprint() uint64 {
	out, code := termuxRun("termux-fingerprint")
	if code != 0 {
		return vm.empty()
	}
	return vm.hs(strings.TrimSpace(out))
}

func (vm *vmState) termuxSensor(name string) uint64 {
	out, code := termuxRun("termux-sensor", "-s", name, "-n", "1")
	if code != 0 {
		return vm.empty()
	}
	return vm.hs(strings.TrimSpace(out))
}

// androidIntent 通过 am start 发起系统 Intent (Android; Termux 下用 termux-am)。
func (vm *vmState) androidIntent(action, uri string) uint64 {
	for _, tool := range []string{"am", "termux-am"} {
		args := []string{"start", "-a", action}
		if uri != "" {
			args = append(args, "-d", uri)
		}
		if _, code := runCapture("", tool, args...); code == 0 {
			return 0
		}
	}
	return mask64
}
