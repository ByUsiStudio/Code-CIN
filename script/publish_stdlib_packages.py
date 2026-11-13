#!/usr/bin/env python3
"""批量发布 ``codecin/lib`` 下全部内置标准库的 pic 包。

用法::

    python script/publish_stdlib_packages.py --build      # 先重建 .cinpkg 再发布
    python script/publish_stdlib_packages.py              # 只发布已构建的包
    python script/publish_stdlib_packages.py --offline    # 只登记本地 registry.json
    python script/publish_stdlib_packages.py --only array,sort
    python script/publish_stdlib_packages.py --dry-run     # 只打印将执行的发布参数

说明:
* 全部内置库共用同一个 Git 仓库 (pic.toml 的 repo), 若逐包创建 ``v<版本>`` 标签会
  互相冲突, 因此本脚本统一传 ``--no-tag``, 只把版本登记到注册表;
* 内置库的改动通常还在工作区中, 因此默认带 ``--allow-dirty``; 需要严格检查时加
  ``--require-clean``;
* 某个包发布失败不会中断后续包, 结束时打印汇总并以非零退出码报告失败数。
"""

from __future__ import annotations

import argparse
import contextlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = ROOT / "codecin" / "lib"
DIST_DIR = ROOT / "dist" / "stdlib-packages"

sys.path.insert(0, str(ROOT))


def configure_stdio() -> None:
    """强制 UTF-8 输出 (Windows 重定向到日志管道时中文会按 cp1252 编码)。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(encoding="utf-8", errors="replace")


def read_version() -> str:
    text = (ROOT / "codecin" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise SystemExit("无法从 codecin/__init__.py 读取 __version__")
    return match.group(1)


def package_names() -> list[str]:
    """返回 ``codecin/lib`` 下所有带 ``src/<name>.cin`` 的包名。"""
    names: list[str] = []
    for package in sorted(LIB_DIR.iterdir()):
        if not package.is_dir():
            continue
        if (package / "src" / f"{package.name}.cin").is_file():
            names.append(package.name)
    return names


def parse_only(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true",
                        help="发布前先运行 sync_stdlib_packages.py --build")
    parser.add_argument("--offline", action="store_true",
                        help="禁用中央注册表, 仅登记本地 registry.json")
    parser.add_argument("--only", help="只发布指定包 (逗号分隔, 如 array,sort)")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将执行的发布参数, 不真正发布")
    parser.add_argument("--require-clean", action="store_true",
                        help="工作区不干净时拒绝发布 (默认 --allow-dirty)")
    args = parser.parse_args(argv)

    version = read_version()
    wanted = parse_only(args.only)
    names = package_names()
    if wanted:
        unknown = sorted(wanted - set(names))
        if unknown:
            parser.error(f"未知的包名: {', '.join(unknown)}")
        names = [name for name in names if name in wanted]
    if not names:
        parser.error("codecin/lib 下没有可发布的包")

    if args.build:
        command = [sys.executable, str(ROOT / "script" / "sync_stdlib_packages.py"),
                   "--build"]
        print(f"$ {' '.join(command)}", flush=True)
        code = subprocess.call(command, cwd=str(ROOT))
        if code != 0:
            print(f"构建失败 (退出码 {code}), 已中止发布", file=sys.stderr)
            return code

    from codecin.pic import main as pic_main  # 延迟导入, --dry-run 无需 pic 依赖

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    failures: list[tuple[str, int]] = []
    total = len(names)
    for index, name in enumerate(names, 1):
        package = LIB_DIR / name
        archive_path = DIST_DIR / f"{name}-{version}.cinpkg"
        command = ["publish", "--manifest", str(package), "--no-tag",
                   "--output", str(archive_path)]
        if not args.require_clean:
            command.append("--allow-dirty")
        if args.offline:
            command.insert(0, "--offline")
        print(f"\n[{index}/{total}] pic {name}@{version}", flush=True)
        if args.dry_run:
            print(f"  pic {' '.join(command)}", flush=True)
            continue
        code = pic_main(command)
        if code != 0:
            failures.append((name, code))
            print(f"  {name} 发布失败 (退出码 {code})", file=sys.stderr)

    if args.dry_run:
        print(f"\n[dry-run] 待发布 {total} 个包: {', '.join(names)}")
        return 0

    print(f"\n完成: {total - len(failures)}/{total} 个包发布成功")
    if failures:
        print("失败的包:", file=sys.stderr)
        for name, code in failures:
            print(f"  - {name} (退出码 {code})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
