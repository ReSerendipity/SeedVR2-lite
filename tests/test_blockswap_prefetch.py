#!/usr/bin/env python3
"""BlockSwap 预取流水单元测试（成本治理 P2-2）。

在 CPU 环境下验证预取开关的传播与包装 forward 的行为正确性
（无 CUDA 时预取路径必须静默降级为同步换入，输出不受影响）。
GPU 上的实际传输重叠由 GPU 冒烟验证覆盖。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import torch

from app.integrated_app.optimization.gpu.blockswap import (
    apply_block_swap_to_dit,
    cleanup_blockswap,
)


class _TinyBlock(torch.nn.Module):
    def __init__(self, dim: int = 8):
        super().__init__()
        self.linear = torch.nn.Linear(dim, dim)

    def forward(self, x):
        return self.linear(x) + 1.0


class _TinyDiT(torch.nn.Module):
    def __init__(self, num_blocks: int = 4, dim: int = 8):
        super().__init__()
        self.blocks = torch.nn.ModuleList([_TinyBlock(dim) for _ in range(num_blocks)])
        self.head = torch.nn.Linear(dim, dim)

    def forward(self, x):
        for block in self.blocks:
            x = block(x)
        return self.head(x)


class TestBlockswapPrefetch:
    """预取开关传播与包装 forward 行为。"""

    def _run_model(self, model: _TinyDiT) -> torch.Tensor:
        return model(torch.ones(2, 8))

    def test_prefetch_flag_stored_when_enabled(self):
        model = _TinyDiT()
        apply_block_swap_to_dit(
            model,
            blocks_to_swap=2,
            swap_io_components=False,
            main_device="cpu",
            offload_device="cpu",
            prefetch=True,
        )

        assert getattr(model, "_blockswap_prefetch", False) is True
        assert model.blocks_to_swap == 1  # effective_blocks - 1
        output = self._run_model(model)
        assert output.shape == (2, 8)

        cleanup_blockswap(model)

    def test_prefetch_flag_off_when_no_block_swap(self):
        model = _TinyDiT()
        apply_block_swap_to_dit(
            model,
            blocks_to_swap=0,
            swap_io_components=True,
            main_device="cpu",
            offload_device="cpu",
            prefetch=True,
        )

        assert getattr(model, "_blockswap_prefetch", False) is False

    def test_prefetch_disabled_explicit(self):
        model = _TinyDiT()
        apply_block_swap_to_dit(
            model,
            blocks_to_swap=2,
            swap_io_components=False,
            main_device="cpu",
            offload_device="cpu",
            prefetch=False,
        )

        assert getattr(model, "_blockswap_prefetch", False) is False

    def test_wrapped_forward_runs_repeatedly_with_prefetch_on(self):
        """连续多次推理（模拟多任务），预取开启下结果稳定。"""
        model = _TinyDiT()
        apply_block_swap_to_dit(
            model,
            blocks_to_swap=2,
            swap_io_components=False,
            main_device="cpu",
            offload_device="cpu",
            prefetch=True,
        )

        outputs = [self._run_model(model) for _ in range(3)]
        for out in outputs[1:]:
            assert torch.allclose(out, outputs[0])

        cleanup_blockswap(model)

    def test_cleanup_clears_prefetch_attrs(self):
        model = _TinyDiT()
        apply_block_swap_to_dit(
            model,
            blocks_to_swap=2,
            swap_io_components=False,
            main_device="cpu",
            offload_device="cpu",
            prefetch=True,
        )
        cleanup_blockswap(model)

        assert not hasattr(model, "_blockswap_prefetch")
        assert not hasattr(model, "_blockswap_prefetch_stream")
        assert not hasattr(model, "_blockswap_prefetch_event")
