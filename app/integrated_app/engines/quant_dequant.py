# app/integrated_app/engines/quant_dequant.py
"""Comfy-Org 量化权重的加载期反量化（int8_convrot / mxfp8 / nvfp4）。

数值语义逐一对齐 ComfyUI 上游 comfy_kitchen（Apache-2.0, Copyright (c) 2025 Comfy Org）：
- int8_tensorwise + convrot：``comfy_kitchen/tensor/int8.py`` + ``int8_utils.py``
  （旋转 ``W_rot = W @ H^T``，H 为归一化 regular Hadamard，h4 核对称故 H 对称，
  反旋转即 ``@ H``；量化为逐行 scale [N,1]）
- mxfp8：``comfy_kitchen/tensor/mxfp8.py`` + ``comfy/float.py::stochastic_round_quantize_mxfp8_by_block``
  （E8M0 块缩放，32 一块，存储前经 ``to_blocked`` swizzle）
- nvfp4：``comfy_kitchen/tensor/nvfp4.py`` + ``comfy/float.py::stochastic_round_quantize_nvfp4_by_block``
  （E2M1 nibble 打包（偶数元素在高 4 位）× e4m3 块缩放（16 一块，同样 swizzle）× 全局标量）

策略为**加载期整图反量化**：state_dict 在进入模型前转成普通浮点权重，
与现有 fp8 分支同模式，不需要 triton kernel、不改变推理路径。
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

import torch

logger = logging.getLogger(__name__)

# 引擎/管理器识别的精度标识 → comfy_quant format 值
COMFY_QUANT_FORMATS: dict[str, str] = {
    "int8_convrot": "int8_tensorwise",  # convrot 标志在 JSON 内层
    "mxfp8": "mxfp8",
    "nvfp4": "nvfp4",
}

_F4_E2M1_MAX = 6.0
_E2M1_LUT = (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0)

_HADAMARD_CACHE: dict[tuple[int, torch.device, torch.dtype], torch.Tensor] = {}


# ---------------------------------------------------------------------------
# Hadamard（与 comfy_kitchen/tensor/int8_utils.py::_build_hadamard 一致）
# ---------------------------------------------------------------------------


def build_hadamard(
    size: int,
    device: torch.device | str = "cpu",
    dtype: torch.dtype = torch.float32,
) -> torch.Tensor:
    """构造归一化 regular Hadamard 矩阵（尺寸为 4 的幂）。"""
    device = torch.device(device)
    key = (size, device, dtype)
    cached = _HADAMARD_CACHE.get(key)
    if cached is not None:
        return cached
    if size < 4 or (size & (size - 1)) != 0 or math.log(size, 4) % 1 != 0:
        raise ValueError(f"Regular Hadamard size must be a power of 4, got {size}")
    h4 = torch.tensor([[1, 1, 1, -1], [1, 1, -1, 1], [1, -1, 1, 1], [-1, 1, 1, 1]], dtype=dtype, device=device)
    h = h4
    current = 4
    while current < size:
        h = torch.kron(h, h4)
        current *= 4
    h = h / math.sqrt(size)
    _HADAMARD_CACHE[key] = h
    return h


# ---------------------------------------------------------------------------
# 块缩放 swizzle（与 comfy/float.py::to_blocked 互逆）
# ---------------------------------------------------------------------------


def to_blocked(input_matrix: torch.Tensor) -> torch.Tensor:
    """行主序块缩放矩阵 → cublas d-block-scaling 布局（ComfyUI 存储格式）。"""
    rows, cols = input_matrix.shape
    n_row_blocks = math.ceil(rows / 128)
    n_col_blocks = math.ceil(cols / 4)
    padded_rows = n_row_blocks * 128
    padded_cols = n_col_blocks * 4
    padded = input_matrix
    if (rows, cols) != (padded_rows, padded_cols):
        padded = torch.zeros((padded_rows, padded_cols), device=input_matrix.device, dtype=input_matrix.dtype)
        padded[:rows, :cols] = input_matrix
    blocks = padded.view(n_row_blocks, 128, n_col_blocks, 4).permute(0, 2, 1, 3)
    rearranged = blocks.reshape(-1, 4, 32, 4).transpose(1, 2).reshape(-1, 32, 16)
    return rearranged.reshape(padded_rows, padded_cols)


_SWIZZLE_INDEX_CACHE: dict[tuple[int, int, torch.device], torch.Tensor] = {}


def _swizzle_source_map(padded_rows: int, padded_cols: int, device: torch.device) -> torch.Tensor:
    """对编号矩阵跑一遍 to_blocked，得到 swizzle 后每个位置对应的源线性索引。"""
    key = (padded_rows, padded_cols, device)
    cached = _SWIZZLE_INDEX_CACHE.get(key)
    if cached is None:
        idx = torch.arange(padded_rows * padded_cols, dtype=torch.float32, device=device).view(padded_rows, padded_cols)
        cached = to_blocked(idx).long().reshape(-1)
        _SWIZZLE_INDEX_CACHE[key] = cached
    return cached


def from_blocked(swizzled: torch.Tensor, rows: int, cols: int) -> torch.Tensor:
    """``to_blocked`` 的精确逆：swizzle 布局 → 行主序 (rows, cols) 逻辑矩阵。"""
    n_row_blocks = math.ceil(rows / 128)
    n_col_blocks = math.ceil(cols / 4)
    padded_rows = n_row_blocks * 128
    padded_cols = n_col_blocks * 4
    src = _swizzle_source_map(padded_rows, padded_cols, swizzled.device)
    flat = swizzled.reshape(-1).to(dtype=torch.float32)
    out = torch.zeros(padded_rows * padded_cols, dtype=torch.float32, device=swizzled.device)
    # src[pos] = 该 swizzle 位置承载的源索引 → 把值放回源位置
    out.scatter_(0, src, flat)
    return out.view(padded_rows, padded_cols)[:rows, :cols]


# ---------------------------------------------------------------------------
# comfy_quant 元数据
# ---------------------------------------------------------------------------


def decode_comfy_quant(raw: torch.Tensor) -> dict[str, Any] | None:
    """把 state_dict 里的 comfy_quant uint8 字节张量解析为 JSON 字典。"""
    if not isinstance(raw, torch.Tensor):
        return None
    try:
        data = raw.detach().to("cpu", torch.uint8).reshape(-1).tolist()
        obj = json.loads(bytes(data).decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 三种格式的反量化
# ---------------------------------------------------------------------------


def dequantize_int8_convrot(
    weight: torch.Tensor,
    weight_scale: torch.Tensor,
    convrot: bool = True,
    groupsize: int = 256,
) -> torch.Tensor:
    """INT8（逐行 scale [+ 分组 Hadamard 逆旋转]）→ float32。

    量化路径：W_rot = W @ H^T 后按行（输出通道）量化存 [N,1] scale；
    反量化：Q×scale 得 W_rot，再 @H（H 对称，H^T·H=I）还原 W。
    """
    w = weight.to(torch.float32) * weight_scale.to(torch.float32)
    if not convrot:
        return w
    out_f, in_f = w.shape
    if in_f % groupsize != 0:
        raise ValueError(f"int8_convrot: in_features {in_f} not divisible by group_size {groupsize}")
    h = build_hadamard(groupsize, device=w.device, dtype=torch.float32)
    grouped = w.reshape(out_f, in_f // groupsize, groupsize)
    return torch.matmul(grouped, h).reshape(out_f, in_f)


def dequantize_mxfp8(weight: torch.Tensor, weight_scale: torch.Tensor) -> torch.Tensor:
    """MXFP8（E4M3 数据 × E8M0 块缩放，32 一块，swizzle 存储）→ float32。"""
    rows, cols = weight.shape
    scale_u8 = weight_scale.to(torch.uint8)
    scales = from_blocked(scale_u8.to(torch.float32), rows, cols // 32)
    # E8M0: 字节即指数域 → 2^(b-127)；用位构造 float32（与 comfy float.py 一致）
    scales = (scales.to(torch.int32) << 23).view(torch.float32)
    data = weight.to(torch.float32)
    return (data.reshape(rows, cols // 32, 32) * scales.unsqueeze(-1)).reshape(rows, cols)


def _e2m1_lut(device: torch.device, dtype: torch.dtype) -> torch.Tensor:
    """E2M1 码表 [16]：低 8 为正值，高 8 为符号位取反。"""
    pos = torch.tensor(_E2M1_LUT, dtype=dtype, device=device)
    return torch.cat([pos, -pos])


def dequantize_nvfp4(
    weight: torch.Tensor,
    weight_scale: torch.Tensor,
    weight_scale_2: torch.Tensor,
) -> torch.Tensor:
    """NVFP4（E2M1 nibble 打包 × e4m3 块缩放(16) × 全局标量）→ float32。

    打包序与 comfy/float.py::stochastic_float_to_fp4_e2m1 一致：
    ``packed = (even << 4) | odd``，即偶数元素在高 4 位。
    """
    rows, packed_cols = weight.shape
    cols = packed_cols * 2
    raw = weight.to(torch.uint8)
    lut = _e2m1_lut(raw.device, torch.float32)
    even = (raw >> 4) & 0x0F
    odd = raw & 0x0F
    data = torch.stack([lut[even.long()], lut[odd.long()]], dim=-1).reshape(rows, cols)
    scale = weight_scale.to(torch.float32)
    scales = from_blocked(scale, rows, cols // 16)
    global_scale = weight_scale_2.to(torch.float32).reshape(())
    out = data.reshape(rows, cols // 16, 16) * scales.unsqueeze(-1) * global_scale
    return out.reshape(rows, cols)


# ---------------------------------------------------------------------------
# state_dict 级统一入口
# ---------------------------------------------------------------------------


def dequantize_state_dict(state_dict: dict[str, torch.Tensor], dtype: torch.dtype = torch.float32) -> int:
    """按 comfy_quant 元数据就地对每个量化 Linear 权重做反量化。

    处理逻辑：扫描 ``*.comfy_quant`` 键，找到兄弟 ``.weight`` / ``.weight_scale``
    [``.weight_scale_2``]，替换 ``.weight`` 为反量化结果并删除量化附属键。

    内存优化：反量化在 float32 中完成（Hadamard/缩放需要精度），完成后立即
    转为 ``dtype``（默认 float32；加载期调用方应传 bf16 以避免全量 float32
    与后续 bf16 转换同时存在导致 RAM 峰值）。

    Args:
        state_dict: load_file 得到的权字典（CPU）。
        dtype: 反量化结果的存储 dtype（默认 float32；加载期建议传 bf16）。

    Returns:
        int: 成功反量化的权重张量数（0 表示非 Comfy-Org 量化包，静默跳过）。

    Raises:
        ValueError: comfy_quant 声明了支持的格式但兄弟张量缺失/形状不符。
    """
    quant_keys = [k for k in state_dict if k.endswith(".comfy_quant")]
    if not quant_keys:
        return 0
    done = 0
    for qk in quant_keys:
        base = qk[: -len(".comfy_quant")]
        meta = decode_comfy_quant(state_dict[qk])
        if not meta:
            raise ValueError(f"{qk}: comfy_quant 元数据无法解析")
        fmt = meta.get("format")
        wk, sk = f"{base}.weight", f"{base}.weight_scale"
        weight = state_dict.get(wk)
        scale = state_dict.get(sk)
        if weight is None or scale is None:
            raise ValueError(f"{wk}/.weight_scale 缺失，无法反量化（format={fmt}）")
        if fmt == "int8_tensorwise":
            convrot = bool(meta.get("convrot", False))
            groupsize = int(meta.get("convrot_groupsize", 256))
            w_deq = dequantize_int8_convrot(weight, scale, convrot=convrot, groupsize=groupsize)
        elif fmt == "mxfp8":
            w_deq = dequantize_mxfp8(weight, scale)
        elif fmt == "nvfp4":
            s2k = f"{base}.weight_scale_2"
            scale2 = state_dict.get(s2k)
            if scale2 is None:
                raise ValueError(f"{s2k} 缺失，无法反量化 nvfp4（{base}）")
            w_deq = dequantize_nvfp4(weight, scale, scale2)
            del state_dict[s2k]
        else:
            raise ValueError(f"不支持的 comfy_quant 格式: {fmt}（{base}）")
        # 反量化在 float32 完成，立即转目标 dtype 以控制 RAM 峰值
        state_dict[wk] = w_deq.to(dtype=dtype) if dtype != torch.float32 else w_deq
        del state_dict[sk]
        del state_dict[qk]
        done += 1
    logger.info("Comfy-Org 量化权重反量化完成: %d 个 Linear (dtype=%s)", done, dtype)
    return done


__all__ = [
    "COMFY_QUANT_FORMATS",
    "build_hadamard",
    "decode_comfy_quant",
    "dequantize_int8_convrot",
    "dequantize_mxfp8",
    "dequantize_nvfp4",
    "dequantize_state_dict",
    "from_blocked",
    "to_blocked",
]
