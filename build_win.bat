@echo off
rem Code CIN Windows 打包 - 统一走 codecin.spec (含 codecin_native.dll 与瘦身 excludes)
rem 建议在干净 venv 中执行:
rem   py -3 -m venv .venv && .venv\Scripts\activate
rem   pip install -r requirements.txt pyinstaller
rem   build_win.bat
pyinstaller --noconfirm --clean codecin.spec
