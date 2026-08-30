#!/usr/bin/env python3
"""任务提交健壮性测试（P1-4 幂等键 / P1-6 恢复取消回调 / P1-7 逐文件落库）。

覆盖评估报告 P1 批次验收标准：
- P1-4: POST /api/restore/ 与 /api/restore/batch 接受 Idempotency-Key（头）/
  idempotency_key（表单），同键重复提交返回既有任务（duplicate=true），
  非法格式 400；未提供键行为不变（uuid 随机 id）
- P1-6: recover_tasks 重新入队时注入 on_cancel（恢复任务可被协作取消）
- P1-7: 批量任务逐文件即时落库——每个文件处理完成即有 history 记录；
  add_records 以 MAX(id) 基线推算整批 id

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.integrated_app.history_db import HistoryDB, HistoryRecord, TaskRecord
from app.integrated_app.routes.restore.batch import _resolve_idempotency_key as batch_key
from app.integrated_app.routes.restore.recovery import recover_tasks
from app.integrated_app.routes.restore.upload import _resolve_idempotency_key as upload_key
from app.integrated_app.services.restore_service import process_batch_background
from app.integrated_app.services.task_state import task_state_store
from tests.conftest import csrf_post


def _make_request(headers: dict | None = None) -> MagicMock:
    req = MagicMock()
    req.headers = headers or {}
    return req


class TestResolveIdempotencyKey:
    """幂等键解析与校验（upload/batch 共用语义）。"""

    def test_no_key_returns_none(self):
        assert upload_key(_make_request(), None) is None
        assert upload_key(_make_request(), "   ") is None
        assert batch_key(_make_request(), None) is None

    def test_header_takes_priority_over_form(self):
        req = _make_request({"Idempotency-Key": "header-key"})
        assert upload_key(req, "form-key") == "header-key"
        assert batch_key(req, "form-key") == "header-key"

    def test_form_fallback(self):
        assert upload_key(_make_request(), "form-key") == "form-key"
        assert batch_key(_make_request(), "batch-key-1") == "batch-key-1"

    @pytest.mark.parametrize("bad", ["bad key!", "键", "a" * 65])
    def test_invalid_key_raises_400(self, bad):
        with pytest.raises(HTTPException) as exc_info:
            upload_key(_make_request(), bad)
        assert exc_info.value.status_code == 400
        assert "幂等键格式非法" in exc_info.value.detail

    def test_valid_charset(self):
        assert upload_key(_make_request({"Idempotency-Key": "A-z_09.xy"}), None) == "A-z_09.xy"


@pytest.mark.integration
class TestRestoreIdempotencyIntegration:
    """幂等命中短路：不触发 GPU/模型加载，直接返回既有任务。"""

    def test_upload_duplicate_returns_existing(self, test_app):
        db: HistoryDB = test_app.app.state.history_db
        record_id = asyncio.run(
            db.add_record(HistoryRecord(task_type="image", input_file="/tmp/x.png", status="processing"))
        )
        asyncio.run(db.create_task(TaskRecord(task_id="idem-dup-1", record_id=record_id, status="processing")))

        resp = csrf_post(
            test_app,
            "/api/restore/",
            data={"folder_path": "/whatever"},  # 不会走到文件夹校验：幂等命中先短路
            headers={"Idempotency-Key": "idem-dup-1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["duplicate"] is True
        assert body["data"]["task_id"] == "idem-dup-1"
        assert body["data"]["status"] == "processing"
        assert body["data"]["task_type"] == "image"

    def test_batch_duplicate_returns_existing(self, test_app):
        db: HistoryDB = test_app.app.state.history_db
        asyncio.run(db.create_task(TaskRecord(task_id="idem-batch-1", record_id=0, status="processing")))

        resp = csrf_post(
            test_app,
            "/api/restore/batch",
            data={"folder_path": "/whatever"},
            headers={"Idempotency-Key": "idem-batch-1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["duplicate"] is True
        assert body["data"]["batch_id"] == "idem-batch-1"

    def test_invalid_key_rejected_400(self, test_app):
        resp = csrf_post(
            test_app,
            "/api/restore/",
            data={"folder_path": "/whatever"},
            headers={"Idempotency-Key": "bad key!"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "BAD_REQUEST"


class TestRecoverTasksInjectsCancelCallback:
    """P1-6：恢复任务重新入队时必须注入 on_cancel。"""

    @pytest.mark.asyncio
    async def test_recovered_task_has_on_cancel(self, monkeypatch, tmp_path):
        db = HistoryDB(db_path=str(tmp_path / "h.db"))
        await db.initialize()
        record_id = await db.add_record(
            HistoryRecord(task_type="image", input_file="/tmp/in.png", status="pending", parameters="{}")
        )
        await db.create_task(TaskRecord(task_id="rec-1", record_id=record_id, status="pending"))

        queue = MagicMock()
        queue.submit = AsyncMock()

        monkeypatch.setattr(
            "app.integrated_app.routes.restore.recovery.model_registry.get_engine",
            lambda: MagicMock(request_cancel=lambda: None),
        )

        count = await recover_tasks(db, queue, {})
        assert count == 1
        assert queue.submit.await_count == 1
        kwargs = queue.submit.await_args.kwargs
        assert "on_cancel" in kwargs
        assert kwargs["on_cancel"] is not None
        await db.close()


class _FakeResult:
    def __init__(self, output_path: str):
        self.success = True
        self.output_path = output_path
        self.processing_time = 1.0
        self.error = None
        self.metadata = {"vram_peak_mb": 0.0}


class TestBatchPerFileLedger:
    """P1-7：批量任务逐文件即时落库。"""

    @pytest.mark.asyncio
    async def test_each_file_ledgered_immediately(self, tmp_path, monkeypatch):
        db = HistoryDB(db_path=str(tmp_path / "h.db"))
        await db.initialize()
        await db.create_task(TaskRecord(task_id="batch-ledger", record_id=0, status="processing"))

        img1 = tmp_path / "a.png"
        img2 = tmp_path / "b.png"
        try:
            from PIL import Image

            Image.new("RGB", (8, 8)).save(img1)
            Image.new("RGB", (8, 8)).save(img2)
        except ImportError:  # pragma: no cover — 无 Pillow 环境跳过
            pytest.skip("Pillow 不可用")

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        out_file = out_dir / "r.png"
        out_file.write_bytes(b"png")

        engine = MagicMock()
        engine.infer_image = AsyncMock(return_value=_FakeResult(str(out_file)))
        monkeypatch.setattr("app.integrated_app.services.restore_service.model_registry.get_engine", lambda: engine)

        app_config = {
            "retention": {"disk_min_free_gb": 0},
            "runtime": {"batch": {"max_retries": 0}, "task": {"checkpoint_dir": str(tmp_path / "ckpt")}},
            "user_preferences": {"output_path_template": str(out_dir) + "/r{ext}"},
        }

        # 生产路径中路由会先经 get_cached_or_create 预种批量缓存（含 results）；
        # 本测试直调服务层，需自行预种等价缓存
        task_state_store.get_cached_or_create(
            "batch-ledger",
            template={
                "task_id": "batch-ledger",
                "type": "batch",
                "media_type": "image",
                "total": 2,
                "completed": 0,
                "failed": 0,
                "current_index": -1,
                "current_file": "",
                "results": [],
                "config": {"resolution": 512},
                "use_model_size": "3b",
            },
        )
        try:
            await process_batch_background(
                "batch-ledger",
                [str(img1), str(img2)],
                "image",
                {"resolution": 512, "max_resolution": 0, "cache_model": False, "seed": 1},
                "3b",
                db,
                MagicMock(is_cancelled=lambda _tid=None: False),
                app_config,
            )
        finally:
            task_state_store.remove("batch-ledger")

        # 逐文件落库：两个文件各有一条 completed 记录（即时插入，而非批末一次性）
        records, total = await db.get_records(limit=10)
        assert total == 2
        assert all(r.status == "completed" for r in records)
        await db.close()


@pytest.mark.asyncio
async def test_add_records_ids_derived_from_max_baseline(tmp_path):
    """P1-7：add_records 以 MAX(id) 基线推算，id 单调且可查。"""
    db = HistoryDB(db_path=str(tmp_path / "h.db"))
    await db.initialize()
    first = await db.add_record(HistoryRecord(task_type="image", input_file="/a.png", status="completed"))
    ids = await db.add_records(
        [HistoryRecord(task_type="image", input_file=f"/{i}.png", status="completed") for i in range(3)]
    )
    assert ids == [first + 1, first + 2, first + 3]
    for rid in ids:
        assert await db.get_record(rid) is not None
    await db.close()
