"""FileCache 单元测试

覆盖缓存模块的文件操作与 TTL 清理。
使用 tmp_path 隔离文件系统。
（P2-10：LRUCache/AdaptiveLRUCache 已作为零引用死代码移除，测试同步移除。）
"""

import os
import time

from app.integrated_app.cache import FileCache

# ---------------------------------------------------------------------------
# FileCache
# ---------------------------------------------------------------------------


class TestFileCache:
    """FileCache 文件缓存管理器测试"""

    def test_generate_unique_filename_preserves_extension(self, tmp_path):
        cache = FileCache(str(tmp_path))
        name = cache.generate_unique_filename("photo.png")
        assert name.endswith(".png")

    def test_generate_unique_filename_no_extension(self, tmp_path):
        cache = FileCache(str(tmp_path))
        name = cache.generate_unique_filename("README")
        assert name.endswith(".bin")

    def test_generate_unique_filename_is_unique(self, tmp_path):
        cache = FileCache(str(tmp_path))
        names = {cache.generate_unique_filename("a.png") for _ in range(20)}
        assert len(names) == 20

    def test_generate_unique_filename_keeps_readable_stem(self, tmp_path):
        """必须保留原始词干：否则历史记录里全是时间戳+哈希，用户认不出是哪张图。"""
        cache = FileCache(str(tmp_path))
        name = cache.generate_unique_filename("猫图 最终版.JPG")
        assert "猫图 最终版" in name
        assert name.endswith(".jpg")  # 扩展名归一为小写
        # 结构应为 <epoch>_<词干>_<随机6位><扩展名>
        head, _, tail = name[: -len(".jpg")].rpartition("_")
        assert head.split("_", 1)[0].isdigit()
        assert len(tail) == 6

    def test_generate_unique_filename_strips_path_and_dotdot(self, tmp_path):
        """返回值会参与缓存路径拼接，必须杜绝目录分隔符与相对路径片段。"""
        cache = FileCache(str(tmp_path))
        hostile = [
            "../../etc/passwd.png",
            "/absolute/path/视频.mov",
            "..",
            "....png",
            "evil.a..b..c.png",
            'x< >:"|?*.mp4',
            "weird\x00null.jpg",
            "",
        ]
        for raw in hostile:
            name = cache.generate_unique_filename(raw)
            assert "/" not in name and "\\" not in name, f"{raw!r} -> {name!r} 含路径分隔符"
            assert ".." not in name, f"{raw!r} -> {name!r} 含 .."
            assert not name.endswith((".", " ")), f"{raw!r} -> {name!r} 结尾点/空格"
            assert len(name) <= 80, f"{raw!r} -> {name!r} 过长"

    def test_generate_unique_filename_rejects_bad_extension(self, tmp_path):
        """扩展名必须是 .1~5位字母数字，否则退回 .bin（防 `.png/../../x` 之类）。"""
        cache = FileCache(str(tmp_path))
        assert cache.generate_unique_filename("a.png/../..").endswith(".bin")
        assert cache.generate_unique_filename("a.verylongext").endswith(".bin")

    def test_generate_unique_filename_no_collision_same_second(self, tmp_path):
        """随机后缀从 12 位缩到 6 位后，必须重新证明同秒同名不碰撞。"""
        cache = FileCache(str(tmp_path))
        names = [cache.generate_unique_filename("same.png") for _ in range(2000)]
        assert len(set(names)) == 2000

    def test_get_cache_path(self, tmp_path):
        cache = FileCache(str(tmp_path))
        path = cache.get_cache_path("file.txt")
        assert path == os.path.join(str(tmp_path), "file.txt")

    def test_save_bytes(self, tmp_path):
        cache = FileCache(str(tmp_path))
        data = b"hello world"
        name, path = cache.save_bytes(data, "test.jpg")
        assert os.path.exists(path)
        with open(path, "rb") as f:
            assert f.read() == data
        assert name.endswith(".jpg")

    def test_save_bytes_with_subdir(self, tmp_path):
        cache = FileCache(str(tmp_path))
        name, path = cache.save_bytes(b"data", "f.bin", sub_dir="sub")
        assert os.path.exists(path)
        assert "sub" in path

    def test_file_exists(self, tmp_path):
        cache = FileCache(str(tmp_path))
        cache.save_bytes(b"x", "a.txt")
        name = os.listdir(str(tmp_path))[0]
        assert cache.file_exists(name)
        assert not cache.file_exists("nonexistent")

    def test_get_file_path(self, tmp_path):
        cache = FileCache(str(tmp_path))
        name, _ = cache.save_bytes(b"x", "a.txt")
        path = cache.get_file_path(name)
        assert path is not None
        assert os.path.exists(path)

    def test_get_file_path_nonexistent(self, tmp_path):
        cache = FileCache(str(tmp_path))
        assert cache.get_file_path("nope") is None

    def test_delete_file(self, tmp_path):
        cache = FileCache(str(tmp_path))
        _, path = cache.save_bytes(b"x", "a.txt")
        assert cache.delete_file(path)
        assert not os.path.exists(path)

    def test_delete_nonexistent_file(self, tmp_path):
        cache = FileCache(str(tmp_path))
        assert not cache.delete_file(str(tmp_path / "nonexistent"))

    def test_cleanup_expired(self, tmp_path):
        cache = FileCache(str(tmp_path), ttl=1)
        cache.save_bytes(b"old", "a.txt")
        time.sleep(1.1)
        cleaned = cache.cleanup_expired()
        assert cleaned == 1

    def test_cleanup_no_expired(self, tmp_path):
        cache = FileCache(str(tmp_path), ttl=3600)
        cache.save_bytes(b"new", "a.txt")
        assert cache.cleanup_expired() == 0

    def test_clear_all(self, tmp_path):
        cache = FileCache(str(tmp_path))
        cache.save_bytes(b"a", "1.txt")
        cache.save_bytes(b"b", "2.txt")
        cache.clear_all()
        assert len(os.listdir(str(tmp_path))) == 0

    def test_get_cache_stats_empty(self, tmp_path):
        cache = FileCache(str(tmp_path))
        stats = cache.get_cache_stats()
        assert stats["total_files"] == 0
        assert stats["total_size_mb"] == 0

    def test_get_cache_stats_with_files(self, tmp_path):
        cache = FileCache(str(tmp_path))
        cache.save_bytes(b"data data data data", "a.txt")
        stats = cache.get_cache_stats()
        assert stats["total_files"] == 1
        assert "cache_dir" in stats
        assert "ttl_seconds" in stats

    def test_stop_cleanup_task_when_none(self, tmp_path):
        cache = FileCache(str(tmp_path))
        cache.stop_cleanup_task()  # should not raise
