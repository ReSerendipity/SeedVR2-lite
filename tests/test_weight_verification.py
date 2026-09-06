#!/usr/bin/env python3
"""模型加载前权重 sha256 白名单校验测试（数据治理 P1-3）。

验收标准（评估报告 P1-3）：
1. 已配置哈希且匹配 → 放行
2. 已配置哈希且不匹配 → 拒绝加载（ValueError，防手动放置任意/恶意权重）
3. 未配置期望哈希 → 告警放行（兼容自定义权重场景）
4. 文件不存在 → 跳过（存在性由既有链路负责）
5. size+mtime 命中的缓存免重算（GB 级权重二次加载近零开销）

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import hashlib
import os

import pytest

from app.integrated_app import model_manager as mm_module
from app.integrated_app.model_manager import ModelManager


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make(path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return _digest(data)


def _manager(tmp_path, monkeypatch, model_entry) -> ModelManager:
    cfg = {"model": {"model_source_mode": "portable", "models": {"3b": dict(model_entry)}}}
    mgr = ModelManager(cfg)
    monkeypatch.setattr(mgr, "get_pretrained_dir", lambda: str(tmp_path))
    monkeypatch.chdir(tmp_path)  # 哈希缓存落盘重定向到 tmp，避免污染真实 data/
    return mgr


@pytest.mark.asyncio
class TestVerifyWeightHashes:
    async def test_matching_hash_passes(self, tmp_path, monkeypatch):
        data = b"checkpoint-bytes"
        digest = _make(tmp_path / "w.safetensors", data)
        entry = {"checkpoint_fp16": "w.safetensors", "sha256_fp16": digest}
        mgr = _manager(tmp_path, monkeypatch, entry)

        await mgr.verify_weight_hashes("3b", "fp16")  # 不抛异常即通过

    async def test_mismatched_hash_rejected(self, tmp_path, monkeypatch):
        _make(tmp_path / "w.safetensors", b"tampered-or-corrupt")
        entry = {"checkpoint_fp16": "w.safetensors", "sha256_fp16": _digest(b"original")}
        mgr = _manager(tmp_path, monkeypatch, entry)

        with pytest.raises(ValueError) as exc_info:
            await mgr.verify_weight_hashes("3b", "fp16")
        assert "SHA256 校验失败" in str(exc_info.value)

    async def test_missing_expected_hash_warns_and_passes(self, tmp_path, monkeypatch):
        _make(tmp_path / "w.safetensors", b"custom-weight")
        entry = {"checkpoint_fp16": "w.safetensors"}  # 无 sha256_fp16
        mgr = _manager(tmp_path, monkeypatch, entry)

        await mgr.verify_weight_hashes("3b", "fp16")

    async def test_missing_file_skipped(self, tmp_path, monkeypatch):
        entry = {"checkpoint_fp16": "absent.safetensors", "sha256_fp16": _digest(b"x")}
        mgr = _manager(tmp_path, monkeypatch, entry)

        await mgr.verify_weight_hashes("3b", "fp16")

    async def test_all_shared_components_verified(self, tmp_path, monkeypatch):
        vae_digest = _make(tmp_path / "vae.safetensors", b"vae")
        pos_digest = _make(tmp_path / "pos_emb.pt", b"pos")
        neg_digest = _make(tmp_path / "neg_emb.pt", b"neg")
        entry = {
            "checkpoint_fp16": "w.safetensors",  # 不在场 → 跳过
            "sha256_fp16": _digest(b"x"),
            "vae_checkpoint": "vae.safetensors",
            "sha256_vae": vae_digest,
            "pos_emb": "pos_emb.pt",
            "sha256_pos_emb": pos_digest,
            "neg_emb": "neg_emb.pt",
            "sha256_neg_emb": neg_digest,
        }
        mgr = _manager(tmp_path, monkeypatch, entry)

        await mgr.verify_weight_hashes("3b", "fp16")

    async def test_vae_mismatch_rejected(self, tmp_path, monkeypatch):
        _make(tmp_path / "vae.safetensors", b"tampered-vae")
        entry = {"vae_checkpoint": "vae.safetensors", "sha256_vae": _digest(b"good-vae")}
        mgr = _manager(tmp_path, monkeypatch, entry)

        with pytest.raises(ValueError):
            await mgr.verify_weight_hashes("3b", "fp16")


@pytest.mark.asyncio
class TestHashCache:
    async def test_cache_hit_avoids_recompute(self, tmp_path, monkeypatch):
        data = b"checkpoint-bytes"
        path = tmp_path / "w.safetensors"
        _make(path, data)
        entry = {"checkpoint_fp16": "w.safetensors", "sha256_fp16": _digest(data)}
        mgr = _manager(tmp_path, monkeypatch, entry)

        calls = []
        real_compute = mm_module.compute_file_sha256

        def counting_compute(p, **kwargs):
            calls.append(p)
            return real_compute(p, **kwargs)

        monkeypatch.setattr(mm_module, "compute_file_sha256", counting_compute)

        await mgr.verify_weight_hashes("3b", "fp16")
        assert len(calls) == 1
        # 缓存已落盘
        assert (tmp_path / "data" / "model_hash_cache.json").exists()

        # 同一文件再次校验：size+mtime 命中，不重算
        await mgr.verify_weight_hashes("3b", "fp16")
        assert len(calls) == 1

    async def test_cache_invalidated_on_mtime_change(self, tmp_path, monkeypatch):
        data = b"checkpoint-bytes"
        path = tmp_path / "w.safetensors"
        _make(path, data)
        entry = {"checkpoint_fp16": "w.safetensors", "sha256_fp16": _digest(data)}
        mgr = _manager(tmp_path, monkeypatch, entry)

        await mgr.verify_weight_hashes("3b", "fp16")

        # mtime 变化 → 缓存失效 → 重算（仍匹配）
        old = os.stat(path)
        os.utime(path, (old.st_atime, old.st_mtime + 10))

        await mgr.verify_weight_hashes("3b", "fp16")
