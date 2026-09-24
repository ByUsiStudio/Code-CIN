"""打包与发布设计门禁。

设计约定 (5.5.0):

* PyPI 包**不携带预编译原生库** —— 库由用户安装时 `setup.py` 用本机 Go 工具链
  现场编译 (见 setup.py: BuildPyWithNative);
* 内置标准库 `codecin/lib/*.cin` 必须随包分发;
* 发布脚本只上传 **sdist**: 如果同时发 wheel, pip 会优先装 wheel 从而跳过构建,
  用户就拿不到原生加速。
"""

import io
import os
import re
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINARY_SUFFIX = ('.dll', '.so', '.dylib')


def _pyproject():
    with open(os.path.join(ROOT, 'pyproject.toml'), 'rb') as f:
        return tomllib.load(f)


def _read(name):
    with io.open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return f.read()


def test_package_data_lists_stdlib():
    data = _pyproject()['tool']['setuptools']['package-data']
    all_patterns = [p for pats in data.values() for p in pats]
    assert any('lib/*.cin' in p for p in all_patterns), \
        f'内置标准库未列入 package-data: {data}'


def test_package_data_has_no_native_binaries():
    """原生库是构建产物, 不能进包 (否则会带错平台/架构的库)。"""
    data = _pyproject()['tool']['setuptools']['package-data']
    bad = [p for pats in data.values() for p in pats
           if p.endswith(BINARY_SUFFIX)]
    assert not bad, f'package-data 仍在打包预编译原生库: {bad}'


def test_manifest_ships_go_sources_but_not_binaries():
    """sdist 必须带 Go 源码 (安装时要编译), 但不能带预编译库。"""
    text = _read('MANIFEST.in')
    assert 'codecin/native' in text and '*.go' in text, \
        'sdist 缺少 Go 源码, 用户安装时无法编译原生库'
    assert 'build.sh' in text and 'build.ps1' in text, \
        'sdist 缺少原生库构建脚本'
    binaries = [ln for ln in text.splitlines()
                if ln.strip().startswith('include') and
                any(ln.rstrip().endswith(s) for s in BINARY_SUFFIX)]
    assert not binaries, f'MANIFEST.in 仍在打包预编译原生库: {binaries}'


def test_license_uses_spdx_string():
    """旧的 { text = "MIT" } 表格写法已弃用, 2027-02 起不再受支持。"""
    project = _pyproject()['project']
    assert isinstance(project['license'], str), \
        'project.license 必须使用 SPDX 字符串写法'
    requires = _pyproject()['build-system']['requires']
    assert any('setuptools>=77' in r for r in requires), \
        'SPDX license 写法需要 setuptools>=77'


def test_publish_scripts_upload_sdist_only():
    """发布脚本不能上传 wheel, 否则安装时的原生库编译会被跳过。"""
    for name in ('build.sh', 'build.bat'):
        text = _read(name)
        assert 'python -m build --sdist' in text, \
            f'{name} 未显式构建 sdist'
        assert re.search(r'upload\s+dist/\*\.tar\.gz', text), \
            f'{name} 应只上传 sdist (dist/*.tar.gz)'
        assert 'twine upload dist/*\n' not in text and \
            'twine upload dist/* ' not in text, \
            f'{name} 不应无条件上传 dist/* 全部产物'


def test_setup_hook_installs_built_library():
    text = _read('setup.py')
    assert '_install_built_libs' in text, \
        'setup.py 必须把编译出的原生库拷进安装目录 (它不在 package-data 里)'
    assert 'build_native_lib()' in text, \
        'setup.py 必须在构建时编译原生库'
