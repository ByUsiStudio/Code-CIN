#!/usr/bin/env sh
# Code CIN 一键打包 wheel —— 含内置标准库, 全平台通用
#
# 产物: dist/codecin-<版本>-py3-none-any.whl
#   * py3-none-any = 纯 Python wheel, Windows / Linux / macOS (x64 / arm64) 通用;
#   * 内置标准库 codecin/lib/<name>/ (pic.toml + src/*.cin) 随包分发;
#   * 不含预编译原生库 (.so / .dll / .dylib): 原生库按项目设计由 setup.py 在
#     用户安装时用本机 Go 工具链编译, 预编译版本放在 GitHub Release 资产里。
#
# 用法: sh build_whl.sh [传给 python -m build 的附加参数, 例如 --no-isolation]

set -e
cd "$(dirname "$0")"

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    PYTHON=python
    command -v python >/dev/null 2>&1 || PYTHON=python3
fi

if ! "$PYTHON" -c "import build" >/dev/null 2>&1; then
    echo "缺少 build 模块, 请先执行: $PYTHON -m pip install --upgrade build" >&2
    exit 1
fi

# 1) 校验 codecin/lib 各 pic 包结构 (pic.toml 必须与源文件头同步)
"$PYTHON" script/sync_stdlib_packages.py --check

# 2) 清理历史 wheel, 避免 dist 里混入旧版本
rm -f dist/codecin-*.whl

# 3) 打包。CODECIN_SKIP_NATIVE=1: 打包时不编译原生库, 保证 wheel 是纯 Python
export CODECIN_SKIP_NATIVE=1
"$PYTHON" -m build --wheel --outdir dist "$@"

# 4) 校验产物确实包含内置标准库, 且没有混入预编译原生库
"$PYTHON" - <<'PY'
import glob
import sys
import zipfile

wheels = sorted(glob.glob("dist/codecin-*.whl"))
if not wheels:
    sys.exit("未生成 wheel")
wheel = wheels[-1]
names = zipfile.ZipFile(wheel).namelist()
libs = [n for n in names if n.startswith("codecin/lib/") and n.endswith(".cin")]
bins = [n for n in names if n.endswith((".so", ".dylib", ".dll"))]
print(f"wheel: {wheel}")
print(f"内置标准库模块: {len(libs)} 个")
if len(libs) < 19:
    sys.exit(f"{wheel} 缺少内置标准库")
if bins:
    sys.exit(f"{wheel} 不应携带预编译原生库: {bins}")
print("校验通过: 含内置标准库, 无预编译原生库, 平台标签 py3-none-any")
PY

echo "完成: 见上方 wheel 路径"
