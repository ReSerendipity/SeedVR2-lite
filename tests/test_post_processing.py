"""post_processing 后处理/颜色增强 CPU 数值测试（MLOps P1-2：覆盖率 omit 偏科修复）。

验收标准：
1. apply_fidelity_weight：0/1 端点精确、0.5 中值（含尺寸不一致自动 resize 路径）；
2. apply_sharpening：strength<=0 恒等、常量图不改变、未知方法恒等、clip 值域；
3. wavelet_reconstruction：恒等输入→恒等输出（PSNR 高）、低频取自 reference、
   高频细节来自 restored、尺寸不一致自动 resize；
4. MultiStepUpscaler.compute_steps：单步/分步/禁用/迭代上限剩余倍率四条路径；
5. apply_post_processing：None 配置恒等、各开关独立生效。
"""

import numpy as np
import pytest

from app.integrated_app.optimization.inference.post_processing import (
    MultiStepUpscaleConfig,
    MultiStepUpscaler,
    apply_fidelity_weight,
    apply_post_processing,
    apply_sharpening,
    wavelet_reconstruction,
)

requires_pywt = pytest.importorskip("pywt")


def _rng():
    return np.random.default_rng(42)


class TestFidelityWeight:
    def test_endpoints(self):
        restored = np.full((8, 8, 3), 200, dtype=np.uint8)
        original = np.full((8, 8, 3), 100, dtype=np.uint8)
        assert np.array_equal(apply_fidelity_weight(restored, original, 0.0), restored)
        assert np.array_equal(apply_fidelity_weight(restored, original, 1.0), original)

    def test_midpoint(self):
        restored = np.full((8, 8, 3), 200, dtype=np.uint8)
        original = np.full((8, 8, 3), 100, dtype=np.uint8)
        out = apply_fidelity_weight(restored, original, 0.5)
        assert np.all(np.abs(out.astype(int) - 150) <= 1)

    def test_shape_mismatch_resizes(self):
        restored = np.full((8, 8, 3), 200, dtype=np.uint8)
        original = np.full((4, 4, 3), 100, dtype=np.uint8)
        out = apply_fidelity_weight(restored, original, 0.5)
        assert out.shape == (8, 8, 3)


class TestSharpening:
    def test_zero_strength_identity(self):
        img = _rng().integers(0, 256, (16, 16, 3), dtype=np.uint8)
        assert np.array_equal(apply_sharpening(img, strength=0.0), img)

    def test_uniform_image_unchanged(self):
        img = np.full((16, 16, 3), 128, dtype=np.uint8)
        for method in ("unsharp_mask", "laplacian"):
            out = apply_sharpening(img, strength=0.5, method=method)
            assert np.array_equal(out, img), method

    def test_unknown_method_identity(self):
        img = _rng().integers(0, 256, (16, 16, 3), dtype=np.uint8)
        assert np.array_equal(apply_sharpening(img, strength=0.5, method="no-such"), img)

    def test_output_clipped_to_uint8(self):
        img = _rng().integers(0, 256, (32, 32, 3), dtype=np.uint8)
        out = apply_sharpening(img, strength=1.0, method="unsharp_mask")
        assert out.dtype == np.uint8 and out.shape == img.shape


class TestWaveletReconstruction:
    def test_identical_inputs_near_identity(self):
        from app.integrated_app.utils.image_metrics import psnr

        img = _rng().integers(0, 256, (64, 64, 3), dtype=np.uint8)
        out = wavelet_reconstruction(img.copy(), img.copy(), level=3)
        assert out.shape == img.shape
        assert psnr(img, out) > 40.0

    def test_low_freq_from_reference_high_freq_kept(self):
        ref = np.full((64, 64, 3), 120, dtype=np.uint8)
        # restored = 竖直条纹（列交替 60/156，低频均值≈108，高频清晰）
        cols = np.indices((64, 64))[1] % 2
        res = np.stack(
            [np.where(cols == 0, 60, 156).astype(np.uint8)] * 3,
            axis=-1,
        )
        out = wavelet_reconstruction(res, ref, level=3, low_freq_weight=0.8)
        # 低频被 reference 拉向 120：融合近似系数均值 ≈ 0.8*120 + 0.2*108 = 117.6
        assert abs(float(out.mean()) - 117.6) < 6.0
        # 高频细节来自 restored：条纹仍在（纯 reference 会是平坦的 120）
        assert int(out.max()) - int(out.min()) > 20

    def test_shape_mismatch_no_crash(self):
        res = _rng().integers(0, 256, (64, 64, 3), dtype=np.uint8)
        ref_small = _rng().integers(0, 256, (32, 32, 3), dtype=np.uint8)
        out = wavelet_reconstruction(res, ref_small, level=2)
        assert out.shape == res.shape


class TestMultiStepUpscale:
    def test_single_step_when_small_target(self):
        up = MultiStepUpscaler(MultiStepUpscaleConfig(target_scale=1.8, max_single_step=2.0))
        assert up.compute_steps() == [1.8]

    def test_split_by_max_step(self):
        up = MultiStepUpscaler(MultiStepUpscaleConfig(target_scale=8.0, max_single_step=2.0, max_iterations=3))
        # 2*2*2 = 8，恰好三步耗尽
        assert up.compute_steps() == [2.0, 2.0, 2.0]

    def test_disabled_single_shot(self):
        up = MultiStepUpscaler(MultiStepUpscaleConfig(enabled=False, target_scale=8.0))
        assert up.compute_steps() == [8.0]

    def test_iteration_cap_appends_remainder(self):
        up = MultiStepUpscaler(MultiStepUpscaleConfig(target_scale=8.0, max_single_step=2.0, max_iterations=1))
        steps = up.compute_steps()
        assert steps[0] == 2.0 and steps[-1] == pytest.approx(4.0)
        assert np.prod(steps) == pytest.approx(8.0)


class TestApplyPostProcessing:
    def test_none_config_returns_input_object(self):
        # 现状锁定（报告外发现登记）：config=None 时直接返回入参对象（非副本）
        img = _rng().integers(0, 256, (16, 16, 3), dtype=np.uint8)
        out = apply_post_processing(img, img, config=None)
        assert out is img

    def test_empty_config_returns_copy(self):
        img = _rng().integers(0, 256, (16, 16, 3), dtype=np.uint8)
        out = apply_post_processing(img, img, config={})
        assert np.array_equal(out, img)
        out[:] = 0  # 返回的是副本：改动不应影响原图
        assert not np.all(img == 0)

    def test_sharpen_only(self):
        img = _rng().integers(0, 256, (32, 32, 3), dtype=np.uint8)
        out = apply_post_processing(img, img, config={"sharpen_strength": 0.5})
        assert out.shape == img.shape
        assert not np.array_equal(out, img)  # 噪声图锐化必有差异

    def test_fidelity_only(self):
        restored = np.full((16, 16, 3), 200, dtype=np.uint8)
        ref = np.full((16, 16, 3), 100, dtype=np.uint8)
        out = apply_post_processing(restored, ref, config={"fidelity_weight": 1.0})
        assert np.array_equal(out, ref)

    def test_wavelet_flag_paths(self):
        img = _rng().integers(0, 256, (64, 64, 3), dtype=np.uint8)
        out = apply_post_processing(img.copy(), img.copy(), config={"wavelet_reconstruction": True})
        assert out.shape == img.shape
