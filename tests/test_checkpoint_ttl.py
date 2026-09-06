#!/usr/bin/env python3
"""checkpoint TTL 孤儿扫描测试（数据治理 P2-1）。

验收标准（评估报告 P2-1）：
1. remove_stale_checkpoints 仅清理超过 TTL 的 *.json
2. 新鲜 checkpoint（可续跑）保留
3. 同目录下训练子系统的 .pt 快照绝不触碰（trainer 自身滚动保留策略管理）
4. TTL=0 禁用
5. config.yaml runtime.task.checkpoint_ttl_minutes 默认 1440 且校验生效

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import os
import time

from app.integrated_app.checkpoint import TaskCheckpoint
from app.integrated_app.config_models import RuntimeTaskConfig


class TestRemoveStaleCheckpoints:
    def test_removes_only_expired_json(self, tmp_path):
        mgr = TaskCheckpoint(str(tmp_path))
        stale = tmp_path / "task-old.json"
        fresh = tmp_path / "task-new.json"
        stale.write_text("{}", encoding="utf-8")
        fresh.write_text("{}", encoding="utf-8")
        now = time.time()
        os.utime(stale, (now - 25 * 3600, now - 25 * 3600))
        os.utime(fresh, (now, now))

        removed = mgr.remove_stale_checkpoints(24 * 3600)

        assert removed == 1
        assert not stale.exists()
        assert fresh.exists()  # 新鲜断点保留，可续跑

    def test_never_touches_training_pt_snapshots(self, tmp_path):
        """data/checkpoints 与训练快照共用：.pt 文件归 trainer 滚动策略管，绝不删除。"""
        mgr = TaskCheckpoint(str(tmp_path))
        stale_ckpt = tmp_path / "task-old.json"
        training_pt = tmp_path / "checkpoint_step_500.pt"
        epoch_pt = tmp_path / "checkpoint_epoch_3.pt"
        stale_ckpt.write_text("{}", encoding="utf-8")
        training_pt.write_bytes(b"pt")
        epoch_pt.write_bytes(b"pt")
        now = time.time()
        for p in (stale_ckpt, training_pt, epoch_pt):
            os.utime(p, (now - 365 * 86400, now - 365 * 86400))

        removed = mgr.remove_stale_checkpoints(24 * 3600)

        assert removed == 1
        assert not stale_ckpt.exists()
        assert training_pt.exists()
        assert epoch_pt.exists()

    def test_zero_ttl_disables(self, tmp_path):
        mgr = TaskCheckpoint(str(tmp_path))
        stale = tmp_path / "task-old.json"
        stale.write_text("{}", encoding="utf-8")
        now = time.time()
        os.utime(stale, (now - 365 * 86400, now - 365 * 86400))

        assert mgr.remove_stale_checkpoints(0) == 0
        assert stale.exists()

    def test_missing_dir_is_noop(self, tmp_path):
        mgr = TaskCheckpoint(str(tmp_path / "nonexistent"))
        assert mgr.remove_stale_checkpoints(24 * 3600) == 0


class TestCheckpointTtlConfig:
    def test_default_ttl_is_24h(self):
        cfg = RuntimeTaskConfig()
        assert cfg.checkpoint_ttl_minutes == 1440

    def test_zero_disables_and_out_of_range_rejected(self):
        import pytest
        from pydantic import ValidationError

        assert RuntimeTaskConfig(checkpoint_ttl_minutes=0).checkpoint_ttl_minutes == 0
        with pytest.raises(ValidationError):
            RuntimeTaskConfig(checkpoint_ttl_minutes=-1)
        with pytest.raises(ValidationError):
            RuntimeTaskConfig(checkpoint_ttl_minutes=50000)
