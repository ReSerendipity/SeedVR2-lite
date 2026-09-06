#!/usr/bin/env python3
"""DB 迁移前自动备份测试（数据治理 P2-4）。

验收标准（评估报告 P2-4）：
1. 旧库（有 history 表且 user_version < 代码版本）初始化时自动生成
   ``{db_path}.bak-v{n}`` 一致性快照，且迁移正常推进
2. 全新空库不产生备份文件
3. 同版本重复启动不覆盖既有备份

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import sqlite3

import pytest

from app.integrated_app.history_db import HistoryDB, HistoryRecord


def _create_v2_legacy_db(db_path: str) -> None:
    """构造 v2 旧库（当前代码版本的上一档，含真实数据）。"""
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
    conn.execute(
        "INSERT INTO history (task_type, input_file, status, created_at) VALUES ('image', 'legacy.png', 'completed', '2026-01-01T00:00:00')"
    )
    conn.execute("PRAGMA user_version=2")
    conn.commit()
    conn.close()


@pytest.mark.asyncio
class TestMigrationBackup:
    async def test_backup_created_on_upgrade(self, tmp_path):
        db_path = str(tmp_path / "history.db")
        _create_v2_legacy_db(db_path)

        async with HistoryDB(db_path=db_path) as db:
            # 迁移正常推进到最新版
            assert await db.get_schema_version() >= 3
            # 旧数据在升级后仍可读
            record = (await db.get_records_filtered(status="completed"))[0]
            assert record.input_file == "legacy.png"

        backup_path = db_path + ".bak-v2"
        assert __import__("os").path.exists(backup_path)
        # 备份是合法 SQLite 库且包含旧数据
        conn = sqlite3.connect(backup_path)
        count = conn.execute("SELECT COUNT(*) FROM history WHERE input_file='legacy.png'").fetchone()[0]
        conn.close()
        assert count == 1

    async def test_fresh_db_no_backup(self, tmp_path):
        db_path = str(tmp_path / "fresh.db")

        async with HistoryDB(db_path=db_path) as db:
            await db.add_record(HistoryRecord(task_type="image", input_file="a.png", status="completed"))

        assert not __import__("os").path.exists(db_path + ".bak-v0")
        assert not __import__("os").path.exists(db_path + ".bak-v3")

    async def test_existing_backup_not_overwritten(self, tmp_path):
        db_path = str(tmp_path / "history.db")
        _create_v2_legacy_db(db_path)
        # 预置一个备份占位（模拟上次升级已备份）
        sentinel = tmp_path / "history.db.bak-v2"
        sentinel.write_bytes(b"sentinel-backup")

        async with HistoryDB(db_path=db_path):
            pass

        # 已存在的备份未被 VACUUM INTO 覆盖
        assert sentinel.read_bytes() == b"sentinel-backup"
