"""lifecycle/background_tasks 单元测试（评估中期项：lifespan 循环体收敛）。

覆盖三个周期循环的可测性改造契约：
- 依赖经参数注入（get_queue 回调解析队列，跟随 app.state 替换）；
- 检查间隔可注入（测试不等待真实 60s/300s 周期）；
- CancelledError 收敛语义（stale_cleanup 静默退出；watchdog/idle_unload 向上传播）。
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.integrated_app.lifecycle.background_tasks import (
    periodic_model_idle_unload,
    periodic_stale_cleanup,
    progress_stall_watchdog,
)
from app.integrated_app.services.task_state import task_state_store


class _FakeQueue:
    """桩队列：current_task_id 兼容「属性/可调用」双形态访问"""

    def __init__(self, running_id: str | None = None):
        self._running_id = running_id
        self.cancelled: list[str] = []

    @property
    def current_task_id(self):
        return self._running_id

    def request_cancel(self, task_id: str) -> bool:
        self.cancelled.append(task_id)
        return True


class TestPeriodicStaleCleanup:
    @pytest.mark.asyncio
    async def test_runs_periodically_and_cancels_cleanly(self, monkeypatch):
        calls = {"n": 0}

        async def _fake_cleanup(history_db, threshold_minutes=30, task_queue=None):
            calls["n"] += 1
            return 2

        monkeypatch.setattr("app.integrated_app.routes.restore.unified.cleanup_stale_tasks", _fake_cleanup)

        progress_publish: dict[str, float] = {"t1": 1.0}
        task = asyncio.create_task(
            periodic_stale_cleanup(
                history_db=object(),
                get_queue=lambda: _FakeQueue(),
                last_progress_publish=progress_publish,
                threshold_minutes=30,
                interval_seconds=0.05,
            )
        )
        await asyncio.sleep(0.25)
        assert calls["n"] >= 2
        # 清理周期会重置进度事件节流表（P2-11 语义保持）
        assert progress_publish == {}
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class TestProgressStallWatchdog:
    @pytest.mark.asyncio
    async def test_cancels_stalled_task(self):
        task_state_store.get_cached_or_create("watch-t1", template={"progress": 50, "message": "", "current_frame": 1})
        queue = _FakeQueue(running_id="watch-t1")
        task = asyncio.create_task(
            progress_stall_watchdog(get_queue=lambda: queue, stall_minutes=0, check_interval_seconds=0.05)
        )
        await asyncio.sleep(0.4)
        # stall_minutes=0 为退化阈值：签名一次未变即触发，随后每个检查周期重复触发
        assert "watch-t1" in queue.cancelled
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_progressing_task_not_cancelled(self):
        task_state_store.get_cached_or_create("watch-t2", template={"progress": 0, "message": "", "current_frame": 0})
        queue = _FakeQueue(running_id="watch-t2")
        task = asyncio.create_task(
            progress_stall_watchdog(get_queue=lambda: queue, stall_minutes=0, check_interval_seconds=0.05)
        )

        async def _keep_progressing():
            for i in range(30):
                task_state_store.update_cached("watch-t2", progress=float(i % 100), message="")
                await asyncio.sleep(0.02)

        updater = asyncio.create_task(_keep_progressing())
        await asyncio.sleep(0.35)
        assert queue.cancelled == []
        updater.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await updater
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_idle_queue_resets_signature(self):
        queue = _FakeQueue(running_id=None)
        task = asyncio.create_task(
            progress_stall_watchdog(get_queue=lambda: queue, stall_minutes=0, check_interval_seconds=0.05)
        )
        await asyncio.sleep(0.2)
        assert queue.cancelled == []
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class TestPeriodicModelIdleUnload:
    @pytest.mark.asyncio
    async def test_unloads_when_idle(self, monkeypatch):
        from app.integrated_app.model_registry import model_registry

        unload_calls: list[int] = []

        class _Mgr:
            async def unload_model(self):
                unload_calls.append(1)
                return {"status": "ok"}

        monkeypatch.setattr(model_registry, "should_idle_unload", lambda **kwargs: True)
        queue = _FakeQueue(running_id=None)
        task = asyncio.create_task(
            periodic_model_idle_unload(
                get_queue=lambda: queue, model_manager=_Mgr(), idle_minutes=15, check_interval_seconds=0.05
            )
        )
        await asyncio.sleep(0.25)
        assert len(unload_calls) >= 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_no_unload_while_task_running(self, monkeypatch):
        from app.integrated_app.model_registry import model_registry

        unload_calls: list[int] = []

        class _Mgr:
            async def unload_model(self):
                unload_calls.append(1)
                return {"status": "ok"}

        def _should_unload(**kwargs):
            return kwargs.get("task_running") is False

        monkeypatch.setattr(model_registry, "should_idle_unload", _should_unload)
        queue = _FakeQueue(running_id="busy")
        task = asyncio.create_task(
            periodic_model_idle_unload(
                get_queue=lambda: queue, model_manager=_Mgr(), idle_minutes=15, check_interval_seconds=0.05
            )
        )
        await asyncio.sleep(0.2)
        assert unload_calls == []
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
