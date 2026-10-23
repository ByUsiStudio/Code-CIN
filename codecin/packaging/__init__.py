"""v6.2.0 工具链共享基础设施 (mkn / pic / CVM)。

本子包刻意不依赖 ``codecin.cin`` / ``codecin.cpu``: 清单解析、包存储、
Git 与归档都是纯标准库实现, 便于单独测试, 也避免工具启动时加载原生引擎。

模块划分:

* :mod:`codecin.packaging.toml`    —— 最小 TOML 读写 (兼容 Python 3.8, 无第三方依赖);
* :mod:`codecin.packaging.model`   —— ``pic.toml`` / ``mkn.toml`` 清单与语义化版本;
* :mod:`codecin.packaging.store`   —— 包安装存储 (``$PIC_HOME``) 与项目依赖目录;
* :mod:`codecin.packaging.vcs`     —— Git 克隆 / 标签 / 干净工作区检查;
* :mod:`codecin.packaging.archive` —— 可复现 zip、路径安全解包与校验和。
"""

from .archive import (  # noqa: F401
    ArchiveError,
    read_checksums,
    read_manifest_member,
    safe_extract,
    sha256_bytes,
    sha256_file,
    write_zip,
)
from .model import (  # noqa: F401
    Dependency,
    ManifestError,
    MknManifest,
    PackageMeta,
    find_project_root,
    load_mkn_manifest,
    load_package_manifest,
    satisfies,
)
from .store import InstalledPackage, PackageStore, default_store  # noqa: F401
from .toml import TomlError, dumps as toml_dumps, loads as toml_loads  # noqa: F401

__all__ = [
    'ArchiveError', 'Dependency', 'InstalledPackage', 'ManifestError',
    'MknManifest', 'PackageMeta', 'PackageStore', 'TomlError',
    'default_store', 'find_project_root', 'load_mkn_manifest',
    'load_package_manifest', 'read_checksums', 'read_manifest_member',
    'safe_extract', 'satisfies', 'sha256_bytes', 'sha256_file', 'toml_dumps',
    'toml_loads', 'write_zip',
]
