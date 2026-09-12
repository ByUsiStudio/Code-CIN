#!/usr/bin/env sh
# Code CIN 原生库构建脚本 (Linux / Termux / macOS)
# 依赖: Go 1.26+ 且启用 cgo
#   Linux:  安装 gcc (如 apt install gcc golang)
#   Termux: pkg install golang (自带 cgo 工具链, 支持 -buildmode=c-shared)
#   macOS:  安装 Xcode Command Line Tools (clang)
# 用法: 在本目录执行  sh build.sh
#     静态链接 (默认):  CODECIN_STATIC=auto|1 ; 失败时 auto 会自动回退动态
#     动态链接:        CODECIN_STATIC=0
set -e
cd "$(dirname "$0")"
export CGO_ENABLED=1

case "$(uname -s)" in
    Darwin)
        OUT="../libcodecin_native.dylib"
        # macOS 不支持把共享库完全静态链接, 始终动态 (仅依赖系统库)
        STATIC_SUPPORTED=0
        ;;
    *)
        OUT="../libcodecin_native.so"
        STATIC_SUPPORTED=1
        ;;
esac

MODE="${CODECIN_STATIC:-auto}"
BUILT=0

if [ "$MODE" != "0" ] && [ "$STATIC_SUPPORTED" = "1" ]; then
    echo "Building (static linking)..."
    if go build -buildmode=c-shared -ldflags '-linkmode external -extldflags "-static"' -o "$OUT" .; then
        BUILT=1
    elif [ "$MODE" = "1" ]; then
        echo "static go build failed" >&2
        exit 1
    else
        echo "static linking unavailable, falling back to dynamic" >&2
    fi
fi

if [ "$BUILT" != "1" ]; then
    echo "Building (dynamic linking)..."
    go build -buildmode=c-shared -o "$OUT" .
fi

# c-shared 附带的头文件, Python ctypes 不需要
rm -f ../codecin_native.h ../libcodecin_native.h
echo "Built: $OUT"
