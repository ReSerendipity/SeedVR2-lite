#!/usr/bin/env python3
"""pinned「标记保留」与清理豁免测试（数据治理 P1-5）。

验收标准（评估报告 P1-5）：
1. schema 迁移 v3 为旧库补 pinned 列
2. set_record_pinned / get_pinned_output_paths 语义
3. cleanup_outputs_once 年龄/数量规则均跳过 pinned 输出
4. plan_cleanup_outputs 只规划不删除，且豁免 pinned
5. POST /api/system/history/{id}/pin API（CSRF 保护下）

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import sqlite3
import time

import pytest

from app.integrated_app.history_db import HistoryDB, HistoryRecord
from app.integrated_app.services.output_retention import cleanup_outputs_once, plan_cleanup_outputs


def _make_file(path, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 128)
    if mtime is not None:
        import os

        os.utime(path, (mtime, mtime))


@pytest.mark.asyncio
class TestPinnedMigration:
    """迁移 v2 → v3：旧库自动补 pinned 列。"""

    async def test_migration_v2_adds_pinned_column(self, tmp_path):
        db_path = str(tmp_path / "v2.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""CREATE TABLE history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                input_file TEXT NOT NULL,
                output_file TEXT DEFAULT '',
                model_size TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                parameters TEXT DEFAULT '{}',
                processing_time REAL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                error_message TEXT DEFAULT '',
                output_size_bytes INTEGER DEFAULT 0,
                vram_peak_mb REAL DEFAULT 0.0,
                input_sha256 TEXT DEFAULT ''
            )""")
        conn.execute("PRAGMA user_version=2")
        conn.commit()
        conn.close()

        async with HistoryDB(db_path=db_path) as db:
            assert await db.get_schema_version() == 3
            rid = await db.add_record(HistoryRecord(task_type="image", input_file="a.png", status="completed"))
            record = await db.get_record(rid)
            assert record.pinned is False
            assert await db.set_record_pinned(rid, True) is True
            assert (await db.get_record(rid)).pinned is True


@pytest.mark.asyncio
class TestPinnedSemantics:
    """set_record_pinned / get_pinned_output_paths。"""

    async def test_set_and_unset_pinned(self, tmp_path):
        async with HistoryDB(db_path=str(tmp_path / "history.db"), max_records=0) as db:
            rid = await db.add_record(
                HistoryRecord(
                    task_type="image", input_file="a.png", output_file="outputs/image/a.png", status="completed"
                )
            )
            assert await db.get_pinned_output_paths() == set()

            assert await db.set_record_pinned(rid, True)
            assert await db.get_pinned_output_paths() == {"outputs/image/a.png"}

            assert await db.set_record_pinned(rid, False)
            assert await db.get_pinned_output_paths() == set()

    async def test_pinned_nonexistent_record_returns_false(self, tmp_path):
        async with HistoryDB(db_path=str(tmp_path / "history.db"), max_records=0) as db:
            assert await db.set_record_pinned(999, True) is False

    async def test_pinned_paths_dedup_and_skip_empty(self, tmp_path):
        async with HistoryDB(db_path=str(tmp_path / "history.db"), max_records=0) as db:
            r1 = await db.add_record(
                HistoryRecord(
                    task_type="image", input_file="a.png", output_file="outputs/image/a.png", status="completed"
                )
            )
            r2 = await db.add_record(
                HistoryRecord(
                    task_type="image", input_file="b.png", output_file="outputs/image/a.png", status="completed"
                )
            )
            r3 = await db.add_record(
                HistoryRecord(task_type="image", input_file="c.png", output_file="", status="failed")
            )
            for rid in (r1, r2, r3):
                await db.set_record_pinned(rid, True)

            assert await db.get_pinned_output_paths() == {"outputs/image/a.png"}


class TestCleanupSkipsPinned:
    """retention 清理豁免 pinned 输出。"""

    def test_age_rule_skips_pinned(self, tmp_path):
        now = time.time()
        pinned_file = tmp_path / "image" / "pinned.png"
        stale_file = tmp_path / "image" / "stale.png"
        _make_file(pinned_file, mtime=now - 30 * 86400)
        _make_file(stale_file, mtime=now - 30 * 86400)

        removed, _freed = cleanup_outputs_once(
            str(tmp_path), max_age_days=14, max_files=0, keep_paths={str(pinned_file)}
        )

        assert removed == 1
        assert pinned_file.exists()
        assert not stale_file.exists()

    def test_max_files_rule_skips_pinned(self, tmp_path):
        now = time.time()
        paths = []
        for i in range(4):
            p = tmp_path / "image" / f"img_{i}.png"
            _make_file(p, mtime=now - (10 - i) * 3600)
            paths.append(p)

        # 上限 2，最旧的 img_0/img_1 应删；pinned 的 img_0 豁免后改为删 img_2
        removed, _freed = cleanup_outputs_once(str(tmp_path), max_age_days=0, max_files=2, keep_paths={str(paths[0])})

        assert removed == 2
        assert paths[0].exists()
        assert not paths[1].exists()
        assert not paths[2].exists()
        assert paths[3].exists()


class TestPlanCleanupOutputs:
    """plan_cleanup_outputs 只规划不删除。"""

    def test_plan_lists_victims_without_deleting(self, tmp_path):
        now = time.time()
        old = tmp_path / "image" / "old.png"
        new = tmp_path / "image" / "new.png"
        _make_file(old, mtime=now - 30 * 86400)
        _make_file(new, mtime=now)

        victims = plan_cleanup_outputs(str(tmp_path), max_age_days=14, max_files=0)

        assert victims == [str(old)]
        assert old.exists()
        assert new.exists()

    def test_plan_respects_keep_paths(self, tmp_path):
        now = time.time()
        old = tmp_path / "image" / "old.png"
        _make_file(old, mtime=now - 30 * 86400)

        victims = plan_cleanup_outputs(str(tmp_path), max_age_days=14, max_files=0, keep_paths={str(old)})

        assert victims == []
        assert old.exists()

    def test_plan_disabled_rules_noop(self, tmp_path):
        assert plan_cleanup_outputs(str(tmp_path), max_age_days=0, max_files=0) == []


class TestPinApi:
    """POST /api/system/history/{id}/pin 端点契约（CSRF 保护 + 404）。"""

    def test_pin_missing_record_returns_404(self, test_app):
        from tests.conftest import csrf_post

        resp = csrf_post(test_app, "/api/system/history/999999/pin", json={"pinned": True})
        assert resp.status_code == 404

    def test_pin_requires_csrf(self, test_app):
        # 缺 X-CSRF-Token header 的 POST 应被 CSRF 中间件拒绝
        resp = test_app.post("/api/system/history/1/pin", json={"pinned": True})
        assert resp.status_code in (403, 400)
