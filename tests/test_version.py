"""版本号单一真源 (docs/SUGGESTIONS.md §2.2)。

以前 5.3.0 同时写在 pyproject.toml 与 codecin/__init__.py (手工同步),
原生库还对外自报一个完全不同的 "1.0"。现在:
  * 唯一真源 = codecin/__init__.py 的 __version__;
  * pyproject.toml 用 dynamic version 从它派生;
  * Go 侧由 script/gen_native_isa.py 生成 engine.BuildVersion (CI --check 校验)。
"""

import os
import re

import pytest

try:
    import tomllib  # Python 3.11+
except ImportError:                     # pragma: no cover - py3.8~3.10
    tomllib = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

needs_tomllib = pytest.mark.skipif(tomllib is None, reason='tomllib 需要 Python 3.11+')


@needs_tomllib
def test_pyproject_has_no_static_version():
    with open(os.path.join(ROOT, 'pyproject.toml'), 'rb') as f:
        cfg = tomllib.load(f)
    assert 'version' not in cfg['project'], \
        'pyproject 不应再手写 version (会与 codecin.__version__ 漂移)'
    assert 'version' in cfg['project'].get('dynamic', [])
    assert cfg['project']['scripts']['codecin'] == 'codecin.cli:main'


def test_go_generated_version_matches_python():
    import codecin
    path = os.path.join(ROOT, 'codecin', 'native', 'engine', 'version_gen.go')
    with open(path, encoding='utf-8') as f:
        text = f.read()
    m = re.search(r'const BuildVersion = "([^"]+)"', text)
    assert m, 'version_gen.go 缺少 BuildVersion'
    assert m.group(1) == codecin.__version__, \
        f'Go 侧 {m.group(1)} != Python 侧 {codecin.__version__}'


def test_version_looks_like_semver():
    import codecin
    assert re.fullmatch(r'\d+\.\d+\.\d+', codecin.__version__), \
        f'版本号不是 x.y.z 形式: {codecin.__version__!r}'


def test_cli_version_flag(capsys):
    import codecin
    from codecin import cli
    with pytest.raises(SystemExit) as ei:
        cli.build_parser().parse_args(['--version'])
    assert ei.value.code == 0
    assert codecin.__version__ in capsys.readouterr().out
