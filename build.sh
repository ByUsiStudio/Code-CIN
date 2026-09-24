#!/usr/bin/env sh
# Code CIN 发布脚本 (PyPI)
#
# 只发布 **sdist** —— 这是刻意的:
#   原生库不打进 PyPI 包, 而是在用户 `pip install codecin` 时由 setup.py 调用
#   用户机器上的 Go 工具链现场编译 (见 setup.py: BuildPyWithNative)。
#   如果同时发布 wheel, pip 会优先装 wheel 而不执行构建, 用户就拿不到原生加速。
#   预编译好的库按平台/架构放在 GitHub Release 资产里, 供没装 Go 的用户使用。
#
# 用法: sh build.sh [twine 附加参数, 例如 -r testpypi]
set -e
cd "$(dirname "$0")"
rm -rf dist
python -m build --sdist
python -m twine check dist/*
python -m twine upload dist/*.tar.gz "$@"
