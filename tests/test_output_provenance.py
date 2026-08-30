"""输出溯源测试（数据治理 P3-1）。

验收标准（对应评估报告 §9.2 P3-1）：
1. 水印可携带 task_id payload 并在提取时还原（输出 → 任务可关联）；
2. history_db.find_by_output_file 可按输出文件反查记录；
3. GET /api/system/history/resolve 支持 output_file / task_id /
   watermark_payload 三种入口，未命中返回 found=false。
"""

import json

import numpy as np
import pytest

from app.integrated_app.history_db import HistoryRecord
from app.integrated_app.security.watermark import embed_watermark, extract_watermark


@pytest.fixture
def sample_image() -> np.ndarray:
    rng = np.random.default_rng(42)  # nosec B311 — 测试用确定性图像
    return rng.integers(0, 255, (256, 256, 3), dtype=np.uint8)


class TestWatermarkPayloadBinding:
    def test_embed_task_id_and_extract(self, sample_image):
        """验收点 1：task_id 作为 payload 嵌入后可提取还原。"""
        task_id = "task-abc123"
        watermarked = embed_watermark(sample_image.copy(), payload=task_id)
        extracted = extract_watermark(watermarked)
        assert extracted is not None
        assert task_id in str(extracted), f"水印未携带 task_id: {extracted}"

    def test_watermark_preserves_image_quality(self, sample_image):
        """水印不可感知：嵌入后 PSNR 应高于 40dB。"""
        from app.integrated_app.utils.image_metrics import psnr

        watermarked = embed_watermark(sample_image.copy(), payload="task-xyz")
        assert psnr(sample_image, watermarked) > 40.0


class TestFindByOutputFile:
    @pytest.mark.asyncio
    async def test_reverse_lookup_by_output_file(self, tmp_path):
        """验收点 2：按输出文件精确反查（取最新一条）。"""
        from app.integrated_app.history_db import HistoryDB

        async with HistoryDB(str(tmp_path / "h.db")) as db:
            rid1 = await db.add_record(
                HistoryRecord(task_type="image", input_file="a.png", status="completed", output_file="out/a_old.png")
            )
            rid2 = await db.add_record(
                HistoryRecord(task_type="image", input_file="b.png", status="completed", output_file="out/a.png")
            )
            found = await db.find_by_output_file("out/a.png")
            assert found is not None and found.id == rid2 and found.input_file == "b.png"
            assert await db.find_by_output_file("not-exist.png") is None
            assert await db.find_by_output_file("") is None
            assert rid1 and rid2


class TestResolveEndpoint:
    @pytest.mark.asyncio
    async def test_resolve_by_output_file(self, tmp_path):
        """验收点 3：output_file 入口命中。"""
        from app.integrated_app.dependencies import get_history_db
        from app.integrated_app.history_db import HistoryDB
        from app.integrated_app.routes.system.history import resolve_output_provenance

        async with HistoryDB(str(tmp_path / "h.db")) as db:
            await db.add_record(
                HistoryRecord(
                    task_type="video",
                    input_file="in.mp4",
                    status="completed",
                    output_file="out/v.mp4",
                    parameters='{"seed": 42}',
                )
            )
            resp = await resolve_output_provenance(history_db=db, output_file="out/v.mp4")
            data = json.loads(resp.body)["data"]
            assert data["found"] is True
            assert data["record"]["input_file"] == "in.mp4"
            assert data["record"]["parameters"] == '{"seed": 42}'
            assert get_history_db is not None

    @pytest.mark.asyncio
    async def test_resolve_by_task_id_and_watermark_payload(self, tmp_path):
        """验收点 3：task_id / watermark_payload 入口命中（水印载荷即 task_id）。"""
        from app.integrated_app.history_db import HistoryDB, TaskRecord
        from app.integrated_app.routes.system.history import resolve_output_provenance

        async with HistoryDB(str(tmp_path / "h2.db")) as db:
            rid = await db.add_record(
                HistoryRecord(task_type="image", input_file="c.png", status="completed", output_file="out/c.png")
            )
            await db.create_task(TaskRecord(task_id="task-777", record_id=rid, status="completed"))

            by_task = json.loads((await resolve_output_provenance(history_db=db, task_id="task-777")).body)["data"]
            assert by_task["found"] is True
            assert by_task["task"]["task_id"] == "task-777"

            by_payload = json.loads(
                (await resolve_output_provenance(history_db=db, watermark_payload="task-777")).body
            )["data"]
            assert by_payload["found"] is True

            missing = json.loads((await resolve_output_provenance(history_db=db, task_id="task-none")).body)["data"]
            assert missing["found"] is False
