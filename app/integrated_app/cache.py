#!/usr/bin/env python3
"""SeedVR2 - 文件缓存与内存缓存管理模块

提供两类缓存实现:
1. FileCache: 上传文件的磁盘缓存，支持大文件异步流式写入、TTL过期自动清理
2. LRUCache: 基于 OrderedDict 的固定容量 LRU 内存缓存，线程安全
3. （P2-10 移除 AdaptiveLRUCache/LRUCache：全仓零引用死代码）
   在高 GPU 负载时自动收缩以释放内存，低负载时扩展以提高命中率

缓存设计遵循以下原则:
- 大文件流式写入，避免阻塞 asyncio 事件循环
- 自动过期清理，防止磁盘空间无限增长
- 线程安全，支持多线程并发访问
- GPU 感知，自适应调整内存缓存容量以配合推理任务
"""

import asyncio
import logging
import os
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


class FileCache:
    """上传文件磁盘缓存管理器

    管理用户上传文件的临时存储，提供:
    - 唯一文件名生成（时间戳 + UUID + 原始扩展名）
    - 大文件/小文件差异化写入策略（大文件异步流式，小文件一次性读取）
    - TTL 过期文件自动清理（后台任务，默认每小时执行一次）
    - 总大小上限清理（按 mtime 淘汰最旧文件，落实 config.cache.max_size_mb 承诺，P1-4）
    - 缓存统计信息查询

    Attributes:
        cache_dir: 缓存文件存储目录路径。
        ttl: 文件存活时间（秒），超过此时间未访问的文件将被清理。
        large_file_threshold: 大文件阈值（字节），超过则使用流式写入。
        chunk_size: 流式写入时的块大小（字节）。
        max_size_bytes: 缓存总大小上限（字节），0 表示不限制（P1-4）。
        _cleanup_task: 后台自动清理的 asyncio Task 引用。
    """

    def __init__(
        self,
        cache_dir: str = "data/uploads",
        ttl: int = 86400,
        *,
        large_file_threshold_mb: int = 10,
        chunk_size_bytes: int = 8192,
        max_size_mb: int = 0,
    ):
        """初始化文件缓存管理器。

        Args:
            cache_dir: 缓存目录路径，不存在时自动创建。
            ttl: 文件存活时间（秒），默认 86400 秒（24小时）。
            large_file_threshold_mb: 大文件阈值（MB），超过此大小使用流式写入，避免阻塞事件循环。
            chunk_size_bytes: 流式写入的块大小（字节），默认 8192（8KB）。
            max_size_mb: 缓存总大小上限（MB），超出自动淘汰最旧文件（按 mtime）；
                0 表示不限制（默认，向后兼容）。
        """
        self.cache_dir = cache_dir
        self.ttl = ttl
        # REFACTOR: 外置原本硬编码的魔法数字 (A4/F1)，支持从 config.runtime.upload 注入
        self.large_file_threshold = large_file_threshold_mb * 1024 * 1024
        self.chunk_size = chunk_size_bytes
        # P1-4：兑现 config.cache.max_size_mb "超出自动清理最旧文件" 的承诺
        self.max_size_bytes = max(0, int(max_size_mb)) * 1024 * 1024
        self._cleanup_task: asyncio.Task | None = None
        os.makedirs(cache_dir, exist_ok=True)

    def generate_unique_filename(self, original_filename: str) -> str:
        """生成唯一文件名，保留原始扩展名

        Args:
            original_filename: 原始文件名

        Returns:
            唯一文件名字符串
        """
        ext = Path(original_filename).suffix or ".bin"
        unique_id = uuid.uuid4().hex[:12]
        timestamp = int(time.time())
        return f"{timestamp}_{unique_id}{ext}"

    def get_cache_path(self, filename: str) -> str:
        """获取缓存文件的完整路径。

        Args:
            filename: 缓存文件名。

        Returns:
            缓存文件的绝对/相对完整路径（拼接 cache_dir 与 filename）。
        """
        return os.path.join(self.cache_dir, filename)

    async def save_upload_file(self, upload_file, sub_dir: str | None = None) -> tuple[str, str]:
        """保存上传文件到缓存

        Args:
            upload_file: FastAPI UploadFile 对象
            sub_dir: 子目录（可选）

        Returns:
            (保存的文件名, 完整路径)
        """
        target_dir = self.cache_dir
        if sub_dir:
            target_dir = os.path.join(self.cache_dir, sub_dir)
            os.makedirs(target_dir, exist_ok=True)

        unique_name = self.generate_unique_filename(upload_file.filename or "upload")
        file_path = os.path.join(target_dir, unique_name)

        # 尝试获取文件大小以决定写入策略
        file_size = 0
        if hasattr(upload_file, "size") and upload_file.size is not None:
            file_size = upload_file.size
        elif hasattr(upload_file, "file") and hasattr(upload_file.file, "seek") and hasattr(upload_file.file, "tell"):
            try:
                pos = upload_file.file.tell()
                upload_file.file.seek(0, 2)  # seek to end
                file_size = upload_file.file.tell()
                upload_file.file.seek(pos)  # restore position
            except (OSError, ValueError):
                file_size = 0

        if file_size > self.large_file_threshold:
            # OPTIMIZE: 大文件异步流式写入。
            # 原实现使用 upload_file.file.read() 同步阻塞事件循环（注释却写着"异步"）；
            # 改为 await upload_file.read() 走 asyncio.to_thread，真正不阻塞 (E7/C10)
            import aiofiles

            await upload_file.seek(0)
            async with aiofiles.open(file_path, "wb") as f:
                while True:
                    chunk = await upload_file.read(self.chunk_size)
                    if not chunk:
                        break
                    await f.write(chunk)
            logger.info(f"文件已缓存(异步写入): {file_path} ({file_size} bytes)")
        else:
            # 小文件：一次性读取
            content = await upload_file.read()
            with open(file_path, "wb") as f:
                f.write(content)
            logger.info(f"文件已缓存: {file_path} ({len(content)} bytes)")

        return unique_name, file_path

    def save_bytes(self, data: bytes, original_filename: str = "upload", sub_dir: str | None = None) -> tuple[str, str]:
        """保存字节数据到缓存

        Args:
            data: 文件数据
            original_filename: 原始文件名（用于获取扩展名）
            sub_dir: 子目录（可选）

        Returns:
            (保存的文件名, 完整路径)
        """
        target_dir = self.cache_dir
        if sub_dir:
            target_dir = os.path.join(self.cache_dir, sub_dir)
            os.makedirs(target_dir, exist_ok=True)

        unique_name = self.generate_unique_filename(original_filename)
        file_path = os.path.join(target_dir, unique_name)

        with open(file_path, "wb") as f:
            f.write(data)

        logger.info(f"数据已缓存: {file_path} ({len(data)} bytes)")
        return unique_name, file_path

    def file_exists(self, filename: str) -> bool:
        """检查缓存文件是否存在。

        Args:
            filename: 缓存文件名。

        Returns:
            文件存在返回 True，否则返回 False。
        """
        return os.path.exists(os.path.join(self.cache_dir, filename))

    def get_file_path(self, filename: str, sub_dir: str | None = None) -> str | None:
        """获取缓存文件路径，不存在则返回 None。

        Args:
            filename: 缓存文件名。
            sub_dir: 可选的子目录名称。

        Returns:
            文件存在时返回完整路径，否则返回 None。
        """
        if sub_dir:
            path = os.path.join(self.cache_dir, sub_dir, filename)
        else:
            path = os.path.join(self.cache_dir, filename)
        return path if os.path.exists(path) else None

    def delete_file(self, file_path: str) -> bool:
        """删除指定缓存文件。

        Args:
            file_path: 要删除的文件完整路径。

        Returns:
            删除成功返回 True，文件不存在或删除失败返回 False。
        """
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"缓存文件已删除: {file_path}")
                return True
            return False
        except OSError as e:
            logger.error(f"删除缓存文件失败: {e}")
            return False

    def cleanup_expired(self) -> int:
        """清理过期文件

        使用 os.scandir 递归遍历，比 os.walk 更高效（DirEntry 缓存文件属性）。

        Returns:
            清理的文件数量
        """
        if not os.path.exists(self.cache_dir):
            return 0

        now = time.time()
        cleaned = 0

        cleaned += self._cleanup_expired_in_dir(self.cache_dir, now)

        if cleaned > 0:
            logger.info(f"清理了 {cleaned} 个过期缓存文件")

        return cleaned

    def cleanup_oversize(self) -> int:
        """按总大小上限淘汰最旧文件（数据治理 P1-4）。

        落实 config.cache.max_size_mb "超出自动清理最旧文件" 的承诺：
        递归收集全部缓存文件的 (路径, 大小, mtime)，总大小超过
        self.max_size_bytes 时按 mtime 升序（最旧优先）逐个删除，
        直到回落到上限以内。

        Returns:
            淘汰的文件数量；未配置上限（max_size_bytes=0）时返回 0。
        """
        if not self.max_size_bytes or not os.path.exists(self.cache_dir):
            return 0

        entries: list[tuple[str, int, float]] = []

        def _collect(dir_path: str) -> None:
            try:
                with os.scandir(dir_path) as it:
                    for entry in it:
                        if entry.is_dir(follow_symlinks=False):
                            _collect(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            try:
                                st = entry.stat(follow_symlinks=False)
                                entries.append((entry.path, st.st_size, st.st_mtime))
                            except OSError:
                                continue
            except OSError:
                pass

        _collect(self.cache_dir)

        total = sum(size for _path, size, _mtime in entries)
        if total <= self.max_size_bytes:
            return 0

        # 最旧优先淘汰（mtime 升序）；总大小恰好回落到上限即停
        entries.sort(key=lambda item: item[2])
        evicted = 0
        for path, size, _mtime in entries:
            if total <= self.max_size_bytes:
                break
            try:
                os.remove(path)
                total -= size
                evicted += 1
            except OSError:
                continue
        if evicted:
            logger.info(
                f"缓存总大小超过上限 ({self.max_size_bytes // (1024 * 1024)}MB)，已按最旧优先淘汰 {evicted} 个文件"
            )
        return evicted

    def _cleanup_expired_in_dir(self, dir_path: str, now: float) -> int:
        """递归清理指定目录下的过期文件（内部辅助方法）。

        Args:
            dir_path: 要扫描的目录路径。
            now: 当前时间戳，用于判断文件是否过期。

        Returns:
            清理的文件数量。
        """
        cleaned = 0
        try:
            with os.scandir(dir_path) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        cleaned += self._cleanup_expired_in_dir(entry.path, now)
                    elif entry.is_file(follow_symlinks=False):
                        try:
                            mtime = entry.stat(follow_symlinks=False).st_mtime
                            if now - mtime > self.ttl:
                                os.remove(entry.path)
                                cleaned += 1
                        except OSError:
                            continue
        except OSError:
            pass
        return cleaned

    def start_cleanup_task(self, interval: int = 3600):
        """启动后台自动清理任务

        Args:
            interval: 清理间隔（秒），默认1小时
        """

        async def _cleanup_loop():
            while True:
                try:
                    self.cleanup_expired()
                    # P1-4：周期性执行总大小上限淘汰
                    self.cleanup_oversize()
                except Exception as e:
                    logger.error(f"自动清理失败: {e}")
                await asyncio.sleep(interval)

        self._cleanup_task = asyncio.create_task(_cleanup_loop())
        logger.info(f"缓存自动清理任务已启动，间隔: {interval}s")

    def stop_cleanup_task(self):
        """停止后台自动清理任务。

        取消正在运行的 asyncio 清理任务，重置 _cleanup_task 引用。
        在应用关闭时调用以确保资源正确释放。
        """
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None
            logger.info("缓存自动清理任务已停止")

    def clear_all(self):
        """清空所有缓存文件（保留目录结构）。

        递归遍历缓存目录，删除所有文件但保留子目录结构。
        使用 os.scandir 替代 os.walk 以获得更好的遍历性能。
        用于手动触发全量缓存清理或用户请求清空缓存。
        """
        if not os.path.exists(self.cache_dir):
            return

        self._clear_all_in_dir(self.cache_dir)
        logger.info("所有缓存文件已清空")

    def _clear_all_in_dir(self, dir_path: str) -> None:
        """递归删除指定目录下的所有文件（保留目录结构）。

        Args:
            dir_path: 要清空的目录路径。
        """
        try:
            with os.scandir(dir_path) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        self._clear_all_in_dir(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        try:
                            os.remove(entry.path)
                        except OSError:
                            continue
        except OSError:
            pass

    def get_cache_stats(self) -> dict:
        """获取缓存统计信息。

        使用 os.scandir 递归遍历，比 os.walk 更高效（DirEntry 缓存 stat 信息）。

        Returns:
            包含以下字段的字典:
            - total_files: 缓存文件总数
            - total_size_mb: 缓存总大小（MB）
            - cache_dir: 缓存目录路径
            - ttl_seconds: 文件 TTL（秒）
        """
        if not os.path.exists(self.cache_dir):
            return {"total_files": 0, "total_size_mb": 0}

        total_files = 0
        total_size = 0

        total_files, total_size = self._collect_stats_in_dir(self.cache_dir)

        return {
            "total_files": total_files,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "cache_dir": self.cache_dir,
            "ttl_seconds": self.ttl,
        }

    def _collect_stats_in_dir(self, dir_path: str) -> tuple[int, int]:
        """递归收集指定目录下的文件统计信息。

        Args:
            dir_path: 要扫描的目录路径。

        Returns:
            (文件总数, 文件总字节数) 元组。
        """
        total_files = 0
        total_size = 0
        try:
            with os.scandir(dir_path) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        sub_files, sub_size = self._collect_stats_in_dir(entry.path)
                        total_files += sub_files
                        total_size += sub_size
                    elif entry.is_file(follow_symlinks=False):
                        try:
                            total_files += 1
                            total_size += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
        except OSError:
            pass
        return total_files, total_size
