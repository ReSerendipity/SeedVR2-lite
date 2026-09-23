"""`scripts/check_upstream_sync.py` 的行为测试（CPU-only，不联网、不碰 model_lib）。

重点锁三件事，因为它们各对应一次真实踩过的坑：
1. **分类正确**：same / upstream_only / local_only / body_differs 四态不得互相窜。
2. **别名只认 confirmed**：candidate 别名参与配对会把真实漂移掩成「已同步」——比误报更危险的失效。
3. **探针坏掉时必须不出报告**：自检门禁不过 ⇒ 退出码 2；网络不可用 ⇒ 退出码 3。两者都不能返回 0，
   否则「链路坏了」会以「无差异」的形式被当成结论消费。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_upstream_sync.py"


def _load_probe():
    """按文件路径加载探针脚本（scripts/ 不在包内，沿用仓内 importlib 装载惯例）。"""
    spec = importlib.util.spec_from_file_location("check_upstream_sync", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


def _write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 归一化：只改写法不改语义的，必须判 same
# --------------------------------------------------------------------------


def test_docstring_and_annotation_style_differences_are_same(tmp_path):
    up = _write(
        tmp_path / "up" / "m.py",
        "from typing import Optional\n\n\ndef f(x: Optional[int]) -> int:\n    return x + 1\n",
    )
    lo = _write(
        tmp_path / "lo" / "m.py",
        '"""模块说明。\n\n多行中文 docstring。\n"""\n\n\ndef f(x: int | None) -> int:\n    """函数说明。"""\n    return x + 1\n',
    )
    diff = probe.compare_file("m.py", up, lo)
    assert diff.same == ["f"]
    assert not diff.has_findings


def test_import_ordering_is_ignored(tmp_path):
    up = _write(tmp_path / "up.py", "import os\nimport sys\n\nA = os.path\nB = sys\n")
    lo = _write(tmp_path / "lo.py", "import sys\nimport os\n\nA = os.path\nB = sys\n")
    diff = probe.compare_file("x.py", up, lo)
    assert diff.same == ["var:A", "var:B"]
    assert not diff.has_findings


# --------------------------------------------------------------------------
# 四态分类
# --------------------------------------------------------------------------


def test_four_state_classification(tmp_path):
    up = _write(
        tmp_path / "up.py",
        "def kept(a):\n    return a\n\n\ndef changed(a):\n    return a\n\n\ndef only_up(b):\n    return b\n\n\n"
        "class Same:\n    x = 1\n\n\nvar_up = 1\n",
    )
    lo = _write(
        tmp_path / "lo.py",
        "def kept(a):\n    return a\n\n\ndef changed(a):\n    return a + 1\n\n\ndef only_local(c):\n    return c\n\n\n"
        "class Same:\n    x = 1\n\n\nvar_up = 1\n",
    )
    diff = probe.compare_file("m.py", up, lo)
    assert "kept" in diff.same and "Same" in diff.same and "var:var_up" in diff.same
    assert diff.upstream_only == ["only_up"]
    assert diff.local_only == ["only_local"]
    assert diff.body_differs == ["changed"]
    assert diff.state_of("only_up") == "upstream_only"
    assert diff.state_of("changed") == "body_differs"
    assert diff.state_of("nope") is None


def test_module_level_variable_changes_are_detected(tmp_path):
    """别名/类型别名这类模块级变量漂移也得抓到（早期只看 def/class 会漏）。"""
    up = _write(tmp_path / "up.py", "_t = int\n")
    lo = _write(tmp_path / "lo.py", "_t = torch.device\n")
    diff = probe.compare_file("types.py", up, lo)
    assert diff.body_differs == ["var:_t"]


def test_local_file_absent_upstream_reports_whole_file(tmp_path):
    lo = _write(tmp_path / "lo.py", "def extra():\n    return 1\n")
    diff = probe.compare_pair("dit_v2/brand_new.py", tmp_path / "missing.py", lo)
    assert diff.local_only == ["<整个文件为本地新增>"]


# --------------------------------------------------------------------------
# 别名表：confirmed 配对，candidate 只展示
# --------------------------------------------------------------------------


def test_confirmed_alias_matches_across_names(tmp_path):
    up = _write(tmp_path / "up.py", "def get_nablock(t):\n    return TABLE[t]\n")
    lo = _write(tmp_path / "lo.py", "def get_na_block(t):\n    return TABLE[t]\n")
    old = probe.ALIAS_MAP
    probe.ALIAS_MAP = {"m.py": {"get_nablock": ("get_na_block", "confirmed")}}
    try:
        diff = probe.compare_file("m.py", up, lo)
    finally:
        probe.ALIAS_MAP = old
    assert diff.same == ["get_nablock"]
    assert not diff.local_only
    assert diff.aliased == [("get_nablock", "get_na_block", "confirmed")]


def test_candidate_alias_does_not_suppress_divergence(tmp_path):
    """最坏失效方向的回归锁：candidate 别名**不得**把「上游有、本地缺」洗成 same。"""
    up = _write(tmp_path / "up.py", "class RotaryEmbedding3d:\n    y = 2\n")
    lo = _write(tmp_path / "lo.py", "class rotary_emb:\n    z = 9\n")
    old = probe.ALIAS_MAP
    probe.ALIAS_MAP = {"m.py": {"RotaryEmbedding3d": ("rotary_emb", "candidate")}}
    try:
        diff = probe.compare_file("m.py", up, lo)
    finally:
        probe.ALIAS_MAP = old
    assert diff.same == []
    assert diff.body_differs == []
    assert diff.upstream_only == ["RotaryEmbedding3d"]
    assert diff.local_only == ["rotary_emb"]
    assert diff.aliased == [("RotaryEmbedding3d", "rotary_emb", "candidate")]


def test_symbol_hash_does_not_mutate_source_node(tmp_path):
    """回归锁：曾在 _symbol_hash 里就地改 node.name，导致同文件所有函数塌成同一个键。"""
    src = _write(tmp_path / "m.py", "def alpha(a):\n    return a\n\n\ndef beta(b):\n    return b\n")
    syms = probe.collect_symbols(src)
    assert set(syms) == {"alpha", "beta"}
    assert syms["alpha"] != syms["beta"]


def test_alias_map_entries_use_known_status_values():
    for rel, pairs in probe.ALIAS_MAP.items():
        for up, (loc, status) in pairs.items():
            assert status in {"confirmed", "candidate"}, f"{rel}: {up}->{loc} 的 status 非法：{status}"


# --------------------------------------------------------------------------
# 自检门禁与退出码契约
# --------------------------------------------------------------------------


def _lookup_with(rel: str, up_src: str, lo_src: str, tmp_path):
    up = _write(tmp_path / "up.py", up_src)
    lo = _write(tmp_path / "lo.py", lo_src)
    return {rel: probe.compare_file(rel, up, lo)}


def test_self_checks_treat_uncompared_file_as_failure(tmp_path):
    """自检必须能识别「期望的符号根本没进比对」——静默放过就等于门禁不存在。"""
    lookup = _lookup_with(
        "dit_v2/window.py", "def window_idx(a):\n    return a\n", "def other(a):\n    return a\n", tmp_path
    )
    failures = probe.run_self_checks(lookup)
    assert failures
    assert any("未纳入比对" in f for f in failures)


def test_self_checks_pass_when_expectations_are_met(tmp_path, monkeypatch):
    """正反两向都要成立：全命中时返回空失败清单，否则真跑会被自己的门禁永久挡死。"""
    up = _write(tmp_path / "up.py", "def kept(a):\n    return a\n\n\ndef gone(a):\n    return a\n")
    lo = _write(tmp_path / "lo.py", "def kept(a):\n    return a\n\n\ndef added(a):\n    return a\n")
    lookup = {"m.py": probe.compare_file("m.py", up, lo)}
    monkeypatch.setattr(probe, "SELF_CHECKS", (("m.py", "kept", "same"), ("m.py", "gone", "upstream_only")))
    assert probe.run_self_checks(lookup) == []
    monkeypatch.setattr(probe, "SELF_CHECKS", (("m.py", "kept", "body_differs"),))
    assert probe.run_self_checks(lookup), "期望与实测不符时必须报失败，不能静默"


def test_main_returns_network_exit_and_writes_no_report(tmp_path, monkeypatch, capsys):
    """网络不可用 ⇒ 退出码 3，且不得产出「无差异」式报告。"""
    monkeypatch.setattr(probe, "ensure_upstream", lambda *a, **k: (False, "模拟：git 不可达"))
    rc = probe.main(["--report-dir", str(tmp_path / "reports")])
    assert rc == probe.EXIT_NETWORK
    captured = capsys.readouterr().err
    assert "无法判定" in captured and "无差异" in captured
    assert not (tmp_path / "reports").exists()


def test_main_returns_selfcheck_exit_and_writes_no_report(tmp_path, monkeypatch, capsys):
    """比对链路坏（这里造一个「上游树为空」的场景）⇒ 退出码 2 且拒绝出报告。"""
    cache = tmp_path / "cache"
    (cache / "models" / "dit").mkdir(parents=True)
    (cache / "models" / "dit_v2").mkdir(parents=True)
    (cache / "models" / "video_vae_v3").mkdir(parents=True)
    monkeypatch.setattr(probe, "ensure_upstream", lambda *a, **k: (True, "模拟：缓存可用"))
    monkeypatch.setattr(probe, "ROOT", tmp_path)  # 让本地树扫描落到空目录上
    rc = probe.main(["--cache-dir", str(cache), "--report-dir", str(tmp_path / "reports")])
    assert rc == probe.EXIT_SELFCHECK_FAILED
    assert "自检门禁失败" in capsys.readouterr().err
    assert not (tmp_path / "reports").exists()


def test_exit_codes_are_distinct():
    """四个退出码必须互不相同，否则 CI 无法区分「干净 / 有发现 / 结论不可信 / 没网络」。"""
    codes = [probe.EXIT_CLEAN, probe.EXIT_FINDINGS, probe.EXIT_SELFCHECK_FAILED, probe.EXIT_NETWORK]
    assert len(set(codes)) == 4
    assert probe.EXIT_CLEAN == 0


# --------------------------------------------------------------------------
# 报告渲染
# --------------------------------------------------------------------------


def test_report_labels_known_local_changes_and_flags_unregistered(tmp_path):
    rel = sorted(probe.KNOWN_LOCAL_CHANGES)[0]
    up = _write(tmp_path / "up.py", "def only_up(a):\n    return a\n")
    lo = _write(tmp_path / "lo.py", "def only_local(a):\n    return a\n")
    lookup = {rel: probe.compare_file(rel, up, lo)}
    other = "dit/zzz_unregistered.py"
    lookup[other] = probe.compare_file(other, up, lo)
    text = probe.render_report(url="u", rev="main", note="测试", stale=True, lookup=lookup)
    assert "可能过期" in text  # --offline 必须留下痕迹
    assert "是：" in text  # 已登记项
    assert f"- `{other}`" in text  # 未登记项被点名


def test_local_only_files_are_excluded_from_scope():
    """为「让目录成为包」而加的 __init__.py 不该每轮都报成本地新增。"""
    for name in ("dit/__init__.py", "dit_v2/__init__.py", "video_vae_v3/modules/__init__.py"):
        assert name in probe.LOCAL_ONLY_FILES


@pytest.mark.parametrize("tree", sorted(probe.TREES))
def test_every_declared_tree_exists_locally(tree):
    assert (probe.ROOT / "model_lib" / tree).is_dir()


# --------------------------------------------------------------------------
# 缓存损坏 ≠ 网络不可用（两者处置动作相反：删缓存重来 vs 换出口重试）
# --------------------------------------------------------------------------


def test_corrupted_cache_is_not_reported_as_network_failure(tmp_path, capsys):
    """回归锁：.git 存在但缺 HEAD/config 的残骸，曾被报成"网络不可用，请换出口重试"，
    等于把人指向一个永远重试不好的方向。"""
    cache = tmp_path / "upstream_cache"
    (cache / ".git" / "refs").mkdir(parents=True)
    rc = probe.main(["--cache-dir", str(cache), "--report-dir", str(tmp_path / "reports")])
    err = capsys.readouterr().err
    assert rc == probe.EXIT_CACHE_INVALID
    assert "缓存损坏" in err
    assert "网络不可用" not in err
    assert not (tmp_path / "reports").exists()


def test_non_empty_non_repo_cache_also_reported_as_cache_problem(tmp_path, capsys):
    cache = tmp_path / "junk_cache"
    (cache / "models").mkdir(parents=True)
    (cache / "stray").write_text("x", encoding="utf-8")
    rc = probe.main(["--cache-dir", str(cache), "--report-dir", str(tmp_path / "reports")])
    assert rc == probe.EXIT_CACHE_INVALID
    assert "缓存" in capsys.readouterr().err


def test_cache_exit_code_is_distinct_from_network_exit_code():
    codes = {
        probe.EXIT_CLEAN,
        probe.EXIT_FINDINGS,
        probe.EXIT_SELFCHECK_FAILED,
        probe.EXIT_NETWORK,
        probe.EXIT_CACHE_INVALID,
    }
    assert len(codes) == 5, "退出码复用会让 CI 无法区分『没网络』与『本地缓存坏了』"
