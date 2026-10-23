"""工具链 CLI 的共享小工具 (仅标准库)。"""

import contextlib
import sys
from typing import Optional, Sequence

__all__ = ['configure_stdio']


def configure_stdio() -> None:
    """把 stdout/stderr 切到 UTF-8 (Windows 中文控制台默认 GBK)。

    与 ``codecin.cli.main`` 的处理保持一致: 无法编码的字符替换而不崩溃。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(encoding='utf-8', errors='replace')


def strip_separator(args: Optional[Sequence[str]]) -> list:
    """去掉 argparse REMAINDER 可能保留的开头 ``--``。"""
    values = list(args or [])
    if values and values[0] == '--':
        values = values[1:]
    return values
