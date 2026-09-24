"""原生库查找顺序: 架构专属文件名必须优先于通用名。

Release 资产按 `libcodecin_native-<os>-<arch>.<ext>` 命名 (六个平台同名会互相
覆盖), 因此同一个目录里可能同时存在两种架构的库。查找必须先命中本机架构的那
一个, 否则会 dlopen 到"能加载但架构不对"的库。
"""

import os
import platform

from codecin import native


def test_os_and_arch_slugs():
    assert native._os_slug() in ('windows', 'macos', 'linux')
    assert native._arch_slug() in ('x64', 'arm64', 'x86') or native._arch_slug()
    # 本机架构必须落在 release.yml 使用的命名集合里
    sysname = platform.system()
    expected_os = {'Windows': 'windows', 'Darwin': 'macos'}.get(sysname, 'linux')
    assert native._os_slug() == expected_os


def test_arch_specific_candidate_comes_first():
    machine = platform.machine().lower()
    if machine not in ('x86_64', 'amd64', 'aarch64', 'arm64'):
        return  # 其它架构不强制约定命名
    names = [os.path.basename(p) for p in native._lib_candidates()]
    arch = native._arch_slug()
    os_slug = native._os_slug()
    specific = [n for n in names if f'-{os_slug}-{arch}.' in n]
    assert specific, f'没有生成架构专属候选名: {names}'
    # 第一个出现的候选必须就是架构专属名 (env 覆盖除外)
    if not os.environ.get('CODECIN_NATIVE_LIB'):
        assert names[0] == specific[0], (
            f'架构专属库名未排在最前: {names[:3]}')


def test_canonical_names_still_candidates():
    """通用名必须保留 (源码树构建 / 旧安装布局仍用通用名)。"""
    names = [os.path.basename(p) for p in native._lib_candidates()]
    assert any(n in ('codecin_native.dll', 'libcodecin_native.so',
                     'libcodecin_native.dylib') for n in names), names


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv('CODECIN_NATIVE_LIB', os.path.join('X:', 'custom.dll'))
    assert native._lib_candidates()[0] == os.path.join('X:', 'custom.dll')
