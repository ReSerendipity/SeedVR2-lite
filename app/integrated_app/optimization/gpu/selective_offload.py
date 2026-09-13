"""Selective Block Offloading — 按 block 重要性选择性 offload（MIA-VSR 启发）。

参考 MIA-VSR 的 Adaptive Block-wise Mask 注意力机制，
将 BlockSwap 从全量交换升级为按 block 重要性选择性 offload。

核心思想：
- 为每个 transformer block 计算重要性分数（基于激活范数/注意力熵/梯度）
- 重要性高的 block 保留在 GPU，重要性低的 offload 到 CPU
- 动态调整：推理过程中根据 VRAM 压力自动调整 offload 比例

与现有 BlockSwap 的关系：
- BlockSwap：按固定数量 offload N 个 block
- SelectiveBlockOffloader：按重要性分数选择 offload 哪些 block
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import torch
import torch.nn as nn

logger = logging.getLogger(__name__)


@dataclass
class BlockImportance:
    """单个 transformer block 的重要性信息。"""

    block_id: int
    importance_score: float = 0.0
    on_gpu: bool = True
    offload_count: int = 0
    last_access_step: int = 0


@dataclass
class SelectiveOffloadConfig:
    """Selective Block Offloading 配置。"""

    enabled: bool = False
    # 目标 offload 比例（0.0-1.0）
    target_offload_ratio: float = 0.3
    # 重要性计算方式：activation_norm / attention_entropy / gradient / fixed
    importance_metric: str = "activation_norm"
    # VRAM 压力阈值（空闲比例低于此值时增加 offload）
    vram_pressure_threshold: float = 0.15
    # 动态调整步长（每次调整的 block 数）
    adjust_step: int = 1
    # 最小保留在 GPU 的 block 数
    min_gpu_blocks: int = 2


class SelectiveBlockOffloader:
    """按重要性选择性 offload transformer block。

    用法::

        offloader = SelectiveBlockOffloader(config)
        model = offloader.apply(model)

        # 推理前更新重要性
        offloader.update_importance(block_id, activation_norm)

        # 根据 VRAM 压力调整
        offloader.adjust_to_vram_pressure()
    """

    def __init__(self, config: SelectiveOffloadConfig | None = None) -> None:
        self.config = config or SelectiveOffloadConfig()
        self.block_importances: dict[int, BlockImportance] = {}
        self.total_blocks: int = 0
        self.current_step: int = 0
        self._applied = False

    def is_enabled(self) -> bool:
        return self.config.enabled

    def apply(self, model: nn.Module) -> nn.Module:
        """应用选择性 offload 到模型。

        扫描模型中的 transformer block，注册重要性跟踪。
        """
        if not self.is_enabled():
            logger.info("Selective Block Offloading 未启用")
            return model

        # 统计 transformer block 数量
        blocks = self._find_transformer_blocks(model)
        self.total_blocks = len(blocks)

        for idx, (_name, _block) in enumerate(blocks):
            self.block_importances[idx] = BlockImportance(
                block_id=idx,
                importance_score=1.0 / self.total_blocks,  # 初始均匀分布
            )

        self._applied = True
        logger.info(
            "Selective Block Offloading 已应用：%d 个 block，目标 offload 比例 %.0f%%",
            self.total_blocks,
            self.config.target_offload_ratio * 100,
        )
        return model

    def _find_transformer_blocks(self, model: nn.Module) -> list[tuple[str, nn.Module]]:
        """查找模型中的 transformer block。"""
        blocks = []
        for name, module in model.named_modules():
            # 常见的 transformer block 命名模式
            if (
                any(key in name.lower() for key in ["block", "layer", "transformer"])
                and isinstance(module, nn.Module)
                and len(list(module.children())) > 0
                and not isinstance(module, (nn.Sequential, nn.ModuleList))
            ):
                blocks.append((name, module))
        return blocks

    def update_importance(self, block_id: int, score: float) -> None:
        """更新某个 block 的重要性分数。

        Args:
            block_id: block 索引
            score: 重要性分数（越大越重要）
        """
        if block_id in self.block_importances:
            info = self.block_importances[block_id]
            # 指数移动平均
            info.importance_score = 0.9 * info.importance_score + 0.1 * score
            info.last_access_step = self.current_step

    def update_from_activations(self, block_id: int, activation: torch.Tensor) -> None:
        """从激活张量计算并更新重要性。

        Args:
            block_id: block 索引
            activation: 该 block 的输出激活
        """
        if self.config.importance_metric == "activation_norm":
            score = activation.norm().item()
            self.update_importance(block_id, score)

    def get_offload_plan(self) -> list[int]:
        """计算当前应该 offload 的 block ID 列表。

        Returns:
            应该 offload 到 CPU 的 block ID 列表
        """
        if not self._applied or self.total_blocks == 0:
            return []

        # 按重要性排序（升序，最不重要的先 offload）
        sorted_blocks = sorted(
            self.block_importances.values(),
            key=lambda x: x.importance_score,
        )

        target_offload = int(self.total_blocks * self.config.target_offload_ratio)
        target_offload = max(0, min(target_offload, self.total_blocks - self.config.min_gpu_blocks))

        return [b.block_id for b in sorted_blocks[:target_offload]]

    def adjust_to_vram_pressure(self, free_vram_ratio: float) -> dict[str, Any]:
        """根据 VRAM 压力动态调整 offload 比例。

        Args:
            free_vram_ratio: 当前空闲 VRAM 比例（0.0-1.0）

        Returns:
            调整信息字典
        """
        if not self.is_enabled():
            return {}

        old_ratio = self.config.target_offload_ratio

        if free_vram_ratio < self.config.vram_pressure_threshold:
            # VRAM 紧张，增加 offload
            self.config.target_offload_ratio = min(
                0.8,
                self.config.target_offload_ratio + 0.05 * self.config.adjust_step,
            )
            action = "increase_offload"
        elif free_vram_ratio > 0.4:
            # VRAM 充足，减少 offload
            self.config.target_offload_ratio = max(
                0.1,
                self.config.target_offload_ratio - 0.05 * self.config.adjust_step,
            )
            action = "decrease_offload"
        else:
            action = "maintain"

        return {
            "action": action,
            "old_ratio": old_ratio,
            "new_ratio": self.config.target_offload_ratio,
            "free_vram_ratio": free_vram_ratio,
            "offload_blocks": len(self.get_offload_plan()),
        }

    def step(self) -> None:
        """推进推理步数（用于重要性衰减）。"""
        self.current_step += 1
        # 长时间未访问的 block 降低重要性
        for info in self.block_importances.values():
            if self.current_step - info.last_access_step > 10:
                info.importance_score *= 0.95


def apply_selective_offload(
    model: nn.Module,
    enabled: bool = False,
    target_offload_ratio: float = 0.3,
) -> tuple[nn.Module, SelectiveBlockOffloader]:
    """便捷函数：应用选择性 offload。

    Args:
        model: 待处理模型
        enabled: 是否启用
        target_offload_ratio: 目标 offload 比例

    Returns:
        (模型, offloader 实例)
    """
    config = SelectiveOffloadConfig(
        enabled=enabled,
        target_offload_ratio=target_offload_ratio,
    )
    offloader = SelectiveBlockOffloader(config)
    model = offloader.apply(model)
    return model, offloader
