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

#: 预编译原生库下载页 (codecin.environment.RELEASES_URL 的兜底值)。
DEFAULT_RELEASES_URL = "https://github.com/ByUsiStudio/Code-CIN/releases"


def _releases_url() -> str:
    """优先复用 codecin.environment 里的单一真源, 包尚不可导入时用兜底值。"""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from codecin.environment import RELEASES_URL
        return RELEASES_URL
    except Exception:
        return DEFAULT_RELEASES_URL


def _should_skip() -> bool:

    if os.environ.get("CODECIN_SKIP_NATIVE", "0") == "1":
        return True
    if os.environ.get("CODECIN_NO_GO", "0") == "1":
        return True
    return False


def _go_available() -> bool:
    if shutil.which("go") is None:
        try:
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            from codecin.environment import install_hint
            hint = install_hint("go").rstrip()
        except Exception:
            hint = "参考 https://go.dev/dl/ 安装 Go 工具链"
        print("[codecin] 'go' not found in PATH — 未构建原生库\n"
              f"{hint}\n"
              "[codecin] 注意: v5.9.0 起已无纯 Python 解释器回退, 缺少 "
              "codecin-native 时程序无法执行 (编译/汇编/反汇编等工具仍可用)。\n"
              "[codecin] 请安装 Go 后重新安装本包 (重跑同一条 `pip install`), "
              "或从 Release 下载对应平台的"
              "预编译库放入 codecin/ (或 codecin/native/) 目录:\n"
              f"[codecin] {_releases_url()}",
              file=sys.stderr)
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
        self._install_built_libs()
        self._report_native_status()

    def _install_built_libs(self):
        if _should_skip():
            print("[codecin] 跳过安装原生库 (CODECIN_SKIP_NATIVE / CODECIN_NO_GO)")
            return
        target = Path(self.build_lib) / "codecin"
        for name in LIB_NAMES:
            src = PKG / name
            if src.exists():
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target / name)
                print(f"[codecin] 原生库已安装到: {target / name}")

    def _report_native_status(self):
        """安装收尾时汇报原生引擎状态, 让 pip 输出自带「能不能跑」的结论。"""
        target = Path(self.build_lib) / "codecin"
        found = next((target / n for n in LIB_NAMES if (target / n).exists()),
                     None)
        if found is not None:
            print(f"[codecin] 原生引擎: 可用 ({found})")
            return
        print("[codecin] 原生引擎: 不可用 — 安装已完成, 但程序无法执行\n"
              "[codecin]   (编译/汇编/反汇编等纯 Python 工具仍可用)\n"
              "[codecin] 修复: 安装 Go 后重新安装本包 (重跑同一条 `pip install`),\n"
              "[codecin]       或从 Release 下载对应平台的预编译库"
              " (文件名带平台/架构后缀)\n"
              f"[codecin]       放入 {target} 或 {target / 'native'} 目录\n"
              f"[codecin]       {_releases_url()}",
              file=sys.stderr)


setup(cmdclass={"build_py": BuildPyWithNative})