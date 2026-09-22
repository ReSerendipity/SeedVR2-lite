"""发布状态门禁（`scripts/check_release_state.py`）的回归测试。

判定核心是纯函数 `evaluate(pyproject_version, changelog_sections, latest_tag)`，全部用构造输入
覆盖，不读 git 状态——否则在没有 v1.5.x tag 的浅克隆里这些用例只能 skip，等于门禁逻辑无回归保护。
另留一条真仓库用例：本仓当前的 pyproject / CHANGELOG / tag 必须自洽。
"""

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None, f"无法加载脚本 {name}.py"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # noqa: S301 - 仓库内受控脚本，非不可信输入
    return module


_gate = _load("check_release_state")

V157 = (1, 5, 7)
MARK = _gate.UNRELEASED_MARK


def _head(version: str, note: str = "") -> tuple[tuple[int, int, int], str]:
    major, minor, patch = (int(part) for part in version.split("."))
    return (major, minor, patch), f"## [{version}] - {note}".rstrip()


class TestParse:
    def test_parse_version_accepts_plain_and_rejects_rest(self):
        assert _gate.parse_version("1.6.0") == (1, 6, 0)
        assert _gate.parse_version(" 1.6.0 ") == (1, 6, 0)
        assert _gate.parse_version("1.6") is None
        assert _gate.parse_version("1.6.0-beta.1") is None
        assert _gate.parse_version("") is None

    def test_changelog_sections_skips_unreleased_and_keeps_note(self):
        text = "## [Unreleased]\n\n### Fixed\n\n## [1.5.8] - 未发版（无 tag）\n\n## [1.5.7] - 2026-09-10\n"
        parsed = _gate.changelog_sections(text)
        assert [ver for ver, _ in parsed] == [(1, 5, 8), (1, 5, 7)]
        assert "未发版" in parsed[0][1] and "未发版" not in parsed[1][1]

    def test_read_pyproject_version_picks_project_table(self, tmp_path):
        toml = tmp_path / "pyproject.toml"
        toml.write_text(
            '[project]\nname = "x"\nversion = "1.6.0"\n\n[tool.black]\nversion = "9.9.9"\n',
            encoding="utf-8",
        )
        assert _gate.read_pyproject_version(toml) == "1.6.0"


class TestEvaluate:
    def test_in_sync_state_passes(self):
        assert _gate.evaluate("1.5.7", [_head("1.5.7", "2026-09-10")], V157) == []

    def test_ahead_without_marker_is_reported(self):
        """真实踩过的坑：pyproject 与 CHANGELOG 都有 1.5.8，而最新 tag 是 v1.5.7。"""
        problems = _gate.evaluate("1.5.8", [_head("1.5.8", "2026-09-13"), _head("1.5.7", "2026-09-10")], V157)
        assert len(problems) == 1 and "未发版" in problems[0] and "1.5.8" in problems[0]

    def test_ahead_with_marker_passes(self):
        problems = _gate.evaluate(
            "1.5.8", [_head("1.5.8", "未发版（条目随 1.6.0 发布）"), _head("1.5.7", "2026-09-10")], V157
        )
        assert problems == []

    def test_ahead_without_changelog_section_is_reported(self):
        problems = _gate.evaluate("1.6.0", [_head("1.5.7", "2026-09-10")], V157)
        assert any("无对应小节" in p for p in problems)

    def test_stale_pyproject_is_reported(self):
        problems = _gate.evaluate("1.5.6", [_head("1.5.7", "2026-09-10")], (1, 5, 7))
        assert any("落后" in p for p in problems)

    def test_released_version_still_marked_unreleased_is_reported(self):
        problems = _gate.evaluate("1.5.7", [_head("1.5.7", "未发版")], V157)
        assert any("仍标着" in p for p in problems)

    def test_changelog_ahead_of_pyproject_also_needs_marker(self):
        """只在 CHANGELOG 里先写一个未来版本，同样是「有账没货」。"""
        problems = _gate.evaluate("1.5.7", [_head("1.5.7", "2026-09-10"), _head("1.6.0", "2026-10-01")], V157)
        assert any("高于最新 tag" in p for p in problems)

    def test_missing_tags_never_silently_passes(self):
        problems = _gate.evaluate("1.5.7", [_head("1.5.7", "2026-09-10")], None)
        assert any("拒绝猜测" in p for p in problems)

    def test_malformed_pyproject_version_is_reported(self):
        assert any("不是 X.Y.Z" in p for p in _gate.evaluate("v1.6", [], V157))


class TestRealRepoIsSelfConsistent:
    def test_current_tree_passes_the_gate(self):
        """本仓的 pyproject / CHANGELOG / tag 三者必须自洽（这条不 skip：拿不到 tag 时也必须明说）。"""
        version = _gate.read_pyproject_version(_REPO_ROOT / "pyproject.toml")
        sections = _gate.changelog_sections((_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
        tags = _gate.git_tags()
        problems = _gate.evaluate(version, sections, tags[-1] if tags else None)
        if not tags:
            assert any("fetch-depth" in p for p in problems), "浅克隆下必须显式报「拿不到 tag」而不是静默通过"
        else:
            assert problems == [], f"仓库发布状态不一致：{problems}"
