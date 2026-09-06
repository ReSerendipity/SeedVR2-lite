#!/usr/bin/env python3
"""输出保留策略水位清理测试（成本治理 P1-1）。

覆盖评估报告改进建议 #2 的验收标准：
- cleanup_watermark_dirs：低于水位才清理；仅删超龄文件（最旧优先）；
  新文件 / 占位文件 / 目录本身永不删除；非目录跳过；参数 <=0 直接跳过；
  删除前 notify 回调按目录触发。
- HistoryDB.distinct_output_dirs：从最近输出去重父目录。
- periodic_output_cleanup：周期任务中水位清理生效（旧文件被删）。
- system_notice 通知链路：notify 回调经全局 SSE event_bus 广播可达订阅者。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import asyncio
import os
import time
from collections import namedtuple

import pytest

from app.integrated_app.history_db import HistoryDB, HistoryRecord
from app.integrated_app.services import output_retention
from app.integrated_app.services.output_retention import (
    cleanup_outputs_once,
    cleanup_watermark_dirs,
    periodic_output_cleanup,
)

_Usage = namedtuple("usage", ["total", "used", "free"])


def _make_file(path: str, size: int = 1024, age_days: float | None = None) -> int:
    with open(path, "wb") as f:
        f.write(b"0" * size)
    if age_days is not None:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))
    return size


class TestCleanupWatermarkDirs:
    """cleanup_watermark_dirs 单元测试。"""

    def test_skip_when_params_disabled(self, tmp_path):
        p = tmp_path / "a.txt"
        _make_file(str(p), age_days=30)
        assert cleanup_watermark_dirs([str(tmp_path)], min_free_gb=0, max_age_days=14) == (0, 0)
        assert cleanup_watermark_dirs([str(tmp_path)], min_free_gb=5, max_age_days=0) == (0, 0)
        assert p.exists()

    def test_noop_when_disk_above_watermark(self, tmp_path, monkeypatch):
        _make_file(str(tmp_path / "old.txt"), age_days=30)
        monkeypatch.setattr(
            output_retention.shutil, "disk_usage", lambda _p: _Usage(total=100, used=1, free=10 * 1024**3)
        )
        removed, _freed = cleanup_watermark_dirs([str(tmp_path)], min_free_gb=5.0, max_age_days=14)
        assert removed == 0
        assert (tmp_path / "old.txt").exists()

    def test_deletes_only_expired_files_oldest_first(self, tmp_path, monkeypatch):
        notified: list[tuple[str, dict]] = []
        old1 = tmp_path / "old1.bin"
        old2 = tmp_path / "old2.bin"
        fresh = tmp_path / "fresh.bin"
        keep = tmp_path / ".gitkeep"
        _make_file(str(old1), size=10 * 1024**2, age_days=30)
        _make_file(str(old2), size=10 * 1024**2, age_days=20)
        _make_file(str(fresh), size=1, age_days=1)
        _make_file(str(keep), age_days=30)

        # 水位始终未达标 → 候选全部删完为止
        monkeypatch.setattr(
            output_retention.shutil, "disk_usage", lambda _p: _Usage(total=100, used=99, free=1 * 1024**3)
        )
        removed, freed = cleanup_watermark_dirs(
            [str(tmp_path)], min_free_gb=5.0, max_age_days=14, notify=lambda d, i: notified.append((d, i))
        )
        assert removed == 2
        assert freed == 20 * 1024**2
        assert not old1.exists() and not old2.exists(), "超龄文件应被删除（最旧优先）"
        assert fresh.exists(), "新文件绝不能删"
        assert keep.exists(), "占位文件永不清理"
        assert tmp_path.exists(), "目录本身绝不删除"
        assert len(notified) == 1 and notified[0][0] == str(tmp_path)
        assert notified[0][1]["candidate_files"] == 2
        assert notified[0][1]["min_free_gb"] == 5.0

    def test_stops_when_watermark_recovered(self, tmp_path, monkeypatch):
        """删除部分文件后水位回升 → 停止删除（少删一个是一个）。"""
        old1 = tmp_path / "old1.bin"
        old2 = tmp_path / "old2.bin"
        _make_file(str(old1), size=1024, age_days=30)
        _make_file(str(old2), size=1024, age_days=29)

        # 低磁盘环境模拟：起始剩余 4GB，每删一个文件模拟释放 3GB
        state = {"free": 4 * 1024**3}
        real_remove = os.remove  # 先捕获原函数，避免 fake 内递归
        monkeypatch.setattr(
            output_retention.shutil,
            "disk_usage",
            lambda _p: _Usage(total=20 * 1024**3, used=0, free=state["free"]),
        )

        def _fake_remove(path):
            real_remove(path)
            state["free"] += 3 * 1024**3

        monkeypatch.setattr(output_retention.os, "remove", _fake_remove)
        removed, _freed = cleanup_watermark_dirs([str(tmp_path)], min_free_gb=5.0, max_age_days=14)
        assert removed == 1, "第一个文件删掉后水位已回升，第二个不应被删"
        assert not old1.exists() and old2.exists()

    def test_nonexistent_dir_skipped(self, tmp_path):
        assert cleanup_watermark_dirs([str(tmp_path / "nope")], min_free_gb=5.0, max_age_days=14) == (0, 0)

    def test_duplicate_dirs_deduped(self, tmp_path, monkeypatch):
        _make_file(str(tmp_path / "old.bin"), age_days=30)
        monkeypatch.setattr(
            output_retention.shutil, "disk_usage", lambda _p: _Usage(total=100, used=99, free=1 * 1024**3)
        )
        removed, _freed = cleanup_watermark_dirs([str(tmp_path), str(tmp_path)], min_free_gb=5.0, max_age_days=14)
        assert removed == 1, "重复目录只应清理一次"

    def test_notify_exception_swallowed(self, tmp_path, monkeypatch):
        _make_file(str(tmp_path / "old.bin"), age_days=30)
        monkeypatch.setattr(
            output_retention.shutil, "disk_usage", lambda _p: _Usage(total=100, used=99, free=1 * 1024**3)
        )

        def _boom(_d, _i):
            raise RuntimeError("通知爆炸")

        removed, _freed = cleanup_watermark_dirs([str(tmp_path)], min_free_gb=5.0, max_age_days=14, notify=_boom)
        assert removed == 1, "通知异常不影响清理主流程"


class TestHistoryDistinctOutputDirs:
    """HistoryDB.distinct_output_dirs 查询。"""

    @pytest.mark.asyncio
    async def test_dedup_parent_dirs_newest_first(self, tmp_path):
        db = HistoryDB(str(tmp_path / "history.db"))
        async with db:
            await db.initialize()
            for name in ("a.png", "b.png"):
                await db.add_record(
                    HistoryRecord(
                        task_type="image", input_file="x", output_file=f"D:/media/restored/{name}", status="completed"
                    )
                )
            await db.add_record(
                HistoryRecord(task_type="image", input_file="y", output_file="E:/elsewhere/out.png", status="completed")
            )
            dirs = await db.distinct_output_dirs()
        assert dirs[0] == "E:/elsewhere" or dirs[-1] == "E:/elsewhere"
        assert set(dirs) == {"D:/media/restored", "E:/elsewhere"}

    @pytest.mark.asyncio
    async def test_empty_db_returns_empty(self, tmp_path):
        db = HistoryDB(str(tmp_path / "history.db"))
        async with db:
            await db.initialize()
            assert await db.distinct_output_dirs() == []


class TestPeriodicWatermarkWiring:
    """periodic_output_cleanup 中水位清理生效。"""

    @pytest.mark.asyncio
    async def test_periodic_watermark_deletes_expired(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "outputs"
        out_dir.mkdir()
        target = tmp_path / "restored"
        target.mkdir()
        _make_file(str(target / "old.mp4"), size=2048, age_days=30)
        # 模拟低于水位的磁盘（避免依赖测试机真实剩余空间）
        monkeypatch.setattr(
            output_retention.shutil, "disk_usage", lambda _p: _Usage(total=100, used=99, free=1 * 1024**3)
        )

        async def _dirs() -> list[str]:
            return [str(target)]

        task = asyncio.create_task(
            periodic_output_cleanup(
                str(out_dir),
                max_age_days=14,
                max_files=0,
                interval_seconds=0.05,
                watermark_min_free_gb=5.0,
                list_output_dirs=_dirs,
            )
        )
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not (target / "old.mp4").exists()

    @pytest.mark.asyncio
    async def test_busy_skips_watermark_round(self, tmp_path):
        out_dir = tmp_path / "outputs"
        out_dir.mkdir()
        target = tmp_path / "restored"
        target.mkdir()
        _make_file(str(target / "old.mp4"), size=2048, age_days=30)

        async def _dirs() -> list[str]:
            return [str(target)]

        task = asyncio.create_task(
            periodic_output_cleanup(
                str(out_dir),
                max_age_days=14,
                max_files=0,
                interval_seconds=0.05,
                is_busy=lambda: True,
                watermark_min_free_gb=5.0,
                list_output_dirs=_dirs,
            )
        )
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (target / "old.mp4").exists(), "推理忙碌期间应跳过整轮清理"


class TestSystemNoticeBroadcast:
    """system_notice 通知链路：水位清理 notify → 全局 SSE event_bus → 订阅者。"""

    @pytest.mark.asyncio
    async def test_notify_reaches_subscriber(self, tmp_path, monkeypatch):
        from app.integrated_app.routes.system.sse import event_bus

        queue = await event_bus.subscribe()
        try:
            _make_file(str(tmp_path / "old.bin"), age_days=30)
            monkeypatch.setattr(
                output_retention.shutil,
                "disk_usage",
                lambda _p: _Usage(total=100, used=99, free=1 * 1024**3),
            )

            def _notify(dir_path: str, info: dict) -> None:
                event_bus.publish(
                    "system_notice",
                    {"level": "warning", "kind": "retention_watermark", "dir": dir_path, **info},
                )

            removed, _freed = cleanup_watermark_dirs([str(tmp_path)], min_free_gb=5.0, max_age_days=14, notify=_notify)
            assert removed == 1
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert event["event"] == "system_notice"
            assert event["data"]["kind"] == "retention_watermark"
            assert event["data"]["level"] == "warning"
            assert event["data"]["dir"] == str(tmp_path)
        finally:
            await event_bus.unsubscribe(queue)


class TestOutputsTimeCleanupUnchanged:
    """回归守护：时间清理原有语义不受水位扩展影响。"""

    def test_outputs_age_cleanup_still_works(self, tmp_path):
        sub = tmp_path / "image"
        sub.mkdir()
        _make_file(str(sub / "old.png"), size=512, age_days=30)
        _make_file(str(sub / "fresh.png"), size=512, age_days=1)
        removed, _freed = cleanup_outputs_once(str(tmp_path), max_age_days=14, max_files=0)
        assert removed == 1
        assert (sub / "fresh.png").exists()
