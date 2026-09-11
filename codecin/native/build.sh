#!/usr/bin/env sh
# UCPU 原生库构建脚本 (Linux / Termux / macOS)
# 依赖: Go 1.21+ 且启用 cgo
#   Linux:  安装 gcc (如 apt install gcc golang)
#   Termux: pkg install golang (自带 cgo 工具链, 支持 -buildmode=c-shared)
#   macOS:  安装 Xcode Command Line Tools (clang)
# 用法: 在本目录执行  sh build.sh
set -e
cd "$(dirname "$0")"

case "$(uname -s)" in
    Darwin)
        OUT="../libucpu_native.dylib"
        ;;
    *)
        OUT="../libucpu_native.so"
        ;;
esac

go build -buildmode=c-shared -o "$OUT" .

# c-shared 附带的头文件, Python ctypes 不需要
rm -f ../ucpu_native.h ../libucpu_native.h
echo "Built: $OUT"
