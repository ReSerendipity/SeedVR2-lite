"""训练侧数据治理测试：权重 sidecar（P2-2）+ epoch 快照保留（P2-3）。

验收标准：
P2-2
  1. sidecar 路径推导 / 落盘 / 读回正确，JSON 结构含哈希与训练来源；
  2. 权重内容变更可被 verify_sidecar_hash 检出（防漂移）；
  3. 无 sidecar 时 read/verify 安全返回 None/False；
  4. describe_provenance 生成可读溯源摘要。
P2-3
  5. keep_last_epoch_checkpoints=0（默认）时 epoch 快照全部保留（兼容旧行为）；
  6. 设为 N 时只保留最近 N 个 epoch 快照，最旧被删除；
  7. 删除 checkpoint 时同步回收其 sidecar，不留孤儿。
"""

import os

import pytest

from training.weight_sidecar import (
    build_sidecar,
    describe_provenance,
    read_sidecar,
    sha256_file,
    sidecar_path_for,
    verify_sidecar_hash,
    write_sidecar,
)


@pytest.fixture
def weight_file(tmp_path):
    p = tmp_path / "checkpoint_step_500.pt"
    p.write_bytes(b"\x00\x01" * 4096)
    return str(p)


class TestWeightSidecar:
    def test_sidecar_path_derivation(self, weight_file):
        """验收点 1：x.pt → x.meta.json。"""
        assert sidecar_path_for(weight_file).endswith("checkpoint_step_500.meta.json")

    def test_write_and_read_roundtrip(self, weight_file):
        """验收点 1：落盘 → 读回，含哈希与训练来源。"""
        training = {"step": 500, "epoch": 1, "dataset_sha256": "a" * 64}
        meta = build_sidecar(weight_file, training=training, parent={"weight_file": "prev.pt", "sha256": "b" * 64})
        path = write_sidecar(meta, weight_file)
        assert os.path.exists(path)

        loaded = read_sidecar(weight_file)
        assert loaded is not None
        assert loaded["schema"] == "seedvr2-weight-sidecar/1"
        assert loaded["sha256"] == sha256_file(weight_file)
        assert loaded["size_bytes"] == os.path.getsize(weight_file)
        assert loaded["training"]["dataset_sha256"] == "a" * 64
        assert loaded["parent"]["weight_file"] == "prev.pt"

    def test_verify_detects_drift(self, weight_file):
        """验收点 2：权重被替换后哈希校验失败。"""
        write_sidecar(build_sidecar(weight_file), weight_file)
        assert verify_sidecar_hash(weight_file) is True
        with open(weight_file, "ab") as f:
            f.write(b"tampered")
        assert verify_sidecar_hash(weight_file) is False

    def test_missing_sidecar_is_safe(self, tmp_path):
        """验收点 3：无 sidecar 时安全返回。"""
        p = tmp_path / "orphan.pt"
        p.write_bytes(b"data")
        assert read_sidecar(str(p)) is None
        assert verify_sidecar_hash(str(p)) is False

    def test_describe_provenance(self, weight_file):
        """验收点 4：溯源摘要可读。"""
        meta = build_sidecar(
            weight_file,
            training={"step": 500, "epoch": 2, "dataset_sha256": "c" * 64},
            parent={"weight_file": "checkpoint_step_250.pt"},
        )
        text = describe_provenance(meta)
        assert "checkpoint_step_500.pt" in text
        assert "step=500" in text and "epoch=2" in text
        assert "cccccccccccc" in text
        assert "checkpoint_step_250.pt" in text
        assert describe_provenance(None).startswith("无 sidecar")


class TestEpochCheckpointRetention:
    """直接测试 DistributedTrainer 的清理逻辑（无需 GPU：只调用私有方法）。"""

    def _make_trainer(self, tmp_path, keep_epoch: int):
        from training.distributed_trainer import DistributedTrainer, TrainingConfig

        cfg = TrainingConfig(
            checkpoint_dir=str(tmp_path / "ckpt"),
            keep_last_checkpoints=3,
            keep_last_epoch_checkpoints=keep_epoch,
        )
        trainer = DistributedTrainer.__new__(DistributedTrainer)  # 绕过 __init__（会初始化分布式）
        trainer.config = cfg
        trainer.global_rank = 0
        trainer.world_size = 1
        trainer._step = 0
        trainer._epoch = 0
        return cfg, trainer

    def _seed_checkpoints(self, checkpoint_dir: str, epochs: int):
        ckpt_dir = os.path.join(checkpoint_dir)
        os.makedirs(ckpt_dir, exist_ok=True)
        created = []
        for i in range(1, epochs + 1):
            p = os.path.join(ckpt_dir, f"checkpoint_step_{i * 100}_epoch_{i}.pt")
            with open(p, "wb") as f:
                f.write(b"ckpt" * 32)
            created.append(p)
        return created

    def test_default_keeps_all_epoch_checkpoints(self, tmp_path):
        """验收点 5：默认 0 → 全部保留（向后兼容）。"""
        cfg, trainer = self._make_trainer(tmp_path, keep_epoch=0)
        files = self._seed_checkpoints(cfg.checkpoint_dir, 5)
        trainer._prune_epoch_checkpoints(__import__("pathlib").Path(cfg.checkpoint_dir))
        assert all(os.path.exists(f) for f in files)

    def test_keeps_only_latest_n(self, tmp_path):
        """验收点 6：keep=2 → 仅保留最新 2 个 epoch 快照。"""
        cfg, trainer = self._make_trainer(tmp_path, keep_epoch=2)
        files = self._seed_checkpoints(cfg.checkpoint_dir, 5)
        trainer._prune_epoch_checkpoints(__import__("pathlib").Path(cfg.checkpoint_dir))
        remaining = [f for f in files if os.path.exists(f)]
        assert len(remaining) == 2
        assert remaining == files[-2:]

    def test_deletes_sidecar_alongside(self, tmp_path):
        """验收点 7：删除 checkpoint 时同步回收 sidecar。"""
        cfg, trainer = self._make_trainer(tmp_path, keep_epoch=1)
        files = self._seed_checkpoints(cfg.checkpoint_dir, 3)
        # 为最旧的两个快照各写一个 sidecar
        for f in files[:2]:
            write_sidecar(build_sidecar(f), f)
        assert os.path.exists(sidecar_path_for(files[0]))

        trainer._prune_epoch_checkpoints(__import__("pathlib").Path(cfg.checkpoint_dir))

        assert not os.path.exists(files[0])
        assert not os.path.exists(sidecar_path_for(files[0])), "sidecar 成为孤儿"
        assert not os.path.exists(files[1])
        assert os.path.exists(files[2])
