#!/usr/bin/env python3
"""历史记录管理路由模块。

提供修复历史记录的查询、统计、删除、取消等端点，
支持分页、筛选、全文搜索。历史表格片段端点（HTMX）已移除——现由
JSON 端点 + 前端渲染接管（契约审计 B 类死码清理，2026-09-03）。

API 端点：
- GET /api/system/history: 获取历史记录列表（JSON）
- GET /api/system/history/statistics: 获取历史统计数据
- GET /api/system/history/resolve: 输出 → 任务反查（数据治理 P3-1）
- DELETE /api/system/history/{record_id}: 删除单条历史记录（连带清理输出文件与断点 JSON）
- POST /api/system/history/{record_id}/pin: 标记/取消保留（retention 清理豁免）
- POST /api/system/history/{record_id}/cancel: 取消关联的进行中任务
- DELETE /api/system/history: 批量清除历史记录

注意：本模块 router 已自带 prefix="/history"，实际路径为 /api/system/history/*

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.integrated_app.checkpoint import TaskCheckpoint
from app.integrated_app.dependencies import get_config, get_history_db, get_task_queue
from app.integrated_app.history_db import HistoryDB, HistoryRecord
from app.integrated_app.security.path_guard import build_default_path_guard
from app.integrated_app.task_queue import TaskQueue
from app.integrated_app.utils.response import respond_success

router = APIRouter(prefix="/api/system/history", tags=["历史记录"])

logger = logging.getLogger(__name__)


class PinRequest(BaseModel):
    """「标记保留」请求体。"""

    pinned: bool = True


async def remove_record_artifacts(records: list[HistoryRecord], history_db: HistoryDB, config: dict) -> int:
    """删除历史记录关联的落盘产物（数据治理 P1-1：「历史可清」的彻底性）。

    - 输出文件：经 PathGuard 白名单校验后删除。不删除用户上传的原始输入
      （可能被其他任务/记录引用），原始上传由 uploads 留存策略统一治理。
    - 断点续跑 JSON：按 tasks 表关联的全部 task_id 回收（批量任务失败残留，
      之前只在批量成功后清理，失败路径会永久留存）。

    Args:
        records: 待删除产物的历史记录列表。
        history_db: 历史数据库实例（查关联 task_id）。
        config: 应用配置（PathGuard 白名单与断点目录）。

    Returns:
        实际删除的文件数（输出文件 + 断点 JSON）。
    """
    removed = 0
    allowed_dirs = config.get("runtime", {}).get("security", {}).get("allowed_base_dirs", ["outputs/", "data/uploads/"])
    path_guard = build_default_path_guard(os.getcwd(), allowed_dirs)
    checkpoint_dir = os.path.join(
        os.getcwd(), config.get("runtime", {}).get("task", {}).get("checkpoint_dir", "data/checkpoints")
    )
    checkpoint_mgr = TaskCheckpoint(checkpoint_dir)

    for record in records:
        output_path = record.output_file
        if output_path and os.path.exists(output_path):
            if path_guard.is_safe_path(output_path):
                try:
                    os.remove(output_path)
                    removed += 1
                except OSError as e:
                    logger.warning(f"删除历史记录输出文件失败 {output_path}: {e}")
            else:
                logger.warning(f"输出文件不在 PathGuard 白名单内，跳过删除: {output_path}")
        for task_id in await history_db.get_task_ids_by_record_id(record.id or 0):
            if checkpoint_mgr.remove_checkpoint(task_id):
                removed += 1
    return removed


@router.get("/resolve")
async def resolve_output_provenance(
    history_db: HistoryDB = Depends(get_history_db),
    output_file: str | None = None,
    task_id: str | None = None,
    watermark_payload: str | None = None,
):
    """输出 → 任务反查（数据治理 P3-1 输出溯源）。

    支持三种入口，任一命中即返回该输出对应的任务、输入与完整参数：
    - output_file: 输出文件路径（精确匹配，取最新一条）
    - task_id: 任务/批量 ID（水印 payload 即为此值）
    - watermark_payload: 从输出图中提取到的水印 payload，等价于 task_id

    API 端点：GET /api/system/history/resolve

    返回格式（JSON，统一包装）:
    {
        "found": true,
        "record": {...},   // 历史记录（含 input_file / parameters / input_sha256）
        "task": {...}      // 关联任务状态（可能为 null）
    }

    Args:
        history_db: 历史数据库实例。
        output_file: 输出文件路径。
        task_id: 任务 ID。
        watermark_payload: 水印提取到的 payload（按 task_id 处理）。

    Returns:
        统一格式的 JSON 响应；未命中时 found=false（HTTP 200，便于前端直接判空）。
    """
    resolve_task_id = task_id or watermark_payload
    record = None

    if resolve_task_id:
        task = await history_db.get_task(resolve_task_id)
        if task is not None:
            record = await history_db.get_record(task.record_id)
    elif output_file:
        record = await history_db.find_by_output_file(output_file)

    if record is None:
        return respond_success({"found": False, "record": None, "task": None})

    task_record = await history_db.get_task_by_record_id(record.id or 0)
    return respond_success(
        {
            "found": True,
            "record": vars(record),
            "task": vars(task_record) if task_record else None,
        }
    )


@router.get("")
async def get_history(
    history_db: HistoryDB = Depends(get_history_db),
    task_type: str | None = None,
    status: str | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """获取历史记录列表（JSON 格式）。

    API 端点：GET /api/system/history

    查询参数：
    - task_type (optional): 按任务类型筛选，"image"/"video"
    - status (optional): 按状态筛选，"pending"/"processing"/"completed"/"failed"/"cancelled"
    - search (optional): 全文搜索关键词
    - page (optional): 页码，默认 1，最小 1
    - page_size (optional): 每页条数，默认 20，范围 1-100

    返回格式（JSON）：
    {
        "records": [ ... ],     // 历史记录列表（使用 vars() 序列化）
        "total": int,           // 总记录数
        "page": int,
        "page_size": int,
        "total_pages": int
    }

    Args:
        history_db: 历史数据库实例（通过依赖注入）。
        task_type: 任务类型筛选。
        status: 状态筛选。
        search: 搜索关键词。
        page: 页码。
        page_size: 每页条数。

    Returns:
        包含历史记录和分页信息的字典。
    """
    if search:
        records, total = await history_db.search_records(query=search, limit=page_size, offset=(page - 1) * page_size)
    else:
        records, total = await history_db.get_records(
            task_type=task_type, status=status, limit=page_size, offset=(page - 1) * page_size
        )

    # 清理已被 retention 策略删除的输出文件引用：记录仍在但文件已不存在时，
    # 清空 output_file 使前端不显示下载按钮和缩略图（否则点击下载返回 404）。
    for r in records:
        if r.status == "completed" and r.output_file and not os.path.exists(r.output_file):
            r.output_file = ""

    return {
        "records": [vars(r) for r in records],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.get("/statistics")
async def get_statistics(history_db: HistoryDB = Depends(get_history_db)):
    """获取历史记录统计数据。

    API 端点：GET /api/system/history/statistics

    请求参数：无

    返回格式（JSON）：由 history_db.get_statistics() 返回的统计信息字典，
    包含总任务数、成功/失败/取消计数等。

    Args:
        history_db: 历史数据库实例（通过依赖注入）。

    Returns:
        统计数据字典。
    """
    return await history_db.get_statistics()


@router.get("/{record_id}/download")
async def download_history_file(
    record_id: int,
    history_db: HistoryDB = Depends(get_history_db),
    config: dict = Depends(get_config),
):
    """下载历史记录关联的输出文件。

    API 端点：GET /api/system/history/{record_id}/download

    路径参数：
    - record_id: 历史记录 ID

    返回：文件流响应，自动设置 Content-Type。

    错误响应：
    - 404: 记录不存在或输出文件不存在
    - 403: 路径不在允许范围内

    Args:
        record_id: 历史记录 ID。
        history_db: 历史数据库实例。
        config: 应用配置。

    Returns:
        FileResponse 文件下载响应。
    """
    record = await history_db.get_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")

    output_path = record.output_file
    if not output_path or not os.path.exists(output_path):
        raise HTTPException(status_code=404, detail="输出文件不存在")

    allowed_dirs = config.get("runtime", {}).get("security", {}).get("allowed_base_dirs", ["outputs/", "data/uploads/"])
    path_guard = build_default_path_guard(os.getcwd(), allowed_dirs)
    if not path_guard.is_safe_path(output_path):
        raise HTTPException(status_code=403, detail="不允许下载该路径")

    filename = os.path.basename(output_path)
    ext = os.path.splitext(filename)[1].lower()

    if record.task_type == "video" or ext in {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}:
        media_type = "video/mp4"
    else:
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".bmp": "image/bmp",
            ".webp": "image/webp",
        }
        media_type = media_types.get(ext, "image/png")

    return FileResponse(
        path=output_path,
        filename=filename,
        media_type=media_type,
    )


@router.delete("/{record_id}")
async def delete_history_record(
    record_id: int,
    history_db: HistoryDB = Depends(get_history_db),
    config: dict = Depends(get_config),
):
    """删除单条历史记录。

    API 端点：DELETE /api/system/history/{record_id}

    数据治理 P1-1：删除记录时连带清理落盘产物（输出文件经 PathGuard
    校验后删除 + 关联任务的断点续跑 JSON），落实隐私政策「历史可清」。

    路径参数：
    - record_id: 历史记录 ID

    返回格式（JSON）：
    {
        "success": bool,
        "removed_files": int
    }

    Args:
        record_id: 要删除的记录 ID。
        history_db: 历史数据库实例（通过依赖注入）。
        config: 应用配置（通过依赖注入）。

    Returns:
        包含删除结果的字典。
    """
    record = await history_db.get_record(record_id)
    success = await history_db.delete_record(record_id)
    removed_files = await remove_record_artifacts([record], history_db, config) if record else 0
    return {"success": success, "removed_files": removed_files}


@router.post("/{record_id}/cancel")
async def cancel_history_record(
    record_id: int,
    history_db: HistoryDB = Depends(get_history_db),
    task_queue: TaskQueue = Depends(get_task_queue),
):
    """取消历史记录关联的进行中任务。

    API 端点：POST /api/system/history/{record_id}/cancel

    路径参数：
    - record_id: 历史记录 ID

    返回格式（JSON）：
    {
        "success": true,
        "task_id": str,
        "status": "cancelled"
    }

    错误响应：
    - 404: 记录不存在或未找到关联任务
    - 400: 记录状态不允许取消

    Args:
        record_id: 历史记录 ID。
        history_db: 历史数据库实例（通过依赖注入）。
        task_queue: 任务队列实例（通过依赖注入）。

    Returns:
        取消操作结果。

    Raises:
        HTTPException: 记录不存在、状态非法或无关联任务时抛出。
    """
    record = await history_db.get_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")

    if record.status not in ("pending", "processing"):
        raise HTTPException(status_code=400, detail=f"记录状态为 {record.status}，无法取消")

    task = await history_db.get_task_by_record_id(record_id)
    if not task:
        raise HTTPException(status_code=404, detail="未找到关联任务")

    task_queue.request_cancel(task.task_id)
    await history_db.update_task(task.task_id, status="cancelled", error_message="用户取消")
    await history_db.update_record(record_id, status="cancelled", error_message="用户取消")
    return {"success": True, "task_id": task.task_id, "status": "cancelled"}


@router.post("/{record_id}/pin")
async def pin_history_record(
    record_id: int,
    req: PinRequest,
    history_db: HistoryDB = Depends(get_history_db),
):
    """标记/取消记录「保留」（数据治理 P1-5）。

    API 端点：POST /api/system/history/{record_id}/pin

    pinned 记录的输出文件被 retention 年龄/数量/水位清理豁免，
    用于把修复结果当长期资产的用户显式排除自动删除。

    请求体（JSON）：
    {
        "pinned": bool  // true 标记保留，false 取消标记
    }

    返回格式（JSON）：
    {
        "success": bool,
        "pinned": bool
    }

    错误响应：
    - 404: 记录不存在

    Args:
        record_id: 历史记录 ID。
        req: 标记请求体。
        history_db: 历史数据库实例（通过依赖注入）。

    Returns:
        标记结果。
    """
    record = await history_db.get_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")
    ok = await history_db.set_record_pinned(record_id, req.pinned)
    return {"success": ok, "pinned": req.pinned}


@router.delete("")
async def clear_history(
    before_date: str | None = None,
    status: str | None = None,
    history_db: HistoryDB = Depends(get_history_db),
    config: dict = Depends(get_config),
):
    """批量清除历史记录。

    API 端点：DELETE /api/system/history

    数据治理 P1-1：清除记录前先取落盘路径，删除记录后连带清理
    输出文件（PathGuard 校验）与断点续跑 JSON。

    查询参数：
    - before_date (optional): 清除此日期之前的记录。
    - status (optional): 仅清除指定状态的记录（如 "failed"、"cancelled"）；
      不提供则清除所有状态（保留已完成记录应传 status=failed 或 status=cancelled）。

    返回格式（JSON）：
    {
        "deleted_count": int,
        "removed_files": int
    }

    Args:
        before_date: 截止日期，可选。
        status: 按状态过滤，可选。
        history_db: 历史数据库实例。
        config: 应用配置（通过依赖注入）。

    Returns:
        包含删除数量的字典。
    """
    records = await history_db.get_records_filtered(before_date, status=status)
    count = await history_db.clear_records(before_date, status=status)
    removed_files = await remove_record_artifacts(records, history_db, config)
    return {"deleted_count": count, "removed_files": removed_files}
