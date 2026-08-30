#!/usr/bin/env python3
"""显存泄漏自动告警 —— 数据治理 P2-4。

背景：项目已有四口径显存监控（allocated/reserved/max_allocated/max_reserved）
并已把峰值落库到 history.vram_peak_mb，但缺少"最后一公里"的异常检测：
长时间运行后显存峰值单调上涨（典型泄漏特征）无人告警，最终以 OOM 收场。

本模块提供纯 Python（不依赖 torch）的泄漏检测器：
- 按 (model_size, input_type) 分组维护最近 N 次任务的峰值序列；
- 满足以下两条即判定为疑似泄漏并告警：
  1. 样本数达到 window_size；
  2. 最近 K 次峰值**逐次递增**，且末次相比窗口内中位数涨幅超过
     growth_ratio 且超过 min_growth_mb（避免小基数抖动误报）。
- 告警有冷却期（cooldown_seconds），避免同一轮泄漏刷屏。

Usage:
    detector = VramLeakDetector()
    alert = detector.record(peak_mb=12345.0, model_size="7b", input_type="video", task_id="abc")
    if alert:
        logger.warning(alert.message)

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class VramLeakAlert:
    """显存泄漏告警。

    Attributes:
        model_size: 触发告警的模型档位。
        input_type: 输入类型（image/video）。
        latest_mb: 最近一次峰值（MB）。
        baseline_mb: 窗口内峰值中位数（MB）。
        growth_mb: 相对基线的绝对增长（MB）。
        growth_ratio: 相对基线的增长比例。
        trend: 最近若干次峰值序列（MB）。
        message: 可直接输出日志的可读告警文案。
    """

    model_size: str
    input_type: str
    latest_mb: float
    baseline_mb: float
    growth_mb: float
    growth_ratio: float
    trend: list[float]
    message: str


@dataclass
class _SeriesState:
    """单个分组的峰值序列状态。"""

    peaks: list[float] = field(default_factory=list)
    last_alert_at: float = 0.0


class VramLeakDetector:
    """显存峰值趋势检测器（线程安全）。

    Attributes:
        window_size: 每个分组保留的最近样本数。
        min_samples: 触发判定所需的最少样本数。
        growth_ratio: 触发告警的最低增长比例（相对基线中位数）。
        min_growth_mb: 触发告警的最低绝对增长（MB）。
        monotone_steps: 要求末段连续递增的次数。
        cooldown_seconds: 同一分组两次告警的最小间隔（秒）。
    """

    def __init__(
        self,
        window_size: int = 10,
        min_samples: int = 5,
        growth_ratio: float = 0.15,
        min_growth_mb: float = 512.0,
        monotone_steps: int = 3,
        cooldown_seconds: float = 600.0,
    ) -> None:
        self.window_size = max(2, int(window_size))
        self.min_samples = max(2, int(min_samples))
        self.growth_ratio = float(growth_ratio)
        self.min_growth_mb = float(min_growth_mb)
        self.monotone_steps = max(2, int(monotone_steps))
        self.cooldown_seconds = float(cooldown_seconds)
        self._series: dict[tuple[str, str], _SeriesState] = {}
        self._lock = threading.Lock()

    def record(
        self,
        peak_mb: float,
        model_size: str = "unknown",
        input_type: str = "unknown",
        task_id: str = "",
    ) -> VramLeakAlert | None:
        """记录一次任务显存峰值，返回告警（无异常时返回 None）。

        Args:
            peak_mb: 本次任务的显存峰值（MB）；<=0 视为无数据，直接忽略。
            model_size: 模型档位（分组键）。
            input_type: 输入类型（分组键）。
            task_id: 任务 ID（仅用于日志）。

        Returns:
            VramLeakAlert 或 None。
        """
        if not peak_mb or peak_mb <= 0:
            return None

        key = (str(model_size), str(input_type))
        now = time.time()
        with self._lock:
            state = self._series.setdefault(key, _SeriesState())
            state.peaks.append(float(peak_mb))
            if len(state.peaks) > self.window_size:
                state.peaks = state.peaks[-self.window_size :]

            alert = self._evaluate(key, state, now, task_id)
            if alert is not None:
                state.last_alert_at = now
            return alert

    def _evaluate(
        self,
        key: tuple[str, str],
        state: _SeriesState,
        now: float,
        task_id: str,
    ) -> VramLeakAlert | None:
        """判定当前序列是否构成疑似泄漏。"""
        model_size, input_type = key
        peaks = state.peaks
        if len(peaks) < self.min_samples:
            return None
        if now - state.last_alert_at < self.cooldown_seconds:
            return None

        # 条件 1：末段 monotone_steps 次严格递增
        tail = peaks[-self.monotone_steps :]
        if len(tail) < self.monotone_steps:
            return None
        if any(b <= a for a, b in zip(tail, tail[1:], strict=False)):
            return None

        # 条件 2：相对基线增长超阈值
        baseline = statistics.median(peaks)
        latest = peaks[-1]
        growth_mb = latest - baseline
        if baseline <= 0:
            return None
        growth_ratio = growth_mb / baseline
        if growth_mb < self.min_growth_mb or growth_ratio < self.growth_ratio:
            return None

        message = (
            f"[显存泄漏告警] model={model_size} type={input_type} task={task_id or '-'}: "
            f"近 {len(peaks)} 次显存峰值持续上涨，最新 {latest:.0f}MB vs 基线 {baseline:.0f}MB "
            f"(+{growth_mb:.0f}MB / {growth_ratio:.1%})，趋势={[round(p) for p in peaks]}。"
            f"建议检查模型缓存释放/VAE 分块/中间张量是否残留"
        )
        logger.warning(message)
        return VramLeakAlert(
            model_size=model_size,
            input_type=input_type,
            latest_mb=latest,
            baseline_mb=baseline,
            growth_mb=growth_mb,
            growth_ratio=growth_ratio,
            trend=list(peaks),
            message=message,
        )

    def reset(self, model_size: str | None = None, input_type: str | None = None) -> None:
        """清空检测状态（换模型/手动复位时使用）。

        Args:
            model_size: 指定模型档位；None 表示所有档位。
            input_type: 指定输入类型；None 表示所有类型。
        """
        with self._lock:
            if model_size is None and input_type is None:
                self._series.clear()
                return
            for key in list(self._series):
                if (model_size is None or key[0] == model_size) and (input_type is None or key[1] == input_type):
                    self._series.pop(key, None)

    def snapshot(self) -> dict:
        """返回当前各分组峰值序列（供 /api/system/metrics 等暴露）。"""
        with self._lock:
            return {f"{ms}/{it}": list(state.peaks) for (ms, it), state in self._series.items()}


# 进程级单例（与 OomBreaker 风格一致）
vram_leak_detector = VramLeakDetector()


__all__ = ["VramLeakAlert", "VramLeakDetector", "vram_leak_detector"]
