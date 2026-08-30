#!/usr/bin/env python3
"""容器编排就绪探针路由模块。

提供 K8s readinessProbe / Docker 健康检查可直接消费的就绪判定端点，
与 health.py 的 /ping（liveness 语义）互补：

API 端点：
- GET /api/system/ready: 就绪探针（模型预热中返回 503）

设计语义（对应云原生评估报告 P1-3）：
- ``load_in_progress=True`` → 503（模型正在加载/权重 SHA256 校验中，暂不接流）
- 其余情况 → 200（含 model_loaded / gpu_available 供编排层诊断）
- 采用独立端点而非改造 /health：/health 的信息性契约已被测试与前端锁定，
  readiness 的 503 语义必须由专用端点承载，避免破坏既有契约

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.integrated_app.dependencies import get_gpu_backend, get_model_manager
from app.integrated_app.gpu_backend import GPUBackendManager
from app.integrated_app.model_manager import ModelManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["系统状态"])


@router.get("/ready")
async def readiness_probe(
    request: Request,
    model_manager: ModelManager = Depends(get_model_manager),
    gpu_backend: GPUBackendManager = Depends(get_gpu_backend),
) -> JSONResponse:
    """就绪探针端点。

    API 端点：GET /api/system/ready

    供 Kubernetes readinessProbe / Docker 健康检查 / 负载均衡器判断实例
    是否可以接收流量。模型预热（加载 + 权重完整性校验）期间返回 503，
    编排层会在 startupProbe 宽限期内持续重试。

    请求参数：无

    返回格式（JSON）：
        200: {"status": "ready", "model_loaded": bool, "gpu_available": bool}
        503: {"status": "unavailable", "reason": "model_loading",
              "model_loaded": bool, "gpu_available": bool}（附 Retry-After 头）

    Args:
        request: FastAPI 请求对象。
        model_manager: 模型管理器实例（通过依赖注入）。
        gpu_backend: GPU 后端管理器实例（通过依赖注入）。

    Returns:
        JSONResponse：就绪返回 200，预热中返回 503。
    """
    try:
        status = model_manager.get_status()
    except Exception:  # 探针永不因依赖异常而 5xx 崩溃，降级为未就绪
        logger.debug("readiness 探针读取模型状态失败，按未加载处理", exc_info=True)
        status = {}

    # 兼容真实 registry 键（model_loaded）与测试 mock 键（loaded）
    model_loaded = bool(status.get("model_loaded", status.get("loaded", False)))
    load_in_progress = bool(status.get("load_in_progress", False))
    gpu_available = bool(gpu_backend.is_gpu_available)

    if load_in_progress:
        return JSONResponse(
            {
                "status": "unavailable",
                "reason": "model_loading",
                "model_loaded": model_loaded,
                "gpu_available": gpu_available,
            },
            status_code=503,
            headers={"Retry-After": "5"},
        )

    return JSONResponse(
        {
            "status": "ready",
            "model_loaded": model_loaded,
            "gpu_available": gpu_available,
        }
    )
