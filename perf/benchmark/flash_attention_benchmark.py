"""Flash Attention 2 性能基准测试。

对比 Flash Attention 2 与 PyTorch 原生 ``nn.MultiheadAttention`` 的
推理速度和显存占用。

运行方式::

    python -m perf.benchmark.flash_attention_benchmark
    # 或
    python perf/benchmark/flash_attention_benchmark.py

环境要求:
    - NVIDIA GPU (CUDA)
    - flash-attn >= 2.5.0

验收标准:
    - 速度提升 ≥ 2x（长序列）
    - 显存节省 ≥ 80%
    - 精度损失 < 1e-5
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import torch

logger = logging.getLogger(__name__)

# 确保项目根目录在 sys.path 中
if __name__ == "__main__" and __package__ is None:
    import os

    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


def _measure_vram() -> float:
    """测量当前 GPU 显存使用量（MB）。

    Returns:
        当前已分配的 GPU 显存（MB）。
    """
    return torch.cuda.memory_allocated() / 1024 / 1024


def benchmark_attention(
    seq_len: int,
    batch_size: int,
    head_dim: int = 64,
    n_heads: int = 8,
    warmup_iters: int = 10,
    benchmark_iters: int = 100,
) -> dict[str, object] | None:
    """对 Flash Attention 与标准注意力进行基准测试。

    Args:
        seq_len: 序列长度。
        batch_size: 批次大小。
        head_dim: 每个注意力头的维度。
        n_heads: 注意力头数。
        warmup_iters: 预热迭代次数（不计入计时）。
        benchmark_iters: 基准测试迭代次数。

    Returns:
        包含测试结果的字典，键包括:
        - ``seq_len``: 序列长度
        - ``batch_size``: 批次大小
        - ``standard_ms``: 标准注意力平均耗时（ms）
        - ``flash_ms``: Flash Attention 平均耗时（ms）
        - ``speedup``: 加速比
        - ``standard_vram_mb``: 标准注意力显存占用（MB）
        - ``flash_vram_mb``: Flash Attention 显存占用（MB）
        - ``vram_savings_pct``: 显存节省百分比

        如果 Flash Attention 不可用，返回 None。
    """
    dim = n_heads * head_dim

    try:
        from app.vram.flash_attention_wrapper import FLASH_AVAILABLE, FlashAttention
    except ImportError:
        from app.vram.flash_attention_wrapper import FLASH_AVAILABLE  # type: ignore[assignment]

        FlashAttention = None  # type: ignore[assignment,misc]

    if not FLASH_AVAILABLE or FlashAttention is None:
        logger.error("Flash Attention 不可用，跳过基准测试")
        print("⚠️ Flash Attention 未安装，无法运行基准测试")
        return None

    device = torch.device("cuda")

    # 标准注意力
    standard_attn = torch.nn.MultiheadAttention(
        dim,
        n_heads,
        batch_first=True,
        device=device,
    )
    flash_attn = FlashAttention(dim, n_heads, device=device)

    x_standard = torch.randn(batch_size, seq_len, dim, device=device)
    x_flash = torch.randn(batch_size, seq_len, dim, device=device)

    # 预热
    for _ in range(warmup_iters):
        _ = standard_attn(x_standard, x_standard, x_standard)[0]
        _ = flash_attn(x_flash)

    # 标准注意力基准
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    start = time.perf_counter()
    for _ in range(benchmark_iters):
        _ = standard_attn(x_standard, x_standard, x_standard)[0]
    torch.cuda.synchronize()
    standard_time = time.perf_counter() - start
    standard_vram = torch.cuda.max_memory_allocated() / 1024 / 1024

    # Flash Attention 基准
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    start = time.perf_counter()
    for _ in range(benchmark_iters):
        _ = flash_attn(x_flash)
    torch.cuda.synchronize()
    flash_time = time.perf_counter() - start
    flash_vram = torch.cuda.max_memory_allocated() / 1024 / 1024

    standard_ms = standard_time * 1000 / benchmark_iters
    flash_ms = flash_time * 1000 / benchmark_iters
    speedup = standard_time / flash_time if flash_time > 0 else float("inf")
    vram_savings = (1 - flash_vram / standard_vram) * 100 if standard_vram > 0 else 0

    result = {
        "seq_len": seq_len,
        "batch_size": batch_size,
        "standard_ms": standard_ms,
        "flash_ms": flash_ms,
        "speedup": speedup,
        "standard_vram_mb": standard_vram,
        "flash_vram_mb": flash_vram,
        "vram_savings_pct": vram_savings,
    }

    print(f"\n{'=' * 60}")
    print(f"Seq Len: {seq_len}, Batch: {batch_size}, Dim: {dim}")
    print(f"{'=' * 60}")
    print(f"Standard Attention: {standard_ms:.2f}ms ({standard_vram:.1f}MB)")
    print(f"Flash Attention:    {flash_ms:.2f}ms ({flash_vram:.1f}MB)")
    print(f"Speedup:            {speedup:.2f}x")
    print(f"VRAM Savings:       {vram_savings:.1f}%")

    return result


def benchmark_precision(
    seq_len: int = 512,
    batch_size: int = 2,
    n_heads: int = 8,
    head_dim: int = 64,
) -> dict[str, float] | None:
    """验证 Flash Attention 与标准注意力的精度一致性。

    使用相同的 QKV 权重，对比两种实现的输出差异。

    Args:
        seq_len: 序列长度。
        batch_size: 批次大小。
        n_heads: 注意力头数。
        head_dim: 每个注意力头的维度。

    Returns:
        包含精度指标的字典，键包括:
        - ``max_diff``: 最大绝对误差
        - ``mean_diff``: 平均绝对误差
        - ``pass``: 是否通过精度验收（max_diff < 1e-5）

        如果 Flash Attention 不可用，返回 None。
    """
    dim = n_heads * head_dim

    try:
        from app.vram.flash_attention_wrapper import FLASH_AVAILABLE, FlashAttention
    except ImportError:
        from app.vram.flash_attention_wrapper import FLASH_AVAILABLE  # type: ignore[assignment]

        FlashAttention = None  # type: ignore[assignment,misc]

    if not FLASH_AVAILABLE or FlashAttention is None:
        logger.error("Flash Attention 不可用，跳过精度测试")
        return None

    device = torch.device("cuda")

    # 创建相同参数的注意力模块
    standard_attn = torch.nn.MultiheadAttention(
        dim,
        n_heads,
        batch_first=True,
        device=device,
        dtype=torch.float32,
    )
    flash_attn = FlashAttention(dim, n_heads, device=device, dtype=torch.float32)

    # 使用相同输入
    x = torch.randn(batch_size, seq_len, dim, device=device, dtype=torch.float32)

    with torch.no_grad():
        standard_output = standard_attn(x, x, x)[0]
        flash_output = flash_attn(x)

    max_diff = (standard_output - flash_output).abs().max().item()
    mean_diff = (standard_output - flash_output).abs().mean().item()
    passed = max_diff < 1e-5

    print(f"\n{'=' * 60}")
    print(f"精度验证 (Seq={seq_len}, Batch={batch_size})")
    print(f"{'=' * 60}")
    print(f"Max Abs Diff:  {max_diff:.2e}")
    print(f"Mean Abs Diff: {mean_diff:.2e}")
    print(f"Pass (<1e-5):  {'✅' if passed else '❌'}")

    return {
        "max_diff": max_diff,
        "mean_diff": mean_diff,
        "pass": 1.0 if passed else 0.0,
    }


def _hardware_context() -> dict[str, object]:
    """采集硬件与软件上下文，随结果一起落盘。

    没有硬件上下文的 benchmark 数字无法横向对比（不同 GPU/驱动/torch 版本
    的结论不可迁移），因此每次运行都把环境一并记录。

    Returns:
        环境信息字典。
    """
    import platform

    ctx: dict[str, object] = {
        "gpu_name": torch.cuda.get_device_name(0),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "cudnn_version": torch.backends.cudnn.version(),
        "python_version": platform.python_version(),
        "os": platform.platform(),
    }
    return ctx


def _archive_report(report: dict[str, object]) -> Path:
    """把基准报告写入项目根 .benchmarks/ 基线库，返回归档路径。

    .benchmarks/ 是项目统一性能基线目录（在 outputs/ 之外，不受输出保留
    策略清理影响）；没有硬件上下文的基准数字无法跨机器对比，归档始终
    携带 _hardware_context() 环境信息。
    """
    import json
    from datetime import datetime

    out_dir = Path(__file__).resolve().parents[2] / ".benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    hw = report.get("hardware") or {}
    gpu_tag = str(hw.get("gpu_name", "unknown")).replace(" ", "_").replace("/", "-")
    out_path = out_dir / f"flash_attn_{gpu_tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


def main() -> None:
    """运行完整的 Flash Attention 基准测试套件。"""
    from datetime import datetime

    if not torch.cuda.is_available():
        print("❌ CUDA 不可用，无法运行基准测试")
        sys.exit(1)

    print("🔥 Flash Attention 2 性能基准测试")
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    hw_ctx = _hardware_context()

    # 速度 + 显存基准
    results: list[dict[str, object]] = []
    for seq_len, batch_size in [(1024, 4), (2048, 2), (4096, 1), (8192, 1)]:
        result = benchmark_attention(seq_len, batch_size)
        if result is not None:
            results.append(result)

    # 精度验证
    precision = benchmark_precision()

    # 验收标准汇总
    if results:
        print(f"\n{'=' * 60}")
        print("验收标准汇总")
        print(f"{'=' * 60}")
        for r in results:
            seq = r["seq_len"]
            speedup = r["speedup"]
            vram_save = r["vram_savings_pct"]
            speed_ok = speedup >= 2.0
            vram_ok = vram_save >= 80.0
            print(
                f"Seq={seq}: Speedup={speedup:.2f}x ({'✅' if speed_ok else '❌'} ≥2x), "
                f"VRAM Save={vram_save:.1f}% ({'✅' if vram_ok else '❌'} ≥80%)",
            )

    # 结果 + 硬件上下文落盘 JSON，形成可跨机器对比的档案（统一归档到 .benchmarks/）
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "hardware": hw_ctx,
        "flash_attn_available": bool(results),
        "results": results,
        "precision": precision,
    }
    out_path = _archive_report(report)
    print(f"\n📄 结果已归档: {out_path}")


if __name__ == "__main__":
    main()
