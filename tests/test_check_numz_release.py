"""`scripts/check_numz_release.py` 的行为测试（不联网：全部走 monkeypatch 的假清单）。

重点锁四类失效：
1. 把「同一档位的另一源命名」误报成新档 —— 噪音一大，监控就会像永远全红的哈希门禁一样被忽略。
2. 网络失败时输出"无新增档位"（把没测到当成测过了）。
3. 自检门禁不过却照样出报告。
4. 退出码彼此混用，CI 无法区分「干净 / 有缺口 / 结论不可信 / 没网络」。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_numz_release.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_numz_release", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mon = _load()

NUMZ = {
    "ema_vae_fp16.safetensors": 480_000_000,
    "seedvr2_ema_3b_fp16.safetensors": 6_300_000_000,
    "seedvr2_ema_3b_fp8_e4m3fn.safetensors": 3_100_000_000,
    "seedvr2_ema_7b_fp16.safetensors": 15_000_000_000,
    "seedvr2_ema_7b_fp8_e4m3fn.safetensors": 7_600_000_000,
    "seedvr2_ema_7b_sharp_fp16.safetensors": 15_000_000_000,
    "seedvr2_ema_7b_sharp_fp8_e4m3fn.safetensors": 7_600_000_000,
    "pos_emb.pt": 1_000_000,
    "neg_emb.pt": 1_000_000,
}
COMFY = {
    "seedvr2_3b_fp16.safetensors": 6_300_000_000,  # 同档、另一源命名
    "seedvr2_3b_mxfp8.safetensors": 3_300_000_000,  # 新容器格式
    "seedvr2_7b_mxfp8.safetensors": 7_900_000_000,
    "seedvr2_ema_vae_fp16.safetensors": 480_000_000,
}
CMEKA = {"seedvr2_ema_3b-Q4_K_M.gguf": 2_000_000_000, "seedvr2_ema_7b-Q8_0.gguf": 8_000_000_000}
REPOS_FILES = {"numz/SeedVR2_comfyUI": NUMZ, "Comfy-Org/SeedVR2": COMFY, "cmeka/SeedVR2-GGUF": CMEKA}

# 一份"已登记"清单，等价于 config.yaml 的 checkpoint_* 集合（不含 mxfp8 / gguf）
REGISTERED = {name: f"3b/checkpoint_x{i}" for i, name in enumerate(NUMZ)}


def _stub_fetch(monkeypatch):
    def fake(repo, endpoint, timeout):
        files = REPOS_FILES.get(repo)
        if files is None:
            return {}, "stub: 未知仓库"
        return dict(files), ""

    monkeypatch.setattr(mon, "fetch_repo_files", fake)


@pytest.fixture
def patched_selfchecks(monkeypatch):
    """把已知答案换成与桩数据一致的集合，否则真实 SELF_CHECKS 会因桩缺文件而必红。"""
    monkeypatch.setattr(
        mon,
        "SELF_CHECKS",
        (
            ("numz/SeedVR2_comfyUI", "seedvr2_ema_7b_sharp_fp8_e4m3fn.safetensors", True),
            ("numz/SeedVR2_comfyUI", "ema_vae_fp16.safetensors", True),
            ("Comfy-Org/SeedVR2", "seedvr2_3b_mxfp8.safetensors", True),
            ("cmeka/SeedVR2-GGUF", "seedvr2_ema_3b-Q4_K_M.gguf", True),
        ),
    )


# --------------------------------------------------------------------------
# 文件名归一化
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, size, variant",
    [
        ("seedvr2_ema_3b_fp16.safetensors", "3b", "fp16"),
        ("seedvr2_3b_fp16.safetensors", "3b", "fp16"),
        ("seedvr2_ema_7b_sharp_fp8_e4m3fn.safetensors", "7b_sharp", "fp8_e4m3fn"),
        ("seedvr2_7b_mxfp8.safetensors", "7b", "mxfp8"),
        ("ema_vae_fp16.safetensors", "-", "vae_fp16"),
        ("pos_emb.pt", "-", "pos_emb"),
        ("seedvr2_ema_3b-Q4_K_M.gguf", "3b", "Q4_K_M@gguf"),
    ],
)
def test_parse_weight_normalizes_two_naming_schemes(name, size, variant):
    assert mon.parse_weight(name) == (size, variant)


def test_numz_and_comfyorg_naming_of_same_tier_collapses_to_one_key():
    """两条命名必须落到同一个「尺寸:档位」键 —— 这正是去噪的机制。"""
    assert mon.parse_weight("seedvr2_ema_3b_fp16.safetensors") == mon.parse_weight("seedvr2_3b_fp16.safetensors")


def test_gguf_container_is_not_confused_with_safetensors_tier():
    assert mon.parse_weight("seedvr2_ema_3b-Q4_K_M.gguf")[1] != mon.parse_weight("seedvr2_ema_3b-Q4_K_M.safetensors")[1]


# --------------------------------------------------------------------------
# 比对分类
# --------------------------------------------------------------------------


def test_compare_separates_new_tiers_from_naming_dupes(monkeypatch):
    _stub_fetch(monkeypatch)
    snaps = {r: mon.RepoSnapshot(repo=r, files=REPOS_FILES[r]) for r in REPOS_FILES}
    rows = {r["repo"]: r for r in mon.compare(snaps, REGISTERED)}
    assert rows["numz/SeedVR2_comfyUI"]["new_variants"] == {}
    # Comfy-Org：fp16 是另一源命名（进 dupes），mxfp8 两尺寸才是新档
    assert "3b:fp16" not in json.dumps(rows["Comfy-Org/SeedVR2"]["new_variants"], ensure_ascii=False)
    assert set(rows["Comfy-Org/SeedVR2"]["new_variants"]) == {"mxfp8"}
    assert rows["Comfy-Org/SeedVR2"]["naming_dupes"] == [
        "seedvr2_3b_fp16.safetensors",
        "seedvr2_ema_vae_fp16.safetensors",
    ]
    assert set(rows["cmeka/SeedVR2-GGUF"]["new_variants"]) == {"Q4_K_M@gguf", "Q8_0@gguf"}


def test_compare_marks_failed_repo_without_raising(monkeypatch):
    snaps = {
        "numz/SeedVR2_comfyUI": mon.RepoSnapshot(repo="numz/SeedVR2_comfyUI", files=NUMZ),
        "Comfy-Org/SeedVR2": mon.RepoSnapshot(repo="Comfy-Org/SeedVR2", error="模拟超时"),
        "cmeka/SeedVR2-GGUF": mon.RepoSnapshot(repo="cmeka/SeedVR2-GGUF", error="模拟超时"),
    }
    rows = {r["repo"]: r for r in mon.compare(snaps, REGISTERED)}
    assert rows["Comfy-Org/SeedVR2"]["status"] == "network_error"
    assert rows["numz/SeedVR2_comfyUI"]["status"] == "ok"


# --------------------------------------------------------------------------
# 自检门禁
# --------------------------------------------------------------------------


def test_self_check_fails_when_upstream_removed_a_known_file(patched_selfchecks):
    files = dict(NUMZ)
    files.pop("ema_vae_fp16.safetensors")
    snaps = {
        "numz/SeedVR2_comfyUI": mon.RepoSnapshot(repo="numz/SeedVR2_comfyUI", files=files),
        "Comfy-Org/SeedVR2": mon.RepoSnapshot(repo="Comfy-Org/SeedVR2", files=COMFY),
        "cmeka/SeedVR2-GGUF": mon.RepoSnapshot(repo="cmeka/SeedVR2-GGUF", files=CMEKA),
    }
    failures = mon.run_self_checks(snaps)
    assert any("ema_vae_fp16" in f for f in failures), "上游真撤档时门禁必须响，并提示人工更新登记"


def test_self_check_passes_on_consistent_snapshot(patched_selfchecks):
    snaps = {
        "numz/SeedVR2_comfyUI": mon.RepoSnapshot(repo="numz/SeedVR2_comfyUI", files=NUMZ),
        "Comfy-Org/SeedVR2": mon.RepoSnapshot(repo="Comfy-Org/SeedVR2", files=COMFY),
        "cmeka/SeedVR2-GGUF": mon.RepoSnapshot(repo="cmeka/SeedVR2-GGUF", files=CMEKA),
    }
    assert mon.run_self_checks(snaps) == []


# --------------------------------------------------------------------------
# 退出码契约
# --------------------------------------------------------------------------


def _main_with(monkeypatch, tmp_path, fetch_impl):
    monkeypatch.setattr(mon, "fetch_repo_files", fetch_impl)
    monkeypatch.setattr(mon, "_load_config_filenames", lambda: dict(REGISTERED))
    return mon.main(["--report-dir", str(tmp_path / "reports")])


def test_main_returns_network_exit_when_every_repo_unreachable(monkeypatch, tmp_path, capsys):
    def all_fail(repo, endpoint, timeout):
        return {}, "模拟：TLS/网络失败"

    assert _main_with(monkeypatch, tmp_path, all_fail) == mon.EXIT_NETWORK
    err = capsys.readouterr().err
    assert "无法判定" in err and "无新增档位" in err
    assert not (tmp_path / "reports").exists()


def test_main_returns_selfcheck_exit_when_chain_broken(monkeypatch, tmp_path, capsys):
    """抓到了清单但内容对不上已知答案 ⇒ 链路可疑 ⇒ 不出报告（不能退化成「无新增」）。"""

    def wrong(repo, endpoint, timeout):
        return {"unrelated.safetensors": 1}, ""

    monkeypatch.setattr(mon, "_load_config_filenames", lambda: dict(REGISTERED))
    monkeypatch.setattr(mon, "fetch_repo_files", wrong)
    assert mon.main(["--report-dir", str(tmp_path / "reports")]) == mon.EXIT_SELFCHECK_FAILED
    assert "自检门禁失败" in capsys.readouterr().err
    assert not (tmp_path / "reports").exists()


def test_main_reports_new_tiers_and_exits_findings(monkeypatch, tmp_path, patched_selfchecks, capsys):
    _stub_fetch(monkeypatch)
    monkeypatch.setattr(mon, "_load_config_filenames", lambda: dict(REGISTERED))
    rc = mon.main(["--report-dir", str(tmp_path / "reports")])
    assert rc == mon.EXIT_FINDINGS
    out = (tmp_path / "reports").glob("numz_release_*.md")
    text = next(out).read_text(encoding="utf-8")
    assert "mxfp8" in text and "Q4_K_M@gguf" in text
    assert "同档位、另一源命名" in text


def test_main_exit_clean_when_all_tiers_registered(monkeypatch, tmp_path):
    """全部档位都已登记时必须退 0 —— 否则「有缺口」和「没缺口」分不开。"""
    monkeypatch.setattr(mon, "_load_config_filenames", lambda: dict(REGISTERED))
    monkeypatch.setattr(mon, "REPOS", ({"repo": "numz/SeedVR2_comfyUI", "role": "test"},))
    monkeypatch.setattr(mon, "SELF_CHECKS", (("numz/SeedVR2_comfyUI", "ema_vae_fp16.safetensors", True),))
    monkeypatch.setattr(mon, "fetch_repo_files", lambda repo, endpoint, timeout: (dict(NUMZ), ""))
    assert mon.main(["--report-dir", str(tmp_path / "reports")]) == mon.EXIT_CLEAN


def test_exit_codes_are_distinct():
    assert len({mon.EXIT_CLEAN, mon.EXIT_FINDINGS, mon.EXIT_SELFCHECK_FAILED, mon.EXIT_NETWORK}) == 4
    assert mon.EXIT_CLEAN == 0


# --------------------------------------------------------------------------
# 传输回退
# --------------------------------------------------------------------------


def test_curl_fallback_used_when_requests_fails(monkeypatch):
    """本机实测 certifi 验不过 huggingface.co，而 Windows 根存储可以 —— 回退链是必需品。"""
    import requests

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"siblings": [{"rfilename": "diffusion_models/seedvr2_3b_mxfp8.safetensors", "size": 10}]}

    def boom(*a, **k):
        raise requests.exceptions.SSLError("unable to get local issuer certificate")

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, json.dumps({"siblings": [{"rfilename": "a/seedvr2_3b_mxfp8.safetensors", "size": 10}]}), ""
        )

    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(subprocess, "run", fake_run)
    files, err = mon.fetch_repo_files("Comfy-Org/SeedVR2", None, 5)
    assert err == ""
    assert list(files) == ["seedvr2_3b_mxfp8.safetensors"]
    assert "--ssl-no-revoke" in captured["cmd"], "回退只跳过吊销检查，不得关掉链验证"
    assert "-k" not in captured["cmd"] and "--insecure" not in captured["cmd"]


def test_both_transports_failing_is_reported_as_error_not_empty_ok(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.exceptions.SSLError("cert verify failed")

    def fail_run(cmd, **kwargs):
        raise FileNotFoundError("curl 不在 PATH")

    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(subprocess, "run", fail_run)
    files, err = mon.fetch_repo_files("numz/SeedVR2_comfyUI", None, 5)
    assert files == {} and err, "两级都失败必须返回错误串，否则上层会把「没抓到」当成「抓到了且为空」"
