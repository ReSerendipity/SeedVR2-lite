#!/usr/bin/env python3
"""outputs/ 输出产物保留策略与周期清理服务。

SeedVR2 的推理输出（outputs/image、outputs/video）历史上只增不删，
是成本资源治理审计（P0-1）确认的存储反模式：长视频临时帧目录
（outputs/video/_frames）在失败路径还会残留数十 GB 帧文件。
本模块提供基于保留策略的自动清理：

- 按文件年龄清理（retention.outputs_max_age_days，0 表示禁用）
- 按数量上限清理（retention.outputs_max_files，0 表示不限制，保留最新 N 个）
- 跳过 .gitkeep 等占位文件
- 顺带回收残留的临时帧目录（仅当其修改时间早于年龄阈值，
  正在写入的活动任务目录 mtime 持续刷新，不会被误删）
- 清理后自底向上移除空目录

周期任务由 app_server lifespan 托管；调用方应在无推理任务运行时
才执行清理（通过 is_busy 回调跳过），避免与活动任务的文件写入竞争。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

# 目录占位文件，永不清理
_PLACEHOLDER_FILES = {".gitkeep"}
# 残留临时帧目录名标记（与 _video_pipeline.py 的 frames_dir 命名对应）
_TEMP_DIR_MARKERS = ("_frames",)
_SECONDS_PER_DAY = 86400


def cleanup_outputs_once(
    outputs_dir: str,
    max_age_days: int = 0,
    max_files: int = 0,
) -> tuple[int, int]:
    """按保留策略执行一次同步清理。

    Args:
        outputs_dir: 输出根目录（通常为 项目根/outputs）。
        max_age_days: 文件最大保留天数，超过即删除；0 表示禁用年龄规则。
        max_files: 保留文件数量上限（保留最新 N 个）；0 表示不限制。

    Returns:
        (删除文件数, 释放字节数) 元组。目录不存在时返回 (0, 0)。
    """
    if not outputs_dir or not os.path.isdir(outputs_dir):
        return 0, 0
    if max_age_days <= 0 and max_files <= 0:
        return 0, 0

    removed_files = 0
    freed_bytes = 0

    # 规则 1: 按年龄清理文件
    if max_age_days > 0:
        cutoff = time.time() - max_age_days * _SECONDS_PER_DAY
        for root, _dirs, files in os.walk(outputs_dir):
            for name in files:
                if name in _PLACEHOLDER_FILES:
                    continue
                path = os.path.join(root, name)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                if stat.st_mtime < cutoff:
                    try:
                        os.remove(path)
                        removed_files += 1
                        freed_bytes += stat.st_size
                    except OSError:
                        continue

    # 规则 2: 按数量上限清理最旧文件
    if max_files > 0:
        candidates: list[tuple[float, int, str]] = []
        for root, _dirs, files in os.walk(outputs_dir):
            for name in files:
                if name in _PLACEHOLDER_FILES:
                    continue
                path = os.path.join(root, name)
                try:
                    stat = os.stat(path)
                    candidates.append((stat.st_mtime, stat.st_size, path))
                except OSError:
                    continue
        excess = len(candidates) - max_files
        if excess > 0:
            candidates.sort()
            for _mtime, size, path in candidates[:excess]:
                try:
                    os.remove(path)
                    removed_files += 1
                    freed_bytes += size
                except OSError:
                    continue

    freed_bytes += _remove_leftover_dirs(outputs_dir, max_age_days)
    if removed_files:
        logger.info(
            f"outputs 保留策略清理: 删除 {removed_files} 个文件，"
            f"释放 {freed_bytes / (1024 * 1024):.1f}MB"
            f"（max_age_days={max_age_days}, max_files={max_files}）"
        )
    return removed_files, freed_bytes


def _remove_leftover_dirs(outputs_dir: str, max_age_days: int) -> int:
    """回收残留的临时帧目录并移除清理后变空的子目录。

    Args:
        outputs_dir: 输出根目录。
        max_age_days: 年龄阈值（天）。仅当 >0 时回收 mtime 早于阈值的
            临时帧目录；活动任务正在写入的目录 mtime 持续刷新，不会命中。

    Returns:
        临时帧目录回收释放的字节数（空目录移除不计入）。
    """
    freed_bytes = 0
    cutoff = time.time() - max_age_days * _SECONDS_PER_DAY if max_age_days > 0 else None

    # os.walk(topdown=False) 自底向上：先访问子目录再访问父目录，
    # 因此父目录在被访问时其空子目录已被移除，可以安全地级联删除
    for root, _dirs, _files in os.walk(outputs_dir, topdown=False):
        base = os.path.basename(root)
        if root != outputs_dir and any(marker in base for marker in _TEMP_DIR_MARKERS):
            try:
                if cutoff is None or os.stat(root).st_mtime < cutoff:
                    freed_bytes += _dir_size(root)
                    shutil.rmtree(root, ignore_errors=True)
            except OSError:
                pass
        if root != outputs_dir:
            try:
                if not os.listdir(root):
                    os.rmdir(root)
            except OSError:
                continue
    return freed_bytes


def _dir_size(path: str) -> int:
    """递归统计目录字节数（best-effort，用于清理日志）。"""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


async def periodic_output_cleanup(
    outputs_dir: str,
    max_age_days: int,
    max_files: int,
    interval_seconds: int,
    is_busy: Callable[[], bool] | None = None,
) -> None:
    """周期执行 outputs 清理的后台任务（由 lifespan 创建与取消）。

    Args:
        outputs_dir: 输出根目录。
        max_age_days: 文件最大保留天数。
        max_files: 保留文件数量上限。
        interval_seconds: 清理周期（秒）。
        is_busy: 忙碌检测回调，返回 True 时跳过本轮（如正在执行推理任务），
            避免与活动任务的临时帧写入竞争。
    """
    while True:
        await asyncio.sleep(interval_seconds)
        if is_busy is not None and is_busy():
            continue
        try:
            await asyncio.to_thread(cleanup_outputs_once, outputs_dir, max_age_days, max_files)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"outputs 周期清理失败: {e}")
