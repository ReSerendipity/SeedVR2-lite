#!/usr/bin/env python3
"""模型下载完整性校验单元测试（成本治理 P1-3）。

覆盖评估报告 P1-3 的验收标准：
- config.yaml 期望哈希映射收集（fp16/fp8/vae/pos/neg 五类字段）
- 下载后 SHA256 校验：一致通过、损坏抛错、无期望哈希跳过

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from pathlib import Path

import pytest

from scripts.download_model import _load_expected_hashes, _sha256_of, verify_downloaded_hashes

_CONFIG_YAML = """
model:
  models:
    3b:
      checkpoint_fp16: seedvr2_ema_3b_fp16.safetensors
      checkpoint_fp8: seedvr2_ema_3b_fp8_e4m3fn.safetensors
      vae_checkpoint: ema_vae_fp16.safetensors
      pos_emb: pos_emb.pt
      neg_emb: neg_emb.pt
      sha256_fp16: aaaa1111
      sha256_fp8: bbbb2222
      sha256_vae: cccc3333
      sha256_pos_emb: dddd4444
      sha256_neg_emb: eeee5555
    7b:
      vae_checkpoint: ema_vae_fp16.safetensors
      sha256_vae: cccc3333
"""


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_CONFIG_YAML, encoding="utf-8")
    return cfg


def test_load_expected_hashes_collects_all_fields(config_file: Path):
    hashes = _load_expected_hashes(config_file)

    assert hashes["seedvr2_ema_3b_fp16.safetensors"] == "aaaa1111"
    assert hashes["seedvr2_ema_3b_fp8_e4m3fn.safetensors"] == "bbbb2222"
    assert hashes["ema_vae_fp16.safetensors"] == "cccc3333"
    assert hashes["pos_emb.pt"] == "dddd4444"
    assert hashes["neg_emb.pt"] == "eeee5555"


def test_verify_passes_on_matching_hash(tmp_path: Path, config_file: Path):
    target = tmp_path / "pos_emb.pt"
    target.write_bytes(b"weights-bytes")
    expected = _sha256_of(target)
    # 把实际哈希写回配置再校验
    cfg_text = config_file.read_text(encoding="utf-8").replace("dddd4444", expected)
    config_file.write_text(cfg_text, encoding="utf-8")

    verify_downloaded_hashes(tmp_path, ["pos_emb.pt"], config_file)


def test_verify_raises_on_corrupted_file(tmp_path: Path, config_file: Path):
    target = tmp_path / "pos_emb.pt"
    target.write_bytes(b"corrupted-bytes")

    with pytest.raises(RuntimeError) as exc_info:
        verify_downloaded_hashes(tmp_path, ["pos_emb.pt"], config_file)
    assert "SHA256" in str(exc_info.value)
    assert "pos_emb.pt" in str(exc_info.value)


def test_verify_deletes_corrupted_file(tmp_path: Path, config_file: Path):
    """MLOps P2-5：校验失败必须删除残缺文件，不留坏字节给续传/加载链路。"""
    target = tmp_path / "pos_emb.pt"
    target.write_bytes(b"corrupted-bytes")

    with pytest.raises(RuntimeError):
        verify_downloaded_hashes(tmp_path, ["pos_emb.pt"], config_file)
    assert not target.exists(), "损坏文件应在抛出前被删除"


def test_verify_skips_files_without_expected_hash(tmp_path: Path, config_file: Path):
    unknown = tmp_path / "unknown.bin"
    unknown.write_bytes(b"whatever")

    # unknown.bin 无期望哈希 → 跳过不报错
    verify_downloaded_hashes(tmp_path, ["unknown.bin"], config_file)


def test_verify_ignores_missing_files(tmp_path: Path, config_file: Path):
    # 文件不存在时不校验（缺失由存在性检查负责）
    verify_downloaded_hashes(tmp_path, ["pos_emb.pt"], config_file)


def test_verify_noop_when_config_missing(tmp_path: Path):
    verify_downloaded_hashes(tmp_path, ["pos_emb.pt"], tmp_path / "nonexistent.yaml")
