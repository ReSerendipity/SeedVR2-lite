"""FP8 量化真实实现（E4M3 权重量化 + 动态缩放）。

适配 ``model_lib/dit/nadit.py`` 的调用::

    FP8Linear(in_features, out_features, bias=bias)
    is_fp8_enabled()
    apply_fp8_linear_optimization(model)

启用方式：环境变量 ``SEEDVR2_FP8_ENABLED=1``。
硬件要求：NVIDIA Ada Lovelace (SM89) / Hopper (SM90) 及以上；
不支持 FP8 的 GPU 自动 fallback 到普通 nn.Linear，保证功能正确性。

技术路线：HunyuanVideo 纯 PyTorch FP8 方案（无 torchao 外部依赖），
权重静态量化为 float8_e4m3fn，激活动态 per-tensor 缩放，
使用 ``torch._scaled_mm`` 执行 FP8 GEMM。
"""

from __future__ import annotations

import logging
import os

import torch
import torch.nn as nn

logger = logging.getLogger(__name__)

__all__ = ["FP8Linear", "apply_fp8_linear_optimization", "is_fp8_enabled"]

# 全局缓存：当前设备是否支持 FP8
_FP8_SUPPORTED: bool | None = None


def is_fp8_enabled() -> bool:
    """是否启用 FP8 量化。

    Returns:
        默认关闭 (False)；环境变量 ``SEEDVR2_FP8_ENABLED=1`` 时启用。
        启用后若硬件不支持 FP8，自动 fallback 到普通 Linear。
    """
    return os.environ.get("SEEDVR2_FP8_ENABLED", "0") in ("1", "true", "True", "TRUE")


def _is_fp8_supported() -> bool:
    """检测当前 GPU 是否支持 FP8 (float8_e4m3fn) 计算。

    Returns:
        True 表示支持 FP8 GEMM；False 表示不支持，将 fallback。
    """
    global _FP8_SUPPORTED
    if _FP8_SUPPORTED is not None:
        return _FP8_SUPPORTED

    if not torch.cuda.is_available():
        _FP8_SUPPORTED = False
        return False

    try:
        capability = torch.cuda.get_device_capability()
        # SM89 (Ada Lovelace) 和 SM90 (Hopper) 及以上支持 FP8
        _FP8_SUPPORTED = capability >= (8, 9)
        if not _FP8_SUPPORTED:
            logger.warning(
                "FP8 不支持：当前 GPU SM %s.%s 需要 SM89+ (Ada/Hopper)，将 fallback 到普通 Linear",
                capability[0],
                capability[1],
            )
    except Exception as e:
        logger.warning("FP8 支持检测失败: %s，将 fallback 到普通 Linear", e)
        _FP8_SUPPORTED = False

    return _FP8_SUPPORTED


def _quantize_to_fp8(tensor: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """将张量量化为 float8_e4m3fn，返回 (量化张量, 缩放因子)。

    使用 per-tensor 对称量化，缩放因子 = max(abs(tensor)) / 448.0
    (E4M3 的最大正值为 448)。

    Args:
        tensor: 输入张量 (float16/bfloat16/float32)

    Returns:
        (quantized_tensor, scale) 量化后的 float8 张量和缩放因子
    """
    # 计算缩放因子
    max_val = tensor.abs().max().clamp(min=1e-12)
    scale = (max_val / 448.0).to(tensor.dtype)
    # 量化
    quantized = (tensor / scale).clamp(-448, 448).to(torch.float8_e4m3fn)
    return quantized, scale


class FP8Linear(nn.Linear):
    """FP8 线性层：权重静态 E4M3 量化 + 激活动态量化 + FP8 GEMM。

    当 ``is_fp8_enabled()=True`` 且硬件支持 FP8 时，使用 FP8 GEMM 加速；
    否则退化为普通 nn.Linear 行为，保证正确性。
    """

    def __init__(self, in_features: int, out_features: int, bias: bool = True) -> None:
        super().__init__(in_features, out_features, bias=bias)
        self._fp8_weight: torch.Tensor | None = None
        self._weight_scale: torch.Tensor | None = None
        self._weight_quantized = False

    def _quantize_weight(self) -> None:
        """将权重静态量化为 FP8（只量化一次，缓存结果）。"""
        if self._weight_quantized:
            return
        if not is_fp8_enabled() or not _is_fp8_supported():
            self._weight_quantized = True
            return
        try:
            self._fp8_weight, self._weight_scale = _quantize_to_fp8(self.weight.data)
            self._weight_quantized = True
        except Exception as e:
            logger.warning("FP8 权重量化失败，fallback 到普通 Linear: %s", e)
            self._fp8_weight = None
            self._weight_scale = None
            self._weight_quantized = True

    def forward(self, input: torch.Tensor) -> torch.Tensor:
        # 未启用 FP8 或硬件不支持 → 普通 Linear
        if not is_fp8_enabled() or not _is_fp8_supported():
            return torch.nn.functional.linear(input, self.weight, self.bias)

        # 确保权重已量化
        self._quantize_weight()

        # 权重量化失败 → fallback
        if self._fp8_weight is None or self._weight_scale is None:
            return torch.nn.functional.linear(input, self.weight, self.bias)

        try:
            # 激活动态量化
            input_fp8, input_scale = _quantize_to_fp8(input)

            # FP8 GEMM: output = (input_fp8 @ weight_fp8.T) * input_scale * weight_scale
            # torch._scaled_mm 需要 2D 输入，处理 batch 维度
            orig_shape = input.shape
            if input.dim() == 3:
                input_2d = input_fp8.reshape(-1, orig_shape[-1])
                output = torch._scaled_mm(
                    input_2d,
                    self._fp8_weight.T,
                    scale_a=input_scale,
                    scale_b=self._weight_scale,
                    out_dtype=input.dtype,
                )
                output = output.reshape(orig_shape[0], orig_shape[1], -1)
            else:
                output = torch._scaled_mm(
                    input_fp8,
                    self._fp8_weight.T,
                    scale_a=input_scale,
                    scale_b=self._weight_scale,
                    out_dtype=input.dtype,
                )

            if self.bias is not None:
                output = output + self.bias
            return output
        except Exception as e:
            logger.warning("FP8 GEMM 失败，fallback 到普通 Linear: %s", e)
            return torch.nn.functional.linear(input, self.weight, self.bias)


def apply_fp8_linear_optimization(model: nn.Module) -> None:
    """将模型中的 nn.Linear 替换为 FP8Linear（递归遍历）。

    当 ``is_fp8_enabled()=False`` 时为空操作（保持模型结构不变）。
    当硬件不支持 FP8 时，FP8Linear 自动 fallback 到普通 Linear 行为。

    Args:
        model: 待优化的模型
    """
    if not is_fp8_enabled():
        return

    if not _is_fp8_supported():
        logger.info("FP8 硬件不支持，apply_fp8_linear_optimization 为空操作")
        return

    replaced = 0
    for name, module in model.named_children():
        if isinstance(module, nn.Linear) and not isinstance(module, FP8Linear):
            # 创建 FP8Linear 并复制权重
            fp8_linear = FP8Linear(
                module.in_features,
                module.out_features,
                bias=module.bias is not None,
            )
            fp8_linear.weight.data.copy_(module.weight.data)
            if module.bias is not None:
                fp8_linear.bias.data.copy_(module.bias.data)
            setattr(model, name, fp8_linear)
            replaced += 1
        else:
            # 递归处理子模块
            apply_fp8_linear_optimization(module)

    if replaced > 0:
        logger.info("FP8 优化：替换了 %d 个 Linear 层为 FP8Linear", replaced)
