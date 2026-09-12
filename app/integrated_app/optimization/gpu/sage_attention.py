"""SageAttention INT8/FP8 注意力加速后端集成。

参考 thuml/SageAttention 的量化注意力方案，为 DiT 模型提供
高效的注意力计算加速。

SageAttention2++: INT8 QK + FP8 PV，即插即用，无需训练。

设计原则：
- 懒加载：triton + sageattention 不可用时自动 fallback 到 SDPA
- 接口统一：与现有 xformers/torch.compile 共存
- 硬件检测：仅在支持的 GPU (SM80+) 上启用

依赖：
- sageattention (pip install sageattention)
- triton (SageAttention 的核心依赖，Linux 原生支持，Windows 需特殊安装)

注意：Windows 平台 triton 支持有限，SageAttention 可能不可用，
此时自动 fallback 到 torch.nn.functional.scaled_dot_product_attention。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 全局缓存
_SAGE_AVAILABLE: bool | None = None
_SAGE_MODULE: Any | None = None


def is_sage_attention_available() -> bool:
    """检测 SageAttention 是否可用（需要 sageattention + triton）。

    Returns:
        True 表示可用；False 表示不可用（将 fallback 到 SDPA）。
    """
    global _SAGE_AVAILABLE, _SAGE_MODULE
    if _SAGE_AVAILABLE is not None:
        return _SAGE_AVAILABLE

    try:
        import sageattention as sa
        import triton  # noqa: F401

        _SAGE_MODULE = sa
        _SAGE_AVAILABLE = True
        logger.info("SageAttention 可用：INT8/FP8 量化注意力加速已启用")
    except ImportError as e:
        _SAGE_AVAILABLE = False
        missing = "triton" if "triton" in str(e) else "sageattention"
        logger.info(
            "SageAttention 不可用（缺少 %s）：注意力计算 fallback 到 SDPA。"
            "安装方式: pip install sageattention triton",
            missing,
        )
    except Exception as e:
        _SAGE_AVAILABLE = False
        logger.warning("SageAttention 导入失败: %s，将 fallback", e)

    return _SAGE_AVAILABLE


def is_gpu_supported() -> bool:
    """检测当前 GPU 是否支持 SageAttention（需要 SM80+）。

    Returns:
        True 表示 GPU 支持；False 表示不支持。
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return False
        capability = torch.cuda.get_device_capability()
        return capability >= (8, 0)
    except Exception:
        return False


def sage_attention_forward(
    query: Any,
    key: Any,
    value: Any,
    attn_mask: Any = None,
    dropout_p: float = 0.0,
    is_causal: bool = False,
    scale: float | None = None,
) -> Any:
    """SageAttention 量化注意力前向传播。

    当 SageAttention 可用时使用 INT8 QK + FP8 PV 量化注意力；
    否则 fallback 到 PyTorch SDPA。

    Args:
        query: 查询张量 [B, H, L, D]
        key: 键张量 [B, H, S, D]
        value: 值张量 [B, H, S, D]
        attn_mask: 注意力掩码
        dropout_p: dropout 概率
        is_causal: 是否因果掩码
        scale: 缩放因子

    Returns:
        注意力输出张量
    """
    import torch.nn.functional as F

    # 不可用 → fallback 到 SDPA
    if not is_sage_attention_available() or not is_gpu_supported():
        return F.scaled_dot_product_attention(
            query,
            key,
            value,
            attn_mask=attn_mask,
            dropout_p=dropout_p,
            is_causal=is_causal,
            scale=scale,
        )

    try:
        sa = _SAGE_MODULE
        # SageAttention API（版本兼容处理）
        if hasattr(sa, "sageattn"):
            return sa.sageattn(
                query,
                key,
                value,
                attn_mask=attn_mask,
                is_causal=is_causal,
                scale=scale,
            )
        elif hasattr(sa, "sageattn_varlen"):
            # 变长序列版本
            return sa.sageattn_varlen(
                query,
                key,
                value,
                is_causal=is_causal,
            )
        else:
            logger.warning("SageAttention API 不兼容，fallback 到 SDPA")
            return F.scaled_dot_product_attention(
                query,
                key,
                value,
                attn_mask=attn_mask,
                dropout_p=dropout_p,
                is_causal=is_causal,
                scale=scale,
            )
    except Exception as e:
        logger.warning("SageAttention 前向失败，fallback 到 SDPA: %s", e)
        return F.scaled_dot_product_attention(
            query,
            key,
            value,
            attn_mask=attn_mask,
            dropout_p=dropout_p,
            is_causal=is_causal,
            scale=scale,
        )


class SageAttentionWrapper:
    """SageAttention 包装器，可直接替换 nn.MultiheadAttention 的注意力计算。

    用法::

        attn = SageAttentionWrapper(embed_dim, num_heads)
        output = attn(query, key, value)
    """

    def __init__(
        self,
        embed_dim: int,
        num_heads: int,
        dropout: float = 0.0,
        bias: bool = True,
    ) -> None:
        import torch.nn as nn

        self.embed_dim = embed_dim
        self.num_heads = num_heads
        self.head_dim = embed_dim // num_heads
        self.dropout = dropout

        # QKV 投影（普通 Linear，量化在注意力计算中进行）
        self.q_proj = nn.Linear(embed_dim, embed_dim, bias=bias)
        self.k_proj = nn.Linear(embed_dim, embed_dim, bias=bias)
        self.v_proj = nn.Linear(embed_dim, embed_dim, bias=bias)
        self.out_proj = nn.Linear(embed_dim, embed_dim, bias=bias)

    def __call__(self, query: Any, key: Any, value: Any, **kwargs: Any) -> Any:

        B, L, _ = query.shape
        # QKV 投影
        q = self.q_proj(query).reshape(B, L, self.num_heads, self.head_dim).transpose(1, 2)
        k = self.k_proj(key).reshape(B, -1, self.num_heads, self.head_dim).transpose(1, 2)
        v = self.v_proj(value).reshape(B, -1, self.num_heads, self.head_dim).transpose(1, 2)

        # 量化注意力
        attn_out = sage_attention_forward(
            q,
            k,
            v,
            dropout_p=self.dropout if self.training else 0.0,
            **kwargs,
        )

        # 输出投影
        attn_out = attn_out.transpose(1, 2).reshape(B, L, self.embed_dim)
        return self.out_proj(attn_out)
