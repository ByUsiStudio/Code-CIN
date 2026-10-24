package engine

import (
	"strings"
	"testing"
)

// lineEditorTestEnv 启用伪键盘监听 (keyReady=true, 不触碰真实终端) 并捕获回显。
func lineEditorTestEnv(t *testing.T, keys []uint64) *strings.Builder {
	t.Helper()
	resetKeyState()
	keyForcedOff = false
	keyMu.Lock()
	keyReady = true
	keyQueue = append(keyQueue[:0], keys...)
	keyMu.Unlock()
	out := &strings.Builder{}
	saved := keyOutSink
	keyOutSink = func(s string) { out.WriteString(s) }
	t.Cleanup(func() {
		keyOutSink = saved
		resetKeyState()
	})
	return out
}

func TestLineEditorInsertAndCursor(t *testing.T) {
	lineEditorTestEnv(t, []uint64{'a', 'b', 'c', keyLeft, 'X', lineCodeEnter})
	line, ok, used := stdinLineEditor()
	if !used || !ok {
		t.Fatalf("应接管并成功读取: used=%v ok=%v", used, ok)
	}
	if line != "abXc" {
		t.Fatalf("光标左移后插入应得到 abXc, 实际 %q", line)
	}
}

func TestLineEditorBackspaceDeleteAndEdit(t *testing.T) {
	// "abd" + 退格 + "c" -> "abc"
	lineEditorTestEnv(t, []uint64{'a', 'b', 'd', lineCodeBackspace, 'c', lineCodeEnter})
	if line, _, _ := stdinLineEditor(); line != "abc" {
		t.Fatalf("退格后输入应得到 abc, 实际 %q", line)
	}

	// Home + Delete: 删除首字符
	resetKeyState()
	lineEditorTestEnv(t, []uint64{'x', 'y', keyHome, keyDel, lineCodeEnter})
	if line, _, _ := stdinLineEditor(); line != "y" {
		t.Fatalf("Home+Delete 应删除首字符, 实际 %q", line)
	}

	// Ctrl+U 清空光标前内容
	resetKeyState()
	lineEditorTestEnv(t, []uint64{'a', 'b', 'c', keyLeft, lineCodeCtrlU, 'z', lineCodeEnter})
	if line, _, _ := stdinLineEditor(); line != "zc" {
		t.Fatalf("Ctrl+U 后输入应得到 zc, 实际 %q", line)
	}

	// Ctrl+W 删除前一个词
	resetKeyState()
	lineEditorTestEnv(t, []uint64{'a', 'b', ' ', 'c', 'd', lineCodeCtrlW, lineCodeEnter})
	if line, _, _ := stdinLineEditor(); line != "ab " {
		t.Fatalf("Ctrl+W 应删除最后一个词, 实际 %q", line)
	}
}

func TestLineEditorUnicodeAndSpecialKeysIgnored(t *testing.T) {
	// 中 (U+4E2D) 与 emoji (U+1F600) 按码点插入; 方向键/功能键不插入字符
	lineEditorTestEnv(t, []uint64{0x4E2D, keyUp, keyDown, keyF1, 0x1F600, lineCodeEnter})
	if line, _, _ := stdinLineEditor(); line != "中😀" {
		t.Fatalf("Unicode 输入/扩展键过滤异常, 实际 %q", line)
	}
}

func TestLineEditorSelectionKeys(t *testing.T) {
	// Enter 的两种编码 (\r / \n) 都结束输入; Ctrl+D 空行 = EOF
	lineEditorTestEnv(t, []uint64{'o', 'k', lineCodeLF})
	if line, ok, _ := stdinLineEditor(); line != "ok" || !ok {
		t.Fatalf("LF 应结束输入, 实际 %q ok=%v", line, ok)
	}

	resetKeyState()
	lineEditorTestEnv(t, []uint64{lineCodeCtrlD})
	if line, ok, used := stdinLineEditor(); !used || ok || line != "" {
		t.Fatalf("空行 Ctrl+D 应为 EOF, 实际 %q ok=%v used=%v", line, ok, used)
	}
}

// 无真实终端且无 GUI 窗口: 不接管, 由字节流路径处理。
func TestLineEditorNotUsedWithoutTerminal(t *testing.T) {
	resetKeyState() // keyForcedOff = true, keyReady = false
	defer resetKeyState()
	if line, ok, used := stdinLineEditor(); used || ok || line != "" {
		t.Fatalf("非交互环境不应接管: line=%q ok=%v used=%v", line, ok, used)
	}
}

func TestRuneWidthAndTotalWidth(t *testing.T) {
	if got := totalWidth([]rune("ab中")); got != 4 {
		t.Fatalf("ab中 应为 4 列, 实际 %d", got)
	}
	if runeWidth('\n') != 0 || runeWidth('a') != 1 || runeWidth('中') != 2 {
		t.Fatalf("显示宽度计算异常")
	}
}
