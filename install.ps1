# Code CIN 一键安装脚本 (Windows PowerShell)
# 用法: powershell -ExecutionPolicy Bypass -File install.ps1
# 功能: 检测/安装 Go -> 编译 Go 原生库与 codecin CLI -> 安装 Python 依赖 -> 生成启动器
$ErrorActionPreference = 'Stop'

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$NATIVE_DIR = Join-Path $ROOT 'codecin\native'
$GOCACHE = Join-Path $ROOT '.gocache'
$GOTMPDIR = Join-Path $ROOT '.gotmp'

function Info($m)  { Write-Host "[Code CIN] $m" -ForegroundColor Cyan }
function Ok($m)    { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m)  { Write-Host "  [!!] $m" -ForegroundColor Yellow }
function Fail($m)  { Write-Host "  [XX] $m" -ForegroundColor Red; exit 1 }

Write-Host "Code CIN 一键安装" -ForegroundColor White

# ---------- 1. 检测 Go ----------
$go = Get-Command go -ErrorAction SilentlyContinue
if ($go) {
    Ok "已检测到 Go: $(& go version)"
} else {
    Warn "未检测到 Go, 尝试通过 winget 自动安装..."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        winget install --id GoLang.Go -e --accept-source-agreements --accept-package-agreements
    }
    if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
        # 更新当前会话 PATH
        $env:Path += ";$env:ProgramFiles\Go\bin;$env:USERPROFILE\go\bin"
    }
    if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
        Fail "无法自动安装 Go, 请手动安装 Go 1.26+: https://go.dev/dl/ 后重试"
    }
    Ok "Go 安装完成"
}

# ---------- 2. 编译 Go 原生库 (c-shared) ----------
New-Item -ItemType Directory -Force -Path $GOCACHE, $GOTMPDIR | Out-Null
$env:CGO_ENABLED = '1'
$env:GOCACHE = $GOCACHE
$env:GOTMPDIR = $GOTMPDIR

Push-Location $NATIVE_DIR
$OUT = Join-Path $ROOT 'codecin\codecin_native.dll'
Info "编译 Go 原生库 -> $OUT (优先静态链接)"
$staticLd = '-linkmode external -extldflags -static'
go build -buildmode=c-shared -ldflags $staticLd -o $OUT .
if ($LASTEXITCODE -eq 0) {
    Ok "原生库编译完成 (静态链接)"
} else {
    Warn "静态链接不可用, 回退动态链接"
    go build -buildmode=c-shared -o $OUT .
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "原生库编译失败 (需要 C 编译器: 安装 MinGW-w64 或 TDM-GCC)" }
    Ok "原生库编译完成 (动态链接)"
}
Remove-Item -Force (Join-Path $ROOT 'codecin\codecin_native.h') -ErrorAction SilentlyContinue

# ---------- 3. 编译 codecin CLI ----------
Info "编译 codecin CLI (Go)"
$CLI_BIN = Join-Path $ROOT 'codecin\codecin.exe'
go build -o $CLI_BIN .\cmd\codecin
if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "CLI 编译失败" }
Ok "CLI 编译完成: $CLI_BIN"
Pop-Location

# ---------- 4. Python 依赖 (rich; 可选) ----------
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    Info "安装 Python 依赖 (rich) ..."
    python -m pip install --quiet -r (Join-Path $ROOT 'requirements.txt') 2>$null
    if ($LASTEXITCODE -ne 0) {
        python -m pip install --quiet "rich>=13,<15" 2>$null
    }
    Ok "Python 依赖就绪"
} else {
    Warn "未检测到 python (仅影响 python cpu.py 路径; Go CLI 已可用)"
}

# ---------- 5. 生成启动器 ----------
$installDir = if ($env:CODECIN_INSTALL_DIR) { $env:CODECIN_INSTALL_DIR } else { Join-Path $env:USERPROFILE '.codecin\bin' }
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
$launcher = Join-Path $installDir 'codecin.cmd'
@"
@echo off
rem Code CIN 启动器 (自动选择 Go CLI, 回退 Python)
if exist "$CLI_BIN" (
  "$CLI_BIN" %*
) else (
  python "$ROOT\cpu.py" %*
)
"@ | Set-Content -Encoding ASCII $launcher
Ok "启动器已创建: $launcher"

Write-Host ""
Write-Host "安装完成!" -ForegroundColor Green
Write-Host "  运行 Go CLI:  $CLI_BIN program.cin" -ForegroundColor White
Write-Host "  运行启动器:   codecin program.cin  (确保 $installDir 在 PATH 中)" -ForegroundColor White
Write-Host "  Python 路径:  python $ROOT\cpu.py program.cin" -ForegroundColor White
