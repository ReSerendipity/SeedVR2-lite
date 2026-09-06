"""diffusion_sampling 采样策略 CPU 数值测试（MLOps P1-2：覆盖率 omit 偏科修复）。

验收标准：
1. RestorationGuidedSampling：开关/衰减公式（linear/cosine/exponential）逐点核对；
2. DynamicCFG / LinearCFGStrategy：端点与单调性；
3. apply_cfg_rescale：factor<=0 恒等、std_cfg=0 恒等、已知统计量公式值；
4. DistilledSampling.get_timesteps：4 步常量表与通用 N 步均匀序列；
5. sd3_time_shift：shift=1 恒等、端点不动、中点公式值。
"""

import math

import pytest
import torch

from app.integrated_app.optimization.inference.diffusion_sampling import (
    DistillationConfig,
    DistilledSampling,
    DynamicCFG,
    LinearCFGStrategy,
    RestorationGuidanceConfig,
    RestorationGuidedSampling,
    apply_cfg_rescale,
    sd3_time_shift,
)


class TestRestorationGuidedSampling:
    def test_disabled_returns_base(self):
        rs = RestorationGuidedSampling(RestorationGuidanceConfig(enabled=False, guidance_scale=2.0))
        assert rs.compute_guidance_scale(7.5, 3, 10) == 7.5

    def test_no_decay_multiplies(self):
        rs = RestorationGuidedSampling(
            RestorationGuidanceConfig(enabled=True, guidance_scale=2.0, timestep_decay=False)
        )
        assert rs.compute_guidance_scale(7.5, 9, 10) == pytest.approx(15.0)

    def test_cosine_decay_boundaries(self):
        rs = RestorationGuidedSampling(
            RestorationGuidanceConfig(
                enabled=True, guidance_scale=2.0, timestep_decay=True, decay_type="cosine", decay_start_ratio=0.5
            )
        )
        # 衰减起点前：完整 guidance
        assert rs.compute_guidance_scale(7.5, 2, 10) == pytest.approx(15.0)
        # 衰减起点（progress 0.5 → decay_progress 0）：cos(0)=1 → 仍 15
        assert rs.compute_guidance_scale(7.5, 5, 10) == pytest.approx(15.0)
        # 区间中点（progress 0.7 → decay_progress 0.4）：cos(pi*0.4/2)
        assert rs.compute_guidance_scale(7.5, 7, 10) == pytest.approx(7.5 * 2 * math.cos(math.pi * 0.4 / 2))
        # 终点（progress 1.0 → decay_progress 1.0）：cos(pi/2)=0 → 0
        assert rs.compute_guidance_scale(7.5, 10, 10) == pytest.approx(0.0, abs=1e-6)

    def test_linear_and_exponential_decay(self):
        lin = RestorationGuidedSampling(
            RestorationGuidanceConfig(
                enabled=True, guidance_scale=2.0, timestep_decay=True, decay_type="linear", decay_start_ratio=0.0
            )
        )
        # progress 0.5 → factor 0.5
        assert lin.compute_guidance_scale(10.0, 5, 10) == pytest.approx(10.0 * 2 * 0.5)
        expo = RestorationGuidedSampling(
            RestorationGuidanceConfig(
                enabled=True,
                guidance_scale=2.0,
                timestep_decay=True,
                decay_type="exponential",
                decay_start_ratio=0.0,
            )
        )
        assert expo.compute_guidance_scale(10.0, 5, 10) == pytest.approx(10.0 * 2 * math.exp(-3 * 0.5))


class TestCFGStrategies:
    def test_dynamic_cfg_linear(self):
        cfg = DynamicCFG(initial_scale=3.0, final_scale=7.5)
        assert cfg.get_scale(0, 10) == pytest.approx(3.0)
        assert cfg.get_scale(5, 10) == pytest.approx(5.25)
        assert cfg.get_scale(10, 10) == pytest.approx(7.5)
        assert cfg.get_scale(0, 0) == pytest.approx(3.0)  # 零步防御：progress=0

    def test_dynamic_cfg_monotonic(self):
        cfg = DynamicCFG(initial_scale=2.0, final_scale=6.0)
        scales = [cfg.get_scale(i, 8) for i in range(9)]
        assert scales == sorted(scales)

    def test_linear_cfg_sigma_endpoints(self):
        s = LinearCFGStrategy(low_noise_scale=7.5, high_noise_scale=3.0)
        assert s.get_scale(14.0, sigma_max=14.0) == pytest.approx(3.0)  # 高噪声端
        assert s.get_scale(0.0, sigma_max=14.0) == pytest.approx(7.5)  # 低噪声端
        assert s.get_scale(7.0, sigma_max=14.0) == pytest.approx(5.25)

    def test_linear_cfg_degenerate_falls_back(self):
        s = LinearCFGStrategy(low_noise_scale=7.5, high_noise_scale=3.0)
        # sigma_max <= sigma_min 才走防御回退
        assert s.get_scale(1.0, sigma_max=0.5, sigma_min=0.7) == 7.5


class TestCfgRescale:
    def test_zero_factor_identity(self):
        x = torch.randn(2, 4, 8, 8)
        p = torch.randn(2, 4, 8, 8)
        assert apply_cfg_rescale(x, p, rescale_factor=0.0) is x

    def test_constant_cfg_identity(self):
        x = torch.full((2, 2, 2, 2), 3.0)  # std=0 → 防御分支
        p = torch.randn(2, 2, 2, 2)
        out = apply_cfg_rescale(x, p, rescale_factor=1.0)
        assert torch.allclose(out, x)

    def test_full_rescale_matches_formula(self):
        p = torch.tensor([[[[1.0, -1.0], [2.0, -2.0]]]])
        x = torch.tensor([[[[3.0, -3.0], [4.0, -4.0]]]])
        std_pos, std_cfg = p.std(), x.std()
        out = apply_cfg_rescale(x, p, rescale_factor=1.0)
        assert torch.allclose(out, x * (std_pos / std_cfg))


class TestDistilledSampling:
    def test_four_step_table(self):
        ds = DistilledSampling(DistillationConfig(num_steps=4))
        assert ds.get_timesteps(1000) == [999, 749, 499, 249]

    def test_general_n_step_uniform(self):
        ds = DistilledSampling(DistillationConfig(num_steps=2))
        assert ds.get_timesteps(1000) == [999, 499]
        ds = DistilledSampling(DistillationConfig(num_steps=5))
        assert ds.get_timesteps(100) == [99, 79, 59, 39, 19]


class TestSd3TimeShift:
    def test_shift_one_identity(self):
        assert sd3_time_shift(0.42, shift=1.0) == 0.42

    def test_endpoints_fixed(self):
        assert sd3_time_shift(0.0, shift=3.0) == 0.0
        assert sd3_time_shift(1.0, shift=3.0) == 1.0

    def test_midpoint_formula(self):
        # shift*t/(1+(shift-1)t) = 1.5/2 = 0.75
        assert sd3_time_shift(0.5, shift=3.0) == pytest.approx(0.75)
        # shift>1 把时间分布推向高噪声端（t_shifted >= t）
        for t in (0.1, 0.3, 0.5, 0.9):
            assert sd3_time_shift(t, shift=3.0) >= t
