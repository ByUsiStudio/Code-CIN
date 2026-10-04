"""environment: 工具链缺失检测与平台安装提示 (单一事实来源)。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codecin import environment  # noqa: E402

_NO_SUCH_TOOL = 'codecin-no-such-tool-xyz'


def test_find_tool_missing_returns_none():
    assert environment.find_tool(_NO_SUCH_TOOL) is None
    assert environment.tool_available(_NO_SUCH_TOOL) is False


def test_install_hint_go_has_platform_command():
    hint = environment.install_hint('go')
    assert 'go version' in hint
    assert environment.GO_DOWNLOAD_URL in hint
    # 当前平台对应的可复制安装命令
    assert any(k in hint for k in ('winget', 'brew', 'pkg', 'apt', 'dnf',
                                   'pacman'))


def test_install_hint_compilers():
    for tool in ('gcc', 'cc', 'g++', 'c++', 'clang++'):
        hint = environment.install_hint(tool)
        # 给出验证命令或 macOS 的 xcode-select 指引
        assert '--version' in hint or 'xcode-select' in hint
        assert any(k in hint for k in ('winget', 'brew', 'pkg', 'apt', 'dnf',
                                       'pacman', 'xcode-select', 'MSYS2'))
    # clang 单独收录 (含 LLVM 安装包指引)
    clang_hint = environment.install_hint('clang')
    assert 'LLVM' in clang_hint or 'xcode-select' in clang_hint


def test_install_hint_unknown_tool_is_generic():
    hint = environment.install_hint(_NO_SUCH_TOOL)
    assert _NO_SUCH_TOOL in hint
    assert 'winget' in hint and 'apt' in hint


def test_missing_tool_message_combines_tool_purpose_hint():
    msg = environment.missing_tool_message('go', 'AOT 构建独立可执行文件')
    assert '未找到 `go`' in msg
    assert 'AOT 构建独立可执行文件' in msg
    assert 'winget' in msg or 'apt' in msg or 'brew' in msg \
        or environment.GO_DOWNLOAD_URL in msg


def test_native_runtime_hint_lists_all_options():
    hint = environment.native_runtime_hint()
    assert environment.RELEASES_URL in hint
    assert 'pip install --force-reinstall codecin' in hint
    assert '--no-native' in hint
