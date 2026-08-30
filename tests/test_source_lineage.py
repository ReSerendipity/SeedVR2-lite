"""源文件内容寻址血缘测试（数据治理 P1-1）。

验收标准（对应评估报告 §9.2 P1-1）：
1. HistoryRecord 支持 input_sha256 字段，落库/读回无损；
2. v1 旧库迁移后自动补齐 input_sha256 列（P0-2 迁移框架生效）；
3. 哈希工具：字节/文件摘要正确、与 hashlib 直接计算一致；
4. 文件不可读时哈希返回空串（血缘缺失不阻断业务）。
"""

import hashlib

import aiosqlite
import pytest
import pytest_asyncio

from app.integrated_app.history_db import SCHEMA_VERSION, HistoryDB, HistoryRecord
from app.integrated_app.utils.hashing import compute_bytes_sha256, compute_file_sha256


@pytest_asyncio.fixture
async def tmp_db(tmp_path):
    db = HistoryDB(str(tmp_path / "history.db"))
    await db.initialize()
    yield db
    await db.close()


class TestInputSha256Roundtrip:
    @pytest.mark.asyncio
    async def test_record_roundtrip_with_sha256(self, tmp_db: HistoryDB):
        """验收点 1：input_sha256 落库/读回无损。"""
        digest = hashlib.sha256(b"seedvr2-lineage").hexdigest()
        record_id = await tmp_db.add_record(
            HistoryRecord(task_type="image", input_file="a.png", status="pending", input_sha256=digest)
        )
        record = await tmp_db.get_record(record_id)
        assert record is not None
        assert record.input_sha256 == digest

    @pytest.mark.asyncio
    async def test_record_without_sha256_defaults_empty(self, tmp_db: HistoryDB):
        record_id = await tmp_db.add_record(HistoryRecord(task_type="image", input_file="b.png", status="pending"))
        record = await tmp_db.get_record(record_id)
        assert record is not None
        assert record.input_sha256 == ""

    @pytest.mark.asyncio
    async def test_update_record_accepts_sha256(self, tmp_db: HistoryDB):
        digest = "f" * 64
        record_id = await tmp_db.add_record(HistoryRecord(task_type="video", input_file="c.mp4", status="pending"))
        assert await tmp_db.update_record(record_id, input_sha256=digest)
        record = await tmp_db.get_record(record_id)
        assert record is not None and record.input_sha256 == digest

    @pytest.mark.asyncio
    async def test_v1_legacy_db_gets_input_sha256_column(self, tmp_path):
        """验收点 2：v1 旧库（有 output_size_bytes/vram_peak_mb，无 input_sha256）迁移补列。"""
        path = str(tmp_path / "v1legacy.db")
        async with aiosqlite.connect(path) as raw:
            await raw.execute("PRAGMA user_version=1")
            await raw.execute("""CREATE TABLE history (
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
                       vram_peak_mb REAL DEFAULT 0.0
                   )""")
            await raw.execute(
                "INSERT INTO history (task_type, input_file, status, created_at) VALUES ('image', 'old.png', 'completed', '2026-01-01')"
            )
            await raw.commit()

        async with HistoryDB(path) as db:
            assert await db.get_schema_version() == SCHEMA_VERSION
            record = await db.get_record(1)
            assert record is not None
            assert record.input_sha256 == ""  # 旧记录无哈希，兜底空串

        async with aiosqlite.connect(path) as raw:
            cursor = await raw.execute("PRAGMA table_info(history)")
            cols = {row[1] for row in await cursor.fetchall()}
        assert "input_sha256" in cols


class TestHashingUtils:
    def test_bytes_sha256_matches_hashlib(self):
        """验收点 3：字节摘要与 hashlib 直接计算一致。"""
        data = b"seedvr2" * 1000
        assert compute_bytes_sha256(data) == hashlib.sha256(data).hexdigest()

    def test_file_sha256_matches_hashlib(self, tmp_path):
        p = tmp_path / "sample.bin"
        payload = b"\x00\x01\x02" * 3 * 1024 * 1024  # 9MB，跨多个 8MB 分块
        p.write_bytes(payload)
        assert compute_file_sha256(str(p)) == hashlib.sha256(payload).hexdigest()

    def test_missing_file_returns_empty_string(self, tmp_path):
        """验收点 4：文件不可读返回空串，不抛异常。"""
        assert compute_file_sha256(str(tmp_path / "not-exist.bin")) == ""
