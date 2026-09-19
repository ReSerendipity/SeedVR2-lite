#!/usr/bin/env python3
"""README 发布版号一致性检查：防止对外口径领先于实际发布。

README 里有两处对外的版本声明（顶部 shields 徽章 `version-X.Y.Z`、正文「当前稳定版 vX.Y.Z」）。
它们都是手写的，一旦给 pyproject 升版本却忘记发 Release/tag，访客按 README 去 `/releases/latest`
就会拿不到东西——v1.5.8 就这么悬空过一次（徽章与正文都写 1.5.8，而仓库最新 tag 停在 v1.5.7）。

口径：README 声明的版本必须等于**最新的稳定 tag**（`vX.Y.Z`，排除带后缀的预发布）。
只比对本地 git 引用，不联网，因此 checkout 需要 `fetch-depth: 0`；拿不到 tag 时直接失败，
不静默放行。
"""

from __future__ import annotations

import re
import subprocess  # nosec B404（仅以参数列表调用 git，无 shell=True，风险可控）
import sys
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"

# 徽章是静态 URL 里的文本，正文允许写成 **v1.5.7** 或 v1.5.7
CLAIM_PATTERNS = (
    re.compile(r"img\.shields\.io/badge/version-(\d+\.\d+\.\d+)-"),
    re.compile(r"当前稳定版\s*\**v?(\d+\.\d+\.\d+)"),
)
STABLE_TAG = re.compile(r"^v(\d+\.\d+\.\d+)$")


def git_tags() -> list[tuple[int, ...]]:
    """返回可按版本排序的稳定 tag（`vX.Y.Z`）。shallow clone 时为空。"""
    out = subprocess.run(  # nosec B603（固定参数列表，无用户输入）
        ["git", "tag", "--list", "v*"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        cwd=README.parent,
    ).stdout
    versions = []
    for line in out.splitlines():
        m = STABLE_TAG.match(line.strip())
        if m:
            versions.append(tuple(int(p) for p in m.group(1).split(".")))
    return sorted(versions)


def claimed_versions() -> list[tuple[str, int]]:
    """README 中声明的版本号及其行号。"""
    if not README.exists():
        print(f"[readme-version] 找不到 {README}", file=sys.stderr)
        sys.exit(1)
    found = []
    for lineno, line in enumerate(README.read_text(encoding="utf-8").splitlines(), 1):
        for pat in CLAIM_PATTERNS:
            for m in pat.finditer(line):
                found.append((m.group(1), lineno))
    return found


def main() -> int:
    claims = claimed_versions()
    if not claims:
        print("[readme-version] README 未找到任何版本声明，跳过（口径若已改，请同步本脚本）")
        return 0

    tags = git_tags()
    if not tags:
        print(
            "[readme-version] 仓库中没有可读的 tag（浅克隆？）。"
            "checkout 需 fetch-depth: 0 才能核对发布版号——拒绝静默放行。",
            file=sys.stderr,
        )
        return 1

    newest = ".".join(map(str, tags[-1]))
    bad = [(v, ln) for v, ln in claims if v != newest]
    for v, ln in claims:
        print(f"[readme-version] L{ln}: 声明 {v} → {'OK' if v == newest else 'MISMATCH'}")
    if bad:
        print(
            "\n::error::README 声明的发布版与最新 tag 不一致："
            + "、".join(f"L{ln}={v}" for v, ln in bad)
            + f"；最新稳定 tag 为 v{newest}。要么发 Release，要么把 README 改回 v{newest}。",
            file=sys.stderr,
        )
        return 1
    print(f"[readme-version] 通过：README 与最新 tag v{newest} 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
