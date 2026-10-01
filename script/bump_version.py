#!/usr/bin/env python3
"""版本提升: 一条命令走完"单一真源 -> Go 常量 -> CHANGELOG"的发布链路。

用法::

    python script/bump_version.py 5.7.0            # 落盘
    python script/bump_version.py --dry-run 5.7.0  # 只打印将要做的改动, 不写任何文件

`--root DIR` 可指向另一棵完整的仓库副本 (含 script/ 与 codecin/), 便于在临时
目录里演练真实落盘流程; 默认操作本脚本所在仓库。

一次完成四件事 (全部成功才算成功, 失败会回滚已写的文件):

  1. 校验入参: 必须严格 `x.y.z`, 且**必须大于**当前版本 (否则非 0 退出, 不动文件);
  2. 改写 `codecin/__init__.py` 的 `__version__` (全项目唯一真源,
     见 pyproject.toml 的 `dynamic version`);
  3. 调用 `script/gen_native_isa.py` 重新生成 Go 侧版本常量
     (`codecin/native/engine/version_gen.go` 的 `engine.BuildVersion`),
     保证 `python script/gen_native_isa.py --check` 仍然通过;
  4. 在 `CHANGELOG.md` 顶部插入新版本小节骨架 (保持 Keep a Changelog 格式,
     小节内容留 `_待补充_` 占位)。

文本一律按 UTF-8 读写, 并**保持文件原有换行风格** (LF/CRLF); `CHANGELOG.md`
是 UTF-8 中文, 不允许改变编码。

退出码: 0 成功; 1 版本未递增 / 生成或自检失败; 2 参数、版本号格式或目标仓库根非法。
"""

import argparse
import contextlib
import datetime
import os
import re
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, REPO_ROOT)

from codecin.version import (  # noqa: E402
    STRICT_VERSION_RE,
    compare,
    version_tuple,
)

INIT_REL = os.path.join('codecin', '__init__.py')
CHANGELOG_REL = 'CHANGELOG.md'
VERSION_GO_REL = os.path.join('codecin', 'native', 'engine', 'version_gen.go')
GEN_ISA_REL = os.path.join('script', 'gen_native_isa.py')

_VERSION_LINE_RE = re.compile(
    r'^__version__[ \t]*=[ \t]*(["\'])([^"\'\r\n]*)\1', re.MULTILINE)
_BUILD_VERSION_RE = re.compile(r'^const BuildVersion = "([^"]*)"', re.MULTILINE)
_CHANGELOG_HEADING_RE = re.compile(r'^## \[', re.MULTILINE)

# 扫描"其他仍含旧版本号的文件"时跳过的目录 (docs/ 属于独立文档仓库)
_SKIP_DIRS = frozenset({
    '.git', 'docs', 'node_modules', '__pycache__',
    '.pytest_tmp', '.venv', 'venv', 'build', 'dist', '.gotmp', '.gocache',
    '.trae', '.idea', '.vscode', '.mypy_cache', '.ruff_cache', '.pytest_cache',
})
_SCAN_EXTS = ('.md', '.toml', '.py', '.go', '.iss', '.yml', '.yaml', '.txt',
              '.json', '.cfg', '.spec', '.ini')


class BumpError(Exception):
    """版本提升被拒绝 (带退出码)。"""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


# ==================== 文本 IO (UTF-8 + 保留换行风格) ====================


def _read_text(path: str) -> str:
    # newline='' : 不做换行翻译, 原样保留 CRLF/LF
    with open(path, encoding='utf-8', newline='') as f:
        return f.read()


def _write_text(path: str, text: str) -> None:
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def _read_bytes(path: str) -> bytes:
    with open(path, 'rb') as f:
        return f.read()


def _write_bytes(path: str, data: bytes) -> None:
    with open(path, 'wb') as f:
        f.write(data)


# ==================== 纯函数 (便于测试) ====================


def read_source_version(text: str) -> str:
    """从 `codecin/__init__.py` 文本中读出 `__version__` 的值。"""
    m = _VERSION_LINE_RE.search(text)
    if m is None:
        raise BumpError(f'{INIT_REL} 中找不到 __version__ = "x.y.z" 赋值', code=2)
    return m.group(2)


def rewrite_source_version(text: str, new_version: str) -> str:
    """把 `__version__` 赋值的值改成 `new_version` (保留引号风格与行尾)。"""
    def repl(m) -> str:
        return f'__version__ = {m.group(1)}{new_version}{m.group(1)}'

    new_text, count = _VERSION_LINE_RE.subn(repl, text, count=1)
    if count != 1:
        raise BumpError(f'{INIT_REL} 中找不到 __version__ = "x.y.z" 赋值', code=2)
    return new_text


def build_changelog_section(version: str, date_str: str, nl: str = '\n') -> str:
    """新版本小节骨架 (Keep a Changelog 风格, 与既有小节结构一致)。"""
    lines = [
        f'## [{version}] - {date_str}',
        '',
        '### 新增 (Added)',
        '',
        '- _待补充_',
        '',
        '### 修复 (Fixed)',
        '',
        '- _待补充_',
        '',
        '### 变更 (Changed)',
        '',
        '- _待补充_',
        '',
    ]
    return nl.join(lines)


def insert_changelog_section(text: str, section: str, nl: str = '\n') -> str:
    """把新小节插到第一个 `## [` 小节之前 (找不到则追加到文件末尾)。"""
    m = _CHANGELOG_HEADING_RE.search(text)
    if m is None:
        head = text if text.endswith(nl) else text + nl
        return head + section + nl
    return text[:m.start()] + section + nl + text[m.start():]


def detect_newline(text: str) -> str:
    """检测文件换行风格 (CRLF 优先, 默认 LF)。"""
    return '\r\n' if '\r\n' in text else '\n'


# ==================== 其他仍含旧版本号的文件 (提示用) ====================


def find_old_version_files(root: str, old_version: str) -> list:
    """列出仓库内 (排除 docs/ 等) 仍出现旧版本号的文本文件, 供人工确认。"""
    hits = []
    if not old_version:
        return hits
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if not name.lower().endswith(_SCAN_EXTS):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace('\\', '/')
            if rel in (CHANGELOG_REL.replace('\\', '/'),
                       INIT_REL.replace('\\', '/'),
                       VERSION_GO_REL.replace('\\', '/')):
                continue                      # 这三个由本脚本负责
            try:
                if os.path.getsize(path) > 2 * 1024 * 1024:
                    continue
                with open(path, encoding='utf-8', errors='ignore') as f:
                    if old_version in f.read():
                        hits.append(rel)
            except OSError:                   # pragma: no cover - 无权限/竞态
                continue
    return sorted(hits)


# ==================== 主流程 ====================


def _parse_date(text: str) -> str:
    try:
        datetime.datetime.strptime(text, '%Y-%m-%d')
    except ValueError:
        raise BumpError(f'--date 必须是 YYYY-MM-DD: {text!r}', code=2) from None
    return text


def run(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.root) if args.root else REPO_ROOT
    gen_script = os.path.join(root, GEN_ISA_REL)
    if not os.path.isfile(gen_script):
        raise BumpError(
            f'找不到 {gen_script}: --root 必须指向一个包含 script/ 与 codecin/ 的'
            f'仓库树 (完整副本); 只做试算请加 --dry-run', code=2)

    new_version = args.version.strip()
    if STRICT_VERSION_RE.match(new_version) is None:
        raise BumpError(f'版本号必须是严格的 x.y.z 形式: {args.version!r}', code=2)
    date_str = _parse_date(args.date or datetime.date.today().isoformat())

    init_path = os.path.join(root, INIT_REL)
    changelog_path = os.path.join(root, CHANGELOG_REL)
    version_go_path = os.path.join(root, VERSION_GO_REL)

    if not os.path.isfile(init_path):
        raise BumpError(f'找不到 {init_path}', code=2)
    if not os.path.isfile(changelog_path):
        raise BumpError(f'找不到 {changelog_path}', code=2)

    init_text = _read_text(init_path)
    current_version = read_source_version(init_text)
    try:
        version_tuple(current_version)
    except ValueError:
        raise BumpError(
            f'当前版本号 {current_version!r} 不是 x.y.z, 无法比较', code=2) from None

    if compare(new_version, current_version) <= 0:
        raise BumpError(
            f'新版本号必须大于当前版本: {new_version} <= {current_version} '
            f'(当前 {INIT_REL})', code=1)

    new_init_text = rewrite_source_version(init_text, new_version)

    changelog_text = _read_text(changelog_path)
    if f'[{new_version}]' in changelog_text:
        raise BumpError(
            f'{CHANGELOG_REL} 已存在 [{new_version}] 小节, 拒绝重复插入', code=1)
    nl = detect_newline(changelog_text)
    section = build_changelog_section(new_version, date_str, nl)
    new_changelog_text = insert_changelog_section(changelog_text, section, nl)

    old_build_version = None
    if os.path.isfile(version_go_path):
        m = _BUILD_VERSION_RE.search(_read_text(version_go_path))
        old_build_version = m.group(1) if m else None

    # ---------------- dry-run: 只打印 ----------------
    if args.dry_run:
        out = sys.stdout
        out.write(f'[bump_version] dry-run: 不修改任何文件 (root={root})\n')
        out.write(f'  当前版本: {current_version}  ->  新版本: {new_version}\n')
        out.write(f'  1) {INIT_REL}: __version__ = "{current_version}" -> '
                  f'"{new_version}"\n')
        out.write('  2) 调用 python script/gen_native_isa.py 重新生成:\n')
        out.write(f'       {VERSION_GO_REL}: BuildVersion = '
                  f'"{old_build_version if old_build_version is not None else "?"}"'
                  f' -> "{new_version}"\n')
        out.write(f'  3) {CHANGELOG_REL}: 顶部插入新版本小节:\n')
        for line in section.split(nl):
            out.write(f'       {line}\n' if line else '\n')
        hints = find_old_version_files(root, current_version)
        if hints:
            out.write(f'  提示: 以下文件仍含旧版本号 {current_version}, '
                      f'请人工确认是否同步 (可能只是示例/历史记录):\n')
            for rel in hints:
                out.write(f'       - {rel}\n')
        return 0

    # ---------------- 落盘 (失败回滚) ----------------
    init_backup = _read_bytes(init_path)
    changelog_backup = _read_bytes(changelog_path)
    _write_text(init_path, new_init_text)
    _write_text(changelog_path, new_changelog_text)
    sys.stdout.write(f'[bump_version] {INIT_REL}: __version__ = "{new_version}"\n')
    sys.stdout.write(f'[bump_version] {CHANGELOG_REL}: 已插入 [{new_version}] 小节 '
                     f'({date_str})\n')

    proc = subprocess.run(
        [sys.executable, gen_script], cwd=root,
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    if proc.returncode != 0:
        _write_bytes(init_path, init_backup)
        _write_bytes(changelog_path, changelog_backup)
        sys.stderr.write(f'[bump_version] 错误: {GEN_ISA_REL} 执行失败 '
                         f'(rc={proc.returncode}), 已回滚 {INIT_REL} 与 '
                         f'{CHANGELOG_REL}\n')
        sys.stderr.write(proc.stdout or '')
        sys.stderr.write(proc.stderr or '')
        return proc.returncode or 1
    for line in (proc.stdout or '').splitlines():
        sys.stdout.write(f'[bump_version]   {line}\n')

    # ---------------- 自检: 三处都必须体现新版本 ----------------
    problems = []
    if read_source_version(_read_text(init_path)) != new_version:
        problems.append(f'{INIT_REL} 未写入新版本')
    m = _BUILD_VERSION_RE.search(_read_text(version_go_path)) \
        if os.path.isfile(version_go_path) else None
    if m is None or m.group(1) != new_version:
        problems.append(f'{VERSION_GO_REL} 的 BuildVersion 不是 {new_version}')
    if f'[{new_version}]' not in _read_text(changelog_path):
        problems.append(f'{CHANGELOG_REL} 未出现 [{new_version}]')

    if problems:
        _write_bytes(init_path, init_backup)
        _write_bytes(changelog_path, changelog_backup)
        sys.stderr.write('[bump_version] 错误: 自检失败, 已回滚文本改动 '
                         '(注意 Go 生成物可能已更新):\n')
        for p in problems:
            sys.stderr.write(f'  - {p}\n')
        return 1

    sys.stdout.write(f'[bump_version] 完成: {current_version} -> {new_version}\n')
    sys.stdout.write('[bump_version] 下一步: 填写 CHANGELOG 小节内容, '
                     '运行 python script/gen_native_isa.py --check 与 '
                     'python -m pytest tests/test_version.py\n')
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='bump_version.py',
        description='版本提升: codecin/__init__.py -> Go version_gen.go -> CHANGELOG.md')
    p.add_argument('version', help='新版本号 (严格 x.y.z, 且必须大于当前版本)')
    p.add_argument('--dry-run', action='store_true',
                   help='只打印将要做的改动, 不写任何文件')
    p.add_argument('--date', default=None, metavar='YYYY-MM-DD',
                   help='CHANGELOG 小节日期 (默认今天)')
    p.add_argument('--root', default=None, metavar='DIR',
                   help='目标仓库根 (默认: 本脚本所在仓库; 非 --dry-run 时该目录'
                        '必须包含 script/gen_native_isa.py 与 codecin/)')
    return p


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(encoding='utf-8', errors='replace')
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except BumpError as e:
        sys.stderr.write(f'[bump_version] 错误: {e}\n')
        return e.code


if __name__ == '__main__':
    sys.exit(main())
