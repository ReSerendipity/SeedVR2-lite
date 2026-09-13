#!/usr/bin/env python3
"""Structure-guard（``.github/scripts/check_layout.py``）根布局守卫测试。

背景：守卫用 ``git check-ignore --stdin`` 询问根条目是否已被 .gitignore 忽略，
据此决定要不要报「Unrecognized root entry」WARN。2026-09-13 发现它漏了给**目录**
补尾斜杠 —— git 只有当路径被判定为目录时才会应用该目录**自身**的 .gitignore
（典型如 ``.uv-cache/.gitignore`` 的 ``*``），于是自忽略的缓存目录每次提交都刷一条
假 WARN。本测试锁定该修复，并保留一条「成因」断言防止有人把斜杠"简化"掉。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_guard():
    spec = importlib.util.spec_from_file_location(
        "check_layout", _REPO_ROOT / ".github" / "scripts" / "check_layout.py"
    )
    assert spec and spec.loader, "无法加载 .github/scripts/check_layout.py"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # noqa: S301 - 受控本地脚本，非不可信输入
    return module


_guard = _load_guard()


class TestBuildIgnoreQuery:
    """目录补尾斜杠、文件不加，且顺序与长度不变。"""

    def test_directory_gets_trailing_slash(self, tmp_path):
        (tmp_path / "adir").mkdir()
        (tmp_path / "afile.txt").write_text("x", encoding="utf-8")
        assert _guard.build_ignore_query(str(tmp_path), ["adir", "afile.txt"]) == ["adir/", "afile.txt"]

    def test_order_and_length_preserved(self, tmp_path):
        for name in ("a", "b", "c"):
            (tmp_path / name).mkdir()
        assert _guard.build_ignore_query(str(tmp_path), ["a", "b", "c"]) == ["a/", "b/", "c/"]

    def test_empty_entries(self, tmp_path):
        assert _guard.build_ignore_query(str(tmp_path), []) == []


class TestSelfIgnoringDirRegression:
    """回归：目录**自身** .gitignore 的忽略规则必须被识别（2026-09-13 修）。"""

    @staticmethod
    def _init_repo_with_self_ignoring_dir(tmp_path: Path) -> Path:
        """造一个「根下有个自忽略缓存目录」的最小仓库（离线，不联网）。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)  # noqa: S603
        cache = repo / ".uv-cache"
        cache.mkdir()
        # uv 的缓存目录自带 .gitignore（内容为 ``*``）与 CACHEDIR.TAG
        (cache / ".gitignore").write_text("*\n", encoding="utf-8")
        (cache / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n", encoding="utf-8")
        (repo / "tracked.txt").write_text("x", encoding="utf-8")
        return repo

    def test_trailing_slash_form_is_detected_as_ignored(self, tmp_path):
        repo = self._init_repo_with_self_ignoring_dir(tmp_path)
        entries = sorted(p.name for p in repo.iterdir() if p.name != ".git")
        query = _guard.build_ignore_query(str(repo), entries)
        ignored = {x.rstrip("/") for x in _guard.ignored_set(str(repo), query)}
        assert ".uv-cache" in ignored, f"自忽略目录未被识别为已忽略（query={query}）"
        assert "tracked.txt" not in ignored, "普通文件不应被误判为已忽略"

    def test_bare_name_form_reproduces_the_original_bug(self, tmp_path):
        """不带尾斜杠时 git 确实识别不到 —— 锁住 bug 成因。

        若哪天本断言失败，说明 git 的 check-ignore 语义变了（或不带斜杠也能命中
        目录自身的 .gitignore），此时应复核 ``build_ignore_query`` 是否还需补斜杠，
        而不是直接删掉本用例。
        """
        repo = self._init_repo_with_self_ignoring_dir(tmp_path)
        assert ".uv-cache" not in _guard.ignored_set(str(repo), [".uv-cache"])
