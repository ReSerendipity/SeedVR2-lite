#!/usr/bin/env python3
"""P2 韧性批次测试（P2-11 SSE 进度推送化 / P2-12 OOM 熔断器）。

覆盖评估报告 P2 批次验收标准：
- P2-11: task_state_store 状态更新触发进度通知钩子（异常不外泄、可注销）；
  /api/restore/{id}/progress 在事件驱动模式下首包快照立即返回
- P2-12: OOM 连续失败打开熔断（503 + Retry-After），成功复位，非 OOM 失败
  重置连续计数，冷却期结束半开放行

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import asyncio

import pytest

from app.integrated_app.history_db import HistoryDB, HistoryRecord, TaskRecord
from app.integrated_app.services.oom_breaker import OomBreaker
from app.integrated_app.services.restore_service import oom_breaker, oom_breaker_remaining
from app.integrated_app.services.task_state import task_state_store
from tests.conftest import csrf_post


class TestProgressNotifier:
    """P2-11：task_state_store 进度通知钩子。"""

    def teardown_method(self):
        task_state_store.set_progress_notifier(None)

    def test_update_cached_triggers_notifier(self):
        received: list[tuple[str, dict]] = []
        task_state_store.set_progress_notifier(lambda tid, state: received.append((tid, state)))
        task_state_store.get_cached_or_create("notify-1", template={"progress": 0})
        task_state_store.update_cached("notify-1", progress=50.5)
        assert received, "update_cached 后应触发通知"
        tid, state = received[-1]
        assert tid == "notify-1"
        assert state["progress"] == 50.5

    def test_notifier_exception_swallowed(self):
        def _boom(_tid, _state):
            raise RuntimeError("通知爆炸")

        task_state_store.set_progress_notifier(_boom)
        task_state_store.get_cached_or_create("notify-2", template={"progress": 0})
        result = task_state_store.update_cached("notify-2", progress=10)
        assert result is not None  # 通知异常不影响主流程
        task_state_store.remove("notify-2")

    def test_unregister_stops_notifications(self):
        received: list = []
        task_state_store.set_progress_notifier(lambda tid, state: received.append(tid))
        task_state_store.get_cached_or_create("notify-3", template={"progress": 0})
        task_state_store.update_cached("notify-3", progress=1)
        assert len(received) == 1
        task_state_store.set_progress_notifier(None)
        task_state_store.update_cached("notify-3", progress=2)
        assert len(received) == 1
        task_state_store.remove("notify-3")


class TestOomBreaker:
    """P2-12：熔断器状态机。"""

    def test_opens_after_threshold(self):
        breaker = OomBreaker(threshold=3, cooldown_seconds=600)
        assert breaker.record_failure(is_oom=True) is False
        assert breaker.record_failure(is_oom=True) is False
        assert breaker.record_failure(is_oom=True) is True  # 第 3 次：打开
        assert breaker.remaining_cooldown() > 0

    def test_non_oom_failure_resets_counter(self):
        breaker = OomBreaker(threshold=3, cooldown_seconds=600)
        breaker.record_failure(is_oom=True)
        breaker.record_failure(is_oom=True)
        assert breaker.record_failure(is_oom=False) is False  # 非 OOM 失败重置
        assert breaker.record_failure(is_oom=True) is False  # 计数从 0 重新开始
        assert breaker.remaining_cooldown() == 0.0

    def test_success_resets(self):
        breaker = OomBreaker(threshold=2, cooldown_seconds=600)
        breaker.record_failure(is_oom=True)
        breaker.record_success()
        assert breaker.record_failure(is_oom=True) is False
        assert breaker.snapshot()["consecutive_ooms"] == 1

    def test_half_open_after_cooldown(self):
        breaker = OomBreaker(threshold=1, cooldown_seconds=1.0)
        assert breaker.record_failure(is_oom=True) is True
        import time

        time.sleep(1.05)
        assert breaker.remaining_cooldown() == 0.0  # 半开：放行探测
        # 探测再 OOM → 立即重新打开（连续计数达阈值-1，再 +1 触发）
        assert breaker.record_failure(is_oom=True) is True


@pytest.mark.integration
class TestOomBreakerRouteWiring:
    """P2-12：熔断打开时提交端点返回 503 + Retry-After。"""

    def test_upload_rejected_when_breaker_open(self, test_app):
        oom_breaker.configure(threshold=3, cooldown_seconds=600)
        for _ in range(3):
            oom_breaker.record_failure(is_oom=True)
        try:
            resp = csrf_post(test_app, "/api/restore/", data={"folder_path": "/whatever"})
            assert resp.status_code == 503
            assert resp.headers.get("Retry-After") is not None
            body = resp.json()
            assert body["success"] is False
            assert "熔断" in body["error"]["message"]
        finally:
            oom_breaker.record_success()  # 复位，避免污染其它测试

    def test_batch_rejected_when_breaker_open(self, test_app):
        oom_breaker.configure(threshold=2, cooldown_seconds=600)
        oom_breaker.record_failure(is_oom=True)
        oom_breaker.record_failure(is_oom=True)
        try:
            resp = csrf_post(test_app, "/api/restore/batch", data={"folder_path": "/whatever"})
            assert resp.status_code == 503
        finally:
            oom_breaker.record_success()

    def test_breaker_disabled_passes_through(self, test_app):
        try:
            remaining = oom_breaker_remaining(
                {"runtime": {"retry": {"oom_breaker": {"enabled": False}}}},
            )
            assert remaining == 0.0
        finally:
            oom_breaker.record_success()


@pytest.mark.integration
class TestSseProgressPushMode:
    """P2-11：/progress 端点在事件驱动模式下首包快照立即可读。"""

    def test_first_snapshot_immediate(self, test_app):
        """starlette TestClient 会整体缓冲流式响应，因此用「终态任务」让 SSE 有界：
        订阅时 task_event_bus 立即投递缓存的 final 事件，生成器输出一次快照即退出。
        事件驱动首包语义（快照立即可读）由此得到验证。"""
        db: HistoryDB = test_app.app.state.history_db
        record_id = asyncio.run(
            db.add_record(HistoryRecord(task_type="image", input_file="/tmp/x.png", status="completed"))
        )
        asyncio.run(db.create_task(TaskRecord(task_id="sse-push-1", record_id=record_id, status="completed")))
        asyncio.run(task_state_store.update("sse-push-1", db, status="completed", progress=42.0))

        with test_app.stream("GET", "/api/restore/sse-push-1/progress") as response:
            assert response.status_code == 200
            body = b"".join(response.iter_raw()).decode("utf-8")
        assert "42.0" in body
        assert '"status": "completed"' in body or '"status":"completed"' in body
