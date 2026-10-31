#!/usr/bin/env python3
"""维护 ``codecin/lib/`` 下 41 个内置标准库的 pic 包元数据。

每个内置库就是一个 pic 包::

    codecin/lib/<name>/
    ├── pic.toml
    └── src/<name>.cin      # 源文件（唯一真源）

``codecin/lib/<name>/src/<name>.cin`` 同时是运行时 ``import "math.cin"`` 解析到的
文件。修改内置库时直接改包内 ``src``；本脚本负责按 ``codecin/__init__.py`` 的版本
与源文件头注释重新生成 ``pic.toml``，并校验包结构是否完整。

用法::

    python script/sync_stdlib_packages.py            # 生成/更新 pic.toml
    python script/sync_stdlib_packages.py --check    # 只校验，漂移时退出码 1
    python script/sync_stdlib_packages.py --build    # 构建全部 .cinpkg
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = ROOT / "codecin" / "lib"
DIST_DIR = ROOT / "dist" / "stdlib-packages"
REPO_URL = "https://gitee.com/codecin/internal-libraries"

sys.path.insert(0, str(ROOT))
from codecin.packaging.toml import dumps as toml_dumps  # noqa: E402

HEADER = "# 由 script/sync_stdlib_packages.py 生成，请勿手工编辑。\n"


def read_version() -> str:
    text = (ROOT / "codecin" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise SystemExit("无法从 codecin/__init__.py 读取 __version__")
    return match.group(1)


def package_sources() -> dict[str, Path]:
    """返回 ``{包名: 包内源文件}``；没有 ``src/<name>.cin`` 的目录忽略。"""
    sources: dict[str, Path] = {}
    if not LIB_DIR.is_dir():
        return sources
    for package in sorted(LIB_DIR.iterdir()):
        if not package.is_dir():
            continue
        source = package / "src" / f"{package.name}.cin"
        if source.is_file():
            sources[package.name] = source
    return sources


def describe(source: Path, name: str) -> str:
    """从库文件头注释提取一句描述。"""
    for raw in source.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if not line.startswith("//"):
            break
        text = line[2:].strip()
        text = re.sub(
            rf"\s*\((?:codecin/lib/)?{re.escape(name)}\.cin\)$", "", text
        )
        text = re.sub(rf"\s*\({re.escape(name)}\)$", "", text)
        if text:
            return text
    return f"Code CIN 官方标准库: {name}"


def render_manifest(name: str, description: str, version: str) -> bytes:
    package = {
        "name": name,
        "version": version,
        "description": description,
        "license": "MIT",
        "authors": ["ByUsi Studio"],
        "entry": f"src/{name}.cin",
        "repo": REPO_URL,
        "keywords": ["codecin", "stdlib", name],
    }
    return (HEADER + toml_dumps({"package": package})).encode("utf-8")


def expected_files() -> dict[Path, bytes]:
    version = read_version()
    return {
        LIB_DIR / name / "pic.toml": render_manifest(
            name, describe(source, name), version
        )
        for name, source in package_sources().items()
    }


def stray_libraries() -> list[Path]:
    """旧的平铺布局 ``codecin/lib/<name>.cin``（迁入包目录后应为空）。"""
    return sorted(LIB_DIR.glob("*.cin"))


def sync() -> int:
    files = expected_files()
    for path, data in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    print(f"已更新 {len(files)} 个内置库的 pic.toml")
    stray = stray_libraries()
    if stray:
        print(
            "警告: codecin/lib 下仍有平铺的 .cin 文件（应改为包目录）:",
            file=sys.stderr,
        )
        for path in stray:
            print(f"  - {path.relative_to(ROOT)}", file=sys.stderr)
    return 0


def check() -> int:
    files = expected_files()
    problems: list[str] = []
    for path, data in files.items():
        relative = path.relative_to(ROOT)
        if not path.is_file():
            problems.append(f"缺少 {relative}")
        elif path.read_bytes() != data:
            problems.append(f"内容漂移 {relative}")

    sources = package_sources()
    if LIB_DIR.is_dir():
        for package in sorted(LIB_DIR.iterdir()):
            if package.is_dir() and package.name not in sources:
                problems.append(
                    f"codecin/lib/{package.name} 缺少 "
                    f"src/{package.name}.cin"
                )

    for path in stray_libraries():
        problems.append(f"平铺文件 {path.relative_to(ROOT)} 应迁入包目录")

    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(
            "请修改 codecin/lib/<name>/src/ 后运行 "
            "python script/sync_stdlib_packages.py 重新生成。",
            file=sys.stderr,
        )
        return 1
    print(f"{len(files)} 个内置库 pic 包结构一致")
    return 0


def build() -> int:
    from codecin.pic import build_package

    sync()
    version = read_version()
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    for name in package_sources():
        out = DIST_DIR / f"{name}-{version}.cinpkg"
        build_package(str(LIB_DIR / name), str(out))
        print(f"构建 {out.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="只校验，不写文件"
    )
    parser.add_argument(
        "--build", action="store_true", help="生成后构建全部 .cinpkg"
    )
    args = parser.parse_args(argv)
    if args.check and args.build:
        parser.error("--check 与 --build 不能同时使用")
    if args.check:
        return check()
    if args.build:
        return build()
    return sync()


if __name__ == "__main__":
    sys.exit(main())
