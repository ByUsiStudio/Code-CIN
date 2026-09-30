package engine

import (
	"os/exec"
	"runtime"
	"strings"
)

// 桌面/移动端集成: 剪贴板、系统通知、打开 URL、Android Intent。
//
// 分发顺序: Termux (Android) -> 平台原生命令 (Windows / macOS / Linux)。
// 每条命令都先 LookPath, 缺失即优雅失败 (返回 -1 / 空串), 不 panic、不阻塞。
// 平台命令行在两个平台上保持同一份实现, 因此行为可预期且可测试。

// runCapture 执行命令 (可选 stdin), 返回 (stdout, 0/-1)。
func runCapture(stdin string, name string, args ...string) (string, uint64) {
	path, err := exec.LookPath(name)
	if err != nil {
		return "", mask64
	}
	cmd := exec.Command(path, args...)
	if stdin != "" {
		cmd.Stdin = strings.NewReader(stdin)
	}
	out, err := cmd.Output()
	if err != nil {
		return string(out), mask64
	}
	return string(out), 0
}

// ---------------- 剪贴板 ----------------

func clipboardRead() (string, uint64) {
	if TermuxAvailable() {
		if out, code := termuxRun("termux-clipboard-get"); code == 0 {
			return out, 0
		}
	}
	switch runtime.GOOS {
	case "windows":
		return runCapture("", "powershell", "-NoProfile", "-Command",
			"Get-Clipboard -Raw")
	case "darwin":
		return runCapture("", "pbpaste")
	default: // linux / android
		candidates := [][]string{
			{"wl-paste", "--no-newline"},
			{"xclip", "-selection", "clipboard", "-o"},
			{"xsel", "-b"},
		}
		for _, cand := range candidates {
			if _, err := exec.LookPath(cand[0]); err != nil {
				continue
			}
			if out, code := runCapture("", cand[0], cand[1:]...); code == 0 {
				return out, 0
			}
		}
	}
	return "", mask64
}

func clipboardWrite(text string) uint64 {
	if TermuxAvailable() {
		if _, code := termuxRun("termux-clipboard-set", text); code == 0 {
			return 0
		}
	}
	switch runtime.GOOS {
	case "windows":
		_, code := runCapture(text, "cmd", "/c", "clip")
		return code
	case "darwin":
		_, code := runCapture(text, "pbcopy")
		return code
	default: // linux / android
		candidates := [][]string{
			{"wl-copy"},
			{"xclip", "-selection", "clipboard", "-i"},
			{"xsel", "-b", "-i"},
		}
		for _, cand := range candidates {
			if _, err := exec.LookPath(cand[0]); err != nil {
				continue
			}
			if _, code := runCapture(text, cand[0], cand[1:]...); code == 0 {
				return 0
			}
		}
	}
	return mask64
}

func (vm *vmState) clipboardGet() uint64 {
	out, code := clipboardRead()
	if code != 0 {
		return vm.empty()
	}
	return vm.hs(strings.TrimRight(out, "\r\n"))
}

func (vm *vmState) clipboardSet(text string) uint64 {
	return clipboardWrite(text)
}

// ---------------- 系统通知 ----------------

func (vm *vmState) notify(title, body string) uint64 {
	if TermuxAvailable() {
		if _, code := termuxRun("termux-notification",
			"--title", title, "--content", body); code == 0 {
			return 0
		}
	}
	switch runtime.GOOS {
	case "windows":
		// Wscript.Shell.Popup: 10 秒后自动消失, 无需额外模块。
		script := "(New-Object -ComObject Wscript.Shell).Popup('" +
			psSingleQuote(body) + "',10,'" + psSingleQuote(title) + "',64)"
		_, code := runCapture("", "powershell", "-NoProfile", "-Command", script)
		return code
	case "darwin":
		script := "display notification \"" + osaDoubleQuote(body) +
			"\" with title \"" + osaDoubleQuote(title) + "\""
		_, code := runCapture("", "osascript", "-e", script)
		return code
	default: // linux / android
		if _, err := exec.LookPath("notify-send"); err == nil {
			_, code := runCapture("", "notify-send", title, body)
			return code
		}
	}
	return mask64
}

// psSingleQuote 转义 PowerShell 单引号字符串内的单引号。
func psSingleQuote(s string) string {
	return strings.ReplaceAll(s, "'", "''")
}

// osaDoubleQuote 转义 AppleScript 双引号字符串内的引号与反斜杠。
func osaDoubleQuote(s string) string {
	s = strings.ReplaceAll(s, "\\", "\\\\")
	return strings.ReplaceAll(s, "\"", "\\\"")
}

// ---------------- 打开 URL / 默认程序 ----------------

func (vm *vmState) openURL(url string) uint64 {
	if TermuxAvailable() {
		if _, code := termuxRun("termux-open-url", url); code == 0 {
			return 0
		}
	}
	switch runtime.GOOS {
	case "windows":
		// start 的第一个参数是窗口标题, 必须给空串
		_, code := runCapture("", "cmd", "/c", "start", "", url)
		return code
	case "darwin":
		_, code := runCapture("", "open", url)
		return code
	case "android":
		return vm.androidIntent("android.intent.action.VIEW", url)
	default: // linux
		if _, err := exec.LookPath("xdg-open"); err == nil {
			_, code := runCapture("", "xdg-open", url)
			return code
		}
	}
	return mask64
}
