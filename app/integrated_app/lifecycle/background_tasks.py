# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""lifespan 周期性后台循环（自 app_server.py 内联闭包收敛，评估中期项）。

约定：
- 依赖全部经参数注入，循环体不感知 FastAPI app 对象，可脱离装配单测；
- 任务队列一律经 ``get_queue`` 回调在每次检查时解析（测试会替换
  app.state.task_queue，循环必须跟随实例而非绑死启动时引用）；
- 重依赖（routes/services/model_registry）在协程内延迟导入，与
  app_server.lifespan 的既有风格一致并规避装配期导入环；
- 所有循环以 ``asyncio.CancelledError`` 收敛退出，由 lifespan 取消。
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.integrated_app.task_queue import TaskQueue

logger = logging.getLogger(__name__)

# 队列解析回调：返回当前 TaskQueue 实例（可能被测试替换为桩对象，故仅作静态提示，
# 运行时不校验具体类型）。此前钉成 object 导致 mypy 对 current_task_id /
# request_cancel / cleanup_stale_tasks(task_queue=...) 四处误报 attr-defined。
QueueProvider = Callable[[], "TaskQueue"]


async def periodic_stale_cleanup(
    history_db,
    get_queue: QueueProvider,
    last_progress_publish: dict[str, float],
    threshold_minutes: int,
    *,
    interval_seconds: float = 300,
    get_checkpoint_mgr: Callable[[], Any] | None = None,
    checkpoint_ttl_minutes: int = 0,
) -> None:
    """定期清理卡死的 processing 任务（每 interval_seconds 检查一次）。

    threshold_minutes <= 0 时按 10**9 分钟处理（等效只依赖 updated_at 的
    极端旧任务才被清理），与原内联实现语义一致。

    Args:
        history_db: 历史数据库实例。
        get_queue: 任务队列解析回调（用于跳过正在运行的任务）。
        last_progress_publish: 进度事件节流表（P2-11），随清理周期重置。
        threshold_minutes: 卡死判定阈值（分钟）。
        interval_seconds: 检查间隔（秒），默认 300；测试可注入小值。
        get_checkpoint_mgr: 断点续跑管理器解析回调（后续建议 R2）：
            提供后周期执行孤儿 checkpoint 清扫（启动扫描的长驻进程补位）。
        checkpoint_ttl_minutes: checkpoint 最长保留分钟数；<=0 时跳过清扫。
    """
    effective_threshold = threshold_minutes if threshold_minutes > 0 else 10**9
    from app.integrated_app.routes.restore import unified as unified_routes
    from app.integrated_app.services.task_events import task_event_bus

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            cleaned = await unified_routes.cleanup_stale_tasks(
                history_db, threshold_minutes=effective_threshold, task_queue=get_queue()
            )
            if cleaned:
                logger.info(f"定期清理：已清理 {cleaned} 个卡死的 processing 任务")
            # P2-11：顺带清理事件总线过期的最终状态缓存（TTL 60s，5min 扫一次足够）
            task_event_bus.cleanup_expired()
            last_progress_publish.clear()
            # 孤儿 checkpoint 周期清扫（后续建议 R2）：启动扫描只补一次，
            # 长驻进程期间失败/中断任务的新残留由本循环按同一 TTL 回收
            if get_checkpoint_mgr is not None and checkpoint_ttl_minutes > 0:
                mgr = get_checkpoint_mgr()
                if mgr is not None:
                    try:
                        removed = mgr.remove_stale_checkpoints(checkpoint_ttl_minutes * 60)
                        if removed:
                            logger.info(
                                f"周期孤儿 checkpoint 清理: 删除 {removed} 个超过 "
                                f"{checkpoint_ttl_minutes} 分钟的残留 JSON"
                            )
                    except Exception as e:
                        logger.debug(f"周期孤儿 checkpoint 清理失败: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"定期清理卡死任务失败: {e}")


async def progress_stall_watchdog(
    get_queue: QueueProvider,
    stall_minutes: int,
    *,
    check_interval_seconds: float = 60,
) -> None:
    """进度停滞看门狗（P1-8）。

    唯一 worker 被单个挂死的推理任务无限占用时，依据任务状态缓存中
    (progress, message, current_frame/current_index/current_file) 签名是否
    变化判定停滞，超过阈值自动 request_cancel（引擎在阶段检查点协作退出）。
    progress 回调由推理线程逐帧/逐阶段驱动，真实推理即使整帧计算很慢，
    current_frame/current_progress 也会持续变化，误杀窗口极大（默认 30 分钟）。

    Args:
        get_queue: 任务队列解析回调。
        stall_minutes: 停滞判定阈值（分钟）。
        check_interval_seconds: 检查间隔（秒），默认 60；测试可注入小值。
    """
    from app.integrated_app.services.task_state import task_state_store

    last_signature = None
    last_change_monotonic = time.monotonic()
    while True:
        await asyncio.sleep(check_interval_seconds)
        try:
            current = get_queue().current_task_id
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
                get_queue().request_cancel(running_id)
                last_change_monotonic = time.monotonic()  # 防止重复触发刷日志
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"进度停滞看门狗检查失败: {e}")


async def periodic_model_idle_unload(
    get_queue: QueueProvider,
    model_manager,
    idle_minutes: int,
    *,
    check_interval_seconds: float = 60,
) -> None:
    """模型空闲超时自动卸载（P1-2）。

    cache_model 驻留模型时，空闲超过阈值自动卸载释放 GPU/CPU 资源；
    任务运行中不卸载（touch_activity 保证排队期间不被误杀）。

    Args:
        get_queue: 任务队列解析回调（用于判断是否有任务运行中）。
        model_manager: ModelManager 实例（提供 unload_model）。
        idle_minutes: 空闲卸载阈值（分钟）。
        check_interval_seconds: 检查间隔（秒），默认 60；测试可注入小值。
    """
    from app.integrated_app.model_registry import model_registry

    while True:
        await asyncio.sleep(check_interval_seconds)
        try:
            current = get_queue().current_task_id
            task_running = (current() if callable(current) else current) is not None
            if model_registry.should_idle_unload(
                model_loaded=model_registry.model_loaded,
                seconds_idle=model_registry.seconds_since_activity,
                idle_minutes=idle_minutes,
                task_running=task_running,
            ):
                logger.info(f"模型已空闲超过 {idle_minutes} 分钟，自动卸载释放资源")
                await model_manager.unload_model()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"模型空闲卸载失败: {e}")


__all__ = [
    "periodic_model_idle_unload",
    "periodic_stale_cleanup",
    "progress_stall_watchdog",
]
