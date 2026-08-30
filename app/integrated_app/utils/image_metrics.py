#!/usr/bin/env python3
"""图像质量客观指标计算（数据治理 P1-3 Golden 质量门禁）。

提供 PSNR / SSIM 两个经典全参考指标的纯 numpy 实现（不引入
scikit-image 等重依赖），供 golden 退化基准对的回归门禁、
质量对比与调试使用。

实现要点：
- PSNR: 基于 MSE 的经典定义，值域 [0, +inf)，单位 dB；
  完全一致时返回 99.0（避免除零）。
- SSIM: 标准 Wang et al. 2004 结构相似度（11×11 高斯窗，
  默认常数 C1/C2 与 skimage 一致），值域 [-1, 1]，1 为完全一致。
  仅支持灰度与 RGB 三通道输入，RGB 取通道平均（与 MATLAB 实现一致）。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import numpy as np

# 与 skimage.metrics.structural_similarity 默认参数一致
_SSIM_GAUSSIAN_SIGMA = 1.5
_SSIM_WIN_SIZE = 11
_SSIM_C1 = 0.01**2
_SSIM_C2 = 0.03**2
# MSE 为 0（图像完全一致）时的 PSNR 哨兵值
_PSNR_PERFECT = 99.0


def _as_float_rgb(image: np.ndarray) -> np.ndarray:
    """把 uint8/float 图像统一为 float64 的 [H, W, C] RGB 数组（值域 [0, 1]）。"""
    arr = np.asarray(image)
    if arr.dtype == np.uint8:
        arr = arr.astype(np.float64) / 255.0
    else:
        arr = arr.astype(np.float64)
    if arr.ndim == 2:
        arr = arr[:, :, None]
    return arr


def _gaussian_window(size: int, sigma: float) -> np.ndarray:
    """构造二维高斯窗（用于 SSIM 局部统计）。"""
    coords = np.arange(size, dtype=np.float64) - size // 2
    g1d = np.exp(-(coords**2) / (2 * sigma**2))
    kernel = np.outer(g1d, g1d)
    return kernel / kernel.sum()


def _filter2d(image: np.ndarray, window: np.ndarray) -> np.ndarray:
    """无外部依赖的二维均值/高斯滤波（same 卷积，边缘采用反射填充）。

    使用 np.pad + 滑动窗口视图实现，避免 scipy.signal 依赖。
    """
    ph, pw = window.shape[0] // 2, window.shape[1] // 2
    padded = np.pad(image, ((ph, ph), (pw, pw)), mode="reflect")
    h, w = image.shape
    # 构造滑动窗口视图（无需复制大数据）
    windows = np.lib.stride_tricks.sliding_window_view(padded, window.shape)
    return np.einsum("ij,mnij->mn", window, windows)[:h, :w]


def psnr(image_a: np.ndarray, image_b: np.ndarray) -> float:
    """计算两幅图像的峰值信噪比（PSNR，dB）。

    Args:
        image_a: 参考图像，uint8 或 float（值域 0-1）。
        image_b: 待比较图像，形状需与 image_a 一致。

    Returns:
        PSNR 值（dB）。完全一致时返回 99.0。
    """
    a = _as_float_rgb(image_a)
    b = _as_float_rgb(image_b)
    if a.shape != b.shape:
        raise ValueError(f"图像形状不一致: {a.shape} vs {b.shape}")
    mse = float(np.mean((a - b) ** 2))
    if mse <= 0.0:
        return _PSNR_PERFECT
    return float(10.0 * np.log10(1.0 / mse))


def ssim(image_a: np.ndarray, image_b: np.ndarray, win_size: int = _SSIM_WIN_SIZE) -> float:
    """计算两幅图像的结构相似度（SSIM，通道平均）。

    Args:
        image_a: 参考图像，uint8 或 float（值域 0-1）。
        image_b: 待比较图像，形状需与 image_a 一致。
        win_size: 高斯窗口边长（奇数），默认 11。

    Returns:
        SSIM 值，值域 [-1, 1]，越接近 1 表示结构越一致。
    """
    a = _as_float_rgb(image_a)
    b = _as_float_rgb(image_b)
    if a.shape != b.shape:
        raise ValueError(f"图像形状不一致: {a.shape} vs {b.shape}")

    window = _gaussian_window(win_size, _SSIM_GAUSSIAN_SIGMA)
    scores: list[float] = []
    for c in range(a.shape[2]):
        x, y = a[:, :, c], b[:, :, c]
        mu_x = _filter2d(x, window)
        mu_y = _filter2d(y, window)
        sigma_xx = _filter2d(x * x, window) - mu_x**2
        sigma_yy = _filter2d(y * y, window) - mu_y**2
        sigma_xy = _filter2d(x * y, window) - mu_x * mu_y
        numerator = (2 * mu_x * mu_y + _SSIM_C1) * (2 * sigma_xy + _SSIM_C2)
        denominator = (mu_x**2 + mu_y**2 + _SSIM_C1) * (sigma_xx + sigma_yy + _SSIM_C2)
        scores.append(float(np.mean(numerator / denominator)))
    return float(np.mean(scores))


def quality_report(image_a: np.ndarray, image_b: np.ndarray) -> dict[str, float]:
    """一次性计算 PSNR + SSIM，便于日志/归档。

    Args:
        image_a: 参考图像。
        image_b: 待比较图像。

    Returns:
        {"psnr_db": float, "ssim": float}。
    """
    return {"psnr_db": psnr(image_a, image_b), "ssim": ssim(image_a, image_b)}


__all__ = ["psnr", "ssim", "quality_report"]
