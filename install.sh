#!/usr/bin/env bash
# Code CIN 一键安装脚本 (Linux / macOS / Termux)
# 用法: bash install.sh
# 功能: 检测/安装 Go → 编译 Go 原生库与 codecin CLI → 安装 Python 依赖 → 生成启动器
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
NATIVE_DIR="$ROOT/codecin/native"
GOCACHE="$ROOT/.gocache"
GOTMPDIR="$ROOT/.gotmp"

# ---------- 颜色 ----------
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_CYAN=$'\033[36m'
else
  C_RESET=''; C_BOLD=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_CYAN=''
fi
info()  { echo "${C_CYAN}[Code CIN]${C_RESET} $*"; }
ok()    { echo "${C_GREEN}  ✔${C_RESET} $*"; }
warn()  { echo "${C_YELLOW}  ⚠${C_RESET} $*"; }
fail()  { echo "${C_RED}  ✘${C_RESET} $*" >&2; exit 1; }

echo "${C_BOLD}Code CIN 一键安装${C_RESET}"

# ---------- 1. 检测 Go ----------
if command -v go >/dev/null 2>&1; then
  GO_VERSION="$(go version 2>/dev/null | awk '{print $3}')"
  ok "已检测到 Go: ${GO_VERSION}"
else
  warn "未检测到 Go, 尝试自动安装…"
  if command -v apt-get >/dev/null 2>&1; then
    info "apt-get install golang-go ..."
    sudo apt-get update -y && sudo apt-get install -y golang-go || fail "请手动安装 Go 1.21+: https://go.dev/dl/"
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm go || fail "请手动安装 Go 1.21+: https://go.dev/dl/"
  elif command -v brew >/dev/null 2>&1; then
    brew install go || fail "请手动安装 Go 1.21+: https://go.dev/dl/"
  elif command -v pkg >/dev/null 2>&1; then   # Termux
    pkg install -y golang || fail "请手动安装 Go 1.21+"
  else
    fail "无法自动安装 Go, 请手动安装 Go 1.21+: https://go.dev/dl/ 后重试"
  fi
  command -v go >/dev/null 2>&1 || fail "Go 安装失败"
  ok "Go 安装完成"
fi

# ---------- 2. 编译 Go 原生库 (c-shared) ----------
mkdir -p "$GOCACHE" "$GOTMPDIR"
export CGO_ENABLED=1 GOCACHE="$GOCACHE" GOTMPDIR="$GOTMPDIR"
cd "$NATIVE_DIR"

case "$(uname -s)" in
  Darwin) OUT="$ROOT/codecin/libcodecin_native.dylib" ;;
  *)      OUT="$ROOT/codecin/libcodecin_native.so" ;;
esac
info "编译 Go 原生库 → $OUT"
go build -buildmode=c-shared -o "$OUT" . || fail "原生库编译失败 (检查是否安装 C 编译器: gcc/clang)"
rm -f "$ROOT/codecin"/codecin_native.h "$ROOT/codecin"/libcodecin_native.h 2>/dev/null || true
ok "原生库编译完成"

# ---------- 3. 编译 codecin CLI ----------
info "编译 codecin CLI (Go)"
CLI_BIN="$ROOT/codecin/codecin"
go build -o "$CLI_BIN" ./cmd/codecin || fail "CLI 编译失败"
ok "CLI 编译完成: $CLI_BIN"

# ---------- 4. Python 依赖 (rich; 可选) ----------
if command -v python3 >/dev/null 2>&1; then
  info "安装 Python 依赖 (rich) ..."
  python3 -m pip install --quiet -r "$ROOT/requirements.txt" 2>/dev/null \
    || python3 -m pip install --quiet "rich>=13,<15" 2>/dev/null \
    || warn "rich 安装失败 (不影响 Go CLI; 仅影响 python cpu.py 路径)"
  ok "Python 依赖就绪"
else
  warn "未检测到 python3 (仅影响 python cpu.py 路径; Go CLI 已可用)"
fi

# ---------- 5. 生成启动器 ----------
INSTALL_BIN="${CODECIN_INSTALL_DIR:-$HOME/.local/bin}"
mkdir -p "$INSTALL_BIN"
LAUNCHER="$INSTALL_BIN/codecin"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Code CIN 启动器 (自动选择 Go CLI, 回退 Python)
if [ -x "$CLI_BIN" ]; then
  exec "$CLI_BIN" "\$@"
else
  exec python3 "$ROOT/cpu.py" "\$@"
fi
EOF
chmod +x "$LAUNCHER"
ok "启动器已创建: $LAUNCHER"

echo
echo "${C_GREEN}安装完成!${C_RESET}"
echo "  运行 Go CLI:  ${C_BOLD}$CLI_BIN program.cin${C_RESET}"
echo "  运行启动器:   ${C_BOLD}codecin program.cin${C_RESET}  (确保 $INSTALL_BIN 在 PATH 中)"
echo "  Python 路径:  ${C_BOLD}python3 $ROOT/cpu.py program.cin${C_RESET}"
