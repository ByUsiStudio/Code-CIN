package compiler

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// 8.1.2: import 解析与 Python 引擎 (codecin/cin.py) 对齐。
//
//	* 裸名字只查内置标准库, 不命中兄弟文件;
//	* 相对导入缺失时报出查找过的绝对路径, 而不是裸 OS 错误;
//	* 通配展开排除其它平台文件并按去平台后缀的路径去重。

func writeFile(t *testing.T, path, text string) {
	t.Helper()
	if dir := filepath.Dir(path); dir != "" {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatalf("mkdir %s: %v", dir, err)
		}
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatalf("write %s: %v", path, err)
	}
}

func TestResolveImportRelativeMissingReportsPath(t *testing.T) {
	dir := t.TempDir()
	from := filepath.Join(dir, "main.cin")
	writeFile(t, from, "")

	_, err := resolveImport("./nope.cin", from, "")
	if err == nil {
		t.Fatal("期望报 Import file not found")
	}
	msg := err.Error()
	if !strings.Contains(msg, "Import file not found") || !strings.Contains(msg, "相对引用") {
		t.Fatalf("提示应包含相对引用查找位置, 实际: %v", err)
	}
	if !strings.Contains(msg, "nope.cin") {
		t.Fatalf("提示应带目标名, 实际: %v", err)
	}
}

func TestResolveImportWildcardWithoutMatch(t *testing.T) {
	dir := t.TempDir()
	from := filepath.Join(dir, "main.cin")
	writeFile(t, from, "")

	_, err := resolveImport("./mods/*.cin", from, "")
	if err == nil || !strings.Contains(err.Error(), "Import pattern matched no .cin file") {
		t.Fatalf("期望通配无匹配报错, 实际: %v", err)
	}
}

func TestResolveImportBareNameIgnoresSibling(t *testing.T) {
	dir := t.TempDir()
	from := filepath.Join(dir, "main.cin")
	writeFile(t, from, "")
	writeFile(t, filepath.Join(dir, "helper.cin"), "function hv() -> int { return 1 }")

	_, err := resolveImport("helper.cin", from, filepath.Join(dir, "lib"))
	if err == nil {
		t.Fatal("裸名字不应命中同目录文件 (默认引擎同样拒绝)")
	}
	if !strings.Contains(err.Error(), "内置标准库") {
		t.Fatalf("提示应说明只查了内置标准库, 实际: %v", err)
	}
}

func TestResolveImportBareNameWithoutLibDirExplainsHowToFix(t *testing.T) {
	from := filepath.Join(t.TempDir(), "main.cin")
	writeFile(t, from, "")

	_, err := resolveImport("math.cin", from, "")
	if err == nil {
		t.Fatal("未指定库目录时应报错")
	}
	msg := err.Error()
	if !strings.Contains(msg, "CODECIN_LIB_DIR") || !strings.Contains(msg, "-lib") {
		t.Fatalf("提示应给出 -lib / CODECIN_LIB_DIR 两条修法, 实际: %v", err)
	}
}

func TestResolveImportBundledLibraryLayout(t *testing.T) {
	lib := t.TempDir()
	writeFile(t, filepath.Join(lib, "mymod", "src", "mymod.cin"),
		"function my_add(int a, int b) -> int { return a + b }")
	from := filepath.Join(t.TempDir(), "main.cin")
	writeFile(t, from, "")

	for _, name := range []string{"mymod.cin", "mymod", "lib/mymod.cin"} {
		got, err := resolveImport(name, from, lib)
		if err != nil {
			t.Fatalf("%s: 期望命中内置库, 实际: %v", name, err)
		}
		want := filepath.Join(lib, "mymod", "src", "mymod.cin")
		if len(got) != 1 || got[0] != want {
			t.Fatalf("%s: 期望 %s, 实际 %v", name, want, got)
		}
	}
}

func TestResolveImportPlatformVariantPrefersCurrent(t *testing.T) {
	dir := t.TempDir()
	from := filepath.Join(dir, "main.cin")
	writeFile(t, from, "")
	writeFile(t, filepath.Join(dir, "p.cin"), "")
	writeFile(t, filepath.Join(dir, "p_win.cin"), "")

	t.Setenv("CIN_TARGET_PLATFORM", "win")
	got, err := resolveImport("./p.cin", from, "")
	if err != nil {
		t.Fatalf("win 变体应命中: %v", err)
	}
	if filepath.Base(got[0]) != "p_win.cin" {
		t.Fatalf("应选 p_win.cin, 实际 %v", got)
	}

	t.Setenv("CIN_TARGET_PLATFORM", "linux")
	got, err = resolveImport("./p.cin", from, "")
	if err != nil {
		t.Fatalf("无 linux 变体应回退 p.cin: %v", err)
	}
	if filepath.Base(got[0]) != "p.cin" {
		t.Fatalf("应回退 p.cin, 实际 %v", got)
	}
}

func TestResolveImportWildcardExcludesOtherPlatform(t *testing.T) {
	dir := t.TempDir()
	from := filepath.Join(dir, "main.cin")
	writeFile(t, from, "")
	writeFile(t, filepath.Join(dir, "mods", "a.cin"), "")
	writeFile(t, filepath.Join(dir, "mods", "a_linux.cin"), "")
	writeFile(t, filepath.Join(dir, "mods", "b.cin"), "")
	writeFile(t, filepath.Join(dir, "mods", "notes.txt"), "")

	t.Setenv("CIN_TARGET_PLATFORM", "win")
	got, err := resolveImport("./mods/*.cin", from, "")
	if err != nil {
		t.Fatalf("通配展开失败: %v", err)
	}
	var names []string
	for _, p := range got {
		names = append(names, filepath.Base(p))
	}
	want := "a.cin,b.cin"
	if strings.Join(names, ",") != want {
		t.Fatalf("期望 %s, 实际 %v", want, names)
	}
}
