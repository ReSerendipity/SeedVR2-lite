"""FileCache 总大小上限淘汰测试（数据治理 P1-4）。

验收标准（对应评估报告 §9.2 P1-4）：
1. max_size_mb=0（默认）时 cleanup_oversize 不动作（向后兼容）；
2. 超限后按 mtime 最旧优先淘汰，直到回落到上限以内；
3. 淘汰后最新文件保留；
4. 上限以内无淘汰。
"""

import os
import time

import pytest

from app.integrated_app.cache import FileCache


@pytest.fixture
def cache_dir(tmp_path):
    d = tmp_path / "uploads"
    d.mkdir()
    return str(d)


def _make_file(cache_dir: str, name: str, size_bytes: int, mtime_offset: float = 0.0) -> str:
    path = os.path.join(cache_dir, name)
    with open(path, "wb") as f:
        f.write(b"\x00" * size_bytes)
    if mtime_offset:
        stamp = time.time() - mtime_offset
        os.utime(path, (stamp, stamp))
    return path


class TestCleanupOversize:
    def test_disabled_when_max_size_zero(self, cache_dir):
        """验收点 1：未配置上限时清理不动作。"""
        cache = FileCache(cache_dir=cache_dir, max_size_mb=0)
        _make_file(cache_dir, "a.bin", 4 * 1024 * 1024)
        assert cache.cleanup_oversize() == 0
        assert os.path.exists(os.path.join(cache_dir, "a.bin"))

    def test_evicts_oldest_first_until_under_limit(self, cache_dir):
        """验收点 2：超限按 mtime 最旧优先淘汰。"""
        cache = FileCache(cache_dir=cache_dir, max_size_mb=5)
        # 3 个文件各 3MB，总 9MB，上限 5MB → 至少淘汰 2 个（最旧的）
        _make_file(cache_dir, "old1.bin", 3 * 1024 * 1024, mtime_offset=3600 * 3)
        _make_file(cache_dir, "old2.bin", 3 * 1024 * 1024, mtime_offset=3600 * 2)
        _make_file(cache_dir, "newest.bin", 3 * 1024 * 1024, mtime_offset=0)

        evicted = cache.cleanup_oversize()
        assert evicted >= 1
        # 最旧文件必然被淘汰
        assert not os.path.exists(os.path.join(cache_dir, "old1.bin"))
        # 回落后总大小不超上限
        assert cache.get_cache_stats()["total_size_mb"] <= 5.0

    def test_newest_file_survives(self, cache_dir):
        """验收点 3：淘汰后最新文件保留。"""
        cache = FileCache(cache_dir=cache_dir, max_size_mb=2)
        _make_file(cache_dir, "old.bin", 2 * 1024 * 1024, mtime_offset=7200)
        _make_file(cache_dir, "new.bin", 1 * 1024 * 1024, mtime_offset=0)
        cache.cleanup_oversize()
        assert not os.path.exists(os.path.join(cache_dir, "old.bin"))
        assert os.path.exists(os.path.join(cache_dir, "new.bin"))

    def test_under_limit_no_eviction(self, cache_dir):
        """验收点 4：上限以内无淘汰。"""
        cache = FileCache(cache_dir=cache_dir, max_size_mb=100)
        _make_file(cache_dir, "f1.bin", 1024)
        _make_file(cache_dir, "f2.bin", 1024)
        assert cache.cleanup_oversize() == 0
        assert os.path.exists(os.path.join(cache_dir, "f1.bin"))
        assert os.path.exists(os.path.join(cache_dir, "f2.bin"))

    def test_subdirs_included(self, cache_dir):
        """子目录（image/video）中的文件计入总大小。"""
        cache = FileCache(cache_dir=cache_dir, max_size_mb=1)
        sub = os.path.join(cache_dir, "image")
        os.makedirs(sub)
        p = os.path.join(sub, "x.png")
        with open(p, "wb") as f:
            f.write(b"\x00" * (2 * 1024 * 1024))
        assert cache.cleanup_oversize() == 1
        assert not os.path.exists(p)
