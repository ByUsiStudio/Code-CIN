"""Git 操作封装 (克隆 / 标签 / 工作区检查)。

只依赖命令行 ``git``: 不引入 GitPython, 保持工具轻量; 所有调用都显式
禁用交互式凭据提示 (``GIT_TERMINAL_PROMPT=0``), 避免 CI/工具里卡住。
"""

import os
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

__all__ = [
    'VcsError', 'clone', 'commit_all', 'create_tag', 'current_rev',
    'git_available', 'git_init', 'is_clean', 'is_git_spec', 'is_repo',
    'list_tags', 'push_tag', 'remote_url', 'split_git_spec',
]

_SHA_RE = re.compile(r'^[0-9a-f]{7,40}$')
_URL_PREFIXES = ('git+', 'git@', 'http://', 'https://', 'ssh://', 'git://',
                 'file://')


class VcsError(RuntimeError):
    """Git 命令失败或仓库状态不满足要求。"""


def git_available() -> bool:
    return shutil.which('git') is not None


def _run(args: List[str], cwd: Optional[str] = None,
         timeout: int = 300) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault('GIT_TERMINAL_PROMPT', '0')
    env.setdefault('GIT_ASKPASS', 'echo')
    env.setdefault('GCM_INTERACTIVE', 'never')
    try:
        proc = subprocess.run(['git', *args], cwd=cwd, env=env,
                              capture_output=True, text=True,
                              encoding='utf-8', errors='replace',
                              timeout=timeout)
    except FileNotFoundError as e:
        raise VcsError('未找到 git 命令; 请安装 Git 后重试') from e
    except subprocess.TimeoutExpired as e:
        raise VcsError(f'git 命令超时 ({timeout}s): git {" ".join(args)}') from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or '').strip()
        raise VcsError(
            f'git {" ".join(args)} 失败 (exit {proc.returncode}):\n{detail}')
    return proc


def is_git_spec(spec: str) -> bool:
    """字符串是否像 Git 地址 (而不是包名)。"""
    text = str(spec or '').strip()
    if not text:
        return False
    low = text.lower()
    if low.startswith(_URL_PREFIXES) or low.endswith('.git'):
        return True
    # 本地已存在的目录: 由调用方决定是否作为路径依赖; 这里不臆测
    return False


def split_git_spec(spec: str) -> Tuple[str, Optional[str]]:
    """拆出 ``URL@ref``; 兼容 ``git@host:user/repo.git`` 形式。"""
    text = str(spec or '').strip()
    if text.lower().startswith('git+'):
        text = text[4:]
    ref: Optional[str] = None
    if text.startswith('git@'):
        # scp 风格: 只有最后一个 @ 之后不含 / 或 : 时才视为 ref
        head, sep, tail = text.rpartition('@')
        if sep and tail and '/' not in tail and ':' not in tail:
            text, ref = head, tail
        return text, ref
    if '@' in text:
        head, tail = text.rsplit('@', 1)
        if tail and '/' not in tail and ':' not in tail:
            text, ref = head, tail
    return text, ref


def clone(url: str, dest: str, ref: Optional[str] = None,
          depth: Optional[int] = 1) -> str:
    """克隆到 ``dest``; ``ref`` 可为标签/分支/提交。

    先克隆到同级临时目录, 成功后再改名, 避免失败时留下半个仓库。
    """
    url, inline_ref = split_git_spec(url)
    ref = ref or inline_ref
    dest = os.path.abspath(dest)
    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)
    # 不用 tempfile.mkdtemp: 受限环境可能让该目录不可写; 普通 makedirs 目录
    # 继承父目录 ACL, 克隆到其子目录后再原子改名。
    tmp = os.path.join(parent, f'.clone-{os.getpid()}-{os.urandom(4).hex()}')
    os.makedirs(tmp, exist_ok=True)
    work = os.path.join(tmp, 'repo')
    attempt: List[str] = ['clone', '--quiet']
    if depth and not (ref and _SHA_RE.match(ref)):
        attempt += ['--depth', str(depth)]
    if ref and not _SHA_RE.match(ref):
        attempt += ['--branch', ref]
    attempt += [url, work]
    try:
        try:
            _run(attempt)
        except VcsError:
            # 浅克隆/指定分支失败时退回完整克隆, 再 checkout
            shutil.rmtree(work, ignore_errors=True)
            _run(['clone', '--quiet', url, work])
            if ref:
                _run(['checkout', '--quiet', ref], cwd=work)
        else:
            if ref and _SHA_RE.match(ref):
                _run(['checkout', '--quiet', ref], cwd=work)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.replace(work, dest)
        return dest
    finally:
        if os.path.exists(tmp):
            shutil.rmtree(tmp, ignore_errors=True)


def is_repo(path: str) -> bool:
    return os.path.isdir(os.path.join(path, '.git')) or (
        os.path.isfile(os.path.join(path, '.git')))


def git_init(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    if not is_repo(path):
        _run(['init', '--quiet'], cwd=path)


def current_rev(path: str) -> str:
    return _run(['rev-parse', 'HEAD'], cwd=path).stdout.strip()


def is_clean(path: str) -> bool:
    """工作区是否干净 (含未跟踪文件)。"""
    return not _run(['status', '--porcelain'], cwd=path).stdout.strip()


def remote_url(path: str, remote: str = 'origin') -> Optional[str]:
    proc = _run(['remote', 'get-url', remote], cwd=path)
    value = proc.stdout.strip()
    return value or None


def list_tags(path: Optional[str] = None, url: Optional[str] = None) -> List[str]:
    if path:
        out = _run(['tag', '--list'], cwd=path).stdout
    elif url:
        out = _run(['ls-remote', '--tags', '--refs', url]).stdout
        return [line.split('refs/tags/', 1)[1] for line in out.splitlines()
                if 'refs/tags/' in line]
    else:
        raise VcsError('list_tags 需要 path 或 url')
    return [line.strip() for line in out.splitlines() if line.strip()]


def create_tag(path: str, tag: str, message: Optional[str] = None) -> bool:
    """创建带注释标签; 已存在且指向同一提交时返回 False。"""
    exists = _run(['tag', '--list', tag], cwd=path).stdout.strip()
    if exists:
        return False
    _run(['tag', '-a', tag, '-m', message or tag], cwd=path)
    return True


def push_tag(path: str, tag: str, remote: str = 'origin') -> None:
    _run(['push', remote, tag], cwd=path, timeout=600)


def commit_all(path: str, message: str) -> None:
    _run(['add', '-A'], cwd=path)
    _run(['commit', '-m', message], cwd=path)
