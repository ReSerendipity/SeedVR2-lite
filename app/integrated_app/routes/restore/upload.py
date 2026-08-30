#!/usr/bin/env python3
"""单文件上传与修复路由模块（HTTP 适配层）。

处理文件上传、修复任务创建；任务执行编排已迁移到
services/restore_service.py（P0-2 分层治理），本模块只负责：
HTTP 参数解析、输入校验（大小/魔数/类型）、磁盘/GPU/模型预检、任务入队。

支持直接上传文件或指定本地文件夹路径两种输入方式，
自动检测媒体类型并分发到图像/视频推理引擎。

API 端点：
- POST /api/restore/: 上传文件或指定文件夹创建修复任务

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import asyncio
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from app.integrated_app.cache import FileCache
from app.integrated_app.config_models import (
    ImageRestoreParams,
    UnifiedRestoreParams,
    VideoRestoreParams,
)
from app.integrated_app.dependencies import (
    get_config,
    get_file_cache,
    get_history_db,
    get_model_manager,
    get_task_queue,
)
from app.integrated_app.gpu_backend import gpu_manager
from app.integrated_app.history_db import HistoryDB, HistoryRecord
from app.integrated_app.model_manager import ModelManager
from app.integrated_app.model_registry import model_registry
from app.integrated_app.routes.restore import common
from app.integrated_app.security.magic_check import validate_upload_magic
from app.integrated_app.services.restore_service import (
    model_size_from_dit_model,
    process_image_task,
    process_video_task,
)
from app.integrated_app.task_queue import TaskQueue
from app.integrated_app.utils.response import respond_success

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/restore", tags=["修复"])


@router.post("/")
async def upload_and_restore(
    request: Request,
    file: UploadFile | None = File(None),
    folder_path: str | None = Form(None),
    raw_params: UnifiedRestoreParams = Depends(common.parse_unified_params),
    history_db: HistoryDB = Depends(get_history_db),
    file_cache: FileCache = Depends(get_file_cache),
    task_queue: TaskQueue = Depends(get_task_queue),
    config: dict = Depends(get_config),
    model_manager: ModelManager = Depends(get_model_manager),
):
    """上传文件或指定本地文件夹，创建修复任务并加入后台队列。

    API 端点：POST /api/restore/

    支持两种输入方式（二选一）：
    1. multipart/form-data 上传文件：字段名 "file"
    2. form 字段 "folder_path" 指定本地文件夹路径（取第一个媒体文件）

    通用表单参数（由 common.parse_unified_params 解析）：
    - task_type: "auto"/"image"/"video"，默认 "auto"
    - dit_model, seed 等修复参数（详见 common.parse_unified_params）

    返回格式（JSON，统一包装 {success, data, error}）：
    {
        "success": true,
        "data": {
            "task_id": str,       // 任务 ID，用于查询进度/下载结果
            "record_id": int,     // 历史记录数据库 ID
            "task_type": "image"|"video",
            "status": "pending",
            "message": "修复任务已创建并加入队列"
        }
    }

    错误响应：
    - 400: 参数错误（未提供文件/路径、格式不支持、文件过大等）
    - 503: GPU 不可用，或模型自动加载失败
    - 507: 磁盘剩余空间不足

    Args:
        request: FastAPI 请求对象。
        file: 上传的文件（multipart/form-data），可选。
        folder_path: 本地文件夹路径（form 字段），可选。
        raw_params: 解析后的统一修复参数（通过依赖注入）。
        history_db: 历史记录数据库实例（通过依赖注入）。
        file_cache: 文件缓存实例（通过依赖注入）。
        task_queue: 任务队列实例（通过依赖注入）。
        config: 应用配置（通过依赖注入）。
        model_manager: 模型管理器（通过依赖注入）。

    Returns:
        统一格式的 JSON 响应，包含任务信息。

    Raises:
        HTTPException: 输入校验失败或服务不可用时抛出。
    """
    if not (file and file.filename) and not (folder_path and folder_path.strip()):
        raise HTTPException(status_code=400, detail="请上传文件或指定文件夹路径")

    if not gpu_manager.is_gpu_available:
        raise HTTPException(
            status_code=503,
            detail="SeedVR2 仅支持 NVIDIA GPU 推理，当前未检测到 NVIDIA GPU。请安装 NVIDIA GPU 并配置 CUDA 驱动。",
        )

    # 自动加载模型：未加载（或尺寸不符）时先加载再修复，避免用户手动预加载
    await common.ensure_model_loaded(model_manager, raw_params.dit_model)

    input_path: str
    detected_type: str | None = None

    if file and file.filename:
        file_ext = os.path.splitext(file.filename)[1].lower()
        detected_type = common.detect_media_type(file_ext)
        if detected_type is None:
            raise HTTPException(status_code=400, detail=f"不支持的文件格式: {file_ext}")

        # 大小限制从配置读取（runtime.security.max_upload_*_mb），常量仅作回退默认值
        security_cfg = (config.get("runtime", {}) or {}).get("security", {}) or {}
        max_image_size = int(security_cfg.get("max_upload_image_mb", 50) or 50) * 1024 * 1024
        max_video_size = int(security_cfg.get("max_upload_video_mb", 500) or 500) * 1024 * 1024

        if detected_type == "image":
            if file_ext not in common.ALLOWED_IMAGE_EXTENSIONS:
                raise HTTPException(status_code=400, detail=f"不支持的图片格式: {file_ext}")
            contents = await file.read()
            if len(contents) > max_image_size:
                raise HTTPException(
                    status_code=400, detail=f"图片文件大小超过限制（最大 {max_image_size // (1024*1024)}MB）"
                )
            # 魔数校验：防止伪装扩展名上传恶意文件 (T4-2)
            magic_ok, _, magic_err = validate_upload_magic(contents, file_ext)
            if not magic_ok:
                raise HTTPException(status_code=400, detail=magic_err)
            await file.seek(0)
            _, input_path = await file_cache.save_upload_file(file, sub_dir="image")
        else:
            if file_ext not in common.ALLOWED_VIDEO_EXTENSIONS:
                raise HTTPException(status_code=400, detail=f"不支持的视频格式: {file_ext}")
            contents = await file.read()
            if len(contents) > max_video_size:
                raise HTTPException(
                    status_code=400, detail=f"视频文件大小超过限制（最大 {max_video_size // (1024*1024)}MB）"
                )
            # 魔数校验：防止伪装扩展名上传恶意文件 (T4-2)
            magic_ok, _, magic_err = validate_upload_magic(contents, file_ext)
            if not magic_ok:
                raise HTTPException(status_code=400, detail=magic_err)
            await file.seek(0)
            _, input_path = await file_cache.save_upload_file(file, sub_dir="video")

    elif folder_path and folder_path.strip():
        folder = Path(folder_path.strip())
        if not await asyncio.to_thread(folder.exists) or not await asyncio.to_thread(folder.is_dir):
            raise HTTPException(status_code=400, detail=f"文件夹不存在: {folder_path}")

        media_files = []
        for root, _dirs, files in await asyncio.to_thread(lambda: list(os.walk(folder))):
            for fname in sorted(files):
                ext = os.path.splitext(fname)[1].lower()
                if ext in common.IMAGE_EXTENSIONS or ext in common.VIDEO_EXTENSIONS:
                    media_files.append((os.path.join(root, fname), ext))
        if not media_files:
            raise HTTPException(status_code=400, detail=f"文件夹中未找到图片或视频: {folder_path}")
        input_path, file_ext = media_files[0]
        detected_type = common.detect_media_type(file_ext)

    # ============== 两倍模式后端互斥校验 ==============
    # 当 double_res=True 且输入是图片时，忽略任何客户端传入的分辨率，
    # 用 Pillow 重新读取真实宽高并按 short_edge × 2 覆写 raw_params.resolution。
    # 输入非图片或解析失败时 fail-safe 保留原数值，不抛异常打断任务。
    common.enforce_double_resolution_if_enabled(raw_params, detected_type, input_path)

    # ============== 磁盘空间预检（成本治理 P0-1） ==============
    # 输出目录所在磁盘剩余空间不足时拒绝任务（DiskSpaceError → 全局处理器 507），
    # 防止长视频帧落盘阶段写满磁盘
    common.ensure_disk_space(
        os.path.join(os.getcwd(), "outputs"),
        float((config.get("retention", {}) or {}).get("disk_min_free_gb", 5.0) or 0),
    )

    task_type = raw_params.task_type
    if task_type == "auto":
        task_type = detected_type or "image"
    elif task_type not in ("image", "video"):
        raise HTTPException(status_code=400, detail=f"无效的任务类型: {task_type}")

    dit_model = raw_params.dit_model
    use_model_size = model_size_from_dit_model(dit_model)
    params: ImageRestoreParams | VideoRestoreParams
    if task_type == "image":
        image_fields = {k: v for k, v in raw_params.model_dump().items() if k in ImageRestoreParams.model_fields}
        params = ImageRestoreParams(**image_fields)
    else:
        video_fields = {
            "seed": raw_params.seed,
            "resolution": raw_params.resolution,
            "max_resolution": raw_params.max_resolution,
            "cache_model": raw_params.dit_cache_model,
            "blocks_to_swap": raw_params.blocks_to_swap,
            "batch_size": raw_params.batch_size,
            "force_reload_dit": raw_params.force_reload_dit,
        }
        params = VideoRestoreParams(**video_fields)

    task_id = uuid.uuid4().hex[: config.get("runtime", {}).get("task", {}).get("id_length", 16)]
    record = HistoryRecord(
        task_type=task_type,
        input_file=input_path,
        model_size=use_model_size,
        status="pending",
        parameters=params.model_dump_json(),
    )
    record_id = await history_db.add_record(record)
    await common.create_task_state(task_id, record_id, history_db, task_type=task_type)
    engine = model_registry.get_engine()
    on_cancel = engine.request_cancel if engine else None

    if task_type == "image":
        img_params = params if isinstance(params, ImageRestoreParams) else ImageRestoreParams()
        await task_queue.submit(
            task_id,
            lambda: process_image_task(task_id, record_id, input_path, img_params, history_db, task_queue),
            on_cancel=on_cancel,
        )
    else:
        vid_params = params if isinstance(params, VideoRestoreParams) else VideoRestoreParams()
        await task_queue.submit(
            task_id,
            lambda: process_video_task(
                task_id, record_id, input_path, use_model_size, vid_params, history_db, task_queue
            ),
            on_cancel=on_cancel,
        )

    return respond_success(
        {
            "task_id": task_id,
            "record_id": record_id,
            "task_type": task_type,
            "status": "pending",
            "message": "修复任务已创建并加入队列",
        }
    )
