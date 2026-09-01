#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""
SeedVR2 - 应用服务器入口模块

所属项目：SeedVR2 (AI-powered video & image super-resolution toolkit)
核心功能：
    - FastAPI 应用创建与配置
    - 应用生命周期管理（启动初始化、优雅关闭）
    - 核心组件初始化与依赖注入
    - 中间件注册（CORS、CSRF、Basic Auth、速率限制、错误处理）
    - 静态文件服务与模板引擎配置
    - 路由自动发现与注册
    - 端口冲突自动处理与服务器启动

核心技术栈：
    - FastAPI 0.100+ 作为 Web 框架
    - Uvicorn 作为 ASGI 服务器
    - Pydantic 用于配置验证
    - Jinja2 用于模板渲染
    - 观察者模式实现模型状态到 SSE 的桥接
"""

import asyncio
import json
import logging
import logging.handlers
import os
import sys
import time
import webbrowser
from contextlib import asynccontextmanager, suppress

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

# 统一日志格式：时间戳 + 级别 + 进程/线程 + 模块位置 + 请求ID + 追踪ID + 消息
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [PID:%(process)d TID:%(thread)d] [%(name)s:%(filename)s:%(lineno)d] [req=%(request_id)s] [trace=%(trace_id)s] %(message)s"  # noqa: E501


class JSONLogFormatter(logging.Formatter):
    """结构化 JSON 日志格式器（LOG_FORMAT=json 时启用）。

    输出单行 JSON 事件流（ts/level/logger/request_id/trace_id/msg...），
    供 ELK / Loki / CloudWatch 等采集器直接解析，无需 grok 正则（12-Factor XI）。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "module": f"{record.module}:{record.lineno}",
            "pid": record.process,
            "request_id": getattr(record, "request_id", "-"),
            "trace_id": getattr(record, "trace_id", "-"),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        try:
            return json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):  # 消息含不可序列化对象时降级为字符串
            payload["msg"] = str(payload.get("msg", ""))
            return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(config: dict | None = None) -> None:
    """集中配置日志：控制台 + 按大小轮转的文件双通道输出。

    从 config.yaml 的 ``logging`` 段读取 level / file / max_size_mb / backup_count，
    与配置文件定义保持一致，修复此前仅 basicConfig 输出到 stderr、日志不落盘的问题。
    日志级别与路径支持环境变量覆盖（优先级高于配置）：
    - ``LOG_LEVEL``：DEBUG / INFO / WARNING / ERROR
    - ``LOG_PATH``：日志文件路径，默认 ``<项目根>/logs/app.log``

    Args:
        config: 应用配置字典，可为 None（使用默认值）。
    """
    log_cfg = (config or {}).get("logging", {}) or {}
    level_name = str(os.environ.get("LOG_LEVEL", log_cfg.get("level", "INFO"))).upper()
    log_level = getattr(logging, level_name, logging.INFO)
    log_file = str(os.environ.get("LOG_PATH", log_cfg.get("file", "logs/app.log")))
    max_bytes = int(log_cfg.get("max_size_mb", 50)) * 1024 * 1024
    backup_count = int(log_cfg.get("backup_count", 3))

    handlers: list[logging.Handler | None] = [
        logging.StreamHandler(sys.stdout),
    ]
    if max_bytes > 0:
        try:
            log_path = os.path.join(PROJECT_ROOT, log_file)
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            handlers.append(
                logging.handlers.RotatingFileHandler(
                    log_path,
                    maxBytes=max_bytes,
                    backupCount=backup_count,
                    encoding="utf-8",
                )
            )
        except (OSError, ValueError) as exc:  # 文件路径不可写时降级为仅控制台
            if handlers[0] is not None:
                handlers[0].setLevel(logging.WARNING)
            logging.getLogger("seedvr2").warning(f"文件日志初始化失败，降级为仅控制台输出: {exc}")

    formatter: logging.Formatter
    # P3-8 结构化日志开关：LOG_FORMAT=json 输出 JSON 事件流（容器/聚合采集场景），
    # 默认 text 保持人类可读格式
    if str(os.environ.get("LOG_FORMAT", "text")).lower() == "json":
        formatter = JSONLogFormatter()
    else:
        formatter = logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    for handler in handlers:
        if handler is not None:
            handler.setFormatter(formatter)
            handler.addFilter(RequestIDLogFilter())
            handler.addFilter(TraceLogFilter())

    logging.basicConfig(
        level=log_level,
        handlers=[h for h in handlers if h is not None],
        force=True,
    )


from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.responses import Response  # noqa: E402

from app.integrated_app.cache import FileCache  # noqa: E402
from app.integrated_app.config import load_config  # noqa: E402
from app.integrated_app.gpu_backend import gpu_manager  # noqa: E402
from app.integrated_app.history_db import HistoryDB  # noqa: E402
from app.integrated_app.i18n import I18n  # noqa: E402
from app.integrated_app.middleware.csrf import CSRFMiddleware  # noqa: E402
from app.integrated_app.middleware.request_id import RequestIDLogFilter, RequestIDMiddleware  # noqa: E402
from app.integrated_app.middleware.security_headers import SecurityHeadersMiddleware  # noqa: E402
from app.integrated_app.middleware.tracing import TraceLogFilter, TracingMiddleware  # noqa: E402
from app.integrated_app.model_manager import ModelManager  # noqa: E402
from app.integrated_app.model_registry import model_registry  # noqa: E402
from app.integrated_app.routes.system.sse import event_bus  # noqa: E402
from app.integrated_app.task_queue import TaskQueue  # noqa: E402
from app.integrated_app.utils.response import respond_error, respond_success  # noqa: E402
from app.integrated_app.version import get_app_version  # noqa: E402

logger = logging.getLogger(__name__)


def _bridge_model_status_to_sse(event_name: str, payload: dict) -> None:
    """将 model_registry 状态变更桥接到 SSE 事件总线。

    作为 model_registry 的观察者监听器，在模型状态变更时通过 event_bus 广播，
    使 SSE 客户端能实时收到 model_status 事件。
    使用观察者模式解耦 model_registry 与 event_bus 的直接依赖。

    Args:
        event_name: 事件名称，如 'model_loading'、'model_loaded'、'model_unloaded'。
        payload: 事件数据字典，包含模型状态详情。
    """
    event_bus.publish(event_name, payload)


class V1AliasMiddleware:
    """``/api/v1/*`` → ``/api/*`` 路径别名重写（P2-9 API 版本化入口）。

    为未来破坏性变更预留 ``/api/v1`` 版本前缀：新客户端可从 /api/v1/... 访问，
    现有 /api/... 路径永久保持兼容。以纯 ASGI 中间件在最外层重写 scope["path"]，
    零路由重复注册；CSRF/Auth 等下游中间件与路由看到的是重写后的规范路径。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            path = scope.get("path") or ""
            if path == "/api/v1":
                scope["path"] = "/api"
            elif path.startswith("/api/v1/"):
                scope["path"] = "/api/" + path[len("/api/v1/") :]
        await self.app(scope, receive, send)


class VersionedStaticFiles(StaticFiles):
    """带版本控制的静态文件处理类。

    继承自 FastAPI StaticFiles，为不同类型的静态资源设置差异化的 Cache-Control 头：
    - CSS/JS 文件：不缓存，每次刷新获取最新版本
    - 字体文件（woff2/woff/ttf/eot/otf）：中期缓存（30天）
    - 图片资源（png/jpg/jpeg/gif/svg/ico/webp）：短期缓存（1天）
    """

    def file_response(self, *args, **kwargs) -> Response:
        """重写 file_response 方法，为不同文件类型添加缓存头。

        Args:
            *args: 位置参数，第一个参数为文件路径。
            **kwargs: 关键字参数。

        Returns:
            Response: 添加了 Cache-Control 头的 HTTP 响应。
        """
        response = super().file_response(*args, **kwargs)
        if args:
            file_path = str(args[0])
            if file_path.endswith((".css", ".js")):
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
                response.headers["Pragma"] = "no-cache"
            elif file_path.endswith((".woff2", ".woff", ".ttf", ".eot", ".otf")):
                response.headers["Cache-Control"] = "public, max-age=2592000"
            elif file_path.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp")):
                response.headers["Cache-Control"] = "public, max-age=86400"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 应用生命周期管理上下文管理器。

    处理应用启动和关闭时的资源初始化与清理：

    启动阶段（yield 前）：
        1. 初始化历史记录数据库
        2. 启动异步任务队列
        3. 注册模型状态到 SSE 的桥接监听器
        4. 恢复数据库中未完成的任务
        5. 启动缓存定期清理任务
        6. 检测 GPU 后端与兼容性
        7. 可选自动加载模型（GPU 可用且配置启用时）
        8. 可选自动打开浏览器访问应用

    关闭阶段（yield 后）：
        1. 移除模型状态监听器
        2. 停止缓存清理任务
        3. 优雅停止任务队列（最多等待30秒）
        4. 卸载模型释放 GPU 显存
        5. 关闭数据库连接

    Args:
        app: FastAPI 应用实例，通过 app.state 访问已初始化的组件。

    Yields:
        None:  yield 点分隔启动和关闭阶段，应用在此期间运行。
    """
    config = app.state.config

    # 核心模块完整性自检 (CWE-912 防御)
    try:
        from app.integrated_app.security.integrity_selfcheck import (
            periodic_selfcheck_loop,
            run_startup_selfcheck,
        )

        security_cfg = config.get("runtime", {}).get("security", {})
        enforce = bool(security_cfg.get("integrity_enforce", False))
        selfcheck = run_startup_selfcheck(enforce=enforce)
        if selfcheck["failed"] > 0:
            logger.error(
                "=" * 60 + "\n"
                "[SECURITY] ⚠️  启动时核心模块完整性自检失败！\n"
                f"    失败文件: {', '.join(selfcheck['failed_files'])}\n"
                "    请检查代码是否被篡改或重新生成清单。\n" + "=" * 60
            )
        recheck_interval = int(security_cfg.get("integrity_recheck_interval_seconds", 1800) or 0)
        if recheck_interval > 0:
            app.state.integrity_recheck_task = asyncio.create_task(periodic_selfcheck_loop(recheck_interval))
            logger.info(f"运行时完整性周期重检已启用（间隔 {recheck_interval}s）")
    except RuntimeError as e:
        # enforce 模式下校验失败：阻断启动，禁止带病运行
        logger.error(f"[SECURITY] 完整性自检强制模式失败，拒绝启动: {e}")
        raise
    except Exception as e:
        logger.debug(f"核心模块完整性自检跳过: {e}")

    history_db: HistoryDB = app.state.history_db
    await history_db.initialize()
    logger.info("历史数据库已初始化")

    task_queue: TaskQueue = app.state.task_queue
    await task_queue.start()
    logger.info("任务队列已启动")

    model_registry.add_listener(_bridge_model_status_to_sse)
    logger.info("已注册模型状态 SSE 桥接监听器")

    # P2-11：任务进度事件接线——task_state_store 的每次状态更新经节流后发布到
    # task_event_bus（按 task_id 分片），SSE /progress 端点由 0.5s 轮询转事件驱动
    from app.integrated_app.services.task_events import task_event_bus
    from app.integrated_app.services.task_state import task_state_store

    _last_progress_publish: dict[str, float] = {}

    def _publish_task_progress(task_id: str, state: dict) -> None:
        status = state.get("status")
        if status in ("completed", "failed", "cancelled"):
            _last_progress_publish.pop(task_id, None)
            task_event_bus.publish_final(
                task_id,
                {
                    "task_id": task_id,
                    "status": status,
                    "progress": state.get("progress", 0),
                    "output_path": state.get("output_path"),
                    "error": state.get("error"),
                    "processing_time": state.get("processing_time", 0),
                    "task_type": state.get("task_type", "single"),
                    "message": state.get("message", ""),
                },
            )
            return
        now = time.monotonic()
        if now - _last_progress_publish.get(task_id, 0.0) < 1.0:
            return  # 进度事件按任务节流到 1s，避免逐帧刷爆订阅者队列
        _last_progress_publish[task_id] = now
        task_event_bus.publish(
            task_id,
            {
                "task_id": task_id,
                "status": status,
                "progress": state.get("progress", 0),
                "current_frame": state.get("current_frame", 0),
                "total_frames": state.get("total_frames", 0),
                "task_type": state.get("task_type", "single"),
                "message": state.get("message", ""),
                "processing_time": state.get("processing_time", 0),
            },
        )

    task_state_store.set_progress_notifier(_publish_task_progress)
    logger.info("任务进度事件总线已接线（SSE 进度推送化，P2-11）")

    try:
        from app.integrated_app.routes.restore import unified as unified_routes

        # 先清理卡死的 processing 任务，再恢复可恢复的任务
        _task_cfg = config.get("runtime", {}).get("task", {})
        stale_threshold = int(_task_cfg.get("stale_threshold_minutes", 30) or 0)
        cleaned_count = await unified_routes.cleanup_stale_tasks(
            history_db, threshold_minutes=stale_threshold if stale_threshold > 0 else 10**9
        )
        if cleaned_count:
            logger.info(f"已清理 {cleaned_count} 个卡死的 processing 任务")

        auto_recover = config.get("runtime", {}).get("task", {}).get("auto_recover", False)
        if auto_recover:
            recovered_count = await unified_routes.recover_tasks(history_db, task_queue, config)
            if recovered_count:
                logger.info(f"已从数据库恢复 {recovered_count} 个未完成任务")
        else:
            logger.info("启动任务自动恢复已关闭 (runtime.task.auto_recover=false)")
    except Exception as e:
        logger.warning(f"恢复未完成任务失败: {e}")

    # 初始化断点续跑管理器并扫描待恢复的 checkpoint
    try:
        from app.integrated_app.checkpoint import TaskCheckpoint

        task_cfg = config.get("runtime", {}).get("task", {})
        ckpt_dir = task_cfg.get("checkpoint_dir", "data/checkpoints")
        project_root_for_ckpt = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        checkpoint_mgr = TaskCheckpoint(os.path.join(project_root_for_ckpt, ckpt_dir))
        pending_checkpoints = checkpoint_mgr.list_checkpoints()
        if pending_checkpoints:
            logger.info(f"发现 {len(pending_checkpoints)} 个待恢复的批量任务 checkpoint")
        app.state.checkpoint_mgr = checkpoint_mgr
    except Exception as e:
        logger.warning(f"初始化断点续跑管理器失败: {e}")
        app.state.checkpoint_mgr = None

    file_cache: FileCache = app.state.file_cache
    file_cache.start_cleanup_task(interval=3600)

    # 启动定期清理卡死任务的后台任务（每5分钟检查一次，阈值 runtime.task.stale_threshold_minutes）
    async def _periodic_stale_cleanup():
        stale_threshold = int(config.get("runtime", {}).get("task", {}).get("stale_threshold_minutes", 30) or 0)
        effective_threshold = stale_threshold if stale_threshold > 0 else 10**9
        while True:
            try:
                await asyncio.sleep(300)  # 每5分钟
                cleaned = await unified_routes.cleanup_stale_tasks(
                    history_db, threshold_minutes=effective_threshold, task_queue=app.state.task_queue
                )
                if cleaned:
                    logger.info(f"定期清理：已清理 {cleaned} 个卡死的 processing 任务")
                # P2-11：顺带清理事件总线过期的最终状态缓存（TTL 60s，5min 扫一次足够）
                task_event_bus.cleanup_expired()
                _last_progress_publish.clear()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"定期清理卡死任务失败: {e}")

    stale_cleanup_task = asyncio.create_task(_periodic_stale_cleanup())
    app.state.stale_cleanup_task = stale_cleanup_task

    # 进度停滞看门狗（P1-8）：唯一 worker 被单个挂死的推理任务无限占用时，
    # 依据任务状态缓存中 (progress, message, current_frame/current_index/current_file)
    # 的签名是否变化判定停滞，超过阈值自动 request_cancel（引擎在阶段检查点协作退出）。
    # progress 回调由推理线程逐帧/逐阶段驱动，真实推理即使整帧计算很慢，
    # current_frame/current_progress 也会持续变化，误杀窗口极大（默认 30 分钟）。
    stall_minutes = int(config.get("runtime", {}).get("task", {}).get("progress_stall_timeout_minutes", 30) or 0)
    if stall_minutes > 0:

        async def _progress_stall_watchdog():
            last_signature = None
            last_change_monotonic = time.monotonic()
            while True:
                await asyncio.sleep(60)
                try:
                    current = app.state.task_queue.current_task_id
                    running_id = current() if callable(current) else current
                    if not running_id:
                        last_signature = None
                        continue
                    state = task_state_store.get_cached(running_id) or {}
                    signature = (
                        state.get("progress"),
                        state.get("message", ""),
                        state.get("current_frame"),
                        state.get("current_index"),
                        state.get("current_file", ""),
                    )
                    if signature != last_signature:
                        last_signature = signature
                        last_change_monotonic = time.monotonic()
                        continue
                    if time.monotonic() - last_change_monotonic >= stall_minutes * 60:
                        logger.warning(f"任务 {running_id} 进度停滞超过 {stall_minutes} 分钟，看门狗自动取消")
                        app.state.task_queue.request_cancel(running_id)
                        last_change_monotonic = time.monotonic()  # 防止重复触发刷日志
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"进度停滞看门狗检查失败: {e}")

        app.state.progress_watchdog_task = asyncio.create_task(_progress_stall_watchdog())
        logger.info(f"进度停滞看门狗已启用（阈值 {stall_minutes} 分钟）")

    # outputs/ 保留策略清理（P0-1）：启动先执行一次回收残留，再挂周期任务。
    # 推理任务运行期间跳过，避免与活动任务的临时帧写入竞争
    try:
        from app.integrated_app.services.output_retention import cleanup_outputs_once, periodic_output_cleanup

        retention_cfg = config.get("retention", {}) or {}
        outputs_dir = os.path.join(os.getcwd(), "outputs")
        max_age_days = int(retention_cfg.get("outputs_max_age_days", 14) or 0)
        max_files = int(retention_cfg.get("outputs_max_files", 0) or 0)
        if max_age_days > 0 or max_files > 0:
            removed, freed = await asyncio.to_thread(cleanup_outputs_once, outputs_dir, max_age_days, max_files)
            if removed:
                logger.info(f"启动 outputs 清理: 删除 {removed} 个过期文件，释放 {freed / (1024 * 1024):.1f}MB")
            cleanup_interval = int(retention_cfg.get("outputs_cleanup_interval_seconds", 3600) or 0)

            def _outputs_busy() -> bool:
                current = app.state.task_queue.current_task_id
                return (current() if callable(current) else current) is not None

            output_cleanup_task = asyncio.create_task(
                periodic_output_cleanup(outputs_dir, max_age_days, max_files, cleanup_interval, is_busy=_outputs_busy)
            )
            app.state.output_cleanup_task = output_cleanup_task
            logger.info(
                f"outputs 保留策略已启用: max_age_days={max_age_days}, max_files={max_files}, "
                f"interval={cleanup_interval}s"
            )
    except Exception as e:
        logger.warning(f"outputs 保留策略清理初始化失败（不影响服务启动）: {e}")

    # 模型空闲超时自动卸载（P1-2）：cache_model 驻留模型时，
    # 空闲超过阈值自动卸载释放 GPU/CPU 资源，兼顾跨任务复用与资源可用性
    idle_unload_minutes = int(config.get("model", {}).get("idle_unload_minutes", 15) or 0)
    if idle_unload_minutes > 0:

        async def _periodic_model_idle_unload():
            while True:
                await asyncio.sleep(60)
                try:
                    current = app.state.task_queue.current_task_id
                    task_running = (current() if callable(current) else current) is not None
                    if model_registry.should_idle_unload(
                        model_loaded=model_registry.model_loaded,
                        seconds_idle=model_registry.seconds_since_activity,
                        idle_minutes=idle_unload_minutes,
                        task_running=task_running,
                    ):
                        logger.info(f"模型已空闲超过 {idle_unload_minutes} 分钟，自动卸载释放资源")
                        await app.state.model_manager.unload_model()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"模型空闲卸载失败: {e}")

        app.state.model_idle_unload_task = asyncio.create_task(_periodic_model_idle_unload())
        logger.info(f"模型空闲自动卸载已启用: {idle_unload_minutes} 分钟无任务即卸载")

    backend_value = gpu_manager.backend.value if gpu_manager.backend else "unavailable"
    logger.info(f"GPU 后端: {backend_value}, 设备: {gpu_manager.device_name}")

    if gpu_manager.is_gpu_available and config.get("model", {}).get("auto_load", True):
        try:
            model_manager: ModelManager = app.state.model_manager
            await model_manager.load_model()
            logger.info("模型自动加载完成")
        except Exception as e:
            logger.warning(f"自动加载模型失败: {e}")
    elif not gpu_manager.is_gpu_available:
        logger.warning("未检测到 NVIDIA GPU，跳过模型自动加载（降级模式）")

    host = config.get("server", {}).get("host", "127.0.0.1")
    port = config.get("server", {}).get("port", 7870)
    if config.get("server", {}).get("auto_open_browser", True):
        url = f"http://{host}:{port}"
        asyncio.get_running_loop().call_later(1.5, lambda: webbrowser.open(url))
        logger.info(f"将在浏览器中打开: {url}")

    logger.info(f"SeedVR2已启动: http://{host}:{port}")

    yield

    model_registry.remove_listener(_bridge_model_status_to_sse)

    file_cache.stop_cleanup_task()

    # 停止定期清理卡死任务的后台任务
    stale_cleanup = getattr(app.state, "stale_cleanup_task", None)
    if stale_cleanup:
        stale_cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await stale_cleanup

    # 停止 outputs 保留策略周期清理任务
    output_cleanup = getattr(app.state, "output_cleanup_task", None)
    if output_cleanup:
        output_cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await output_cleanup

    # 停止进度停滞看门狗
    progress_watchdog = getattr(app.state, "progress_watchdog_task", None)
    if progress_watchdog:
        progress_watchdog.cancel()
        with suppress(asyncio.CancelledError):
            await progress_watchdog

    # 停止模型空闲卸载任务
    idle_unload_task = getattr(app.state, "model_idle_unload_task", None)
    if idle_unload_task:
        idle_unload_task.cancel()
        with suppress(asyncio.CancelledError):
            await idle_unload_task

    # 停止运行时完整性周期重检任务
    integrity_recheck = getattr(app.state, "integrity_recheck_task", None)
    if integrity_recheck:
        integrity_recheck.cancel()
        with suppress(asyncio.CancelledError):
            await integrity_recheck

    task_queue = app.state.task_queue
    try:
        await asyncio.wait_for(task_queue.stop(), timeout=30.0)
        logger.info("任务队列已优雅停止")
    except TimeoutError:
        logger.warning("任务队列停止超时（30s），强制退出")

    model_manager = app.state.model_manager
    await model_manager.unload_model()

    history_db = app.state.history_db
    await history_db.close()

    logger.info("SeedVR2已关闭")


def create_app(config: dict | None = None) -> FastAPI:
    """创建并配置 FastAPI 应用实例。

    完整的应用构建流程：
    1. 加载配置（未提供时从 config.yaml 加载）
    2. 创建 FastAPI 实例，配置标题、描述、版本和生命周期
    3. 注册中间件（CORS、CSRF、全局错误处理）
    4. 初始化所有核心组件并挂载到 app.state：
       - config: 应用配置字典
       - model_manager: 模型加载/卸载/切换管理器
       - gpu_backend: GPU 后端管理器
       - history_db: SQLite 历史记录数据库
       - task_queue: 单 worker 异步任务队列
       - event_bus: SSE 事件总线
       - i18n: 国际化支持
       - file_cache: 上传文件缓存
       - jinja_env: Jinja2 模板环境
    5. 挂载版本化静态文件目录
    6. 自动发现并注册所有 API 路由和页面路由
    7. 可选初始化多引擎调度器和专用引擎

    Args:
        config: 应用配置字典，为 None 时自动从 config.yaml 加载。

    Returns:
        FastAPI: 配置完成的 FastAPI 应用实例，可直接传入 uvicorn.run()。
    """
    if config is None:
        config = load_config()

    app = FastAPI(
        title="SeedVR2",
        description="SeedVR2 - AI-powered video & image super-resolution toolkit",
        version=get_app_version(),
        lifespan=lifespan,
    )

    allowed_origins = config.get("server", {}).get(
        "allowed_origins", ["http://127.0.0.1:7870", "http://localhost:7870"]
    )
    # P3-8：容器/编排部署经环境变量注入 CORS 白名单（优先级高于 config.yaml），
    # 逗号分隔；替代硬编码 localhost，避免经 Ingress/Service 域名访问被 CORS 拦截
    env_origins = os.environ.get("SEEDVR2_ALLOWED_ORIGINS", "").strip()
    if env_origins:
        allowed_origins = [origin.strip() for origin in env_origins.split(",") if origin.strip()]
    allow_credentials = "*" not in allowed_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(CSRFMiddleware)

    # Basic Auth 中间件 (公网部署保护, CWE-306 防御)
    from app.integrated_app.middleware.basic_auth import should_enable_auth

    if should_enable_auth(config):
        from app.integrated_app.middleware.basic_auth import BasicAuthMiddleware

        auth_cfg = config.get("security", {}).get("auth", {})
        import os as _os

        app.add_middleware(
            BasicAuthMiddleware,
            username=auth_cfg.get("username", "admin"),
            password=_os.environ.get("SEEDVR2_AUTH_PASSWORD", auth_cfg.get("password", "")),
            realm=auth_cfg.get("realm", "SeedVR2"),
            max_auth_failures=int(auth_cfg.get("max_auth_failures", 5)),
            auth_failure_window_seconds=float(auth_cfg.get("auth_failure_window_seconds", 300)),
            auth_ban_seconds=float(auth_cfg.get("auth_ban_seconds", 600)),
        )
        logger.info("Basic Auth 中间件已注册")

    # 请求速率限制中间件 (上传/推理端点防护, CWE-770 防御)
    # 上限取自 config.yaml runtime.security.rate_limit_per_minute (默认 30 次/分钟)
    from app.integrated_app.middleware.rate_limit import RateLimitMiddleware

    _rate_limit_per_minute = int(config.get("runtime", {}).get("security", {}).get("rate_limit_per_minute", 30))
    if _rate_limit_per_minute >= 1:
        app.add_middleware(RateLimitMiddleware, rate_limit_per_minute=_rate_limit_per_minute)
        logger.info(f"Rate Limit 中间件已注册 (limit={_rate_limit_per_minute}/min)")

    from app.integrated_app.middleware.error_handler import register_error_handlers

    register_error_handlers(app)

    app.state.config = config
    app.state.model_manager = ModelManager(config)
    app.state.gpu_backend = gpu_manager
    app.state.history_db = HistoryDB(
        db_path=config.get("history", {}).get("db_path", "data/history.db"),
        max_records=config.get("history", {}).get("max_records", 10000),
    )
    _runtime_task_cfg = config.get("runtime", {}).get("task", {})
    app.state.task_queue = TaskQueue(
        maxsize=_runtime_task_cfg.get("queue_maxsize", 100),
        task_timeout_seconds=_runtime_task_cfg.get("max_timeout_seconds", 3600),
    )
    app.state.event_bus = event_bus
    app.state.i18n = I18n(
        locales_dir=os.path.join(os.path.dirname(__file__), "locales"),
        default_locale=config.get("i18n", {}).get("default_locale", "zh"),
    )
    app.state.file_cache = FileCache(
        cache_dir="data/uploads",
        ttl=config.get("cache", {}).get("ttl", 86400),
        max_size_mb=config.get("cache", {}).get("max_size_mb", 0) or 0,
    )

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    templates_dir = os.path.join(os.path.dirname(__file__), "templates")

    if os.path.exists(static_dir):
        app.mount("/static", VersionedStaticFiles(directory=static_dir), name="static")

    import jinja2

    if os.path.exists(templates_dir):
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(templates_dir),
            autoescape=jinja2.select_autoescape(["html", "xml"]),
        )
        app.state.jinja_env = env
    else:
        logger.warning(f"模板目录不存在: {templates_dir}")
        os.makedirs(templates_dir, exist_ok=True)
        app.state.jinja_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(templates_dir),
            autoescape=jinja2.select_autoescape(["html", "xml"]),
        )

    from app.integrated_app.routes import auto_discover_routes, register_page_routes

    # 顺序敏感：必须先注册页面路由、后自动发现 API 路由。
    # FastAPI 0.141 的懒加载 include（_IncludedRouter）下，若 API 路由先于
    # 普通路由注册，页面路由注册后 restore/system 等懒加载路由会整体失配，
    # 请求直接落入 404 handler（实测 scan-folder/health 等全部 404）。
    register_page_routes(app)

    auto_discover_routes(app)

    try:
        from app.integrated_app.optimization.engine.engine_scheduler import EngineScheduler

        _engine_scheduler = EngineScheduler()
        logger.info("Engine Scheduler initialized")
    except Exception as e:
        _engine_scheduler = None
        logger.debug(f"Engine Scheduler not available: {e}")

    if _engine_scheduler is not None:
        from fastapi import APIRouter

        engine_router = APIRouter(prefix="/api/engine", tags=["engine"])

        @engine_router.get("/list")
        async def list_engines():
            """列出所有已注册的推理引擎。

            Returns:
                dict: 统一响应格式，包含所有引擎名称列表和当前可用引擎列表。
            """
            from app.integrated_app.optimization.engine.engine_scheduler import EngineRegistry

            all_engines = EngineRegistry.get_all_registered()
            available_engines = EngineRegistry.get_available_engines()
            return respond_success(
                {
                    "engines": list(all_engines.keys()),
                    "available": available_engines,
                }
            )

        @engine_router.get("/detect")
        async def detect_engines():
            """检测所有推理引擎的可用性状态。

            Returns:
                dict: 统一响应格式，包含各引擎名称到可用性状态的映射。

            Raises:
                Exception: 检测过程出错时返回错误信息。
            """
            try:
                status = _engine_scheduler.detect_available_engines()
                return respond_success({k: v.value for k, v in status.items()})
            except Exception:
                logger.exception("引擎可用性检测失败")
                return respond_error(
                    code="ENGINE_DETECT_FAILED",
                    message="引擎可用性检测失败，请查看服务端日志",
                    status=500,
                )

        @engine_router.post("/submit")
        async def submit_task(
            engine_name: str | None = None,
            input_path: str = "",
            output_path: str = "",
        ):
            """向指定引擎提交推理任务。

            Args:
                engine_name: 引擎名称，为 None 时自动选择。
                input_path: 输入文件路径。
                output_path: 输出文件路径。

            Returns:
                dict: 统一响应格式，成功时包含 task_id，失败时包含错误信息。

            Raises:
                Exception: 任务提交失败时返回错误信息。
            """
            try:
                task_id = _engine_scheduler.submit(
                    engine_name=engine_name,
                    input_path=input_path,
                    output_path=output_path,
                )
                return respond_success({"task_id": task_id})
            except Exception:
                logger.exception("引擎任务提交失败")
                return respond_error(
                    code="ENGINE_SUBMIT_FAILED",
                    message="引擎任务提交失败，请检查引擎可用性",
                    status=500,
                )

        @engine_router.get("/task/{task_id}")
        async def get_task_status(task_id: str):
            """查询任务状态和结果。

            Args:
                task_id: 任务唯一标识符。

            Returns:
                dict: 统一响应格式，包含任务状态和结果数据（如已完成）。
            """
            status = _engine_scheduler.get_task_status(task_id)
            result = _engine_scheduler.get_result(task_id)
            return respond_success(
                {
                    "task_id": task_id,
                    "status": status,
                    "result": result.__dict__ if result else None,
                }
            )

        app.include_router(engine_router)
        logger.info("Engine Scheduler API routes registered")

    try:
        from app.integrated_app.optimization.webui_enhancement import FileListManager, SettingsPersistence

        _file_list_manager = FileListManager()
        _settings_persistence = SettingsPersistence()
        logger.info("WebUI Enhancement modules loaded")
    except Exception as e:
        _file_list_manager = None
        _settings_persistence = None
        logger.debug(f"WebUI Enhancement not available: {e}")

    # 注册 request_id 中间件（add_middleware 为 LIFO，最后添加最先生效，
    # 确保 request_id 在 CSRF/Auth/路由 handler 之前注入日志上下文）
    app.add_middleware(RequestIDMiddleware)
    # /api/v1 别名必须最外层重写（LIFO 下最后添加 = 最先执行），
    # 让 CSRF/Auth/路由全部看到重写后的规范路径
    app.add_middleware(V1AliasMiddleware)
    # 为根 logger 补充 RequestIDLogFilter，使全链路日志自动携带 request_id
    root_logger = logging.getLogger()
    if not any(isinstance(f, RequestIDLogFilter) for f in root_logger.filters):
        root_logger.addFilter(RequestIDLogFilter())
    if not any(isinstance(f, TraceLogFilter) for f in root_logger.filters):
        root_logger.addFilter(TraceLogFilter())

    # W3C traceparent 传播（P3-8，LIFO 最先执行：完整覆盖全部中间件与 handler 的日志上下文）
    app.add_middleware(TracingMiddleware)

    # 安全响应头中间件（M-02）：最外层，确保全部响应（含 401/403/429）携带 CSP 等安全头。
    # 必须最后注册（Starlette add_middleware 为 LIFO，最后添加 = 最先执行 / 响应最后装配）。
    app.add_middleware(SecurityHeadersMiddleware, config=config)

    return app


def _kill_port_process(port: int) -> bool:
    """尝试终止占用指定端口的进程（Windows 平台专用）。

    使用 netstat 命令查找 LISTENING 状态占用指定端口的进程 PID，
    然后使用 taskkill /F 强制终止该进程。

    Args:
        port: 要释放的端口号，如 7870。

    Returns:
        bool: 成功找到并终止进程返回 True，未找到或终止失败返回 False。

    Note:
        - 仅在 Windows 平台有效，依赖 netstat 和 taskkill 系统命令
        - 终止后等待1秒让端口释放
        - 此函数仅在端口被占用且需要自动释放时调用
    """
    import subprocess

    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                pid = int(parts[-1])
                logger.warning(f"端口 {port} 被进程 PID={pid} 占用，尝试终止...")
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=5)
                import time

                time.sleep(1)
                return True
    except Exception as e:
        logger.warning(f"终止端口占用进程失败: {e}")
    return False


def find_available_port(start_port: int, max_attempts: int = 200) -> int:
    """从 start_port 开始向上查找第一个可用的端口。

    通过临时绑定 socket 探测端口是否空闲，避免死守单个端口导致
    启动时因端口被占用而失败 (Errno 10048 / Address already in use)。

    Args:
        start_port: 起始端口号（含）。
        max_attempts: 最多向上探测的端口数量。

    Returns:
        int: 第一个可用的端口号。

    Raises:
        OSError: 在给定范围内未找到可用端口时抛出。
    """
    import socket

    for offset in range(max_attempts):
        candidate = start_port + offset
        if candidate > 65535:
            break
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    raise OSError(f"在 {start_port}~{start_port + max_attempts} 范围内未找到可用端口")


def main() -> None:
    """启动 SeedVR2 FastAPI 应用服务器。

    完整启动流程：
    1. 加载配置文件
    2. 自动寻找可用端口（配置端口被占用时向上顺延，不再死守单一端口）
    3. 创建 FastAPI 应用实例
    4. 配置日志级别和格式
    5. 启动 Uvicorn 服务器

    服务器配置：
    - 默认监听地址：127.0.0.1
    - 默认端口：config.yaml 中的 server.port（默认 7870）
    - debug 模式下启用热重载（从配置读取）

    Raises:
        OSError: 找不到任何可用端口时抛出。
        SystemExit: Uvicorn 运行出错时可能触发。
    """
    import uvicorn

    config = load_config()
    host = config.get("server", {}).get("host", "127.0.0.1")
    base_port = config.get("server", {}).get("port", 7870)
    debug = config.get("server", {}).get("debug", False)

    # 自动寻找可用端口：配置端口被占用时向上顺延，避免启动失败
    port = find_available_port(base_port)
    if port != base_port:
        logger.warning(f"端口 {base_port} 已被占用，自动切换到可用端口 {port}")
        # 同步更新配置，让 lifespan 打开浏览器 / 输出日志时使用正确端口
        config["server"]["port"] = port
        # 将动态端口加入 CORS 白名单，避免跨域访问受限
        origins = config["server"].setdefault("allowed_origins", [])
        origin = f"http://{host}:{port}"
        if origin not in origins:
            origins.append(origin)

    log_level = config.get("logging", {}).get("level", "INFO")
    setup_logging(config)

    app = create_app(config)

    logger.info(f"SeedVR2启动中... http://{host}:{port}")
    try:
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level=log_level.lower(),
            reload=debug,
        )
    except OSError as e:
        if "10048" in str(e) or "already in use" in str(e).lower():
            logger.warning(f"端口 {port} 已被占用，尝试自动终止占用进程...")
            if _kill_port_process(port):
                logger.info(f"端口 {port} 已释放，重新启动服务器...")
                uvicorn.run(
                    app,
                    host=host,
                    port=port,
                    log_level=log_level.lower(),
                    reload=debug,
                )
            else:
                logger.error(f"无法释放端口 {port}，请手动终止占用进程后重试")
                raise
        else:
            raise


if __name__ == "__main__":
    main()
