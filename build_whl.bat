@echo off
rem Code CIN one-click wheel packaging - universal (all platforms) wheel with stdlib
rem
rem Output: dist\codecin-<version>-py3-none-any.whl
rem   * py3-none-any = pure-Python wheel: Windows / Linux / macOS (x64 / arm64);
rem   * bundles the built-in standard libraries in codecin\lib\<name>\;
rem   * no prebuilt native libs (.so / .dll / .dylib): setup.py compiles them on
rem     install with the local Go toolchain; prebuilt ones ship as Release assets.
rem
rem NOTE: keep this file ASCII-only. cmd.exe misparses UTF-8 batch files that
rem contain multibyte characters (CJK) in comments.
rem
rem Usage: build_whl.bat [extra args for python -m build, e.g. --no-isolation]
setlocal
cd /d "%~dp0"

if "%PYTHON%"=="" set "PYTHON=python"

"%PYTHON%" -c "import build" >nul 2>&1
if errorlevel 1 (
    echo Missing module build; run: %PYTHON% -m pip install --upgrade build
    exit /b 1
)

rem 1) Validate the pic package layout of codecin\lib
"%PYTHON%" script\sync_stdlib_packages.py --check
if errorlevel 1 exit /b 1

rem 2) Remove stale wheels so dist\ does not mix versions
if exist dist (
    for %%f in (dist\codecin-*.whl) do del /q "%%f" 2>nul
)

rem 3) Build. CODECIN_SKIP_NATIVE=1 keeps the wheel pure Python (no native build)
set CODECIN_SKIP_NATIVE=1
"%PYTHON%" -m build --wheel --outdir dist %*
if errorlevel 1 exit /b 1

rem 4) Verify the wheel contains stdlib sources and no prebuilt native libs
"%PYTHON%" -c "import glob,zipfile,sys; w=sorted(glob.glob('dist/codecin-*.whl'))[-1]; n=zipfile.ZipFile(w).namelist(); l=[x for x in n if x.startswith('codecin/lib/') and x.endswith('.cin')]; b=[x for x in n if x.endswith(('.so','.dll','.dylib'))]; print('wheel:',w); print('stdlib modules:',len(l)); sys.exit(0 if (len(l)>=19 and not b) else 1)"
if errorlevel 1 (
    echo Verify failed: wheel lacks stdlib modules or contains native libs
    exit /b 1
)

echo.
echo Done: the wheel in dist\ works on Windows / Linux / macOS
