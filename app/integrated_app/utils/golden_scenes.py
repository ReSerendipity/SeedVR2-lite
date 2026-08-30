#!/usr/bin/env python3
"""Golden 测试场景生成器（数据治理 P1-3）。

复用应用层现成的 HierarchicalDegradationProcessor（原本仅服务于训练增广），
生成"源图 → 退化图"的确定性基准对，供质量门禁脚本与单测使用。

设计要点：
- 合成场景为纯 numpy 生成（渐变/棋盘格/同心圆/肤色块），确定性无随机源，
  不依赖任何外部素材，保证 CI 可重现；
- 退化过程走生产代码路径（apply_degradation），并固定随机种子，
  使"退化图"稳定可复现，避免出现只能靠审计截图判断质量的局面；
- 返回的源图为 uint8 RGB，退化图与源图同形同类型，可直接送入
  color_fix 等后处理模块做质量回归比对。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# 场景随机种子（退化噪声用），固定值保证基准对可复现
DEFAULT_DEGRADATION_SEED = 20260830


@dataclass(frozen=True)
class GoldenScene:
    """一个 golden 场景定义。

    Attributes:
        name: 场景名称（用于落盘文件名与日志）。
        source: 源图 uint8 RGB 数组 [H, W, 3]。
        degraded: 退化后 uint8 RGB 数组 [H, W, 3]。
        params_desc: 退化参数的可读描述。
    """

    name: str
    source: np.ndarray
    degraded: np.ndarray
    params_desc: str


def _gradient_scene(size: int) -> np.ndarray:
    """线性渐变场景（含冷暖过渡，便于检验色偏修复）。"""
    xs = np.linspace(0, 1, size, dtype=np.float32)
    ys = np.linspace(0, 1, size, dtype=np.float32)
    grid_x, grid_y = np.meshgrid(xs, ys)
    r = grid_x
    g = grid_y
    b = 0.5 + 0.5 * np.sin(grid_x * np.pi)
    rgb = np.stack([r, g, b], axis=-1)
    return (np.clip(rgb, 0, 1) * 255).astype(np.uint8)


def _checker_scene(size: int, block: int = 16) -> np.ndarray:
    """棋盘格 + 边缘场景（便于检验模糊/降采样退化）。"""
    yy, xx = np.mgrid[0:size, 0:size]
    checker = ((xx // block) + (yy // block)) % 2
    base = np.where(checker == 0, 0.15, 0.85).astype(np.float32)
    rgb = np.stack([base, base * 0.8, 1.0 - base], axis=-1)
    return (np.clip(rgb, 0, 1) * 255).astype(np.uint8)


def _skin_tone_scene(size: int) -> np.ndarray:
    """类肤色 + 同心圆场景（贴近人像修复的真实分布）。"""
    yy, xx = np.mgrid[0:size, 0:size]
    center = size / 2.0
    radius = np.sqrt((xx - center) ** 2 + (yy - center) ** 2) / center
    rings = 0.5 + 0.5 * np.cos(radius * 8.0)
    r = 0.86 * rings + 0.10
    g = 0.62 * rings + 0.12
    b = 0.48 * rings + 0.14
    rgb = np.stack([r, g, b], axis=-1)
    return (np.clip(rgb, 0, 1) * 255).astype(np.uint8)


_SCENE_BUILDERS = {
    "gradient": _gradient_scene,
    "checker": _checker_scene,
    "skin_tone": _skin_tone_scene,
}


def build_sources(size: int = 128) -> dict[str, np.ndarray]:
    """生成全部 golden 源图（确定性，无随机）。

    Args:
        size: 图像边长（正方形），默认 128（CI 内快速执行）。

    Returns:
        {场景名: uint8 RGB 源图}。
    """
    return {name: builder(size) for name, builder in _SCENE_BUILDERS.items()}


def build_golden_scenes(
    size: int = 128,
    *,
    noise_std: float = 25.0 / 255.0,
    blur_sigma: float = 2.0,
    blur_kernel_size: int = 7,
    downsample_scale: float = 2.0,
    color_shift: float = 0.08,
    seed: int = DEFAULT_DEGRADATION_SEED,
) -> list[GoldenScene]:
    """生成源图 → 退化图的 golden 基准对。

    退化走生产路径 HierarchicalDegradationProcessor.apply_degradation，
    并固定 torch 随机种子，保证多次调用结果一致（CI 可重现）。

    Args:
        size: 图像边长。
        noise_std: 高斯噪声标准差（0-1 尺度）。
        blur_sigma: 高斯模糊标准差。
        blur_kernel_size: 高斯模糊核大小（奇数）。
        downsample_scale: 降采样倍数（>1 生效）。
        color_shift: 色彩偏移强度。
        seed: torch 随机种子（噪声/色偏使用）。

    Returns:
        GoldenScene 列表；torch 不可用时返回空列表（调用方据此跳过门禁）。
    """
    try:
        import torch

        from app.integrated_app.optimization.video.video_processing_enhance import (
            DegradationParams,
            HierarchicalDegradationConfig,
            HierarchicalDegradationProcessor,
        )
    except ImportError as e:  # pragma: no cover - 仅当环境无 torch
        logger.warning(f"Golden 场景生成跳过：缺少依赖 {e}")
        return []

    params = DegradationParams(
        downsample_scale=downsample_scale,
        noise_std=noise_std,
        blur_kernel_size=blur_kernel_size,
        blur_sigma=blur_sigma,
        color_shift=color_shift,
    )
    processor = HierarchicalDegradationProcessor(HierarchicalDegradationConfig())

    scenes: list[GoldenScene] = []
    for name, source in build_sources(size).items():
        torch.manual_seed(seed)
        tensor = torch.from_numpy(source.astype(np.float32) / 255.0).permute(2, 0, 1).contiguous()
        degraded_t = processor.apply_degradation(tensor, params)
        degraded = (degraded_t.clamp(0, 1).permute(1, 2, 0).numpy() * 255).astype(np.uint8)
        scenes.append(
            GoldenScene(
                name=name,
                source=source,
                degraded=degraded,
                params_desc=(
                    f"noise_std={noise_std:.4f}, blur=({blur_kernel_size},{blur_sigma}), "
                    f"downsample={downsample_scale}, color_shift={color_shift}"
                ),
            )
        )
    return scenes


__all__ = ["GoldenScene", "DEFAULT_DEGRADATION_SEED", "build_sources", "build_golden_scenes"]
