#!/usr/bin/env python3
"""tests/test_mypy_gate.py — pre-commit mypy 钩子的解释器探测测试。

两类断言，缺一不可：

1. **反漂移**：探测顺序的权威定义在 ``.githooks/pre-commit``（四级回退）。钩子入口一旦
   与它各写各的，就会出现"钩子能跑、git 钩子跑不动"或反之的分裂状态，而这种分裂只在
   部分主机显形、平时无人察觉。所以直接把 .githooks 里的字面量抓出来对拍。
2. **三种结局各归其位**：没有 Python 才放行；装了 Python 没装 mypy 必须报错（这是真缺
   依赖，静默放行等于把门禁拆了）；mypy 真跑了就透传退出码（类型错误照样拦）。

全部用 monkeypatch 造环境，不依赖本机是否装了 .venv / mypy。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts import mypy_gate

REPO_ROOT = Path(__file__).resolve().parents[1]


def _githooks_candidates() -> tuple[str, ...]:
    """从 .githooks/pre-commit 抓出它自己的四级回退：venv 循环 + 紧随其后的 PATH 循环。"""
    text = (REPO_ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")
    loops: list[list[str]] = []
    for m in re.finditer(r"^[ \t]*for\s+\w+\s+in\s+(.+?)[ \t]*(?:;\s*do)?[ \t]*$", text, re.M):
        loops.append([tok.strip("\"'") for tok in re.findall(r'"[^"]*"|[^\s]+', m.group(1))])

    venv_at = next((i for i, toks in enumerate(loops) if any(t.startswith(".venv/") for t in toks)), None)
    assert venv_at is not None, "没能从 .githooks/pre-commit 抓到 venv 回退循环"
    path_loop = next((t for t in loops[venv_at + 1 :] if "python" in t and "python3" in t), None)
    assert path_loop is not None, "没能从 .githooks/pre-commit 抓到 PATH 回退循环"

    return tuple(t for t in loops[venv_at] if t.startswith(".venv/")) + tuple(path_loop)


class TestProbeOrderMatchesGithooks:
    def test_candidate_tuple_is_the_githooks_four_level_fallback(self):
        assert _githooks_candidates() == mypy_gate.INTERPRETER_CANDIDATES

    def test_windows_and_posix_venv_layouts_are_both_covered(self):
        cands = mypy_gate.INTERPRETER_CANDIDATES
        assert ".venv/Scripts/python.exe" in cands, "Windows venv 布局缺失"
        assert ".venv/bin/python" in cands, "POSIX venv 布局缺失"
        assert cands[-2:] == ("python", "python3"), "PATH 回退必须排在 venv 之后"


class TestResolveExecutables:
    def test_venv_relative_paths_use_exists_and_path_uses_which(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
        monkeypatch.setattr(mypy_gate.shutil, "which", lambda name: f"/usr/bin/{name}")

        assert mypy_gate.resolve_executables() == [
            ".venv/bin/python",
            "/usr/bin/python",
            "/usr/bin/python3",
        ]

    def test_missing_venv_falls_back_to_path(self, monkeypatch, tmp_path):
        """linked worktree 的真实形态：没有 .venv，只有 PATH 上的解释器。"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(mypy_gate.shutil, "which", lambda name: "/usr/bin/python" if name == "python" else None)

        assert mypy_gate.resolve_executables() == ["/usr/bin/python"]

    def test_nothing_installed_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(mypy_gate.shutil, "which", lambda name: None)

        assert mypy_gate.resolve_executables() == []


class TestThreeOutcomes:
    def test_skips_with_zero_when_no_interpreter_at_all(self, monkeypatch, capsys):
        monkeypatch.setattr(mypy_gate, "resolve_executables", lambda: [])

        assert mypy_gate.run("mypy", ["app/integrated_app"]) == 0
        err = capsys.readouterr().err
        assert "跳过" in err and "typecheck" in err, "放行必须说清为什么放行、权威门禁在哪"

    def test_fails_loudly_when_python_lacks_mypy(self, monkeypatch, capsys):
        """装了 Python 没装 mypy ≠ 可以跳过：静默放行等于拆掉本地门禁。"""
        monkeypatch.setattr(mypy_gate, "resolve_executables", lambda: ["/usr/bin/python"])
        monkeypatch.setattr(mypy_gate, "has_module", lambda exe, module: False)

        assert mypy_gate.run("mypy", ["app/integrated_app"]) == 1
        err = capsys.readouterr().err
        assert "pip install mypy" in err

    @pytest.mark.parametrize("mypy_rc", [0, 1, 2])
    def test_real_mypy_exit_code_is_propagated(self, monkeypatch, mypy_rc):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)

            class _R:
                returncode = mypy_rc

            return _R()

        monkeypatch.setattr(mypy_gate, "resolve_executables", lambda: ["/venv/bin/python"])
        monkeypatch.setattr(mypy_gate, "has_module", lambda exe, module: True)
        monkeypatch.setattr(mypy_gate.subprocess, "run", fake_run)

        assert mypy_gate.run("mypy", ["app/integrated_app"]) == mypy_rc
        assert calls == [["/venv/bin/python", "-m", "mypy", "app/integrated_app"]]

    def test_prefers_the_first_interpreter_that_actually_has_mypy(self, monkeypatch):
        """.venv 在但没装 mypy 时，不该拿它去跑然后 not found —— 应继续向下试。"""
        chosen = []

        def fake_run(cmd, **kwargs):
            chosen.append(cmd[0])

            class _R:
                returncode = 0

            return _R()

        monkeypatch.setattr(mypy_gate, "resolve_executables", lambda: [".venv/bin/python", "/usr/bin/python"])
        monkeypatch.setattr(mypy_gate, "has_module", lambda exe, module: exe == "/usr/bin/python")
        monkeypatch.setattr(mypy_gate.subprocess, "run", fake_run)

        assert mypy_gate.run("mypy", ["app/integrated_app"]) == 0
        assert chosen == ["/usr/bin/python"]


class TestEntryPointContract:
    def test_config_entry_routes_through_the_gate(self):
        """钩子必须改走 gate；回退成写死路径就是把这个 PR 撤销了。"""
        text = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        assert "scripts/mypy_gate.py" in text
        assert ".venv/Scripts/python.exe -m mypy" not in text

    def test_usage_error_on_empty_argv(self, monkeypatch, capsys):
        assert mypy_gate.main([]) == 2
        assert "用法" in capsys.readouterr().err
