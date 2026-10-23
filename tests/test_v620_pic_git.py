"""v6.2.0: pic 的 Git 发布/安装路径 (使用本地仓库, 不访问网络)。"""

import os
import shutil
import subprocess

import pytest

from codecin import pic
from codecin.packaging.store import PackageStore

pytestmark = pytest.mark.skipif(shutil.which('git') is None,
                                reason='需要 git 命令')


def _git(cwd, *args):
    subprocess.run(['git', *args], cwd=cwd, check=True,
                   capture_output=True, text=True, encoding='utf-8')


def _init_and_commit(path, name='greet', version='1.0.0'):
    assert pic.main(['init', name, '--path', path, '--version', version]) == 0
    _git(path, 'init', '--quiet')
    _git(path, 'config', 'user.email', 'test@example.com')
    _git(path, 'config', 'user.name', 'Test')
    _git(path, 'add', '-A')
    _git(path, 'commit', '-m', 'init', '--quiet')
    _git(path, 'tag', '-a', f'v{version}', '-m', f'v{version}')
    return path


def test_pic_install_from_git_tag(workdir, monkeypatch):
    monkeypatch.setenv('PIC_HOME', os.path.join(workdir, 'home'))
    repo = _init_and_commit(os.path.join(workdir, 'greet'))
    assert pic.main(['install', f'git+{repo}@v1.0.0']) == 0
    pkg = PackageStore().get('greet')
    assert pkg is not None and pkg.version == '1.0.0'
    assert pkg.rev


def test_pic_install_by_name_from_registry_git(workdir, monkeypatch):
    monkeypatch.setenv('PIC_HOME', os.path.join(workdir, 'home'))
    repo = _init_and_commit(os.path.join(workdir, 'greet'), version='1.2.0')
    assert pic.main(['registry', 'add', 'greet', repo]) == 0
    assert pic.main(['install', 'greet@^1.0']) == 0
    pkg = PackageStore().get('greet')
    assert pkg is not None and pkg.version == '1.2.0'


def test_pic_publish_creates_tag(workdir, monkeypatch):
    monkeypatch.setenv('PIC_HOME', os.path.join(workdir, 'home'))
    repo = _init_and_commit(os.path.join(workdir, 'greet'), version='2.0.0')
    assert pic.main(['publish', '--manifest', repo,
                     '--repo', 'https://example.com/greet.git']) == 0
    tags = subprocess.run(['git', 'tag', '--list'], cwd=repo, check=True,
                          capture_output=True, text=True,
                          encoding='utf-8').stdout
    assert 'v2.0.0' in tags
