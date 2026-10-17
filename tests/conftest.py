"""pytest 共享夹具与本地临时目录 (workspace 内, 兼容沙箱环境)。"""

import os
import shutil
import sys
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _choose_tmp_root() -> str:
    """选一个真正可写的 workspace 临时根。

    某些 Windows 沙箱/ACL 故障会让旧的 ``.pytest_tmp`` 目录变成"存在但
    不可写、也删不掉"; 此时回退到 ``pytest-tmp-<pid>-<uuid>`` (已被
    .gitignore 覆盖), 避免整个测试会话因夹具初始化失败。
    """
    explicit = os.environ.get('CODECIN_TEST_TMP')
    candidates = [explicit] if explicit else []
    candidates += [
        os.path.join(ROOT, '.pytest_tmp'),
        os.path.join(ROOT, f'.pytest_tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}'),
    ]
    for root in candidates:
        try:
            os.makedirs(root, exist_ok=True)
            probe = os.path.join(root, '.write-probe')
            with open(probe, 'w', encoding='utf-8') as f:
                f.write('ok')
            os.remove(probe)
            return root
        except OSError:
            continue
    return os.path.join(ROOT, f'.pytest_tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}')


# 本地临时工作目录 (随会话清理); CI/无沙箱环境下同样安全
TMP_ROOT = _choose_tmp_root()
# AOT 增量缓存也隔离到本次测试临时目录, 避免污染用户缓存或跨测试串味。
os.environ.setdefault('CODECIN_AOT_CACHE', os.path.join(TMP_ROOT, 'aot-cache'))


@pytest.fixture(scope='session', autouse=True)
def _clean_tmp():
    os.makedirs(TMP_ROOT, exist_ok=True)
    yield
    shutil.rmtree(TMP_ROOT, ignore_errors=True)


@pytest.fixture()
def workdir():
    """返回一个唯一的本地工作目录 (test 级, 随会话清理)。"""
    d = os.path.join(TMP_ROOT, uuid.uuid4().hex)
    os.makedirs(d, exist_ok=True)
    return d
