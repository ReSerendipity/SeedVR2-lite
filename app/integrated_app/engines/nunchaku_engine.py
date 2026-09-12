"""Nunchaku SVDQuant 4-bit 量化推理引擎集成。

参考 MIT-HAN-LAB/nunchaku 的 SVDQuant 4-bit 量化方案，
为扩散模型提供低显存推理支持。

设计原则：
- 懒加载：nunchaku 库不可用时自动 fallback，不影响主流程
- 接口统一：与现有 quant_dequant.py 的 Comfy-Org 反量化共存
- 配置驱动：通过 config.yaml 的 nunchaku 字段控制启用

依赖：nunchaku (pip install git+https://github.com/MIT-HAN-LAB/nunchaku.git)
注意：nunchaku 需要 CUDA 编译，Windows 平台可能需要从源码编译。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 全局缓存：nunchaku 是否可用
_NUNCHAKU_AVAILABLE: bool | None = None
_NUNCHAKU_MODULE: Any | None = None


def is_nunchaku_available() -> bool:
    """检测 nunchaku 库是否可用。

    Returns:
        True 表示 nunchaku 已安装且可导入；False 表示不可用。
    """
    global _NUNCHAKU_AVAILABLE, _NUNCHAKU_MODULE
    if _NUNCHAKU_AVAILABLE is not None:
        return _NUNCHAKU_AVAILABLE

    try:
        import nunchaku as nc  # noqa: F401

        _NUNCHAKU_MODULE = nc
        _NUNCHAKU_AVAILABLE = True
        logger.info("nunchaku 可用：SVDQuant 4-bit 量化推理已启用")
    except ImportError:
        _NUNCHAKU_AVAILABLE = False
        logger.info(
            "nunchaku 不可用：4-bit 量化推理将 fallback 到 FP16/FP8。"
            "安装方式: pip install git+https://github.com/MIT-HAN-LAB/nunchaku.git"
        )
    except Exception as e:
        _NUNCHAKU_AVAILABLE = False
        logger.warning("nunchaku 导入失败: %s，将 fallback", e)

    return _NUNCHAKU_AVAILABLE


class NunchakuQuantizer:
    """Nunchaku SVDQuant 4-bit 量化器。

    对扩散模型的 Linear 层应用 SVDQuant 4-bit 量化，
    配合异步 offload 实现低显存推理。

    用法::

        quantizer = NunchakuQuantizer(config)
        if quantizer.is_available():
            model = quantizer.quantize_model(model)
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """初始化量化器。

        Args:
            config: 量化配置，支持以下键：
                - enabled: 是否启用（默认 False）
                - num_bits: 量化位数（默认 4）
                - offload: 是否启用异步 CPU offload（默认 True）
                - offload_ratio: offload 比例（默认 0.5）
        """
        self.config = config or {}
        self.enabled = self.config.get("enabled", False)
        self.num_bits = self.config.get("num_bits", 4)
        self.offload = self.config.get("offload", True)
        self.offload_ratio = self.config.get("offload_ratio", 0.5)
        self._quantized = False

    def is_available(self) -> bool:
        """检查量化器是否可用（库已安装且配置启用）。"""
        return self.enabled and is_nunchaku_available()

    def quantize_model(self, model: Any) -> Any:
        """对模型应用 SVDQuant 4-bit 量化。

        Args:
            model: 待量化的 PyTorch 模型

        Returns:
            量化后的模型；若 nunchaku 不可用则返回原模型
        """
        if not self.is_available():
            logger.info("Nunchaku 量化未启用或库不可用，跳过量化")
            return model

        if self._quantized:
            return model

        try:
            nc = _NUNCHAKU_MODULE
            # 使用 nunchaku 的量化 API
            # 具体 API 取决于 nunchaku 版本，这里提供通用接口
            if hasattr(nc, "quantize"):
                model = nc.quantize(
                    model,
                    num_bits=self.num_bits,
                    offload=self.offload,
                    offload_ratio=self.offload_ratio,
                )
            elif hasattr(nc, "Nunchaku"):
                # 旧版 API
                quantizer = nc.Nunchaku(
                    num_bits=self.num_bits,
                    offload=self.offload,
                )
                model = quantizer.quantize(model)
            else:
                logger.warning("nunchaku API 不兼容，跳过量化")
                return model

            self._quantized = True
            logger.info(
                "Nunchaku SVDQuant %d-bit 量化完成，offload=%s (ratio=%.1f)",
                self.num_bits,
                self.offload,
                self.offload_ratio,
            )
            return model

        except Exception as e:
            logger.warning("Nunchaku 量化失败，fallback 到原模型: %s", e)
            return model

    def get_memory_savings_estimate(self, model_params_mb: float) -> dict[str, float]:
        """估算显存节省量。

        Args:
            model_params_mb: 模型参数量（MB，FP16）

        Returns:
            包含估算显存的字典
        """
        if self.num_bits == 4:
            # 4-bit 量化理论上节省 75% 权重显存
            weight_ratio = 0.25
        elif self.num_bits == 8:
            weight_ratio = 0.5
        else:
            weight_ratio = 1.0

        quantized_mb = model_params_mb * weight_ratio
        savings_mb = model_params_mb - quantized_mb

        return {
            "original_fp16_mb": model_params_mb,
            "quantized_mb": quantized_mb,
            "savings_mb": savings_mb,
            "savings_percent": (savings_mb / model_params_mb * 100) if model_params_mb > 0 else 0,
        }


def apply_nunchaku_to_model(
    model: Any,
    enabled: bool = False,
    num_bits: int = 4,
    offload: bool = True,
) -> Any:
    """便捷函数：对模型应用 Nunchaku 量化。

    Args:
        model: 待量化模型
        enabled: 是否启用
        num_bits: 量化位数
        offload: 是否启用异步 offload

    Returns:
        量化后的模型（或原模型）
    """
    quantizer = NunchakuQuantizer(
        {
            "enabled": enabled,
            "num_bits": num_bits,
            "offload": offload,
        }
    )
    return quantizer.quantize_model(model)
