#!/usr/bin/env python3
"""水印嵌入失败处置策略测试（评估报告 R2：fail-closed 兜底）。

覆盖：
- resolve_watermark_failure_policy：默认值 / 合法值 / 非法值回退
- embed_with_retry：成功路径 / 首次失败重试成功 / 双重失败返回错误
- handle_watermark_failure：block 抛异常 + 审计 / mark_metadata 降级审计 / ignore 静默
- write_provenance_sidecar：侧车文件内容与路径规则

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import json
from pathlib import Path

import numpy as np
import pytest

from app.integrated_app.services import watermark_policy
from app.integrated_app.services.watermark_policy import (
    DEFAULT_WATERMARK_FAILURE_POLICY,
    WatermarkEmbedError,
    embed_with_retry,
    handle_watermark_failure,
    resolve_watermark_failure_policy,
    write_provenance_sidecar,
)


def _img() -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.integers(0, 255, size=(64, 64, 3), dtype=np.uint8)


# ---------- resolve_watermark_failure_policy ----------


def test_resolve_default_when_unconfigured():
    assert resolve_watermark_failure_policy({}) == DEFAULT_WATERMARK_FAILURE_POLICY


@pytest.mark.parametrize("policy", ["mark_metadata", "block", "ignore"])
def test_resolve_valid_values(policy):
    config = {"runtime": {"security": {"watermark_on_failure": policy}}}
    assert resolve_watermark_failure_policy(config) == policy


def test_resolve_invalid_falls_back_to_default():
    config = {"runtime": {"security": {"watermark_on_failure": "explode"}}}
    assert resolve_watermark_failure_policy(config) == DEFAULT_WATERMARK_FAILURE_POLICY


# ---------- embed_with_retry ----------


def test_embed_success(monkeypatch):
    import app.integrated_app.security.watermark as wm

    monkeypatch.setattr(wm, "embed_watermark", lambda img, payload=None: img + 1)
    out, embedded, err = embed_with_retry(_img(), payload="task-1")
    assert embedded is True
    assert err is None


def test_embed_retry_then_success(monkeypatch):
    import app.integrated_app.security.watermark as wm

    calls = {"n": 0}

    def flaky(img, payload=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("transient")
        return img

    monkeypatch.setattr(wm, "embed_watermark", flaky)
    out, embedded, err = embed_with_retry(_img())
    assert embedded is True and err is None and calls["n"] == 2


def test_embed_double_failure_returns_original(monkeypatch):
    import app.integrated_app.security.watermark as wm

    def broken(img, payload=None):
        raise ValueError("deterministic")

    monkeypatch.setattr(wm, "embed_watermark", broken)
    src = _img()
    out, embedded, err = embed_with_retry(src)
    assert embedded is False
    assert err is not None and "deterministic" in err
    assert np.array_equal(out, src)


# ---------- handle_watermark_failure ----------


def test_handle_block_raises_and_audits(monkeypatch):
    events = []
    monkeypatch.setattr(watermark_policy, "audit_event", lambda event, **kw: events.append((event, kw)))
    with pytest.raises(WatermarkEmbedError, match="block"):
        handle_watermark_failure(policy="block", error="boom", payload="task-9")
    assert events and events[0][0] == "WATERMARK_EMBED_BLOCKED"


def test_handle_mark_metadata_audits(monkeypatch, caplog):
    events = []
    monkeypatch.setattr(watermark_policy, "audit_event", lambda event, **kw: events.append((event, kw)))
    handle_watermark_failure(policy="mark_metadata", error="boom", payload="task-9")
    assert events and events[0][0] == "WATERMARK_EMBED_DEGRADED"
    assert any("mark_metadata" in r.message for r in caplog.records)


def test_handle_ignore_is_silent(monkeypatch):
    events = []
    monkeypatch.setattr(watermark_policy, "audit_event", lambda event, **kw: events.append((event, kw)))
    handle_watermark_failure(policy="ignore", error="boom")
    assert events == []


# ---------- write_provenance_sidecar ----------


def test_sidecar_written_next_to_output(tmp_path):
    output = tmp_path / "output.png"
    output.write_bytes(b"\x89PNG fake")
    sidecar = write_provenance_sidecar(str(output), payload="task-1")
    assert sidecar.endswith("output.provenance.json")
    body = json.loads(Path(sidecar).read_text(encoding="utf-8"))
    assert body["watermark_embedded"] is False
    assert body["payload"] == "task-1"
    assert body["tool"] == "SeedVR2"
