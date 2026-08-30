#!/usr/bin/env python3
"""模型驻留治理单元测试（成本治理 P1-2）。

覆盖评估报告 P1-2 的验收标准：
1. DiT 加载签名守卫：参数一致可复用、任一加载参数变化即判定重载
2. 空闲卸载判定：阈值/禁用/任务运行中/未加载各分支语义正确
3. 活动时间戳跟踪：touch 后空闲时间重置

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from app.integrated_app.engines._memory_utils import build_dit_load_signature
from app.integrated_app.model_registry import model_registry


class TestBuildDitLoadSignature:
    """DiT 加载签名守卫。"""

    def _sig(self, **overrides):
        base = {
            "checkpoint_path": "model/seedvr2_ema_3b_fp16.safetensors",
            "precision": "fp16",
            "blocks_to_swap": 32,
            "swap_io_components": True,
            "offload_device": "cpu",
            "attention_mode": "sdpa",
            "torch_compile_args": {"enabled": False},
        }
        base.update(overrides)
        return build_dit_load_signature(**base)

    def test_identical_args_same_signature(self):
        assert self._sig() == self._sig()

    def test_blocks_to_swap_change_detected(self):
        """OOM 降级重试提高 blocks_to_swap 后必须判定重载。"""
        assert self._sig(blocks_to_swap=36) != self._sig()

    def test_precision_change_detected(self):
        assert self._sig(precision="fp8") != self._sig()

    def test_checkpoint_change_detected(self):
        assert self._sig(checkpoint_path="model/7b_fp16.safetensors") != self._sig()

    def test_torch_compile_change_detected(self):
        assert self._sig(torch_compile_args={"enabled": True}) != self._sig()

    def test_none_and_empty_compile_args_equivalent(self):
        assert self._sig(torch_compile_args=None) == self._sig(torch_compile_args={})


class TestShouldIdleUnload:
    """空闲卸载判定。"""

    def test_unloads_after_threshold(self):
        assert model_registry.should_idle_unload(
            model_loaded=True, seconds_idle=16 * 60, idle_minutes=15, task_running=False
        )

    def test_keeps_below_threshold(self):
        assert not model_registry.should_idle_unload(
            model_loaded=True, seconds_idle=5 * 60, idle_minutes=15, task_running=False
        )

    def test_disabled_when_zero(self):
        assert not model_registry.should_idle_unload(
            model_loaded=True, seconds_idle=99999, idle_minutes=0, task_running=False
        )

    def test_never_while_task_running(self):
        assert not model_registry.should_idle_unload(
            model_loaded=True, seconds_idle=99999, idle_minutes=15, task_running=True
        )

    def test_never_when_not_loaded(self):
        assert not model_registry.should_idle_unload(
            model_loaded=False, seconds_idle=99999, idle_minutes=15, task_running=False
        )


class TestActivityTracking:
    """活动时间戳跟踪。"""

    def test_touch_resets_idle_clock(self):
        model_registry.touch_activity()
        assert model_registry.seconds_since_activity < 5.0

    def test_seconds_since_activity_non_negative(self):
        assert model_registry.seconds_since_activity >= 0.0
