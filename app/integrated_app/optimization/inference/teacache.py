"""TeaCache — 时间步缓存与跳过（FlashVSR 启发）。

参考 FlashVSR 的 TeaCache 机制，缓存相似时间步的中间结果，
跳过冗余计算，提升扩散模型推理速度。

核心思想：
- 扩散采样过程中，相邻时间步的中间特征可能高度相似
- 计算相似度，若低于阈值则复用上一步的结果（跳过当前步计算）
- 缓存最近 K 步的中间特征，支持 LRU 淘汰

与现有 BlockSwap 的关系：
- BlockSwap：优化显存（offload 不活跃的 block）
- TeaCache：优化计算（跳过相似的时间步）
两者可同时使用，互补。
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import torch

logger = logging.getLogger(__name__)


@dataclass
class CachedStep:
    """缓存的时间步结果。"""

    timestep: float
    latent: torch.Tensor
    similarity_score: float = 1.0
    access_count: int = 0


@dataclass
class TeaCacheConfig:
    """TeaCache 配置。"""

    enabled: bool = False
    # 相似度阈值：低于此值则跳过（复用缓存）
    similarity_threshold: float = 0.95
    # 缓存最大步数
    max_cache_size: int = 10
    # 相似度计算方式：cosine / l2 / feature_diff
    similarity_metric: str = "cosine"
    # 最多连续跳过的步数（防止质量累积下降）
    max_consecutive_skips: int = 3
    #  warmup 步数（前 N 步不跳过，确保初始质量）
    warmup_steps: int = 2


class TeaCache:
    """时间步缓存与跳过管理器。

    用法::

        cache = TeaCache(config)

        for step, t in enumerate(timesteps):
            # 检查是否可以跳过
            if cache.can_skip(latent, t, step):
                latent = cache.get_cached(latent, t)
                continue

            # 正常计算
            latent = model(latent, t)

            # 更新缓存
            cache.update(latent, t)
    """

    def __init__(self, config: TeaCacheConfig | None = None) -> None:
        self.config = config or TeaCacheConfig()
        self._cache: OrderedDict[float, CachedStep] = OrderedDict()
        self._consecutive_skips: int = 0
        self._total_steps: int = 0
        self._skipped_steps: int = 0

    def is_enabled(self) -> bool:
        return self.config.enabled

    def reset(self) -> None:
        """重置缓存（新视频/新推理时调用）。"""
        self._cache.clear()
        self._consecutive_skips = 0
        self._total_steps = 0
        self._skipped_steps = 0

    def _compute_similarity(self, a: torch.Tensor, b: torch.Tensor) -> float:
        """计算两个张量的相似度。

        Args:
            a: 张量 A
            b: 张量 B

        Returns:
            相似度分数（0.0-1.0，越大越相似）
        """
        if self.config.similarity_metric == "cosine":
            a_flat = a.flatten().float()
            b_flat = b.flatten().float()
            cos_sim = torch.nn.functional.cosine_similarity(a_flat.unsqueeze(0), b_flat.unsqueeze(0))
            return cos_sim.item()
        elif self.config.similarity_metric == "l2":
            diff = (a.float() - b.float()).norm()
            norm = a.float().norm().clamp(min=1e-8)
            return 1.0 - (diff / norm).item()
        else:  # feature_diff
            diff = (a.float() - b.float()).abs().mean()
            return 1.0 - diff.item()

    def can_skip(self, latent: torch.Tensor, timestep: float, step: int) -> bool:
        """检查当前时间步是否可以跳过（复用缓存）。

        Args:
            latent: 当前 latent
            timestep: 当前时间步
            step: 当前步数（用于 warmup 判断）

        Returns:
            True 表示可以跳过
        """
        if not self.is_enabled():
            return False

        # warmup 阶段不跳过
        if step < self.config.warmup_steps:
            return False

        # 连续跳过次数限制
        if self._consecutive_skips >= self.config.max_consecutive_skips:
            return False

        # 查找最相似的缓存步
        best_similarity = 0.0
        for cached in self._cache.values():
            sim = self._compute_similarity(latent, cached.latent)
            if sim > best_similarity:
                best_similarity = sim

        can_skip = best_similarity >= self.config.similarity_threshold
        if can_skip:
            self._consecutive_skips += 1
            self._skipped_steps += 1
        else:
            self._consecutive_skips = 0

        self._total_steps += 1
        return can_skip

    def get_cached(self, latent: torch.Tensor, timestep: float) -> torch.Tensor:
        """获取缓存的结果（跳过计算时调用）。

        简单策略：返回当前 latent（即不做修改，相当于跳过这一步的去噪）。
        更高级的策略可以返回缓存的 latent 或插值。

        Args:
            latent: 当前 latent
            timestep: 当前时间步

        Returns:
            跳过计算后的 latent
        """
        # 找到最相似的缓存步
        best_cached = None
        best_similarity = 0.0
        for cached in self._cache.values():
            sim = self._compute_similarity(latent, cached.latent)
            if sim > best_similarity:
                best_similarity = sim
                best_cached = cached

        if best_cached is not None:
            best_cached.access_count += 1
            # 简单策略：直接返回当前 latent（跳过去噪）
            # 未来可优化为缓存 latent 与当前 latent 的插值
            return latent

        return latent

    def update(self, latent: torch.Tensor, timestep: float) -> None:
        """更新缓存（正常计算后调用）。

        Args:
            latent: 计算后的 latent
            timestep: 当前时间步
        """
        if not self.is_enabled():
            return

        # 已存在则更新
        if timestep in self._cache:
            self._cache[timestep].latent = latent.detach().cpu()
            self._cache.move_to_end(timestep)
        else:
            # 新增
            self._cache[timestep] = CachedStep(
                timestep=timestep,
                latent=latent.detach().cpu(),
            )

        # LRU 淘汰
        while len(self._cache) > self.config.max_cache_size:
            self._cache.popitem(last=False)

    def get_stats(self) -> dict[str, Any]:
        """获取缓存统计信息。

        Returns:
            统计字典
        """
        return {
            "enabled": self.is_enabled(),
            "total_steps": self._total_steps,
            "skipped_steps": self._skipped_steps,
            "skip_ratio": self._skipped_steps / max(self._total_steps, 1),
            "cache_size": len(self._cache),
            "consecutive_skips": self._consecutive_skips,
        }


def create_teacache(
    enabled: bool = False,
    similarity_threshold: float = 0.95,
    max_cache_size: int = 10,
) -> TeaCache:
    """便捷函数：创建 TeaCache 实例。

    Args:
        enabled: 是否启用
        similarity_threshold: 相似度阈值
        max_cache_size: 最大缓存步数

    Returns:
        TeaCache 实例
    """
    config = TeaCacheConfig(
        enabled=enabled,
        similarity_threshold=similarity_threshold,
        max_cache_size=max_cache_size,
    )
    return TeaCache(config)
