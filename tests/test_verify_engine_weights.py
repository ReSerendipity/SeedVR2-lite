"""`scripts/verify_engine.py` 的权重别名解析测试。

为什么必须走真实文件系统：既有测试一律 `@patch("os.path.exists")` 打桩，
而 verify_engine 的 bug 恰恰是"直查 config 登记名、不走引擎的别名解析器"——
打桩环境里这个 bug 永远不会暴露（桩说存在就存在）。所以这里全部用 tmp_path 真文件。
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "verify_engine.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("verify_engine", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ve = _load_script()

NUMZ_NAME = "seedvr2_ema_3b_fp8_e4m3fn.safetensors"
COMFY_NAME = "seedvr2_3b_fp8_e4m3fn.safetensors"

MODEL_CFG = {
    "checkpoint_fp8": NUMZ_NAME,
    "sha256_fp8": "a" * 64,
    "sha256_fp8_alt": "b" * 64,
    "vae_checkpoint": "ema_vae_fp16.safetensors",
    "pos_emb": "pos_emb.pt",
    "neg_emb": "neg_emb.pt",
}


def _touch(path: Path, payload: bytes = b"x") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
def weights_dir(tmp_path):
    """只放 Comfy-Org 命名的 DiT 权重 —— 正是历史上被假报"不存在"的那种布置。"""
    root = tmp_path / "model"
    _touch(root / COMFY_NAME)
    _touch(root / "ema_vae_fp16.safetensors")
    _touch(root / "pos_emb.pt")
    _touch(root / "neg_emb.pt")
    return root


def test_resolves_registered_name_when_file_uses_alias(weights_dir):
    """核心回归锁：登记名是 numz 命名、盘上是 Comfy-Org 命名 ⇒ 必须解析成功并给出命中名。"""
    resolved = ve.resolve_registered_weights(weights_dir, MODEL_CFG, "fp8")
    dit = resolved["DiT-fp8"]
    assert dit["resolved"] is not None, "文件在场却报缺失 = 旧 bug 复现"
    assert dit["hit"] == COMFY_NAME
    assert dit["registered"] == NUMZ_NAME


def test_hash_candidates_include_alt_so_alias_file_does_not_false_fail(weights_dir):
    """存在性过了但哈希只比主哈希，会把合法的另一源命名判成"校验失败"。"""
    resolved = ve.resolve_registered_weights(weights_dir, MODEL_CFG, "fp8")
    assert set(resolved["DiT-fp8"]["candidates"]) == {"a" * 64, "b" * 64}


def test_shared_files_resolve(weights_dir):
    resolved = ve.resolve_registered_weights(weights_dir, MODEL_CFG, "fp8")
    assert resolved["VAE"]["resolved"] is not None
    assert resolved["pos_emb"]["resolved"] is not None
    assert resolved["neg_emb"]["resolved"] is not None


def test_exact_registered_name_still_works(tmp_path):
    root = tmp_path / "model"
    _touch(root / NUMZ_NAME)
    resolved = ve.resolve_registered_weights(root, MODEL_CFG, "fp8")
    assert resolved["DiT-fp8"]["hit"] == NUMZ_NAME


def test_missing_file_reports_none_not_crash(tmp_path):
    root = tmp_path / "model"
    root.mkdir()
    resolved = ve.resolve_registered_weights(root, MODEL_CFG, "fp8")
    assert resolved["DiT-fp8"]["resolved"] is None
    assert resolved["VAE"]["resolved"] is None


def test_unconfigured_purposes_resolve_to_none(weights_dir):
    """没登记 checkpoint 的精度：不得抛 KeyError，也不得把空字符串当文件名去找。"""
    resolved = ve.resolve_registered_weights(weights_dir, MODEL_CFG, "nvfp4")
    dit = resolved["DiT-nvfp4"]
    assert dit["registered"] == ""
    assert dit["resolved"] is None
    assert dit["candidates"] == []


def test_check_model_files_accepts_alias_named_weight(tmp_path, monkeypatch, capsys):
    """端到端一点：整段文件检查在只有别名权重时必须 PASS（旧实现此处直接 return False）。"""
    root = tmp_path / "model"
    digest = _touch(root / COMFY_NAME)
    _touch(root / "ema_vae_fp16.safetensors")
    cfg = {"model": {"models": {"3b": dict(MODEL_CFG, sha256_fp8=digest, sha256_fp8_alt="b" * 64)}}}
    config_path = tmp_path / "config.yaml"
    import yaml

    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    monkeypatch.setattr(ve, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ve, "print_info", lambda *a, **k: None)
    monkeypatch.setattr(ve, "print_ok", lambda *a, **k: None)
    monkeypatch.setattr(ve, "print_warn", lambda *a, **k: None)
    monkeypatch.setattr(ve, "print_fail", lambda *a, **k: print("FAIL:", *a))

    assert ve.check_model_files("3b", "fp8") is True
    out = capsys.readouterr().out
    assert "FAIL:" not in out, f"不该有任何失败项：{out}"


def test_check_model_files_still_fails_when_really_absent(tmp_path, monkeypatch, capsys):
    """修复不得削弱判定：真缺文件时仍必须 fail-closed。"""
    root = tmp_path / "model"
    root.mkdir()
    cfg = {"model": {"models": {"3b": dict(MODEL_CFG)}}}
    import yaml

    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(ve, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ve, "print_info", lambda *a, **k: None)
    monkeypatch.setattr(ve, "print_ok", lambda *a, **k: None)
    monkeypatch.setattr(ve, "print_warn", lambda *a, **k: None)
    monkeypatch.setattr(ve, "print_header", lambda *a, **k: None)
    monkeypatch.setattr(ve, "print_fail", lambda *a, **k: print("FAIL:", *a))

    assert ve.check_model_files("3b", "fp8") is False
    assert "不存在" in capsys.readouterr().out
