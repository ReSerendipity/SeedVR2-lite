#!/usr/bin/env python3
"""系统健康检查路由模块。

提供系统存活探针和详细健康检查端点，用于负载均衡、监控和服务状态查询。

API 端点：
- GET /api/system/ping: 轻量级存活探针
- GET /api/system/health: 详细系统健康检查（含系统资源、模型、GPU 信息）

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
注意：SeedVR2 仅支持 NVIDIA CUDA GPU。
"""

import asyncio
import logging
import os
import platform
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.integrated_app.dependencies import get_gpu_backend, get_model_manager
from app.integrated_app.gpu_backend import GPUBackendManager
from app.integrated_app.model_manager import ModelManager
from app.integrated_app.version import get_app_version

logger = logging.getLogger(__name__)


class PingResponse(BaseModel):
    """GET /api/system/ping 响应模型（P2-9：核心端点 OpenAPI 契约化）。"""

    status: str
    version: str
    gpu_available: bool


router = APIRouter(prefix="/api/system", tags=["系统状态"])

_start_time = time.time()


@router.post("/shutdown")
async def shutdown_server(request: Request) -> JSONResponse:
    """本机优雅关闭端点（桌面端迁移 B-5）。

    仅允许 127.0.0.1 / ::1 本机调用。触发共享的 shutdown_components 优雅关闭
    （停队列、卸载模型、关历史库）后退出进程，供桌面壳替代 taskkill /F 使用。

    API 端点：POST /api/system/shutdown

    请求参数：无（调用方需为本机回环地址）

    返回格式（JSON）：
    {
        "status": "shutting_down"
    }
    """
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="仅允许本机调用")

    from app.integrated_app.app_server import shutdown_components

    async def _do_shutdown() -> None:
        await asyncio.sleep(0.2)  # 让响应先发出再开始关闭
        try:
            await shutdown_components(request.app)
        finally:
            os._exit(0)

    asyncio.create_task(_do_shutdown())
    return JSONResponse({"status": "shutting_down"})


@router.get("/ping", response_model=PingResponse)
async def api_health_check(
    request: Request,
    gpu_backend: GPUBackendManager = Depends(get_gpu_backend),
):
    """轻量级存活探针端点。

    API 端点：GET /api/system/ping

    供负载均衡器或监控系统快速检测服务是否存活。响应体小、处理快，
    不做复杂的资源检查。

    请求参数：无

    返回格式（JSON）：
    {
        "status": "ok",
        "version": str,
        "gpu_available": bool
    }

    Args:
        request: FastAPI 请求对象。
        gpu_backend: GPU 后端管理器实例（通过依赖注入）。

    Returns:
        JSON 响应，包含存活状态、版本号和 GPU 可用性。
    """
    return {
        "status": "ok",
        "version": get_app_version(),
        "gpu_available": gpu_backend.is_gpu_available,
    }


@router.get("/health")
async def health_check(
    request: Request,
    model_manager: ModelManager = Depends(get_model_manager),
    gpu_backend: GPUBackendManager = Depends(get_gpu_backend),
):
    """详细系统健康检查端点。

    API 端点：GET /api/system/health

    返回系统运行状态的详细信息，包括：
    - 服务状态与运行时长
    - 系统信息（平台、Python 版本、CPU、内存）
    - 模型加载状态
    - GPU 信息（后端、设备名、可用性）
    - 安全状态（核心模块完整性自检结果，供桌面壳展示篡改告警）

    请求参数：无

    返回格式（JSON）：
    {
        "status": "ok",
        "uptime_seconds": float,
        "system": { ... },
        "model": { ... },      // 模型状态详情
        "gpu": { ... },
        "security": {
            "integrity": {
                "checked": bool,
                "total": int,
                "failed": int,
                "failed_files": [str],
                "manifest_signed": bool
            }
        }
    }

    注意：如 psutil 未安装，系统资源字段返回 0。

    Args:
        request: FastAPI 请求对象（读取 app.state 中的完整性自检状态）。
        model_manager: 模型管理器实例（通过依赖注入）。
        gpu_backend: GPU 后端管理器实例（通过依赖注入）。

    Returns:
        JSONResponse 包含详细健康信息。
    """
    try:
        import psutil

        cpu_count = psutil.cpu_count()
        from app.integrated_app.engines._memory_utils import _get_system_memory

        mem = _get_system_memory()
        memory_total_gb = round(mem.total / (1024**3), 2)
        memory_available_gb = round(mem.available / (1024**3), 2)
        memory_pct = mem.percent
    except ImportError:
        cpu_count = 0
        memory_total_gb = 0
        memory_available_gb = 0
        memory_pct = 0

    uptime = round(time.time() - _start_time, 1)

    integrity = getattr(request.app.state, "integrity_selfcheck", None)
    if integrity is None:
        integrity = {
            "checked": False,
            "total": 0,
            "failed": 0,
            "skipped": 0,
            "failed_files": [],
            "manifest_signed": False,
        }

    preflight = getattr(request.app.state, "startup_preflight", None)
    if preflight is None:
        preflight = {
            "cuda_available": False,
            "cuda_device": "",
            "ffmpeg_available": False,
        }

    return JSONResponse(
        {
            "status": "ok",
            "uptime_seconds": uptime,
            "system": {
                "platform": platform.platform(),
                "python_version": platform.python_version(),
                "cpu_count": cpu_count,
                "memory_total_gb": memory_total_gb,
                "memory_available_gb": memory_available_gb,
                "memory_utilization_pct": memory_pct,
            },
            "model": model_manager.get_status(),
            "gpu": {
                "backend": gpu_backend.backend.value,
                "device_name": gpu_backend.device_name,
                "is_gpu_available": gpu_backend.is_gpu_available,
            },
            "security": {"integrity": integrity},
            "preflight": preflight,
        }
    )
