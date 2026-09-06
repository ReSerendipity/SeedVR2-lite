#!/usr/bin/env python3
"""历史删除连带产物清理测试（数据治理 P1-1）。

验收标准（评估报告 P1-1）：
1. 删除历史记录时连带删除输出文件（经 PathGuard 白名单校验）
2. 白名单外的输出路径拒绝删除（防任意路径删除面）
3. 关联任务的断点续跑 JSON 一并回收（失败残留此前永久留存）
4. 用户上传的原始输入文件不连带删除（由 uploads 留存策略统一治理）
5. clear_records 批量路径同样连带清理

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import pytest

from app.integrated_app.history_db import HistoryDB, HistoryRecord, TaskRecord
from app.integrated_app.routes.system.history import remove_record_artifacts


def _write(path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _config(tmp_path, allowed) -> dict:
    return {
        "runtime": {
            "security": {"allowed_base_dirs": [str(allowed)]},
            "task": {"checkpoint_dir": str(tmp_path / "checkpoints")},
        }
    }


@pytest.mark.asyncio
class TestRemoveRecordArtifacts:
    """remove_record_artifacts 连带清理。"""

    async def test_removes_output_file_and_checkpoint(self, tmp_path):
        out_file = tmp_path / "outputs" / "image" / "r.png"
        input_file = tmp_path / "uploads" / "in.png"
        _write(out_file)
        _write(input_file)

        async with HistoryDB(db_path=str(tmp_path / "history.db"), max_records=0) as db:
            rid = await db.add_record(
                HistoryRecord(
                    task_type="image", input_file=str(input_file), output_file=str(out_file), status="completed"
                )
            )
            await db.create_task(TaskRecord(task_id="t1", record_id=rid, status="failed"))
            ckpt = tmp_path / "checkpoints" / "t1.json"
            _write(ckpt, b"{}")

            record = await db.get_record(rid)
            removed = await remove_record_artifacts([record], db, _config(tmp_path, tmp_path))

        assert removed == 2
        assert not out_file.exists()
        assert not ckpt.exists()
        # 原始上传输入不连带删除
        assert input_file.exists()

    async def test_output_outside_whitelist_is_skipped(self, tmp_path):
        out_file = tmp_path / "outputs" / "r.png"
        _write(out_file)

        async with HistoryDB(db_path=str(tmp_path / "history.db"), max_records=0) as db:
            rid = await db.add_record(
                HistoryRecord(task_type="image", input_file="a.png", output_file=str(out_file), status="completed")
            )
            record = await db.get_record(rid)
            # 白名单仅允许 other/，输出路径不在其中 → 拒删且不抛异常
            removed = await remove_record_artifacts([record], db, _config(tmp_path, tmp_path / "other"))

        assert removed == 0
        assert out_file.exists()

    async def test_multiple_task_checkpoints_all_removed(self, tmp_path):
        async with HistoryDB(db_path=str(tmp_path / "history.db"), max_records=0) as db:
            rid = await db.add_record(HistoryRecord(task_type="video", input_file="a.mp4", status="failed"))
            for tid in ("t1", "t2"):
                await db.create_task(TaskRecord(task_id=tid, record_id=rid, status="failed"))
                _write(tmp_path / "checkpoints" / f"{tid}.json", b"{}")

            record = await db.get_record(rid)
            removed = await remove_record_artifacts([record], db, _config(tmp_path, tmp_path))

        assert removed == 2
        assert not (tmp_path / "checkpoints" / "t1.json").exists()
        assert not (tmp_path / "checkpoints" / "t2.json").exists()


@pytest.mark.asyncio
class TestHistoryDbFilterHelpers:
    """get_task_ids_by_record_id / get_records_filtered 查询辅助。"""

    async def test_get_task_ids_by_record_id(self, tmp_path):
        async with HistoryDB(db_path=str(tmp_path / "history.db"), max_records=0) as db:
            rid = await db.add_record(HistoryRecord(task_type="image", input_file="a.png", status="failed"))
            other_rid = await db.add_record(HistoryRecord(task_type="image", input_file="b.png", status="failed"))
            await db.create_task(TaskRecord(task_id="t1", record_id=rid, status="failed"))
            await db.create_task(TaskRecord(task_id="t2", record_id=rid, status="failed"))
            await db.create_task(TaskRecord(task_id="t3", record_id=other_rid, status="failed"))

            task_ids = await db.get_task_ids_by_record_id(rid)

        assert set(task_ids) == {"t1", "t2"}

    async def test_get_records_filtered_by_status_and_date(self, tmp_path):
        async with HistoryDB(db_path=str(tmp_path / "history.db"), max_records=0) as db:
            await db.add_record(HistoryRecord(task_type="image", input_file="a.png", status="failed"))
            await db.add_record(HistoryRecord(task_type="image", input_file="b.png", status="completed"))
            await db.add_record(HistoryRecord(task_type="image", input_file="c.png", status="failed"))

            failed = await db.get_records_filtered(status="failed")
            assert {r.input_file for r in failed} == {"a.png", "c.png"}

            future = "9999-12-31T23:59:59"
            none_after = await db.get_records_filtered(before_date=future)
            assert len(none_after) == 3
            past = await db.get_records_filtered(before_date="2000-01-01T00:00:00")
            assert past == []
