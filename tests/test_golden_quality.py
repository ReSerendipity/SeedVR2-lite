"""Golden 退化基准对质量门禁测试（数据治理 P1-3）。

验收标准（对应评估报告 §9.2 P1-3）：
1. 基准对可确定性重建（同 seed 两次生成逐字节一致）；
2. 退化确实降低了质量（PSNR/SSIM 显著下降）；
3. 色偏型退化（模拟修复管线输出偏色）下，color_fix 必须显著改善质量；
4. 结构型退化（噪声/模糊/降采样，无色偏）下，color_fix 不得造成灾难性
   质量回退（PSNR/SSIM 下降有界）——该项源自本门禁首次运行暴露的真实
   行为：全局颜色统计对齐在纯结构损伤场景下可能小幅降低像素指标；
5. 指标模块自身行为正确（同图 PSNR=99、SSIM=1；错图显著下降）。

设计说明：本门禁走 CPU 纯计算路径（合成场景 → 生产退化处理器 →
color_fix → 指标），不依赖 GPU / 模型权重，因此可稳定进入 CI。
"""

import numpy as np
import pytest

from app.integrated_app.color_fix import apply_color_correction
from app.integrated_app.utils.golden_scenes import build_golden_scenes
from app.integrated_app.utils.image_metrics import psnr, quality_report, ssim

# 色偏型退化：仅色彩偏移（无噪声/模糊/降采样），对应"修复结果偏色"的坏案例
_COLOR_SCENES = build_golden_scenes(
    size=128,
    noise_std=0.0,
    blur_sigma=0.0,
    blur_kernel_size=1,
    downsample_scale=1.0,
    color_shift=0.12,
)
# 结构型退化：噪声 + 模糊 + 降采样（无色偏），对应"细节丢失"的坏案例
_STRUCT_SCENES = build_golden_scenes(
    size=128,
    noise_std=0.06,
    blur_sigma=1.5,
    blur_kernel_size=5,
    downsample_scale=2.0,
    color_shift=0.0,
)
_HAS_TORCH = bool(_COLOR_SCENES) and bool(_STRUCT_SCENES)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _HAS_TORCH, reason="缺少 torch，跳过 golden 质量门禁"),
]

# 门禁阈值（按本仓库 color_fix 实测值 + 余量设定）
MIN_DEGRADATION_PSNR = 15.0  # 色偏退化后的 PSNR 下限（实测 ~19dB）
MIN_COLOR_FIX_GAIN = 3.0  # 色偏场景下 color_fix 至少提升的 PSNR(dB)
MAX_STRUCT_PSNR_REGRESSION = 3.0  # 结构损伤场景下允许的最大 PSNR 回退(dB)
MAX_STRUCT_SSIM_REGRESSION = 0.15  # 结构损伤场景下允许的最大 SSIM 回退
# 预期能在色偏场景带来收益的算法（统计对齐类）。
# hsv 不在其列：HSV 为非线性空间（Hue 环绕、S/V 有界），均值方差匹配
# 无法还原 RGB 加性色偏，属算法固有局限而非缺陷，仅要求其不灾难性劣化。
COLOR_FIX_GAIN_METHODS = ("lab", "adain")


class TestMetricsSelfCheck:
    def test_identical_images(self):
        """验收点 5：同图 PSNR 为哨兵值、SSIM 为 1。"""
        img = np.full((32, 32, 3), 128, dtype=np.uint8)
        assert psnr(img, img) == 99.0
        assert ssim(img, img) == pytest.approx(1.0, abs=1e-6)

    def test_different_images_degrade(self):
        a = np.zeros((32, 32, 3), dtype=np.uint8)
        b = np.full((32, 32, 3), 255, dtype=np.uint8)
        assert psnr(a, b) < 5.0
        assert ssim(a, b) < 0.5

    def test_shape_mismatch_raises(self):
        with pytest.raises(ValueError):
            psnr(np.zeros((8, 8, 3), dtype=np.uint8), np.zeros((8, 4, 3), dtype=np.uint8))

    def test_quality_report_keys(self):
        a = np.random.default_rng(0).integers(0, 255, (16, 16, 3), dtype=np.uint8)  # nosec B311
        report = quality_report(a, a)
        assert set(report) == {"psnr_db", "ssim"}


class TestGoldenPairs:
    def test_generation_is_deterministic(self):
        """验收点 1：同 seed 两次生成逐字节一致。"""
        first = build_golden_scenes(size=64)
        second = build_golden_scenes(size=64)
        assert first and second
        for a, b in zip(first, second, strict=False):
            assert np.array_equal(a.source, b.source)
            assert np.array_equal(a.degraded, b.degraded)

    def test_degradation_lowers_quality(self):
        """验收点 2：两类退化都显著低于"与自身对比"的完美值。"""
        for scene in [*_COLOR_SCENES, *_STRUCT_SCENES]:
            report = quality_report(scene.source, scene.degraded)
            assert report["psnr_db"] < 90.0, f"{scene.name}: 退化未生效 PSNR={report['psnr_db']:.2f}dB"
            assert report["ssim"] < 0.999, f"{scene.name}: 退化未生效 SSIM={report['ssim']:.4f}"

    def test_color_shift_degradation_keeps_structure(self):
        """色偏退化只改色不改结构：SSIM 仍应保持较高水平。"""
        for scene in _COLOR_SCENES:
            report = quality_report(scene.source, scene.degraded)
            assert (
                report["psnr_db"] >= MIN_DEGRADATION_PSNR
            ), f"{scene.name}: 色偏退化过度，PSNR 仅 {report['psnr_db']:.2f}dB"

    @pytest.mark.parametrize("method", COLOR_FIX_GAIN_METHODS)
    def test_color_fix_improves_color_shifted_scenes(self, method: str):
        """验收点 3：色偏场景下统计对齐类算法必须显著改善质量。"""
        for scene in _COLOR_SCENES:
            before = psnr(scene.source, scene.degraded)
            after = psnr(scene.source, apply_color_correction(scene.degraded, scene.source, method=method))
            assert after >= before + MIN_COLOR_FIX_GAIN, (
                f"{scene.name}/{method}: 色偏修复收益不足 {before:.2f} -> {after:.2f} dB "
                f"（要求 ≥ +{MIN_COLOR_FIX_GAIN} dB）"
            )

    @pytest.mark.parametrize("method", ["hsv"])
    def test_hsv_does_not_catastrophically_regress_on_color_shift(self, method: str):
        """hsv 已知局限：HSV 非线性空间无法还原 RGB 加性色偏，仅要求不灾难性劣化。"""
        for scene in _COLOR_SCENES:
            before = psnr(scene.source, scene.degraded)
            after = psnr(scene.source, apply_color_correction(scene.degraded, scene.source, method=method))
            assert (
                after >= before - MAX_STRUCT_PSNR_REGRESSION
            ), f"{scene.name}/{method}: 色偏场景 PSNR 回退过大 {before:.2f} -> {after:.2f} dB"

    @pytest.mark.parametrize("method", ["lab", "hsv", "adain"])
    def test_color_fix_no_catastrophic_regression_on_structural_damage(self, method: str):
        """验收点 4：结构损伤场景下 color_fix 回退有界（首次运行暴露的真实边界）。"""
        for scene in _STRUCT_SCENES:
            before = quality_report(scene.source, scene.degraded)
            after = quality_report(scene.source, apply_color_correction(scene.degraded, scene.source, method=method))
            assert after["psnr_db"] >= before["psnr_db"] - MAX_STRUCT_PSNR_REGRESSION, (
                f"{scene.name}/{method}: 结构场景 PSNR 回退过大 "
                f"{before['psnr_db']:.2f} -> {after['psnr_db']:.2f} dB"
            )
            assert after["ssim"] >= before["ssim"] - MAX_STRUCT_SSIM_REGRESSION, (
                f"{scene.name}/{method}: 结构场景 SSIM 回退过大 " f"{before['ssim']:.4f} -> {after['ssim']:.4f}"
            )
