#!/usr/bin/env python3
"""系统设置管理路由模块。

提供系统配置读取/更新、模型加载/卸载/切换、语言切换、
本地目录浏览、资源管理器打开等端点。包含路径安全校验防止路径遍历。

API 端点：
- GET /api/system/settings: 获取当前设置
- POST /api/system/settings: 更新设置
- POST /api/system/model/load: 加载模型
- POST /api/system/model/unload: 卸载模型
- POST /api/system/model/switch: 切换模型
- GET /api/system/model/status: 获取模型状态
- POST /api/system/locale: 切换语言
- GET /api/system/locales: 获取可用语言列表
- GET /api/system/browse-dir: 浏览本地目录
- POST /api/system/open-explorer: 在资源管理器中打开路径

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.integrated_app.config import save_config
from app.integrated_app.dependencies import (
    get_config,
    get_i18n,
    get_model_manager,
)
from app.integrated_app.i18n import I18n
from app.integrated_app.model_manager import ModelManager
from app.integrated_app.security.audit import audit_event
from app.integrated_app.security.path_guard import PathGuard, build_default_path_guard

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["设置"])

# 兼容保留：历史遗留的模块级白名单常量（恒为空 = 不限制）。
# 生产端点 browse-dir / open-explorer 已改为经 _path_guard_from_config()
# 显式传入与 scan/download 同源的 runtime.security.allowed_base_dirs 白名单，
# 不再依赖该默认值；直接调用 validate_path() 而不传 allowed_roots 视为不受限。
ALLOWED_ROOT_DIRS: list[str] = []


def _path_guard_from_config(config: dict) -> PathGuard:
    """从应用配置构建 browse/open 端点的路径白名单。

    与 scan-folder / download 端点同源（runtime.security.allowed_base_dirs），
    保证所有用户可控路径端点共用同一份白名单事实来源。

    Args:
        config: 应用配置字典（get_config 依赖注入）。

    Returns:
        PathGuard: 已按项目根解析的路径守卫实例。
    """
    security_cfg = config.get("runtime", {}).get("security", {})
    allowed_dirs = security_cfg.get("allowed_base_dirs", ["outputs/", "data/uploads/"])
    return build_default_path_guard(os.getcwd(), allowed_dirs)


def _allowed_roots_of(path_guard: PathGuard) -> list[str]:
    """取白名单根目录的字符串形式（validate_path 入参）。"""
    return [str(p) for p in path_guard.allowed_dirs]


def validate_path(path: str, allowed_roots: list[str] | None = None) -> str:
    """验证路径安全性，防止路径遍历攻击（内部工具函数）。

    安全校验步骤：
    1. 拒绝空路径
    2. 拒绝原始输入中包含 '..' 的路径
    3. 使用 os.path.realpath() 解析真实路径（消除符号链接）
    4. 再次检查解析后的路径是否包含 '..'
    5. 如配置了允许根目录列表，验证路径在允许范围内
       （Path.is_relative_to 语义：`allowed` 不会误放行兄弟目录 `allowed_evil`，
        修复历史 startswith 前缀匹配的兄弟目录绕过）

    Args:
        path: 待验证的路径字符串。
        allowed_roots: 允许的根目录列表，为空或 None 则不限制（除路径遍历外）。

    Returns:
        解析后的真实绝对路径。

    Raises:
        HTTPException: 路径不安全时抛出 400 或 403。
    """
    if not path:
        raise HTTPException(status_code=400, detail="路径为空")

    if ".." in path:
        raise HTTPException(status_code=400, detail="路径不允许包含 '..'")

    real_path = os.path.realpath(path)

    if ".." in real_path:
        raise HTTPException(status_code=400, detail="解析后的路径不允许包含 '..'")

    roots = allowed_roots if allowed_roots is not None else ALLOWED_ROOT_DIRS
    if roots and not any(Path(real_path).is_relative_to(Path(os.path.realpath(r))) for r in roots):
        raise HTTPException(status_code=403, detail="路径不在允许的目录范围内")

    return real_path


class ModelLoadRequest(BaseModel):
    """模型加载请求体。

    Attributes:
        size: 模型尺寸，如 "3b"、"7b"，默认 "3b"。
        device: 目标设备，如 "cuda:0"，默认 None（自动选择）。
        precision: 精度模式，"fp16"/"fp8"，默认 None（自动选择）。
    """

    size: str = "3b"
    device: str | None = None
    precision: str | None = None


class ModelSwitchRequest(BaseModel):
    """模型切换请求体。

    Attributes:
        size: 目标模型尺寸，如 "3b"、"7b"，默认 "3b"。
        device: 目标设备，默认 None。
        precision: 精度模式，默认 None。
    """

    size: str = "3b"
    device: str | None = None
    precision: str | None = None


class SettingsUpdateRequest(BaseModel):
    """设置更新请求体。

    所有字段均为可选，仅传入需要更新的字段。

    Attributes:
        default_model_size: 默认模型尺寸。
        default_precision: 默认精度。
        default_locale: 默认语言。
        auto_load: 是否自动加载模型。
        default_resolution_h: 默认输出高度。
        default_resolution_w: 默认输出宽度。
        seed: 默认随机种子。
        allowed_base_dirs: 允许访问的基础目录白名单，None 表示不修改。
    """

    default_model_size: str | None = None
    default_precision: str | None = None
    default_locale: str | None = None
    auto_load: bool | None = None
    default_resolution_h: int | None = None
    default_resolution_w: int | None = None
    seed: int | None = None
    allowed_base_dirs: list[str] | None = None


@router.get("/settings")
async def get_settings(config: dict = Depends(get_config)):
    """获取当前系统设置（含用户偏好）。

    API 端点：GET /api/system/settings

    请求参数：无

    返回格式（JSON）：
    {
        "model": { ... },        // 模型相关配置
        "gpu": { ... },          // GPU 相关配置
        "i18n": { ... },         // 国际化配置
        "restore": { ... },      // 修复相关配置
        "security": { ... },     // 安全相关配置（含 allowed_base_dirs 白名单）
        "user_preferences": { ... }  // 用户偏好设置
    }

    Args:
        config: 应用配置（通过依赖注入）。

    Returns:
        JSONResponse 包含当前配置。
    """
    try:
        from app.integrated_app.optimization.webui_enhancement import SettingsPersistence

        persistence = SettingsPersistence()
        user_prefs = persistence.load().to_dict()
    except Exception:
        user_prefs = {}

    return JSONResponse(
        {
            "model": config.get("model", {}),
            "gpu": config.get("gpu", {}),
            "i18n": config.get("i18n", {}),
            "restore": config.get("restore", {}),
            "security": config.get("runtime", {}).get("security", {}),
            "user_preferences": user_prefs,
        }
    )


@router.post("/settings")
async def update_settings(
    request: Request,
    settings: SettingsUpdateRequest,
    config: dict = Depends(get_config),
):
    """更新系统设置并保存到配置文件。

    API 端点：POST /api/system/settings

    数据治理 P1-4：变更成功后写入安全审计通道（CONFIG_UPDATE 事件），
    allowed_base_dirs 等安全敏感键的修改可事后追溯。

    请求体（JSON，所有字段可选）：见 SettingsUpdateRequest。

    返回格式（JSON）：
    {
        "status": "ok",
        "message": "设置已更新"
    }

    Args:
        request: 当前请求（审计元数据用）。
        settings: 设置更新请求体。
        config: 应用配置（通过依赖注入）。

    Returns:
        JSONResponse 确认更新成功。
    """
    changed_keys: list[str] = []
    if settings.default_model_size is not None:
        config.setdefault("model", {})["default_size"] = settings.default_model_size
        changed_keys.append("model.default_size")
    if settings.default_precision is not None:
        config.setdefault("model", {})["default_precision"] = settings.default_precision
        changed_keys.append("model.default_precision")
    if settings.auto_load is not None:
        config.setdefault("model", {})["auto_load"] = settings.auto_load
        changed_keys.append("model.auto_load")
    if settings.default_locale is not None:
        config.setdefault("i18n", {})["default_locale"] = settings.default_locale
        changed_keys.append("i18n.default_locale")
    if settings.default_resolution_h is not None:
        config.setdefault("restore", {})["default_resolution_h"] = settings.default_resolution_h
        changed_keys.append("restore.default_resolution_h")
    if settings.default_resolution_w is not None:
        config.setdefault("restore", {})["default_resolution_w"] = settings.default_resolution_w
        changed_keys.append("restore.default_resolution_w")
    if settings.seed is not None:
        config.setdefault("restore", {})["seed"] = settings.seed
        changed_keys.append("restore.seed")
    if settings.allowed_base_dirs is not None:
        config.setdefault("runtime", {}).setdefault("security", {})["allowed_base_dirs"] = settings.allowed_base_dirs
        changed_keys.append("runtime.security.allowed_base_dirs")

    await run_in_threadpool(save_config, config)

    # 数据治理 P1-4：配置热改审计（best-effort，绝不阻断业务）
    if changed_keys:
        audit_event("CONFIG_UPDATE", request=request, keys=changed_keys)

    try:
        from app.integrated_app.optimization.webui_enhancement import SettingsPersistence

        persistence = SettingsPersistence()
        prefs = persistence.load()
        if settings.default_resolution_h is not None:
            prefs.default_resolution = settings.default_resolution_h
        if settings.seed is not None:
            prefs.default_seed = settings.seed
        persistence.save(prefs)
    except Exception as e:
        logger.debug(f"用户偏好同步保存跳过: {e}")

    return JSONResponse({"status": "ok", "message": "设置已更新"})


@router.post("/model/load")
async def load_model(
    req: ModelLoadRequest,
    model_manager: ModelManager = Depends(get_model_manager),
):
    """加载 SeedVR2 模型到 GPU。

    API 端点：POST /api/system/model/load

    请求体（JSON）：见 ModelLoadRequest。

    返回格式（JSON）：模型加载结果状态。

    错误响应：
    - 500: 模型加载失败，返回错误信息

    Args:
        req: 模型加载请求体。
        model_manager: 模型管理器实例（通过依赖注入）。

    Returns:
        JSONResponse 包含加载结果。
    """
    try:
        result = await model_manager.load_model(model_size=req.size, device=req.device, precision=req.precision)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"模型加载失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@router.post("/model/unload")
async def unload_model(model_manager: ModelManager = Depends(get_model_manager)):
    """从 GPU 卸载当前模型，释放显存。

    API 端点：POST /api/system/model/unload

    请求体：无

    返回格式（JSON）：卸载结果状态。

    错误响应：
    - 500: 卸载失败

    Args:
        model_manager: 模型管理器实例（通过依赖注入）。

    Returns:
        JSONResponse 包含卸载结果。
    """
    try:
        result = await model_manager.unload_model()
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"模型卸载失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@router.post("/model/switch")
async def switch_model(
    req: ModelSwitchRequest,
    model_manager: ModelManager = Depends(get_model_manager),
):
    """切换到另一个尺寸/精度的模型（先卸载后加载）。

    API 端点：POST /api/system/model/switch

    请求体（JSON）：见 ModelSwitchRequest。

    返回格式（JSON）：切换结果状态。

    错误响应：
    - 500: 切换失败

    Args:
        req: 模型切换请求体。
        model_manager: 模型管理器实例（通过依赖注入）。

    Returns:
        JSONResponse 包含切换结果。
    """
    try:
        result = await model_manager.switch_model(model_size=req.size, device=req.device, precision=req.precision)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"模型切换失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@router.get("/model/status")
async def model_status(model_manager: ModelManager = Depends(get_model_manager)):
    """获取当前模型加载状态。

    API 端点：GET /api/system/model/status

    请求参数：无

    返回格式（JSON）：模型状态详情（是否加载、模型尺寸、设备、显存占用等）。

    Args:
        model_manager: 模型管理器实例（通过依赖注入）。

    Returns:
        JSONResponse 包含模型状态。
    """
    return JSONResponse(model_manager.get_status())


@router.post("/locale")
async def set_locale(
    request: Request,
    i18n: I18n = Depends(get_i18n),
    config: dict = Depends(get_config),
):
    """切换界面语言。

    API 端点：POST /api/system/locale

    请求体（JSON）：
    {
        "locale": str  // 语言代码，如 "zh"、"zh-TW"、"en"、"ja"、"fr"
    }

    返回格式（JSON）：
    {
        "status": "ok",
        "locale": str,
        "message": str
    }

    Args:
        request: FastAPI 请求对象。
        i18n: 国际化实例（通过依赖注入）。
        config: 应用配置（通过依赖注入）。

    Returns:
        JSONResponse 确认语言切换。
    """
    try:
        body = await request.json()
        locale = body.get("locale", "zh")
    except Exception:
        locale = "zh"

    i18n.set_locale(locale)

    config.setdefault("i18n", {})["default_locale"] = locale
    await run_in_threadpool(save_config, config)

    # 数据治理 P1-4：配置热改审计（语言切换同样落审计通道）
    audit_event("CONFIG_UPDATE", request=request, keys=["i18n.default_locale"], value=locale)

    return JSONResponse(
        {
            "status": "ok",
            "locale": locale,
            "message": f"语言已切换为 {i18n.get_locale_name(locale)}",
        }
    )


@router.get("/locales")
async def get_locales(i18n: I18n = Depends(get_i18n)):
    """获取可用语言列表。

    API 端点：GET /api/system/locales

    请求参数：无

    返回格式（JSON）：
    {
        "current": str,        // 当前语言代码
        "locales": [
            {
                "code": str,   // 语言代码
                "name": str    // 语言名称
            }
        ]
    }

    Args:
        i18n: 国际化实例（通过依赖注入）。

    Returns:
        JSONResponse 包含可用语言列表。
    """
    locales = []
    for code in i18n.available_locales:
        locales.append(
            {
                "code": code,
                "name": i18n.get_locale_name(code),
            }
        )
    return JSONResponse(
        {
            "current": i18n.current_locale,
            "locales": locales,
        }
    )


@router.get("/browse-dir")
async def browse_directory(
    path: str = "",
    show_files: bool = False,
    config: dict = Depends(get_config),
):
    """浏览白名单内的本地目录，返回子目录列表（用于文件夹选择器）。

    API 端点：GET /api/system/browse-dir

    查询参数：
    - path (optional): 要浏览的目录路径；为空则返回白名单根目录列表
      （runtime.security.allowed_base_dirs，与 scan/download 端点同源）。
      不再枚举盘符，避免白名单外文件系统结构信息泄漏。
    - show_files (optional): 是否同时显示文件，默认 false

    返回格式（JSON）：
    {
        "current_path": str,
        "parent_path": str,
        "items": [
            {
                "name": str,
                "path": str,
                "type": "directory"|"file",
                "ext"?: str,    // 文件扩展名（仅文件）
                "size"?: int    // 文件大小（仅文件）
            }
        ]
    }

    错误响应：
    - 403: 路径不在白名单范围内
    - 404: 路径不存在
    - 400: 路径不是目录

    Args:
        path: 目录路径，空字符串返回白名单根目录列表。
        show_files: 是否包含文件列表。
        config: 应用配置（get_config 依赖注入，提供路径白名单）。

    Returns:
        JSONResponse 包含目录内容。

    Raises:
        HTTPException: 路径无效、越出白名单或无权限时抛出。
    """
    path_guard = _path_guard_from_config(config)

    if not path:
        # 根视图：列出白名单根目录（文件夹选择器从安全模型内的入口开始导航）
        items: list[dict[str, str | int]] = []
        for root in path_guard.allowed_dirs:
            if await asyncio.to_thread(os.path.isdir, root):
                items.append({"name": str(root), "path": str(root), "type": "directory"})
        return JSONResponse({"current_path": "", "parent_path": "", "items": items})

    path = validate_path(path, allowed_roots=_allowed_roots_of(path_guard))

    if not await asyncio.to_thread(os.path.exists, path):
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    if not await asyncio.to_thread(os.path.isdir, path):
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    # 类型注解已在根视图分支声明（同一变量复用，避免 mypy no-redef）
    items = []
    try:
        entries = await asyncio.to_thread(
            lambda: sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}") from e

    for entry in entries:
        try:
            if entry.is_dir():
                items.append(
                    {
                        "name": entry.name,
                        "path": entry.path,
                        "type": "directory",
                    }
                )
            elif show_files and entry.is_file():
                ext = os.path.splitext(entry.name)[1].lower()
                size = (await asyncio.to_thread(entry.stat)).st_size
                items.append(
                    {
                        "name": entry.name,
                        "path": entry.path,
                        "type": "file",
                        "ext": ext,
                        "size": size,
                    }
                )
        except (PermissionError, OSError):
            continue

    parent = os.path.dirname(path.rstrip("/\\"))
    if parent == path.rstrip("/\\"):
        parent = ""
    # 「向上一级」越出白名单时收回到根视图（前端以空 path 回到白名单根列表）
    if parent and not path_guard.is_safe_path(parent):
        parent = ""

    return JSONResponse(
        {
            "current_path": path,
            "parent_path": parent,
            "items": items,
        }
    )


@router.post("/open-explorer")
async def open_in_explorer(request: Request, config: dict = Depends(get_config)):
    """在系统资源管理器中打开白名单内的指定目录。

    API 端点：POST /api/system/open-explorer

    请求体（JSON）：
    {
        "path": str  // 要打开的目录路径（仅目录，且须在路径白名单内）
    }

    返回格式（JSON）：
    {
        "success": true,
        "message": str
    }

    支持平台：
    - Windows: 使用 os.startfile() 打开资源管理器
    - macOS: 使用 open 命令
    - Linux: 使用 xdg-open 命令

    错误响应：
    - 400: 路径为空 / 非目录路径
    - 403: 路径不在白名单范围内
    - 500: 打开失败

    Args:
        request: FastAPI 请求对象。
        config: 应用配置（get_config 依赖注入，提供路径白名单）。

    Returns:
        JSONResponse 确认打开操作。

    Raises:
        HTTPException: 路径无效、越出白名单或打开失败时抛出。
    """
    body = await request.json()
    path = body.get("path", "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="路径为空")

    path_guard = _path_guard_from_config(config)
    path = validate_path(path, allowed_roots=_allowed_roots_of(path_guard))

    # 收敛为仅目录：对文件调用 os.startfile 会以默认程序打开文件，
    # 在暴露部署下等价于远程触发本机文件执行的原语（评估报告 R1）
    if not await asyncio.to_thread(os.path.isdir, path):
        raise HTTPException(status_code=400, detail="仅支持打开目录路径")

    try:
        if sys.platform == "win32":
            await run_in_threadpool(os.startfile, path)
        elif sys.platform == "darwin":
            await run_in_threadpool(subprocess.Popen, ["open", path])
        else:
            await run_in_threadpool(subprocess.Popen, ["xdg-open", path])
        return JSONResponse({"success": True, "message": f"已打开: {path}"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"打开失败: {str(e)}") from e
