#!/usr/bin/env python3
"""单文件任务磁盘预检锁定测试（成本治理 P0-1 回归守护）。

背景：2026-09-06 成本资源治理评估曾误判「单文件任务路径无磁盘预检」
（当时 grep 范围漏了 routes/restore/ 子目录）。实际实现已存在：
- POST /api/restore/（upload.py）提交前调用 common.ensure_disk_space
- POST /api/restore/batch（batch.py）提交前同样调用
- 批量执行编排（process_batch_background）启动前二次校验

本测试锁定该行为：磁盘剩余空间低于 retention.disk_min_free_gb 时，
单文件提交必须返回 507 INSUFFICIENT_DISK，防止未来重构静默丢失预检。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from types import SimpleNamespace

import pytest

from tests.conftest import csrf_post


@pytest.mark.integration
class TestSingleTaskDiskPreflight:
    def test_upload_rejected_when_disk_below_threshold(self, test_app, tmp_path, monkeypatch):
        from PIL import Image

        import app.integrated_app.routes.restore.upload as upload_module
        from app.integrated_app.services import restore_service

        folder = tmp_path / "media"
        folder.mkdir()
        Image.new("RGB", (8, 8)).save(folder / "in.png")
        monkeypatch.setattr(upload_module, "gpu_manager", SimpleNamespace(is_gpu_available=True))
        # 模拟磁盘剩余 1GB < 最低要求 5GB（retention.disk_min_free_gb 默认值）
        monkeypatch.setattr(
            restore_service.shutil,
            "disk_usage",
            lambda _p: SimpleNamespace(total=100, used=99, free=1 * 1024**3),
        )

        resp = csrf_post(test_app, "/api/restore/", data={"folder_path": str(folder)})
        assert resp.status_code == 507
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INSUFFICIENT_DISK"

    def test_batch_rejected_when_disk_below_threshold(self, test_app, tmp_path, monkeypatch):
        from PIL import Image

        import app.integrated_app.routes.restore.batch as batch_module
        from app.integrated_app.services import restore_service

        folder = tmp_path / "media"
        folder.mkdir()
        Image.new("RGB", (8, 8)).save(folder / "in.png")
        monkeypatch.setattr(batch_module, "gpu_manager", SimpleNamespace(is_gpu_available=True))
        monkeypatch.setattr(
            restore_service.shutil,
            "disk_usage",
            lambda _p: SimpleNamespace(total=100, used=99, free=1 * 1024**3),
        )

        resp = csrf_post(test_app, "/api/restore/batch", data={"folder_path": str(folder)})
        assert resp.status_code == 507
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INSUFFICIENT_DISK"
