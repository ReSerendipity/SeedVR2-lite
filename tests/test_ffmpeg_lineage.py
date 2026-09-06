#!/usr/bin/env python3
"""ffmpeg 版本血缘测试（数据治理 P1-2）。

验收标准（评估报告 P1-2）：
1. get_ffmpeg_version 进程级缓存一次探测，失败返回空串不影响主流程
2. _lineage_parameters 仅视频任务注入 ffmpeg_version，图像任务原样透传
3. 注入合并进现有参数 JSON（不破坏既有血缘字段）
4. 批量链路（restore_service）视频记录同样注入

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import json

import pytest

import app.integrated_app.video_processor as vp
from app.integrated_app.routes.restore.upload import _lineage_parameters


@pytest.fixture(autouse=True)
def _clear_version_cache():
    vp._FFMPEG_VERSION_CACHE.clear()
    yield
    vp._FFMPEG_VERSION_CACHE.clear()


class TestGetFfmpegVersion:
    def test_returns_first_line_and_caches_once(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)

            class _R:
                returncode = 0
                stdout = "ffmpeg version 7.1-full_build-www.gyan.dev\nbuilt with: gcc 13.2.0"

            return _R()

        monkeypatch.setattr(vp.subprocess, "run", fake_run)

        v1 = vp.get_ffmpeg_version("/fake/ffmpeg")
        v2 = vp.get_ffmpeg_version("/fake/ffmpeg")

        assert v1.startswith("ffmpeg version 7.1")
        assert "\n" not in v1
        assert v2 == v1
        assert len(calls) == 1  # 进程级缓存：同一路径只探测一次

    def test_failure_returns_empty_without_raising(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise OSError("no such file")

        monkeypatch.setattr(vp.subprocess, "run", fake_run)

        assert vp.get_ffmpeg_version("/missing/ffmpeg") == ""


class TestLineageParameters:
    def test_video_injects_ffmpeg_version(self, monkeypatch):
        monkeypatch.setattr(vp, "get_ffmpeg_version", lambda path=None: "ffmpeg version 7.1-test")

        result = json.loads(_lineage_parameters('{"seed": 42}', "video"))

        assert result["ffmpeg_version"] == "ffmpeg version 7.1-test"
        assert result["seed"] == 42

    def test_image_task_passthrough(self, monkeypatch):
        # 图像链路不经过 ffmpeg：即使版本可用也不注入
        monkeypatch.setattr(vp, "get_ffmpeg_version", lambda path=None: "ffmpeg version 7.1-test")

        original = '{"resolution": 2048}'
        assert _lineage_parameters(original, "image") == original

    def test_unavailable_ffmpeg_passthrough(self, monkeypatch):
        monkeypatch.setattr(vp, "get_ffmpeg_version", lambda path=None: "")

        original = '{"seed": 1}'
        assert _lineage_parameters(original, "video") == original

    def test_invalid_json_passthrough(self, monkeypatch):
        monkeypatch.setattr(vp, "get_ffmpeg_version", lambda path=None: "ffmpeg version 7.1-test")

        assert _lineage_parameters("not-json", "video") == "not-json"


@pytest.mark.asyncio
async def test_batch_video_record_injects_ffmpeg_version(tmp_path, monkeypatch):
    """批量链路：apply_ffmpeg_lineage 注入后落库的 video 记录含 ffmpeg_version。"""
    from app.integrated_app.history_db import HistoryDB, HistoryRecord
    from app.integrated_app.services.restore_service import apply_ffmpeg_lineage

    monkeypatch.setattr(vp, "get_ffmpeg_version", lambda path=None: "ffmpeg version 7.1-batch")

    # 模拟批量路径：降级 JSON 先写入，血缘注入在后（不被覆盖）
    batch_parameters_json = json.dumps({"degradation": {"degraded": True}}, ensure_ascii=False)
    batch_parameters_json = apply_ffmpeg_lineage(batch_parameters_json, "video")

    async with HistoryDB(db_path=str(tmp_path / "history.db"), max_records=0) as db:
        rid = await db.add_record(
            HistoryRecord(task_type="video", input_file="a.mp4", status="completed", parameters=batch_parameters_json)
        )
        record = await db.get_record(rid)

    data = json.loads(record.parameters)
    assert data["ffmpeg_version"] == "ffmpeg version 7.1-batch"
    assert data["degradation"]["degraded"] is True  # 既有血缘字段不被破坏


def test_batch_image_record_skips_injection(monkeypatch):
    from app.integrated_app.services.restore_service import apply_ffmpeg_lineage

    monkeypatch.setattr(vp, "get_ffmpeg_version", lambda path=None: "ffmpeg version 7.1-batch")

    assert apply_ffmpeg_lineage('{"seed": 1}', "image") == '{"seed": 1}'
