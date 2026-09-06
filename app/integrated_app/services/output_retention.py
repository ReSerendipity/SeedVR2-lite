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
- 磁盘水位触发清理（成本治理 P1-1）：当默认输出模板把成品写到
  outputs/ 之外（如 {input_dir}/restored/）时，outputs/ 时间清理
  管不到真实成品落点；此时按 retention.disk_min_free_gb 水位对
  「历史任务实际输出目录」触发同样的年龄规则清理，删除前经 notify
  回调广播系统通知。

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
from collections.abc import Awaitable, Callable, Sequence

logger = logging.getLogger(__name__)

# 目录占位文件，永不清理
_PLACEHOLDER_FILES = {".gitkeep"}
# 残留临时帧目录名标记（与 _video_pipeline.py 的 frames_dir 命名对应）
_TEMP_DIR_MARKERS = ("_frames",)
_SECONDS_PER_DAY = 86400
_GB = 1024**3


def _normalize_keep_paths(keep_paths: set[str] | None) -> set[str]:
    """把保留路径集合归一化为可比较形式（绝对路径 + Windows 大小写不敏感）。"""
    if not keep_paths:
        return set()
    return {os.path.normcase(os.path.abspath(p)) for p in keep_paths}


def plan_cleanup_outputs(
    outputs_dir: str,
    max_age_days: int = 0,
    max_files: int = 0,
    keep_paths: set[str] | None = None,
) -> list[str]:
    """只规划不删除：返回将被保留策略清理的文件清单（数据治理 P1-5 删除前广播）。

    与 cleanup_outputs_once 使用同一套规则圈定受害者（年龄 / 数量上限 /
    占位豁免 / pinned 豁免），供删除前经 SSE 广播「即将清理清单」。

    Args:
        outputs_dir: 输出根目录。
        max_age_days: 文件最大保留天数；0 表示禁用年龄规则。
        max_files: 保留文件数量上限；0 表示不限制。
        keep_paths: 豁免路径集合（pinned 记录的输出文件）。

    Returns:
        将被删除的文件绝对路径列表（不含临时帧目录，目录回收不可单文件枚举）。
    """
    if not outputs_dir or not os.path.isdir(outputs_dir):
        return []
    if max_age_days <= 0 and max_files <= 0:
        return []

    keep_set = _normalize_keep_paths(keep_paths)
    victims: list[str] = []

    def _keep(path: str) -> bool:
        return os.path.normcase(os.path.abspath(path)) in keep_set

    age_candidates: list[tuple[float, int, str]] = []
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
                if stat.st_mtime < cutoff and not _keep(path):
                    age_candidates.append((stat.st_mtime, stat.st_size, path))
    victims.extend(path for _mtime, _size, path in age_candidates)

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
            # 与 cleanup_outputs_once 规则 2 对齐：pinned 占额度不被删，
            # 跳过年龄规则已圈定的文件避免重复计入
            candidates.sort()
            planned = {p for _m, _s, p in age_candidates}
            taken = 0
            for _mtime, _size, path in candidates:
                if taken >= excess:
                    break
                if _keep(path) or path in planned:
                    continue
                victims.append(path)
                taken += 1

    return victims


def cleanup_outputs_once(
    outputs_dir: str,
    max_age_days: int = 0,
    max_files: int = 0,
    keep_paths: set[str] | None = None,
) -> tuple[int, int]:
    """按保留策略执行一次同步清理。

    Args:
        outputs_dir: 输出根目录（通常为 项目根/outputs）。
        max_age_days: 文件最大保留天数，超过即删除；0 表示禁用年龄规则。
        max_files: 保留文件数量上限（保留最新 N 个）；0 表示不限制。
        keep_paths: 豁免路径集合（数据治理 P1-5：pinned 记录的输出文件），
            年龄规则与数量规则均跳过这些文件。

    Returns:
        (删除文件数, 释放字节数) 元组。目录不存在时返回 (0, 0)。
    """
    if not outputs_dir or not os.path.isdir(outputs_dir):
        return 0, 0
    if max_age_days <= 0 and max_files <= 0:
        return 0, 0

    keep_set = _normalize_keep_paths(keep_paths)
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
                if stat.st_mtime < cutoff and os.path.normcase(os.path.abspath(path)) not in keep_set:
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
            # pinned 文件计入额度但不参与删除：从最旧开始删，
            # 跳过 pinned，直到删满 excess 个（保证总量收敛到上限）
            candidates.sort()
            deleted = 0
            for _mtime, size, path in candidates:
                if deleted >= excess:
                    break
                if os.path.normcase(os.path.abspath(path)) in keep_set:
                    continue
                try:
                    os.remove(path)
                    removed_files += 1
                    freed_bytes += size
                    deleted += 1
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


def cleanup_watermark_dirs(
    output_dirs: Sequence[str],
    min_free_gb: float,
    max_age_days: int,
    notify: Callable[[str, dict], None] | None = None,
    keep_paths: set[str] | None = None,
) -> tuple[int, int]:
    """磁盘水位触发的任务输出目录清理（成本治理 P1-1）。

    当默认输出模板把成品写到 outputs/ 之外（如 ``{input_dir}/restored/``）时，
    outputs/的时间清理覆盖不到真实成品落点。本函数对「历史任务实际输出目录」
    做水位检查：磁盘剩余空间低于 ``min_free_gb`` 时，仅删除目录内
    **超过 max_age_days 天**的旧文件（最旧优先），直到回升到水位线或
    可删文件耗尽——绝不删除新鲜文件，绝不删除目录本身。

    Args:
        output_dirs: 候选输出目录列表（通常来自历史库输出去重父目录）。
        min_free_gb: 磁盘最低剩余空间水位（GB），<=0 时直接跳过。
        max_age_days: 可删除文件的最低年龄（天），<=0 时直接跳过
            （水位清理复用年龄规则圈定受害者，避免误删新产物）。
        notify: 删除前回调 ``notify(dir, info)``，info 含 free_gb /
            min_free_gb / candidate_files / candidate_bytes，用于广播
            系统通知；异常由本函数吞掉，不影响清理。
        keep_paths: 豁免路径集合（数据治理 P1-5：pinned 记录的输出文件）。

    Returns:
        (删除文件数, 释放字节数) 元组。
    """
    if min_free_gb <= 0 or max_age_days <= 0:
        return 0, 0
    cutoff = time.time() - max_age_days * _SECONDS_PER_DAY
    keep_set = _normalize_keep_paths(keep_paths)

    removed_files = 0
    freed_bytes = 0
    seen: set[str] = set()

    for raw_dir in output_dirs:
        if not raw_dir or raw_dir in seen:
            continue
        seen.add(raw_dir)
        if not os.path.isdir(raw_dir):
            continue

        try:
            free_gb = shutil.disk_usage(raw_dir).free / _GB
        except OSError:
            continue
        if free_gb >= min_free_gb:
            continue

        # 圈定受害者：仅超龄文件（pinned 豁免），最旧优先
        candidates: list[tuple[float, int, str]] = []
        for root, _dirs, files in os.walk(raw_dir):
            for name in files:
                if name in _PLACEHOLDER_FILES:
                    continue
                path = os.path.join(root, name)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                if stat.st_mtime < cutoff and os.path.normcase(os.path.abspath(path)) not in keep_set:
                    candidates.append((stat.st_mtime, stat.st_size, path))
        if not candidates:
            logger.debug(f"水位清理：{raw_dir} 低于水位 {min_free_gb:.1f}GB 但无超龄文件可删")
            continue
        candidates.sort()

        info = {
            "free_gb": round(free_gb, 2),
            "min_free_gb": min_free_gb,
            "candidate_files": len(candidates),
            "candidate_bytes": sum(size for _mtime, size, _path in candidates),
            "max_age_days": max_age_days,
        }
        if notify is not None:
            try:
                notify(raw_dir, info)
            except Exception as e:  # noqa: BLE001 — 通知失败不影响清理
                logger.warning(f"水位清理通知回调失败: {e}")

        dir_removed = 0
        dir_freed = 0
        for _mtime, size, path in candidates:
            try:
                os.remove(path)
            except OSError:
                continue
            removed_files += 1
            dir_removed += 1
            freed_bytes += size
            dir_freed += size
            # 每删一个即复查水位（disk_usage 为廉价系统调用），
            # 回升即停——少删一个是一个，大文件场景尤其重要
            try:
                if shutil.disk_usage(raw_dir).free / _GB >= min_free_gb:
                    break
            except OSError:
                break
        if dir_removed:
            logger.info(
                f"水位清理（{raw_dir}）: 删除 {dir_removed} 个超龄文件，"
                f"释放 {dir_freed / (1024 * 1024):.1f}MB"
                f"（水位 {min_free_gb:.1f}GB，仅删 mtime 早于 {max_age_days} 天的文件）"
            )

    return removed_files, freed_bytes


async def periodic_output_cleanup(
    outputs_dir: str,
    max_age_days: int,
    max_files: int,
    interval_seconds: int,
    is_busy: Callable[[], bool] | None = None,
    watermark_min_free_gb: float = 0.0,
    list_output_dirs: Callable[[], Awaitable[list[str]]] | None = None,
    notify: Callable[[str, dict], None] | None = None,
    keep_paths_provider: Callable[[], Awaitable[set[str]]] | None = None,
    notify_plan: Callable[[list[str]], None] | None = None,
) -> None:
    """周期执行 outputs 清理 + 输出目录水位清理的后台任务（由 lifespan 创建与取消）。

    Args:
        outputs_dir: 输出根目录。
        max_age_days: 文件最大保留天数。
        max_files: 保留文件数量上限。
        interval_seconds: 清理周期（秒）。
        is_busy: 忙碌检测回调，返回 True 时跳过本轮（如正在执行推理任务），
            避免与活动任务的临时帧写入竞争。
        watermark_min_free_gb: 输出目录水位清理阈值（GB），<=0 时禁用水位清理
            （成本治理 P1-1：覆盖默认模板写到 outputs/ 之外的成品落点）。
        list_output_dirs: 异步回调，返回历史任务实际输出目录列表（水位清理范围）。
        notify: 水位清理删除前通知回调（如经全局 SSE event_bus 广播系统通知）。
        keep_paths_provider: 异步回调，返回本轮清理的豁免路径集合
            （数据治理 P1-5：pinned 记录的输出文件）；失败时本轮不豁免。
        notify_plan: 删除前广播回调（数据治理 P1-5）：把本轮将被清理的文件
            清单投递给调用方（如经 SSE system_notice 提前告知用户）。
    """
    while True:
        await asyncio.sleep(interval_seconds)
        if is_busy is not None and is_busy():
            continue

        keep_paths: set[str] | None = None
        if keep_paths_provider is not None:
            try:
                keep_paths = await keep_paths_provider()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"获取 pinned 豁免清单失败（本轮不豁免）: {e}")

        # 删除前广播「即将清理清单」（best-effort，失败不影响清理主流程）
        if notify_plan is not None and (max_age_days > 0 or max_files > 0):
            try:
                victims = await asyncio.to_thread(
                    plan_cleanup_outputs, outputs_dir, max_age_days, max_files, keep_paths
                )
                if victims:
                    notify_plan(victims)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"清理计划广播失败: {e}")

        try:
            await asyncio.to_thread(cleanup_outputs_once, outputs_dir, max_age_days, max_files, keep_paths)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"outputs 周期清理失败: {e}")

        # 水位清理：复用年龄规则圈定受害者（max_age_days<=0 时 cleanup 内部直接跳过）
        if watermark_min_free_gb > 0 and list_output_dirs is not None:
            try:
                dirs = await list_output_dirs()
                if dirs:
                    await asyncio.to_thread(
                        cleanup_watermark_dirs, dirs, watermark_min_free_gb, max_age_days, notify, keep_paths
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"输出目录水位清理失败: {e}")


# uploads/ 内的成品子树标记："上传后原位修复"把成品写到 data/uploads/restored/，
# 该子树属于用户产物，按 outputs 同策略判龄，而非原始上传的更短留存（数据治理 P0-1）
_RESTORED_DIR_MARKER = "restored"


def cleanup_uploads_once(
    uploads_dir: str,
    uploads_max_age_days: int = 0,
    restored_max_age_days: int = 0,
) -> tuple[int, int]:
    """按保留策略执行一次 data/uploads/ 清理（数据治理 P0-1）。

    与 cleanup_outputs_once 的差异：
    - 不做临时帧目录回收与空目录级联删除（uploads 的 image/video/restored
      子目录是常驻结构，上传/修复代码按需写入，删空目录无收益）
    - restored/ 成品子树按 outputs 同策略（restored_max_age_days）单独判龄，
      原始上传按 uploads_max_age_days 判龄——原始上传比修复产物更隐私敏感，
      默认留存更短

    Args:
        uploads_dir: 上传根目录（通常为 项目根/data/uploads）。
        uploads_max_age_days: 原始上传文件最大保留天数；0 表示禁用该规则。
        restored_max_age_days: restored/ 成品子树最大保留天数；0 表示禁用该规则
            （与 outputs_max_age_days 同源，outputs 清理禁用时 restored 同步禁用）。

    Returns:
        (删除文件数, 释放字节数) 元组。目录不存在时返回 (0, 0)。
    """
    if not uploads_dir or not os.path.isdir(uploads_dir):
        return 0, 0
    if uploads_max_age_days <= 0 and restored_max_age_days <= 0:
        return 0, 0

    now = time.time()
    uploads_cutoff = now - uploads_max_age_days * _SECONDS_PER_DAY if uploads_max_age_days > 0 else None
    restored_cutoff = now - restored_max_age_days * _SECONDS_PER_DAY if restored_max_age_days > 0 else None

    removed_files = 0
    freed_bytes = 0
    for root, _dirs, files in os.walk(uploads_dir):
        rel_parts = os.path.normpath(os.path.relpath(root, uploads_dir)).split(os.sep)
        in_restored = _RESTORED_DIR_MARKER in rel_parts
        cutoff = restored_cutoff if in_restored else uploads_cutoff
        if cutoff is None:
            continue
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

    if removed_files:
        logger.info(
            f"uploads 保留策略清理: 删除 {removed_files} 个过期文件，"
            f"释放 {freed_bytes / (1024 * 1024):.1f}MB"
            f"（uploads_max_age_days={uploads_max_age_days}, restored_max_age_days={restored_max_age_days}）"
        )
    return removed_files, freed_bytes


async def periodic_uploads_cleanup(
    uploads_dir: str,
    uploads_max_age_days: int,
    restored_max_age_days: int,
    interval_seconds: int,
    is_busy: Callable[[], bool] | None = None,
) -> None:
    """周期执行 uploads 清理的后台任务（由 lifespan 创建与取消）。

    Args:
        uploads_dir: 上传根目录。
        uploads_max_age_days: 原始上传文件最大保留天数。
        restored_max_age_days: restored/ 成品子树最大保留天数。
        interval_seconds: 清理周期（秒）。
        is_busy: 忙碌检测回调，返回 True 时跳过本轮（如正在执行推理任务），
            避免与活动任务的文件写入竞争。
    """
    while True:
        await asyncio.sleep(interval_seconds)
        if is_busy is not None and is_busy():
            continue
        try:
            await asyncio.to_thread(cleanup_uploads_once, uploads_dir, uploads_max_age_days, restored_max_age_days)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"uploads 周期清理失败: {e}")
