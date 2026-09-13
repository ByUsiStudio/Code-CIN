package aot

import (
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

// DefaultMemSize 与解释器 / Go CLI 保持一致 (64 KiB)。
const DefaultMemSize = 65536

// Target 是构建目标平台。
type Target struct {
	GOOS   string
	GOARCH string
}

func (t Target) String() string { return t.GOOS + "/" + t.GOARCH }

// ExeSuffix 返回该平台可执行文件的后缀。
func (t Target) ExeSuffix() string {
	if t.GOOS == "windows" {
		return ".exe"
	}
	return ""
}

// SupportedTargets 是文档/CLI 帮助里列出的常用目标。
var SupportedTargets = []string{
	"windows/amd64", "windows/arm64",
	"linux/amd64", "linux/arm64",
	"darwin/amd64", "darwin/arm64",
}

// BuildOptions 描述一次 AOT 构建。
type BuildOptions struct {
	// ModuleDir 是含 `module codecin-native` 的 go.mod 的目录。
	ModuleDir string
	Bytecode  []byte
	MemImage  []byte
	// Out 是输出可执行文件路径 (相对当前工作目录)。
	Out    string
	Target Target
	// KeepTemp 为真时保留临时构建目录 (排查构建失败用)。
	KeepTemp bool
	Stdout   io.Writer
	Stderr   io.Writer
}

// Build 生成独立静态可执行文件, 返回产物的绝对路径。
//
// 静态性来自 CGO_ENABLED=0: Windows / Linux / macOS 三个平台都不再依赖
// libc 或任何动态库, 也不需要 Python 与 Go 运行时。
func Build(opts BuildOptions) (string, error) {
	if opts.ModuleDir == "" {
		return "", fmt.Errorf("未指定 Go 模块目录 (含 codecin-native/go.mod 的目录)")
	}
	if opts.Out == "" {
		return "", fmt.Errorf("未指定输出路径")
	}
	if opts.Target.GOOS == "" {
		opts.Target.GOOS = runtime.GOOS
	}
	if opts.Target.GOARCH == "" {
		opts.Target.GOARCH = runtime.GOARCH
	}
	if len(opts.Bytecode) == 0 {
		return "", fmt.Errorf("字节码为空")
	}

	// 临时包放在模块内并以 '.' 开头: Go 工具链会忽略这类目录,
	// 因此既不影响 `go build ./...`, 也无需 replace/绝对路径。
	tmp, err := os.MkdirTemp(opts.ModuleDir, ".aotbuild-")
	if err != nil {
		return "", fmt.Errorf("创建临时构建目录失败 (模块目录可写?): %w", err)
	}
	if !opts.KeepTemp {
		defer func() { _ = os.RemoveAll(tmp) }()
	}

	write := func(name string, data []byte) error {
		return os.WriteFile(filepath.Join(tmp, name), data, 0o644)
	}
	if err := write("main.go", []byte(stubSource)); err != nil {
		return "", fmt.Errorf("写入 main.go 失败: %w", err)
	}
	if err := write("program.ucbc", opts.Bytecode); err != nil {
		return "", fmt.Errorf("写入 program.ucbc 失败: %w", err)
	}
	if err := write("program.mem", opts.MemImage); err != nil {
		return "", fmt.Errorf("写入 program.mem 失败: %w", err)
	}

	outAbs, err := filepath.Abs(opts.Out)
	if err != nil {
		return "", fmt.Errorf("解析输出路径失败: %w", err)
	}
	if dir := filepath.Dir(outAbs); dir != "" {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return "", fmt.Errorf("创建输出目录失败: %w", err)
		}
	}

	pkg := "." + string(filepath.Separator) + filepath.Base(tmp)
	cmd := exec.Command("go", "build",
		"-trimpath",
		"-tags", "netgo,osusergo", // 纯 Go 网络/用户实现, 保证无 libc 依赖
		"-ldflags", "-s -w", // 去掉符号表与调试信息
		"-o", outAbs, pkg)
	cmd.Dir = opts.ModuleDir
	cmd.Env = append(os.Environ(),
		"CGO_ENABLED=0", // 关键: 静态链接
		"GOOS="+opts.Target.GOOS,
		"GOARCH="+opts.Target.GOARCH,
	)
	cmd.Stdout = opts.Stdout
	cmd.Stderr = opts.Stderr
	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("go build 失败 (目标 %s): %w", opts.Target, err)
	}
	return outAbs, nil
}

// FindModuleDir 从给定的若干起点向上查找 `module codecin-native` 所在目录。
func FindModuleDir(starts ...string) string {
	for _, start := range starts {
		if start == "" {
			continue
		}
		dir, err := filepath.Abs(start)
		if err != nil {
			continue
		}
		if fi, err := os.Stat(dir); err == nil && !fi.IsDir() {
			dir = filepath.Dir(dir)
		}
		for {
			gomod := filepath.Join(dir, "go.mod")
			if data, err := os.ReadFile(gomod); err == nil &&
				strings.Contains(string(data), "module codecin-native") {
				return dir
			}
			parent := filepath.Dir(dir)
			if parent == dir {
				break
			}
			dir = parent
		}
	}
	return ""
}
