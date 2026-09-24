@echo off
rem Code CIN 发布脚本 (PyPI) - 只发 sdist
rem
rem 原生库不打进 PyPI 包, 而是在用户 pip install codecin 时由 setup.py 调用
rem 用户机器上的 Go 工具链现场编译 (见 setup.py: BuildPyWithNative)。
rem 若同时发布 wheel, pip 会优先装 wheel 而不执行构建, 用户就拿不到原生加速。
rem 预编译好的库按平台/架构放在 GitHub Release 资产里。
rem
rem 用法: build.bat [twine 附加参数, 例如 -r testpypi]
setlocal
cd /d "%~dp0"
if exist dist rmdir /s /q dist
python -m build --sdist || exit /b 1
python -m twine check dist/* || exit /b 1
python -m twine upload dist/*.tar.gz %*
