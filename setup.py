import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT   = Path(__file__).resolve().parent
NATIVE = ROOT / "codecin" / "native"
PKG    = ROOT / "codecin"

EXT = {"Windows": ".dll", "Darwin": ".dylib"}.get(platform.system(), ".so")

LIB_NAMES = ["libcodecin_native" + EXT, "codecin_native" + EXT]


def _should_skip() -> bool:

    if os.environ.get("CODECIN_SKIP_NATIVE", "0") == "1":
        return True
    if os.environ.get("CODECIN_NO_GO", "0") == "1":
        return True
    return False


def _go_available() -> bool:
    if shutil.which("go") is None:
        print("[codecin] 'go' not found in PATH — 跳过原生库构建", file=sys.stderr)
        return False
    return True


def build_native_lib() -> None:
    if _should_skip():
        print("[codecin] 环境变量要求跳过原生构建")
        return

    if os.environ.get("CODECIN_FORCE_REBUILD", "1") != "1":
        if any((PKG / n).exists() for n in LIB_NAMES):
            print("[codecin] 原生库已存在，跳过重建")
            return

    if not _go_available():
        return

    system = platform.system()
    if system == "Windows":
        script = NATIVE / "build.ps1"
        cmd = [
            "powershell", "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", str(script),
        ]
    else:
        script = NATIVE / "build.sh"
        cmd = ["bash", str(script)]

    if not script.exists():
        print(f"[codecin] 未找到 {script}，跳过", file=sys.stderr)
        return

    print(f"[codecin] 编译原生库: {' '.join(cmd)} (cwd={NATIVE})")
    subprocess.check_call(cmd, cwd=str(NATIVE))


class BuildPyWithNative(build_py):

    def run(self):
        build_native_lib()
        super().run()


setup(cmdclass={"build_py": BuildPyWithNative})