"""AutoWrappedModule：模块级精细显存管理（FlashVSR 启发）。

参考 FlashVSR 的 AutoWrappedModule / AutoWrappedLinear 设计，
实现比 BlockSwap 更细粒度的显存管理：
- 模块级自动 wrap，按需 offload 到 CPU
- 前向传播时自动 prefetch，后向传播时自动 evict
- 与现有 BlockSwap 共存，可通过 config 切换

与 BlockSwap 的区别：
- BlockSwap：粗粒度，按 transformer block 整体交换
- AutoWrappedModule：细粒度，按子模块（Linear/Attention/MLP）独立管理

设计参考：FlashVSR VRAM Management Framework
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

import torch
import torch.nn as nn

logger = logging.getLogger(__name__)

# 全局配置
_AUTO_WRAP_ENABLED: bool = False
_AUTO_WRAP_OFFLOAD_DEVICE: torch.device = torch.device("cpu")
_AUTO_WRAP_MAIN_DEVICE: torch.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
_PREFETCH_STREAM: torch.cuda.Stream | None = None


def configure_auto_wrap(
    enabled: bool = False,
    offload_device: str = "cpu",
    main_device: str | None = None,
) -> None:
    """配置 AutoWrap 全局参数。

    Args:
        enabled: 是否启用 AutoWrap
        offload_device: offload 目标设备（默认 cpu）
        main_device: 主计算设备（默认 cuda）
    """
    global _AUTO_WRAP_ENABLED, _AUTO_WRAP_OFFLOAD_DEVICE, _AUTO_WRAP_MAIN_DEVICE, _PREFETCH_STREAM
    _AUTO_WRAP_ENABLED = enabled
    _AUTO_WRAP_OFFLOAD_DEVICE = torch.device(offload_device)
    if main_device:
        _AUTO_WRAP_MAIN_DEVICE = torch.device(main_device)
    elif torch.cuda.is_available():
        _AUTO_WRAP_MAIN_DEVICE = torch.device("cuda")
    else:
        _AUTO_WRAP_MAIN_DEVICE = torch.device("cpu")

    if enabled and torch.cuda.is_available():
        _PREFETCH_STREAM = torch.cuda.Stream()
        logger.info(
            "AutoWrap 已启用：offload=%s, main=%s",
            _AUTO_WRAP_OFFLOAD_DEVICE,
            _AUTO_WRAP_MAIN_DEVICE,
        )
    else:
        _PREFETCH_STREAM = None


def is_auto_wrap_enabled() -> bool:
    """检查 AutoWrap 是否启用。"""
    return _AUTO_WRAP_ENABLED


class AutoWrappedLinear(nn.Module):
    """自动 wrap 的 Linear 层：权重按需在 GPU/CPU 间迁移。

    前向传播时：
    1. 若权重在 CPU，异步 prefetch 到 GPU
    2. 计算完成后，若配置了 evict_after_forward，将权重移回 CPU

    用法::

        linear = AutoWrappedLinear(512, 512)
        output = linear(input)
    """

    def __init__(
        self,
        in_features: int,
        out_features: int,
        bias: bool = True,
        evict_after_forward: bool = True,
    ) -> None:
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.evict_after_forward = evict_after_forward
        self._on_gpu = True

        # 权重参数（创建在主设备上）
        self.weight = nn.Parameter(torch.empty(out_features, in_features))
        if bias:
            self.bias = nn.Parameter(torch.empty(out_features))
        else:
            self.register_parameter("bias", None)

        # 初始化
        nn.init.kaiming_uniform_(self.weight, a=5**0.5)
        if self.bias is not None:
            fan_in, _ = nn.init._calculate_fan_in_and_fan_out(self.weight)
            bound = 1 / fan_in**0.5 if fan_in > 0 else 0
            nn.init.uniform_(self.bias, -bound, bound)

    def _ensure_on_gpu(self) -> None:
        """确保权重在 GPU 上（异步 prefetch）。"""
        if not self._on_gpu and is_auto_wrap_enabled():
            if _PREFETCH_STREAM is not None:
                with torch.cuda.stream(_PREFETCH_STREAM):
                    self.weight.data = self.weight.data.to(_AUTO_WRAP_MAIN_DEVICE, non_blocking=True)
                    if self.bias is not None:
                        self.bias.data = self.bias.data.to(_AUTO_WRAP_MAIN_DEVICE, non_blocking=True)
                torch.cuda.current_stream().wait_stream(_PREFETCH_STREAM)
            else:
                self.weight.data = self.weight.data.to(_AUTO_WRAP_MAIN_DEVICE)
                if self.bias is not None:
                    self.bias.data = self.bias.data.to(_AUTO_WRAP_MAIN_DEVICE)
            self._on_gpu = True

    def _evict_to_cpu(self) -> None:
        """将权重移回 CPU。"""
        if self._on_gpu and is_auto_wrap_enabled() and self.evict_after_forward:
            self.weight.data = self.weight.data.to(_AUTO_WRAP_OFFLOAD_DEVICE)
            if self.bias is not None:
                self.bias.data = self.bias.data.to(_AUTO_WRAP_OFFLOAD_DEVICE)
            self._on_gpu = False

    def forward(self, input: torch.Tensor) -> torch.Tensor:
        self._ensure_on_gpu()
        output = torch.nn.functional.linear(input, self.weight, self.bias)
        self._evict_to_cpu()
        return output

    def extra_repr(self) -> str:
        return (
            f"in_features={self.in_features}, out_features={self.out_features}, "
            f"bias={self.bias is not None}, evict_after_forward={self.evict_after_forward}"
        )


class AutoWrappedModule(nn.Module):
    """自动 wrap 的模块容器：递归管理子模块的显存。

    将模块包装后，其子模块的参数会按需在 GPU/CPU 间迁移。
    支持 prefetch 下一个模块的同时计算当前模块。

    用法::

        wrapped = AutoWrappedModule(model, offload_modules=["mlp", "attn"])
        output = wrapped(input)
    """

    def __init__(
        self,
        module: nn.Module,
        offload_modules: list[str] | None = None,
        prefetch: bool = True,
    ) -> None:
        super().__init__()
        self.module = module
        self.offload_modules = offload_modules or []
        self.prefetch = prefetch
        self._offloaded_params: dict[str, torch.Tensor] = {}

    def _get_named_modules_to_offload(self) -> list[tuple[str, nn.Module]]:
        """获取需要 offload 的子模块列表。"""
        if not self.offload_modules:
            # 默认 offload 所有 Linear 和大模块
            return [(n, m) for n, m in self.module.named_modules() if isinstance(m, (nn.Linear, nn.LayerNorm))]
        result = []
        for name, module in self.module.named_modules():
            if any(key in name for key in self.offload_modules):
                result.append((name, module))
        return result

    def offload(self) -> None:
        """将指定模块的参数 offload 到 CPU。"""
        if not is_auto_wrap_enabled():
            return
        for name, module in self._get_named_modules_to_offload():
            for param_name, param in module.named_parameters(recurse=False):
                key = f"{name}.{param_name}"
                if param.device != _AUTO_WRAP_OFFLOAD_DEVICE:
                    self._offloaded_params[key] = param.data.to(_AUTO_WRAP_OFFLOAD_DEVICE, non_blocking=True)
                    param.data = self._offloaded_params[key]

    def restore(self) -> None:
        """将 offload 的参数恢复到 GPU。"""
        if not is_auto_wrap_enabled():
            return
        for name, module in self._get_named_modules_to_offload():
            for param_name, param in module.named_parameters(recurse=False):
                key = f"{name}.{param_name}"
                if key in self._offloaded_params:
                    param.data = self._offloaded_params[key].to(_AUTO_WRAP_MAIN_DEVICE, non_blocking=True)
        if _PREFETCH_STREAM is not None:
            torch.cuda.current_stream().wait_stream(_PREFETCH_STREAM)
        self._offloaded_params.clear()

    @contextmanager
    def auto_wrap_context(self):
        """AutoWrap 上下文：进入时 restore，退出时 offload。"""
        self.restore()
        try:
            yield
        finally:
            self.offload()

    def forward(self, *args: Any, **kwargs: Any) -> Any:
        with self.auto_wrap_context():
            return self.module(*args, **kwargs)


def apply_auto_wrap_to_model(
    model: nn.Module,
    offload_modules: list[str] | None = None,
    replace_linears: bool = False,
) -> nn.Module:
    """便捷函数：对模型应用 AutoWrap。

    Args:
        model: 待包装的模型
        offload_modules: 需要 offload 的模块名关键词列表
        replace_linears: 是否将 nn.Linear 替换为 AutoWrappedLinear

    Returns:
        包装后的模型
    """
    if not is_auto_wrap_enabled():
        logger.info("AutoWrap 未启用，返回原模型")
        return model

    if replace_linears:
        # 递归替换 Linear 为 AutoWrappedLinear
        replaced = 0
        for name, module in model.named_children():
            if isinstance(module, nn.Linear) and not isinstance(module, AutoWrappedLinear):
                aw_linear = AutoWrappedLinear(
                    module.in_features,
                    module.out_features,
                    bias=module.bias is not None,
                )
                aw_linear.weight.data.copy_(module.weight.data)
                if module.bias is not None:
                    aw_linear.bias.data.copy_(module.bias.data)
                setattr(model, name, aw_linear)
                replaced += 1
            else:
                apply_auto_wrap_to_model(module, offload_modules, replace_linears)
        if replaced > 0:
            logger.info("AutoWrap：替换了 %d 个 Linear 为 AutoWrappedLinear", replaced)

    return AutoWrappedModule(model, offload_modules)
