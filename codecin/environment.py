"""宿主环境检测与安装提示 (environment)。

C/C++/Go 兼容层与 AOT/原生加速依赖本机工具链与原生运行时; 本模块把它们
的"缺失检测"与"怎么装"集中成单一来源, 保证三条规则:

  1. 检测只做只读探测 (shutil.which), 绝不自动安装;
  2. 提示必须给出**当前平台可复制执行**的安装命令 (winget/apt/brew/pkg/...);
  3. 缺失提示在所有入口一致: AOT 构建、pip 安装 (setup.py)、解释器
     调用宿主内建 (无原生库)、以及 ``--build-info`` 的环境自检。

对外接口::

    find_tool(tool)         -> 绝对路径 / None
    tool_available(tool)    -> bool
    install_hint(tool)      -> str  (多行, 平台相关, 含命令)
    missing_tool_message(tool, purpose) -> str
    native_runtime_hint()   -> str  (原生库缺失时的安装指引)
"""

import os
import platform
import shutil
import sys
from typing import Optional

__all__ = [
    'find_tool',
    'tool_available',
    'install_hint',
    'missing_tool_message',
    'native_runtime_hint',
]

#: GitHub Releases (预编译原生库下载地址)。
RELEASES_URL = 'https://github.com/ByUsiStudio/Code-CIN/releases'
#: Go 官方下载页 (所有平台的兜底指引)。
GO_DOWNLOAD_URL = 'https://go.dev/dl/'


def _system() -> str:
    """统一平台名: windows / darwin / linux / termux / 其他。"""
    if sys.platform == 'win32':
        return 'windows'
    if sys.platform == 'darwin':
        return 'darwin'
    if sys.platform.startswith('linux'):
        # Termux 的 sys.platform 也是 linux; 用前缀目录特征识别
        if os.path.exists('/data/data/com.termux/files/usr/bin/pkg'):
            return 'termux'
        return 'linux'
    return sys.platform


def find_tool(tool: str) -> Optional[str]:
    """在 PATH 中查找可执行文件, 找到返回绝对路径, 否则 None。"""
    if not tool:
        return None
    return shutil.which(tool)


def tool_available(tool: str) -> bool:
    """工具是否已在 PATH 中可用 (只读探测, 不执行任何命令)。"""
    return find_tool(tool) is not None


def install_hint(tool: str) -> str:
    """返回在**当前平台**安装 `tool` 的可操作指引 (多行文本)。

    已支持: go / gcc / g++ / cc / c++ / clang / clang++ / make。
    未收录的工具返回通用提示 (自行用包管理器安装)。
    """
    sysname = _system()

    if tool == 'go':
        if sysname == 'windows':
            return ("安装 Go 工具链 (Windows):\n"
                    "  winget install GoLang.Go\n"
                    f"  或从 {GO_DOWNLOAD_URL} 下载 MSI 安装包\n"
                    "  安装后重新打开终端, `go version` 可用即成功")
        if sysname == 'darwin':
            return ("安装 Go 工具链 (macOS):\n"
                    "  brew install go\n"
                    f"  或从 {GO_DOWNLOAD_URL} 下载 pkg 安装包\n"
                    "  安装后重新打开终端, `go version` 可用即成功")
        if sysname == 'termux':
            return ("安装 Go 工具链 (Termux):\n"
                    "  pkg install golang\n"
                    "  安装后 `go version` 可用即成功")
        return ("安装 Go 工具链 (Linux):\n"
                "  Debian/Ubuntu : sudo apt install golang-go\n"
                "  Fedora        : sudo dnf install golang\n"
                "  Arch          : sudo pacman -S go\n"
                f"  或从 {GO_DOWNLOAD_URL} 下载 tarball 解压到 /usr/local\n"
                "  安装后重新打开终端, `go version` 可用即成功")

    if tool in ('gcc', 'cc'):
        if sysname == 'windows':
            return ("安装 C 编译器 (Windows):\n"
                    "  MSYS2 : pacman -S mingw-w64-ucrt-x86_64-gcc\n"
                    "          (然后把 <MSYS2>\\ucrt64\\bin 加入 PATH)\n"
                    "  WinLibs: winget install BrechtSanders.WinLibs.MSYS-UCRT\n"
                    "  安装后 `gcc --version` 可用即成功")
        if sysname == 'darwin':
            return ("安装 C 编译器 (macOS):\n"
                    "  xcode-select --install   (提供 clang, gcc 别名同 clang)\n"
                    "  安装后 `gcc --version` 可用即成功")
        if sysname == 'termux':
            return ("安装 C 编译器 (Termux):\n"
                    "  pkg install clang\n"
                    "  安装后 `cc --version` 可用即成功")
        return ("安装 C 编译器 (Linux):\n"
                "  Debian/Ubuntu : sudo apt install gcc\n"
                "  Fedora        : sudo dnf install gcc\n"
                "  Arch          : sudo pacman -S gcc\n"
                "  安装后 `gcc --version` 可用即成功")

    if tool in ('g++', 'c++', 'clang++'):
        if sysname == 'windows':
            return ("安装 C++ 编译器 (Windows):\n"
                    "  MSYS2 : pacman -S mingw-w64-ucrt-x86_64-gcc\n"
                    "          (含 g++, 然后把 <MSYS2>\\ucrt64\\bin 加入 PATH)\n"
                    "  WinLibs: winget install BrechtSanders.WinLibs.MSYS-UCRT\n"
                    "  安装后 `g++ --version` 可用即成功")
        if sysname == 'darwin':
            return ("安装 C++ 编译器 (macOS):\n"
                    "  xcode-select --install   (提供 clang++)\n"
                    "  安装后 `c++ --version` 可用即成功")
        if sysname == 'termux':
            return ("安装 C++ 编译器 (Termux):\n"
                    "  pkg install clang\n"
                    "  安装后 `clang++ --version` 可用即成功")
        return ("安装 C++ 编译器 (Linux):\n"
                "  Debian/Ubuntu : sudo apt install g++\n"
                "  Fedora        : sudo dnf install gcc-c++\n"
                "  Arch          : sudo pacman -S gcc\n"
                "  安装后 `g++ --version` 可用即成功")

    if tool in ('clang',):
        return ("安装 Clang:\n"
                "  Windows : winget install LLVM.LLVM\n"
                "  macOS   : xcode-select --install\n"
                "  Linux   : sudo apt install clang (Debian/Ubuntu)\n"
                "  Termux  : pkg install clang\n")

    return (f"未找到 {tool}: 请使用当前平台的包管理器安装\n"
            f"  (Windows: winget / Linux: apt|dnf|pacman / macOS: brew)\n")


def missing_tool_message(tool: str, purpose: str) -> str:
    """「缺工具 + 用途 + 怎么装」合一的完整错误提示。"""
    lines = [
        f"未找到 `{tool}` 命令 — {purpose} 需要 {tool} 工具链 (当前平台: "
        f"{platform.system()})。",
    ]
    lines.append(install_hint(tool).rstrip())
    return '\n'.join(lines)


def native_runtime_hint() -> str:
    """原生 Go 运行库缺失时的安装指引 (多行文本)。

    适用场景: 宿主能力内建 (音频/GUI/文件/网络/键盘...) 在无原生库时被调用。
    只有其中的原生依赖项 (如 go_os_args_*) 会走到这里。
    """
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    return (
        "宿主能力内建需要原生 Go 运行库 (c-shared DLL/SO), 当前不可用。\n"
        "解决方案 (任选其一):\n"
        "  1. 安装 Go 工具链后重装本包 (安装时现场编译原生库):\n"
        "       pip install --force-reinstall codecin\n"
        "  2. 从 Release 下载预编译原生库 (平台-架构 后缀), 放到:\n"
        f"       {pkg_dir}\n"
        f"       {RELEASES_URL}\n"
        "  3. 本地手动构建: 运行 codecin/native/build.ps1 (Windows)\n"
        "     或 codecin/native/build.sh (Linux/Termux/macOS)。"
    )
