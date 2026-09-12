# Code CIN 原生库构建脚本 (Windows)
# 依赖: Go 1.26+ 且启用 cgo (需 C 编译器, 如 MinGW-w64 / TDM-GCC 的 gcc / clang)
# 用法: 在本目录执行  .\build.ps1
#     静态链接: $env:CODECIN_STATIC = '1'  (默认尝试静态, 失败自动回退动态)
#     动态链接: $env:CODECIN_STATIC = '0'
param()
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir
$env:CGO_ENABLED = '1'

$out = Join-Path $dir '..\codecin_native.dll'
$mode = if ($env:CODECIN_STATIC) { $env:CODECIN_STATIC } else { 'auto' }

$staticLd = '-linkmode external -extldflags -static'
$built = $false

if ($mode -ne '0') {
    Write-Host "Building (static linking)..."
    go build -buildmode=c-shared -ldflags $staticLd -o $out .
    if ($LASTEXITCODE -eq 0) { $built = $true }
    elseif ($mode -eq '1') { throw "static go build failed (exit $LASTEXITCODE)" }
    else { Write-Host "static linking unavailable, falling back to dynamic" -ForegroundColor Yellow }
}
if (-not $built) {
    Write-Host "Building (dynamic linking)..."
    go build -buildmode=c-shared -o $out .
    if ($LASTEXITCODE -ne 0) { throw "go build failed (exit $LASTEXITCODE)" }
}

# c-shared 会附带生成头文件, Python ctypes 不需要
Remove-Item -Force (Join-Path $dir '..\codecin_native.h') -ErrorAction SilentlyContinue
Write-Host ("Built: " + (Resolve-Path $out).Path)
