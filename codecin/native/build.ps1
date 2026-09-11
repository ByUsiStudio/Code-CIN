# UCPU 原生库构建脚本 (Windows)
# 依赖: Go 1.21+ 且启用 cgo (需 C 编译器, 如 MinGW-w64 / TDM-GCC 的 gcc)
# 用法: 在本目录执行  .\build.ps1
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir

$out = Join-Path $dir '..\ucpu_native.dll'
go build -buildmode=c-shared -o $out .
if ($LASTEXITCODE -ne 0) { throw "go build failed (exit $LASTEXITCODE)" }

# c-shared 会附带生成头文件, Python ctypes 不需要
Remove-Item -Force (Join-Path $dir '..\ucpu_native.h') -ErrorAction SilentlyContinue
Write-Host ("Built: " + (Resolve-Path $out).Path)
