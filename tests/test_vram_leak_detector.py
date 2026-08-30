"""显存泄漏自动告警测试（数据治理 P2-4）。

验收标准（对应评估报告 §9.2 P2-4）：
1. 样本不足 / 无效峰值（<=0）时不告警；
2. 峰值平稳（抖动）不告警（避免误报）；
3. 末段连续递增且涨幅超阈值 → 告警，告警内容含趋势与涨幅；
4. 告警冷却期内不重复告警；
5. 分组隔离（不同 model_size/input_type 独立判定）；
6. reset 与 snapshot 行为正确。
"""

from app.integrated_app.optimization.gpu.vram_leak_detector import VramLeakDetector


def _detector(**kwargs) -> VramLeakDetector:
    """构造一个冷却期为 0 的测试检测器（便于连续触发验证）。"""
    defaults: dict = {
        "window_size": 10,
        "min_samples": 5,
        "growth_ratio": 0.15,
        "min_growth_mb": 512.0,
        "cooldown_seconds": 0.0,
    }
    defaults.update(kwargs)
    return VramLeakDetector(**defaults)


class TestVramLeakDetector:
    def test_invalid_peak_ignored(self):
        """验收点 1：无效峰值不产生样本、不告警。"""
        d = _detector()
        assert d.record(0) is None
        assert d.record(-100.0) is None
        for peak in [1000.0] * 6:
            assert d.record(peak, "3b", "image") is None

    def test_insufficient_samples_no_alert(self):
        """验收点 1：样本不足 min_samples 时即使大涨也不告警。"""
        d = _detector(min_samples=5)
        for peak in [1000.0, 2000.0, 4000.0]:
            assert d.record(peak, "3b", "image") is None

    def test_stable_peaks_no_alert(self):
        """验收点 2：平稳/抖动序列不误报。"""
        d = _detector()
        peaks = [8000.0, 8100.0, 7950.0, 8050.0, 8000.0, 8120.0, 7980.0, 8060.0]
        alerts = [d.record(p, "7b", "video") for p in peaks]
        assert all(a is None for a in alerts)

    def test_monotonic_growth_triggers_alert(self):
        """验收点 3：末段连续递增 + 涨幅超阈值 → 告警。"""
        d = _detector()
        alerts = []
        for peak in [6000.0, 6100.0, 6050.0, 9000.0, 11000.0, 14000.0]:
            alert = d.record(peak, "7b", "video", task_id="t-1")
            if alert:
                alerts.append(alert)
        assert alerts, "持续上涨未触发告警"
        alert = alerts[0]
        # 首次满足条件即告警：第 5 个样本 11000（tail=[6050,9000,11000] 严格递增）
        assert alert.latest_mb == 11000.0
        assert alert.growth_mb >= 512.0
        assert alert.growth_ratio >= 0.15
        assert "显存泄漏告警" in alert.message
        assert "t-1" in alert.message
        assert alert.trend[-1] == 11000.0

    def test_cooldown_suppresses_repeat_alerts(self):
        """验收点 4：冷却期内不重复告警。"""
        d = _detector(cooldown_seconds=600.0)
        peaks = [6000.0, 6100.0, 6050.0, 9000.0, 11000.0, 14000.0, 17000.0]
        alerts = [a for a in (d.record(p, "7b", "video") for p in peaks) if a]
        assert len(alerts) == 1, f"冷却期未抑制重复告警: {len(alerts)} 次"

    def test_group_isolation(self):
        """验收点 5：不同分组的序列互不影响。"""
        d = _detector()
        for peak in [6000.0, 6100.0, 6050.0, 9000.0, 11000.0, 14000.0]:
            d.record(peak, "7b", "video")
        # 另一分组仅 1 个样本，不应继承告警状态
        assert d.record(3000.0, "3b", "image") is None
        snap = d.snapshot()
        assert "7b/video" in snap and "3b/image" in snap
        assert len(snap["3b/image"]) == 1

    def test_reset_and_snapshot(self):
        """验收点 6：reset 清空状态，snapshot 返回当前序列。"""
        d = _detector()
        for peak in [1000.0, 1100.0, 1200.0]:
            d.record(peak, "3b", "image")
        assert d.snapshot()["3b/image"] == [1000.0, 1100.0, 1200.0]
        d.reset(model_size="3b")
        assert d.snapshot().get("3b/image", []) == []
        for peak in [1000.0, 1100.0]:
            d.record(peak, "7b", "video")
        d.reset()
        assert d.snapshot() == {}

    def test_window_trims_old_samples(self):
        """窗口大小生效：只保留最近 window_size 个样本。"""
        d = _detector(window_size=4, min_samples=5)
        for i in range(10):
            d.record(1000.0 + i, "3b", "image")
        assert len(d.snapshot()["3b/image"]) == 4
