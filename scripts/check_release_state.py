#!/usr/bin/env python3
"""发布状态一致性检查：`pyproject.toml` / `CHANGELOG.md` 必须对得上仓库里真实存在的 tag。

背景（2026-09-22 实测）：`pyproject.toml` 写着 1.5.8、`CHANGELOG.md` 有 `## [1.5.8] - 2026-09-13`
段落，而仓库最新稳定 tag 停在 v1.5.7——**1.5.8 从未发过版**。`check_readme_release_version.py`
抓不到这种状态，因为它的口径是「README 声明 == 最新 tag」，README 当时老老实实写着 1.5.7，
于是门禁全绿而账本上挂着一个有版本号、有条目、没有产物的版本。访客按 CHANGELOG 去找
v1.5.8 的 Release 是拿不到东西的。

口径（三条，全部以「最新稳定 tag」为锚）：
- `pyproject` 版本 == 最新 tag：正常。此时 CHANGELOG 里该版本小节**不得**再标「未发版」。
- `pyproject` 版本 > 最新 tag：允许，但 CHANGELOG 必须有对应小节且标题含「未发版」标注
  ——即"代码已备好、尚未发布"必须写明，不能伪装成已发布。
- `pyproject` 版本 < 最新 tag：失败。说明发版后没回写版本位。
- 附加：CHANGELOG 中任何**高于最新 tag** 的版本小节都必须带「未发版」标注（防止只在
  CHANGELOG 里先写了一个还没进 pyproject 的版本号）。

拿不到任何 tag（浅克隆等）时直接失败，不静默放行；判定逻辑是纯函数，可被
`tests/test_release_state.py` 用构造输入覆盖。
"""

from __future__ import annotations

import contextlib
import importlib
import re
import sys
from collections.abc import Callable
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        with contextlib.suppress(OSError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

# 与 README 版号门禁共用同一份「什么算稳定 tag」的口径，两处各写一遍必然漂移。
# 走 importlib 而非静态 import：同目录脚本靠上面的 sys.path 注入才可解析，静态导入会让
# mypy 报「找不到模块」而只能挂 ignore——这里显式标注函数签名，类型信息不丢。
git_tags: Callable[[], list[tuple[int, int, int]]] = importlib.import_module("check_readme_release_version").git_tags

PREFIX = "[release-state]"
UNRELEASED_MARK = "未发版"

VERSION_HEAD = re.compile(r"^## \[(\d+)\.(\d+)\.(\d+)\]\s*(.*)$")


def parse_version(text: str) -> tuple[int, int, int] | None:
    """把 `X.Y.Z` 解析成可比较的三元组；带预发布后缀或非法值返回 None。"""
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", (text or "").strip())
    if not m:
        return None
    return tuple(int(g) for g in m.groups())  # type: ignore[return-value]


def read_pyproject_version(path: Path) -> str:
    """读 `[project].version`。刻意不解析 TOML：只取版本行，避免为一行配置引依赖。"""
    in_project = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_project = line == "[project]"
            continue
        if in_project and line.startswith("version"):
            _, _, value = line.partition("=")
            return value.strip().strip("\"'")
    raise ValueError(f"{path} 里找不到 [project].version")


def changelog_sections(text: str) -> list[tuple[tuple[int, int, int], str]]:
    """返回 CHANGELOG 里所有 `## [X.Y.Z] ...` 小节，连同标题剩余部分（用于查「未发版」标注）。"""
    found = []
    for line in text.splitlines():
        m = VERSION_HEAD.match(line.strip())
        if m:
            found.append(((int(m.group(1)), int(m.group(2)), int(m.group(3))), line.strip()))
    return found


def evaluate(
    pyproject_version: str,
    changelog: list[tuple[tuple[int, int, int], str]],
    latest_tag: tuple[int, int, int] | None,
) -> list[str]:
    """纯判定：返回问题列表，空列表即通过。"""
    if latest_tag is None:
        return [f"{PREFIX} 仓库里没有可读的稳定 tag（浅克隆？checkout 需 fetch-depth: 0），拒绝猜测发布状态"]

    ver = parse_version(pyproject_version)
    if ver is None:
        return [f"{PREFIX} pyproject 版本 {pyproject_version!r} 不是 X.Y.Z 形式，无法与 tag 对齐"]

    problems: list[str] = []
    if ver < latest_tag:
        problems.append(
            f"pyproject 版本 {'.'.join(map(str, ver))} 落后于已发布 tag "
            f"v{'.'.join(map(str, latest_tag))}：发版后须回写版本位"
        )

    for section_ver, header in changelog:
        if section_ver <= latest_tag:
            continue
        if UNRELEASED_MARK not in header:
            problems.append(
                f"CHANGELOG 小节 {header!r} 高于最新 tag "
                f"v{'.'.join(map(str, latest_tag))} 却未标注「{UNRELEASED_MARK}」——"
                "读者会以为该版本已有 Release 产物"
            )

    if ver > latest_tag:
        own = [header for section_ver, header in changelog if section_ver == ver]
        if not own:
            problems.append(
                f"pyproject 版本 {'.'.join(map(str, ver))} 高于最新 tag 但 CHANGELOG 无对应小节："
                "要么补小节并标「未发版」，要么把版本退回已发布版本"
            )
    elif ver == latest_tag:
        for header in [h for section_ver, h in changelog if section_ver == ver]:
            if UNRELEASED_MARK in header:
                problems.append(f"{header!r} 已等于最新 tag 却仍标着「{UNRELEASED_MARK}」，发版后应去掉该标注")

    return problems


def main() -> int:
    tags = git_tags()
    latest_tag = tags[-1] if tags else None
    pyproject_version = read_pyproject_version(ROOT / "pyproject.toml")
    sections = changelog_sections((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))

    newest = ".".join(map(str, latest_tag)) if latest_tag else "无"
    print(f"{PREFIX} pyproject={pyproject_version} 最新稳定 tag=v{newest} CHANGELOG 版本小节={len(sections)}")

    problems = evaluate(pyproject_version, sections, latest_tag)
    for problem in problems:
        print(f"::error::{PREFIX} {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"{PREFIX} 通过：版本位、CHANGELOG 与已发布 tag 三者口径一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
