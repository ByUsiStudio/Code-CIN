"""版本号单一真源 (docs/SUGGESTIONS.md §2.2) + 版本设施 (workstream C)。

以前 5.3.0 同时写在 pyproject.toml 与 codecin/__init__.py (手工同步),
原生库还对外自报一个完全不同的 "1.0"。现在:
  * 唯一真源 = codecin/__init__.py 的 __version__;
  * pyproject.toml 用 dynamic version 从它派生;
  * Go 侧由 script/gen_native_isa.py 生成 engine.BuildVersion (CI --check 校验)。

workstream C 把版本号从"一个字符串"升级为可编程/可自检/可发布的设施:
  * codecin/version.py: version_info() / version_tuple() / compare() 纯函数 +
    build_info() 环境自检 (绝不抛异常);
  * CLI: --build-info 与 --build-info --json;
  * script/bump_version.py: 一条命令走完 真源 -> Go 常量 -> CHANGELOG 骨架;
  * CHANGELOG.md 必须存在当前版本小节 (发布链路门禁, 由 bump_version 保证)。
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

try:
    import tomllib  # Python 3.11+
except ImportError:                     # pragma: no cover - py3.8~3.10
    tomllib = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUMP_SCRIPT = os.path.join(ROOT, 'script', 'bump_version.py')

#: bump_version 落盘时会触碰的文件: dry-run / 被拒绝的调用必须一个字节都不变。
BUMP_TARGETS = (
    os.path.join('codecin', '__init__.py'),
    'CHANGELOG.md',
    os.path.join('codecin', 'native', 'engine', 'version_gen.go'),
    os.path.join('codecin', 'native', 'engine', 'isa_gen.go'),
    os.path.join('codecin', 'native', 'compiler', 'syscalls.go'),
)

#: 在本仓库上做"未被改动"断言时只盯 workstream C 拥有的文件: isa_gen.go /
#: syscalls.go 可能被并行改指令集的同事重新生成, 那不是本工具的副作用。
BUMP_OWNED = (
    os.path.join('codecin', '__init__.py'),
    'CHANGELOG.md',
    os.path.join('codecin', 'native', 'engine', 'version_gen.go'),
)

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


def test_cli_help_intro_leads_with_version(capsys):
    """--help 首行必须始终是当前版本号 (HELP_INTRO 由唯一真源生成)。"""
    import codecin
    from codecin import cli
    assert cli.main(['--help']) == 0
    first_line = capsys.readouterr().out.splitlines()[0]
    assert codecin.__version__ in first_line, first_line


# ==================== workstream C: 版本设施 (codecin/version.py) ====================


def test_version_info_matches_dunder_version():
    """version_info() 必须与唯一真源 __version__ 完全一致。"""
    import codecin
    from codecin import version

    expected = tuple(int(part) for part in codecin.__version__.split('.'))
    assert version.version_info() == expected
    assert version.version_info() == version.version_tuple(codecin.__version__)
    assert version.current_version() == codecin.__version__


def test_version_tuple_is_pure_and_strict():
    from codecin import version

    assert version.version_tuple('1.2.3') == (1, 2, 3)
    assert version.version_tuple('  v10.20.30  ') == (10, 20, 30)
    assert version.version_tuple('5.6.0-rc.1+build.7') == (5, 6, 0)
    assert version.is_version_string('5.6.0') is True
    assert version.try_version_tuple('5.6') is None
    for bad in ('', '5', '5.6', '1.2.3.4', 'abc', '5.6.x', None):
        assert version.is_version_string(bad) is False
        with pytest.raises(ValueError):
            version.version_tuple(bad)


def test_compare_orders_versions_numerically():
    from codecin import version

    assert version.compare('1.2.3', '1.2.3') == 0
    assert version.compare('5.6.0', '5.6.0-rc1') == 0      # 只比较 x.y.z 数值
    assert version.compare('1.2.3', '1.2.4') == -1
    assert version.compare('1.10.0', '1.9.9') == 1         # 数值比较, 不是字典序
    assert version.compare('2.0.0', '10.0.0') == -1
    with pytest.raises(ValueError):
        version.compare('1.2', '1.2.3')


def test_version_info_fails_loudly_on_non_semver(monkeypatch):
    """非 x.y.z 的 __version__ 必须显式失败 (不允许静默给 (0, 0, 0))。"""
    import codecin
    from codecin import version

    monkeypatch.setattr(codecin, '__version__', 'not-a-version', raising=False)
    assert version.current_version() == 'not-a-version'
    with pytest.raises(ValueError):
        version.version_info()
    assert version.try_version_tuple(codecin.__version__) is None


def test_build_info_has_all_documented_fields():
    import codecin
    from codecin import version

    info = version.build_info()
    for key in ('version', 'version_info', 'python', 'platform', 'native',
                'native_version', 'jit', 'package_path'):
        assert key in info, f'build_info() 缺少字段: {key}'
    assert info['version'] == codecin.__version__
    assert info['version_info'] == list(version.version_info())
    assert re.match(r'\d+\.', info['python']), info['python']
    assert isinstance(info['platform'], str) and info['platform']
    assert isinstance(info['native'], bool)
    assert info['native_version'] is None or isinstance(info['native_version'], str)
    assert isinstance(info['jit'], bool)
    assert os.path.isdir(info['package_path'])
    assert os.path.isfile(os.path.join(info['package_path'], 'version.py'))
    json.dumps(info)                       # 必须可直接 JSON 序列化


def test_build_info_covers_real_native_engine():
    """本机有 codecin_native.dll: 原生分支做真实覆盖 (没编译则跳过)。"""
    from codecin import native, version

    if native.get_engine() is None:
        pytest.skip('native library not built')
    info = version.build_info()
    assert info['native'] is True
    assert isinstance(info['native_version'], str) and info['native_version']
    assert info['native_path'] and os.path.isfile(info['native_path'])
    expected = info['version'] in info['native_version']
    assert info['native_version_matches'] is expected


def test_build_info_degrades_when_native_missing(monkeypatch):
    """原生库不存在时必须优雅降级, 其余字段照常可用。"""
    from codecin import native, version

    monkeypatch.setattr(native, 'get_engine', lambda logger=None: None)
    info = version.build_info()
    assert info['native'] is False
    assert info['native_version'] is None
    assert info['native_path'] is None
    assert info['native_version_matches'] is None
    assert info['version'] == version.current_version()
    assert '不可用' in version.format_build_info(info)


def test_build_info_survives_broken_native_library(monkeypatch):
    """dlopen 失败 (OSError) 或 ABI/自报版本异常时, build_info() 绝不能抛异常。"""
    from codecin import native, version

    def boom(logger=None):
        raise OSError('simulated dlopen failure')

    monkeypatch.setattr(native, 'get_engine', boom)
    info = version.build_info()
    assert info['native'] is False
    assert info['native_version'] is None

    class _BadAbi:
        def version(self):
            raise RuntimeError('symbol mismatch')

    monkeypatch.setattr(native, 'get_engine', lambda logger=None: _BadAbi())
    info = version.build_info()
    assert info['native'] is True
    assert info['native_version'] is None
    assert info['native_version_matches'] is None


def test_build_info_survives_platform_probe_failure(monkeypatch):
    """任何探测函数炸掉时, build_info() 仍返回完整 dict。"""
    from codecin import version

    def boom(*_args, **_kwargs):
        raise RuntimeError('probe failed')

    monkeypatch.setattr(version, 'current_version', boom)
    monkeypatch.setattr(version.platform, 'python_version', boom)
    monkeypatch.setattr(version.platform, 'machine', boom)
    monkeypatch.setattr(version, 'jit_available', boom)
    info = version.build_info()
    assert info['version'] == ''
    assert info['version_info'] is None
    assert info['machine'] == ''
    assert info['jit'] is False
    assert info['python']                     # 回退到 sys.version


def test_format_build_info_and_json_are_plain_text():
    import codecin
    from codecin import version

    info = version.build_info()
    text = version.format_build_info(info)
    assert codecin.__version__ in text
    assert 'native' in text and 'jit' in text
    assert '\x1b[' not in text                 # 无 ANSI: 可重定向/贴报告
    assert json.loads(version.build_info_json(info))['version'] == codecin.__version__


def test_cli_build_info_text(capsys):
    import codecin
    from codecin import cli

    assert cli.main(['--build-info']) == 0
    out = capsys.readouterr().out
    assert codecin.__version__ in out
    assert 'native' in out and 'python' in out
    assert '\x1b[' not in out


def test_cli_build_info_json(capsys):
    import codecin
    from codecin import cli, version

    assert cli.main(['--build-info', '--json']) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['version'] == codecin.__version__
    assert data['version_info'] == list(version.version_info())
    assert isinstance(data['native'], bool)
    assert isinstance(data['jit'], bool)


def test_cli_json_flag_requires_build_info(capsys):
    from codecin import cli

    assert cli.main(['--json']) != 0
    captured = capsys.readouterr()
    assert '--build-info' in (captured.err + captured.out)


def test_changelog_lists_current_version():
    """发布链路门禁: CHANGELOG.md 必须存在当前版本的小节。"""
    import codecin

    with open(os.path.join(ROOT, 'CHANGELOG.md'), encoding='utf-8') as f:
        text = f.read()
    assert re.search(r'^## \[' + re.escape(codecin.__version__) + r'\]', text,
                     re.MULTILINE), (
        f'CHANGELOG.md 缺少 [{codecin.__version__}] 小节: 发布前请运行 '
        f'`python script/bump_version.py <新版本>` 并补齐内容')


# ==================== workstream C: bump_version 发布链路 ====================


def _snapshot(root, rels=BUMP_TARGETS):
    """(sha256, 字节数, mtime_ns) 快照: 未落盘的调用必须完全不变。"""
    snap = {}
    for rel in rels:
        path = os.path.join(root, rel)
        with open(path, 'rb') as f:
            data = f.read()
        stat = os.stat(path)
        snap[rel] = (hashlib.sha256(data).hexdigest(), len(data), stat.st_mtime_ns)
    return snap


def _bump(*args, cwd=None):
    return subprocess.run(
        [sys.executable, BUMP_SCRIPT, *args], cwd=cwd or ROOT,
        capture_output=True, text=True, encoding='utf-8', errors='replace')


def _make_repo_copy(dst):
    """复制一棵可真实运行 script/gen_native_isa.py 的最小仓库树。"""
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '*.dll', '*.so',
                                    '*.dylib', '.git')
    try:
        shutil.copytree(os.path.join(ROOT, 'codecin'), os.path.join(dst, 'codecin'),
                        ignore=ignore)
        os.makedirs(os.path.join(dst, 'script'), exist_ok=True)
        shutil.copy(os.path.join(ROOT, 'script', 'gen_native_isa.py'),
                    os.path.join(dst, 'script', 'gen_native_isa.py'))
        shutil.copy(os.path.join(ROOT, 'CHANGELOG.md'),
                    os.path.join(dst, 'CHANGELOG.md'))
    except OSError as e:                    # 并行编辑 (文件增删) 导致复制失败
        pytest.skip(f'副本复制失败 (并行编辑中?): {e}')
    return dst


def test_bump_version_dry_run_does_not_touch_any_file():
    before = _snapshot(ROOT, BUMP_OWNED)
    r = _bump('--dry-run', '9.9.9')
    assert r.returncode == 0, r.stdout + r.stderr
    assert '9.9.9' in r.stdout
    assert 'dry-run' in r.stdout
    assert _snapshot(ROOT, BUMP_OWNED) == before, '--dry-run 不允许改动任何文件'


def test_bump_version_dry_run_on_repo_copy(workdir):
    """在 workdir 的副本上试算: 打印骨架, 且一个字节都不落盘。"""
    repo = _make_repo_copy(os.path.join(workdir, 'copy'))
    before = _snapshot(repo)
    r = _bump('--root', repo, '--dry-run', '6.0.0')
    assert r.returncode == 0, r.stdout + r.stderr
    assert '## [6.0.0]' in r.stdout
    assert '### 新增 (Added)' in r.stdout
    assert _snapshot(repo) == before, '--dry-run 不允许改动副本文件'


@pytest.mark.parametrize('bad', ['abc', '5.6', '1.2.3.4', '5.6.0-rc1', 'v5.7.0'])
def test_bump_version_rejects_invalid_version_arguments(bad):
    before = _snapshot(ROOT, BUMP_OWNED)
    r = _bump('--dry-run', bad)
    assert r.returncode != 0, f'{bad!r} 应被拒绝 (stdout={r.stdout})'
    assert _snapshot(ROOT, BUMP_OWNED) == before


@pytest.mark.parametrize('old', ['5.6.0', '5.5.3'])
def test_bump_version_rejects_non_increasing_versions(old):
    r = _bump('--dry-run', old)
    assert r.returncode != 0, f'{old} 不是递增版本, 应被拒绝'
    assert '大于' in (r.stdout + r.stderr)


def test_bump_version_rejected_without_dry_run_keeps_files():
    """连非 dry-run 的非法调用 (回退版本) 也不允许写任何文件。"""
    before = _snapshot(ROOT, BUMP_OWNED)
    r = _bump('5.5.0')
    assert r.returncode != 0
    assert _snapshot(ROOT, BUMP_OWNED) == before


def test_bump_version_full_run_in_repo_copy(workdir):
    """真实落盘链路演练: 真源 -> Go 常量再生 -> CHANGELOG 骨架 (在副本里跑, 不碰本仓库)。"""
    import codecin

    repo = _make_repo_copy(os.path.join(workdir, 'copy'))
    # 副本是工作树的快照: 若同事正在改 codecin/, 这个副本可能导不进来 —— 那是
    # 并行编辑, 不是 bump_version 的问题, 跳过而不是误报失败。
    probe = subprocess.run([sys.executable, '-c', 'import codecin; print(codecin.__version__)'],
                           cwd=repo, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
    if probe.returncode != 0:
        pytest.skip(f'副本 codecin 包不可导入 (并行编辑中?): {probe.stderr[-200:]}')

    before_real = _snapshot(ROOT, BUMP_OWNED)
    # 目标版本必须**严格大于**当前版本, 所以由当前版本推导而不是写死 —— 写死会在
    # 每次提升版本后失效 (例如 5.7.0 在版本升到 5.7.4 之后就不再是合法目标)。
    cur = tuple(int(x) for x in codecin.__version__.split('.'))
    target = f'{cur[0]}.{cur[1]}.{cur[2] + 1}'
    r = _bump('--root', repo, '--date', '2030-01-02', target)
    assert r.returncode == 0, r.stdout + r.stderr

    with open(os.path.join(repo, 'codecin', '__init__.py'), encoding='utf-8') as f:
        init_text = f.read()
    escaped = target.replace('.', r'\.')
    assert re.search(rf'^__version__ = "{escaped}"$', init_text, re.MULTILINE), init_text[:200]

    with open(os.path.join(repo, 'codecin', 'native', 'engine', 'version_gen.go'),
              encoding='utf-8') as f:
        go_text = f.read()
    assert f'const BuildVersion = "{target}"' in go_text, go_text

    with open(os.path.join(repo, 'CHANGELOG.md'), 'rb') as f:
        changelog_bytes = f.read()
    assert b'\r\n' not in changelog_bytes, 'CHANGELOG 换行风格被改成了 CRLF'
    changelog = changelog_bytes.decode('utf-8')          # UTF-8 中文不得损坏
    assert f'## [{target}] - 2030-01-02' in changelog
    assert '### 修复 (Fixed)' in changelog
    assert changelog.index(f'[{target}]') < changelog.index(f'[{codecin.__version__}]')

    # 副本里的 Go 生成物必须仍然自洽 (CI 的 --check 门禁)
    check = subprocess.run(
        [sys.executable, os.path.join(repo, 'script', 'gen_native_isa.py'), '--check'],
        cwd=repo, capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert check.returncode == 0, check.stdout + check.stderr

    assert _snapshot(ROOT, BUMP_OWNED) == before_real, \
        'bump_version --root 不允许碰真实仓库'
