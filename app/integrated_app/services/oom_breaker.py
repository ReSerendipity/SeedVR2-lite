"""SeedVR2 - OOM 连续失败熔断器（P2-12）。

评估报告 P2-12：此前连续 OOM 只会逐任务重试，队列持续白烧 GPU 时间。
本模块在「同型号场景连续 N 次 OOM」时打开熔断：新任务提交被 503 拒绝
（带 Retry-After），直到冷却期结束或一次成功推理复位。

设计要点:
    - 进程内单例（oom_breaker），线程安全（Lock 保护状态）
    - 只有 classify 归类为 OOM 的失败才计数；普通失败不影响熔断
    - 熔断打开后经过 cooldown_seconds 自动半开（放行一次以探测显存是否已释放）
    - 阈值/冷却期来自 config.yaml runtime.retry.oom_breaker，可在配置中禁用

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


class OomBreaker:
    """OOM 连续失败熔断器。

    状态机:
        closed（正常）--连续 OOM 达到 threshold--> open（熔断）
        open --冷却期结束--> half_open（放行探测）
        half_open --成功--> closed；half_open --再 OOM--> open
    """

    def __init__(self, threshold: int = 3, cooldown_seconds: float = 600.0):
        """初始化熔断器。

        Args:
            threshold: 连续 OOM 失败次数阈值（>=1），达到即熔断。
            cooldown_seconds: 熔断打开后的冷却期（秒），期间拒绝新任务。
        """
        self._lock = threading.Lock()
        self._threshold = max(1, int(threshold))
        self._cooldown = max(1.0, float(cooldown_seconds))
        self._consecutive_ooms = 0
        self._opened_at: float | None = None

    def configure(self, threshold: int, cooldown_seconds: float) -> None:
        """按配置更新阈值/冷却期（app 启动时调用）。"""
        with self._lock:
            self._threshold = max(1, int(threshold))
            self._cooldown = max(1.0, float(cooldown_seconds))

    def record_failure(self, is_oom: bool) -> bool:
        """记录一次推理失败。

        Args:
            is_oom: 失败是否归类为 OOM（非 OOM 失败重置连续计数——
                说明显存约束并非当前主因）。

        Returns:
            bool: 记录后熔断是否处于打开状态。
        """
        with self._lock:
            if not is_oom:
                self._consecutive_ooms = 0
                return self._opened_at is not None
            self._consecutive_ooms += 1
            if self._consecutive_ooms >= self._threshold and self._opened_at is None:
                self._opened_at = time.monotonic()
                logger.warning(f"连续 OOM {self._consecutive_ooms} 次，OOM 熔断打开（冷却 {self._cooldown:.0f}s）")
            return self._opened_at is not None

    def record_success(self) -> None:
        """记录一次成功推理：完全复位熔断状态。"""
        with self._lock:
            if self._opened_at is not None or self._consecutive_ooms:
                logger.info("推理成功，OOM 熔断器复位")
            self._consecutive_ooms = 0
            self._opened_at = None

    def remaining_cooldown(self) -> float:
        """查询剩余拒绝时间（秒）。

        Returns:
            float: >0 表示熔断打开且仍在冷却期（应拒绝新任务）；
                0 表示 closed 或冷却期已过（半开放行探测）。
        """
        with self._lock:
            if self._opened_at is None:
                return 0.0
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._cooldown:
                # 冷却期结束：半开——复位打开时间，允许一次探测；
                # 探测再 OOM 会立即重新打开（连续计数已达阈值）
                self._opened_at = None
                self._consecutive_ooms = self._threshold - 1
                logger.info("OOM 熔断冷却期结束，半开放行探测")
                return 0.0
            return self._cooldown - elapsed

    def snapshot(self) -> dict:
        """当前状态快照（诊断用）。"""
        with self._lock:
            return {
                "consecutive_ooms": self._consecutive_ooms,
                "threshold": self._threshold,
                "open": self._opened_at is not None,
                "remaining_seconds": round(
                    max(
                        0.0,
                        (self._cooldown - (time.monotonic() - self._opened_at)) if self._opened_at is not None else 0.0,
                    ),
                    1,
                ),
            }
