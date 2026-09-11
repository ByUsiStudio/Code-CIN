import re
from typing import Any, Optional

from rich import box as _box_mod
from rich.console import Console as RichConsole
from rich.panel import Panel as RichPanel
from rich.table import Table as RichTable
from rich.text import Text

_ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def _to_renderable(obj: Any) -> Any:
    """将任意对象转换为 rich 可渲染对象。

    str 含 ANSI 时转 Text (rich 渲染时按终端能力自动上/去色)。
    """
    if isinstance(obj, str):
        if '\x1b' in obj:
            return Text.from_ansi(obj)
        return obj
    if hasattr(obj, '__rich_console__'):
        return obj
    return str(obj)


class Colors:
    """ANSI 颜色码 (历史 API, 输出层自动转换为 rich Text)。"""

    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'

    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'

    BG_BLACK = '\033[40m'
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'
    BG_MAGENTA = '\033[45m'
    BG_CYAN = '\033[46m'
    BG_WHITE = '\033[47m'

    @staticmethod
    def strip(text: str) -> str:
        return _ANSI_RE.sub('', text)

    @staticmethod
    def colorize(text: str, color: str, bold: bool = False) -> str:
        return f"{Colors.BOLD if bold else ''}{color}{text}{Colors.RESET}"


class Console:
    """rich 控制台封装。"""

    def __init__(self, force_color: Optional[bool] = None):
        kwargs: dict = {}
        if force_color is not None:
            kwargs['force_terminal'] = force_color
            kwargs['color_system'] = 'truecolor' if force_color else None
        self._rc = RichConsole(
            highlight=False, emoji=False, markup=False,
            soft_wrap=False, **kwargs)

    @property
    def rich(self) -> RichConsole:
        """底层 rich Console (供 logger/异常渲染复用)。"""
        return self._rc

    @property
    def color_support(self) -> bool:
        return self._rc.color_system is not None

    def width(self) -> int:
        return self._rc.width

    def print(self, *args, **kwargs) -> None:
        objs = [_to_renderable(a) for a in args]
        self._rc.print(*objs, sep=kwargs.pop('sep', ' '),
                       markup=False, highlight=False, **kwargs)

    def print_exception(self, **kwargs) -> None:
        """rich 彩色堆栈回溯。"""
        self._rc.print_exception(word_wrap=True, **kwargs)

    def clear(self) -> None:
        self._rc.clear()

    def rule(self, title: str = "", style: str = "cyan") -> None:
        self._rc.rule(Text.from_ansi(title) if '\x1b' in title else title,
                      style=style, align='center')

    def clear_line(self) -> None:
        self._rc.print('\r' + ' ' * 80 + '\r', end='')


class Panel:
    """rich Panel 适配器, 兼容 str(Panel(...)) 用法。"""

    def __init__(self, content, title: str = "", border_style: str = "",
                 box=None):
        self._content = content
        self._title = title
        self._border_style = border_style or 'cyan'
        self._box = box or _box_mod.ROUNDED

    def build(self) -> RichPanel:
        content = _to_renderable(self._content)
        title = Text.from_ansi(self._title) if '\x1b' in self._title \
            else self._title
        return RichPanel(content, title=title, border_style=self._border_style,
                         box=self._box, expand=False)

    def __rich_console__(self, console, options):
        return self.build().__rich_console__(console, options)

    def __str__(self) -> str:
        import io
        buf = io.StringIO()
        c = RichConsole(file=buf, width=80, highlight=False, emoji=False,
                        markup=False)
        c.print(self.build())
        return buf.getvalue().rstrip('\n')


class Table:
    """rich Table 适配器, 兼容旧 add_column/add_row API。

    print(table) 时按 rich 全彩渲染; str(table) 输出纯文本。
    """

    def __init__(self, title: str = "", box=None, border_style: str = "",
                 show_lines: bool = False):
        self._title = title
        self._box = box or _box_mod.ROUNDED
        self._border_style = border_style or 'cyan'
        self._show_lines = show_lines
        self._cols: list = []
        self._rows: list = []

    def add_column(self, name, style: str = "", width: int = 0, **kw) -> None:
        self._cols.append((str(name), style or None, width or None, kw))

    def add_row(self, *args) -> None:
        self._rows.append([_to_renderable(a) if isinstance(a, str) else a
                           for a in args])

    @property
    def rows(self) -> list:
        return self._rows

    @property
    def headers(self) -> list:
        return [name for name, _, _, _ in self._cols]

    def build(self) -> RichTable:
        t = RichTable(title=self._title or None, box=self._box,
                      border_style=self._border_style,
                      show_lines=self._show_lines, highlight=False)
        for name, style, width, kw in self._cols:
            t.add_column(name, style=style, width=width, **kw)
        for row in self._rows:
            t.add_row(*row)
        return t

    def __rich_console__(self, console, options):
        return self.build().__rich_console__(console, options)

    def __str__(self) -> str:
        import io
        buf = io.StringIO()
        c = RichConsole(file=buf, width=80, highlight=False, emoji=False,
                        markup=False)
        c.print(self.build())
        return buf.getvalue().rstrip('\n')
