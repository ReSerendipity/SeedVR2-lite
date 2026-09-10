"""GPU 显存检测与 OOM 预防工具模块 - SeedVR2 视频修复项目

本模块提供 GPU 显存查询、模型显存估算、缓存清理和 OOM 保护装饰器等工具函数，
是显存管理的底层工具集，为上层模块（模型管理器、内存管理器等）提供基础能力。

所属项目: SeedVR2 (基于 ComfyUI-SeedVR2_VideoUpscaler 独立重构)
核心技术栈: PyTorch CUDA API, psutil, functools, garbage collection

主要功能:
    - GPU 显存实时监控（总显存、已分配、已保留、可用、利用率）
    - 系统内存信息查询
    - 模型加载显存需求估算（考虑模型大小、精度、分辨率）
    - VRAM 预检 + 精度/分块参数推荐（借鉴 Image_MultiModel）
    - GPU 缓存清理与强制垃圾回收
    - OOM 保护装饰器（捕获显存不足异常并自动清理）
    - 完整系统信息聚合（GPU + 内存 + OS）

常量说明:
    显存阈值以 config.yaml 为单一事实来源（P0-3）：
    - 权重显存基线 model.models.<size>.baseline_vram_{fp16,fp8}_gb（加载预检）
    - 最低运行显存 model.models.<size>.min_vram_{fp16,fp8}_gb（参数推荐）
    - Transformer 块数 model.models.<size>.num_blocks（BlockSwap 推荐）
    - VAE 分块档位 gpu.vram_tile_tiers（tile_size/tile_overlap 推荐）
    本模块内置字典仅为配置不可读时的回退默认值。
"""

import functools
import gc
import logging
from collections.abc import Callable, Sequence
from functools import lru_cache

logger = logging.getLogger(__name__)

# 模块级一次性导入 torch，避免每次函数调用都重新导入
# torch 不可用时优雅降级
try:
    import torch

    _HAS_TORCH_CUDA = torch.cuda.is_available()
except ImportError:
    torch = None  # type: ignore[assignment]
    _HAS_TORCH_CUDA = False

# ===========================================================================
# 显存估算常量 — P0-3 单一事实来源改造
# ===========================================================================
# 权重基线 / 最低运行显存 / 块数 / VAE 分块档位全部以 config.yaml 为权威来源
# （models.*.baseline_vram_*_gb、models.*.min_vram_*_gb、models.*.num_blocks、
# gpu.vram_tile_tiers）。下方字典仅为 config.yaml 不可读时的**回退默认值**，
# 数值与 config.yaml 逐项一致，保证回退路径行为不变。
_FALLBACK_WEIGHTS_VRAM_MB = {
    "3b": {"fp16": 8192, "fp8": 4096},  # 3B 模型 8GB(FP16) / 4GB(FP8) 权重基线
    "7b": {"fp16": 16384, "fp8": 8192},  # 7B 模型 16GB(FP16) / 8GB(FP8) 权重基线
    "7b_sharp": {"fp16": 16384, "fp8": 8192},
}
_DEFAULT_MODEL_VRAM_MB = {"fp16": 8192, "fp8": 4096}  # 未知模型大小的默认估值
_BASE_RESOLUTION_PIXELS = 1080 * 1920  # 基准分辨率（用于计算像素比例因子）
_BASE_INFERENCE_VRAM_MB = 4000  # 推理额外显存基线（4GB 起，随分辨率线性增长）

# ===========================================================================
# VRAM 预检常量 — 用于 estimate_vram_requirements / recommend_params（回退默认值，
# 与 config.yaml 中 models.*.min_vram_*_gb 对齐）
# ===========================================================================
_FALLBACK_MODEL_VRAM_BASE_GB: dict[str, dict[str, float]] = {
    "3b": {"fp16": 16.0, "fp8": 8.0},
    "7b": {"fp16": 24.0, "fp8": 12.0},
    "7b_sharp": {"fp16": 24.0, "fp8": 12.0},
}
# 模型 Transformer 块数回退值（与 config models.*.num_blocks 权威值一致）
_FALLBACK_MODEL_NUM_BLOCKS: dict[str, int] = {
    "3b": 32,
    "7b": 36,
    "7b_sharp": 36,
}
# VAE 分块推荐档位回退值（config gpu.vram_tile_tiers）
_FALLBACK_TILE_TIERS: list[dict[str, float]] = [
    {"min_available_gb": 20.0, "tile_size": 1024, "tile_overlap": 512},
    {"min_available_gb": 12.0, "tile_size": 768, "tile_overlap": 256},
    {"min_available_gb": 8.0, "tile_size": 512, "tile_overlap": 128},
    {"min_available_gb": 0.0, "tile_size": 256, "tile_overlap": 64},
]


@lru_cache(maxsize=1)
def _vram_config_snapshot() -> dict:
    """读取 config.yaml 中显存相关配置的快照（P0-3 单一事实来源）。

    config.yaml 是权威来源；读取失败（文件缺失/损坏/依赖不可用）时返回空字典，
    调用方回退到上方内置默认值，保证任何环境下行为可预期。
    测试需要刷新快照时可调用 ``_vram_config_snapshot.cache_clear()``。

    Returns:
        dict: 含 model.models（各模型显存配置）与 gpu.vram_tile_tiers 的字典。
    """
    try:
        from app.integrated_app.config import get_app_config

        cfg = get_app_config()
        if cfg is None:
            return {}
        if hasattr(cfg, "model_dump"):
            cfg = cfg.model_dump()
        models = (cfg.get("model", {}) or {}).get("models", {}) or {}
        tiers = (cfg.get("gpu", {}) or {}).get("vram_tile_tiers", []) or []
        return {"models": models, "tiers": tiers}
    except Exception as e:  # pragma: no cover — 仅在配置系统不可用时触发
        logger.debug(f"读取显存配置失败，回退内置默认值: {e}")
        return {}


def _weights_vram_mb() -> dict[str, dict[str, int]]:
    """模型权重显存基线表（MB，加载预检用）。

    来源：config.yaml ``model.models.<size>.baseline_vram_fp16_gb / baseline_vram_fp8_gb``。
    未配置（0）或 config 不可读的条目回退内置默认值。
    """
    models = _vram_config_snapshot().get("models", {})
    table: dict[str, dict[str, int]] = {k: dict(v) for k, v in _FALLBACK_WEIGHTS_VRAM_MB.items()}
    for size, m in models.items():
        w16 = float(m.get("baseline_vram_fp16_gb", 0) or 0)
        w8 = float(m.get("baseline_vram_fp8_gb", 0) or 0)
        entry = dict(table.get(size) or _DEFAULT_MODEL_VRAM_MB)
        if w16 > 0:
            entry["fp16"] = int(w16 * 1024)
        if w8 > 0:
            entry["fp8"] = int(w8 * 1024)
        table[size] = entry
    return table


def _model_vram_base_gb() -> dict[str, dict[str, float]]:
    """模型最低运行显存表（GB，recommend_params 用）。

    来源：config.yaml ``model.models.<size>.min_vram_fp16_gb / min_vram_fp8_gb``。
    """
    models = _vram_config_snapshot().get("models", {})
    table: dict[str, dict[str, float]] = {k: dict(v) for k, v in _FALLBACK_MODEL_VRAM_BASE_GB.items()}
    for size, m in models.items():
        fp16 = float(m.get("min_vram_fp16_gb", 0) or 0)
        fp8 = float(m.get("min_vram_fp8_gb", 0) or 0)
        entry = dict(table.get(size) or {"fp16": 16.0, "fp8": 8.0})
        if fp16 > 0:
            entry["fp16"] = fp16
        if fp8 > 0:
            entry["fp8"] = fp8
        table[size] = entry
    return table


def _model_num_blocks() -> dict[str, int]:
    """模型 Transformer 块数表。来源：config.yaml ``model.models.<size>.num_blocks``。"""
    models = _vram_config_snapshot().get("models", {})
    table = dict(_FALLBACK_MODEL_NUM_BLOCKS)
    for size, m in models.items():
        nb = int(m.get("num_blocks", 0) or 0)
        if nb > 0:
            table[size] = nb
    return table


def _tile_tiers() -> list[dict[str, float]]:
    """VAE 分块推荐档位表。来源：config.yaml ``gpu.vram_tile_tiers``（按 min_available_gb 降序）。"""
    tiers = _vram_config_snapshot().get("tiers", [])
    if not tiers:
        return [dict(t) for t in _FALLBACK_TILE_TIERS]
    normalized: list[dict[str, float]] = []
    for t in tiers:
        if hasattr(t, "model_dump"):
            t = t.model_dump()
        try:
            normalized.append(
                {
                    "min_available_gb": float(t.get("min_available_gb", 0)),
                    "tile_size": int(t.get("tile_size", 512)),
                    "tile_overlap": int(t.get("tile_overlap", 128)),
                }
            )
        except (TypeError, ValueError):
            continue
    normalized.sort(key=lambda x: x["min_available_gb"], reverse=True)
    return normalized or [dict(t) for t in _FALLBACK_TILE_TIERS]


# BlockSwap 开启时模型权重显存削减比例（默认 swap 32/36 块，约 50% 削减）
_BLOCKSWAP_REDUCTION = 0.5
# 安全阈值：推荐参数时使用可用显存的 90% 作为安全线
_SAFE_THRESHOLD_RATIO = 0.9
# 分辨率额外开销系数：超过 1080p 后每单位 resolution_factor 增加 2GB
_RESOLUTION_OVERHEAD_PER_UNIT_GB = 2.0
# 视频帧缓冲冗余系数
_FRAME_BUFFER_REDUNDANCY = 1.5
# 每帧每通道字节数（FP16 下 2 字节 × 3 通道 RGB）
_FRAME_BYTES_PER_PIXEL = 3 * 2
_GB = 1024**3  # 1 GB 的字节数


def _precision_residency_key(precision: str | None) -> str:
    """把「存储精度」映射到「显存驻留精度档位」（估算查表用）。

    显存估算只关心权重在 GPU 上实际驻留多大：

    - `fp8`：真 fp8 检查点，驻留减半 → 返回 `"fp8"`
    - `mxfp8` / `int8_convrot` / `nvfp4`：加载期反量化，驻留 ≈ fp16 → 返回 `"fp16"`
    - 其它/未知精度（含 None）：保守按 `"fp16"` 档位

    Args:
        precision: 存储精度标识。

    Returns:
        str: 显存基线查表用的档位键（`"fp16"` 或 `"fp8"`）。
    """
    return "fp8" if precision == "fp8" else "fp16"


def get_gpu_memory_info() -> dict:
    """获取 GPU 显存详细信息（使用 mem_get_info 获取实际可用显存）

    使用 PyTorch CUDA API 查询设备 0 的显存状态，区分已分配（allocated）、
    已保留（reserved）和实际可用（free）三种状态。

    Returns:
        dict: 包含以下键的显存信息字典：
            - total_mb (int): 总显存（MB）
            - allocated_mb (int): PyTorch 已分配显存（MB，张量实际占用）
            - reserved_mb (int): PyTorch 已保留显存（MB，缓存分配器管理）
            - available_mb (int): 实际可用显存（MB，通过 mem_get_info 获取）
            - utilization_pct (float): 显存利用率百分比（0-100）

        查询失败时返回全 0 的默认字典。
    """
    try:
        if _HAS_TORCH_CUDA:
            # mem_get_info 返回 (free, total)，反映驱动层面实际可用显存
            free_memory, total_memory = torch.cuda.mem_get_info(0)
            allocated = torch.cuda.memory_allocated(0)
            reserved = torch.cuda.memory_reserved(0)
            used = total_memory - free_memory

            return {
                "total_mb": total_memory // (1024 * 1024),
                "allocated_mb": allocated // (1024 * 1024),
                "reserved_mb": reserved // (1024 * 1024),
                "available_mb": free_memory // (1024 * 1024),
                "utilization_pct": float((used / total_memory) * 100) if total_memory > 0 else 0.0,
            }
    except Exception as e:
        logger.error(f"获取 GPU 显存信息失败: {e}")

    return {
        "total_mb": 0,
        "allocated_mb": 0,
        "reserved_mb": 0,
        "available_mb": 0,
        "utilization_pct": 0.0,
    }


def check_vram_available(required_mb: int) -> tuple[bool, int]:
    """检查是否有足够的可用显存

    Args:
        required_mb: 需要的显存大小（MB）

    Returns:
        tuple[bool, int]: (是否足够, 当前可用显存MB)
            - 第一个元素：可用显存 >= required_mb 时为 True
            - 第二个元素：当前实际可用显存（MB）
    """
    info = get_gpu_memory_info()
    available = info["available_mb"]
    return available >= required_mb, available


def check_vram_available_for_load(required_mb: int) -> tuple[bool, int]:
    """模型加载前的显存预算检查（把本进程已保留的显存计回预算）。

    与 `check_vram_available` 的区别：驱动层的 `available` **不包含**本进程缓存
    分配器已保留（reserved）的显存。模型常驻时（`cache_model` 命中、或空闲卸载
    尚未触发）权重就住在 reserved 里，若仍按裸 `available` 索要权重基线，等于对
    同一份权重二次扣减 → 加载被误拒（表现为「第二次提交反而报显存不足」）。

    Args:
        required_mb: 需要的显存大小（MB）

    Returns:
        tuple[bool, int]: (预算是否够, 计入 reserved 后的可用预算MB)
    """
    info = get_gpu_memory_info()
    budget = info["available_mb"] + info["reserved_mb"]
    return budget >= required_mb, budget


def estimate_model_vram(model_size: str, resolution: tuple | None = None, precision: str = "fp16") -> int:
    """估算模型加载和推理所需的总显存（MB）

    显存估算公式：
        总显存 = 模型权重显存 + 推理额外显存
        - 模型权重显存：根据模型大小和精度查表（_BASE_VRAM_MB）
        - 推理额外显存：与分辨率像素数成正比（相对于 1080x1920 基准）
          推理显存 = BASE_INFERENCE_VRAM_MB * max(1.0, pixel_factor)

    Args:
        model_size: 模型大小标识，支持 "3b" / "7b"
        resolution: 目标分辨率 (height, width) 元组；为 None 时仅计算权重显存
        precision: 存储精度，支持 "fp16" / "fp8" / "mxfp8" / "int8_convrot" / "nvfp4"。
            权重**驻留**显存按 `_precision_residency_key` 归档：量化包与 fp8 检查点均在
            加载期反量化为 fp16（见 engines/quant_dequant.py），故与 fp16 同档。

    Returns:
        int: 估算的总显存需求（MB）
    """
    # 查表获取模型权重显存基线（config.yaml 单一事实来源，P0-3）
    model_vram = _weights_vram_mb().get(model_size, _DEFAULT_MODEL_VRAM_MB)
    base_vram = model_vram.get(_precision_residency_key(precision), model_vram["fp16"])

    if resolution:
        h, w = resolution
        # 推理额外显存与像素数成正比：高分辨率需要更多中间激活显存
        pixel_factor = (h * w) / _BASE_RESOLUTION_PIXELS
        inference_vram = int(_BASE_INFERENCE_VRAM_MB * max(1.0, pixel_factor))
        return base_vram + inference_vram

    return base_vram


def clear_gpu_cache():
    """清理 GPU 显存缓存

    调用 torch.cuda.empty_cache() 释放 PyTorch 缓存分配器持有的未使用显存，
    归还给 CUDA 驱动。不会释放正在使用的张量显存。

    注意：这不会减少 torch.cuda.memory_allocated() 的显示值，
    但会增加 torch.cuda.mem_get_info() 报告的可用显存。
    """
    try:
        if _HAS_TORCH_CUDA:
            torch.cuda.empty_cache()
            logger.info("GPU 缓存已清理")
    except Exception as e:
        logger.error(f"GPU 缓存清理失败: {e}")


def force_garbage_collect():
    """强制进行 Python 垃圾回收并清理 GPU 缓存

    执行完整的二级清理流程：
        1. gc.collect()：回收 Python 层不可达对象，释放其持有的张量引用
        2. clear_gpu_cache()：释放 CUDA 缓存分配器的空闲显存

    通常在 OOM 后或模型卸载后调用，最大化显存回收。
    """
    gc.collect()
    clear_gpu_cache()


def oom_protect(func: Callable) -> Callable:
    """OOM 保护装饰器 - 异步函数显存不足自动捕获与恢复

    为异步推理函数提供显存异常保护：
        1. 捕获 RuntimeError 中包含 "out of memory" 或 "CUDA" 的异常
        2. 自动执行垃圾回收和 GPU 缓存清理
        3. 转换为友好的 MemoryError 并抛出，附带用户解决建议
        4. 非 OOM 异常原样抛出

    Args:
        func: 被装饰的异步函数

    Returns:
        Callable: 包装后的异步函数

    Raises:
        MemoryError: 捕获到 CUDA OOM 时抛出，包含解决建议信息
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except RuntimeError as e:
            # 仅识别显存不足类错误。不能用宽泛的 "CUDA" 关键词匹配：
            # device-side assert / 驱动错误等非 OOM 失败会被误转成 MemoryError，
            # 进而被坏案例重试链路当成 OOM 降级重试，白烧 GPU 时间
            msg = str(e).lower()
            if "out of memory" in msg or "no available memory" in msg:
                logger.error(f"GPU 显存不足: {e}")
                # OOM 后立即强制清理，尽可能回收显存
                force_garbage_collect()
                raise MemoryError(
                    "GPU 显存不足，请尝试：\n1. 切换到 3B 模型\n2. 降低输出分辨率\n3. 关闭其他占用显存的程序"
                ) from e
            raise
        except Exception as e:
            logger.error(f"推理执行失败: {e}")
            raise

    return wrapper


def _normalize_model_name(model_name: str) -> str:
    """将用户输入的模型名称标准化为内部 key。

    支持的输入格式：
        - "3b" / "3B"
        - "7b" / "7B"
        - "7b-sharp" / "7b_sharp" / "7B-Sharp" / "7bsharp"

    Args:
        model_name: 用户输入的模型名称。

    Returns:
        str: 标准化后的内部 key（"3b" / "7b" / "7b_sharp"）。
    """
    key = model_name.lower().replace("-", "_").replace(" ", "")
    if "sharp" in key:
        return "7b_sharp"
    return key


def estimate_vram_requirements(
    model_name: str,
    precision: str,
    input_width: int,
    input_height: int,
    num_frames: int = 1,
    blocks_to_swap: int = 0,
) -> float:
    """估算推理所需 VRAM（GB）。

    估算公式：
        总显存 = 模型基线（含 BlockSwap 削减） + 分辨率额外开销 + 视频帧缓冲
        - 模型基线：根据模型大小和精度查表（与 config.yaml min_vram_*_gb 对齐）
        - 分辨率额外开销：超过 1080p 后按平方根缩放，每单位增加 2GB
        - 视频帧缓冲：每帧 (W×H×3×2) 字节 × num_frames × 1.5 倍冗余

    Args:
        model_name: 模型名称，支持 "3b" / "7b" / "7b-sharp" / "7b_sharp"。
        precision: 计算精度，"fp16" / "fp8" / "mxfp8" / "int8_convrot" / "nvfp4"。
            ⚠ 量化格式（mxfp8/int8_convrot/nvfp4）是**存储/加载期**反量化，权重以
            bf16/fp16 驻留显存（见 engines/quant_dequant.py），故按 fp16 同档计入；
            只有真 fp8 检查点驻留减半。仅用文件体积判断精度会低估需求。
        input_width: 输入宽度（像素）。
        input_height: 输入高度（像素）。
        num_frames: 帧数，图像=1，视频=实际帧数。
        blocks_to_swap: BlockSwap 换出到 CPU 的块数，0 表示未启用。启用时按
            权重基线的 `_BLOCKSWAP_REDUCTION` 比例削减常驻显存（速度换显存）。

    Returns:
        float: 估算所需 VRAM（GB），保留两位小数。
    """
    model_key = _normalize_model_name(model_name)
    base_table = _model_vram_base_gb()
    base_vram = base_table.get(model_key, base_table["3b"])
    residency_key = _precision_residency_key(precision)
    base = base_vram.get(residency_key, base_vram["fp16"])
    if blocks_to_swap > 0:
        base -= base * _BLOCKSWAP_REDUCTION

    # 分辨率额外开销（平方根缩放，1080p 为基准）
    resolution_factor = max(1.0, ((input_width * input_height) / _BASE_RESOLUTION_PIXELS) ** 0.5)
    resolution_overhead = (resolution_factor - 1.0) * _RESOLUTION_OVERHEAD_PER_UNIT_GB

    # 视频帧缓冲：(W×H×3×2) bytes × num_frames × 1.5 倍冗余
    frame_buffer_gb = (
        input_width * input_height * _FRAME_BYTES_PER_PIXEL * _FRAME_BUFFER_REDUNDANCY * max(1, num_frames)
    ) / _GB

    total = base + resolution_overhead + frame_buffer_gb
    return round(total, 2)


def recommend_params(
    model_name: str,
    input_width: int,
    input_height: int,
    num_frames: int = 1,
    available_vram_gb: float | None = None,
    available_precisions: Sequence[str] | None = None,
) -> dict:
    """根据输入参数和可用显存推荐精度/分块/BlockSwap 参数组合。

    推荐逻辑（逐级回退）：
        1. fp16 档（按实际持有精度展示）不开 BlockSwap → 满足安全阈值即推荐（risk=low）
        2. fp8 不开 BlockSwap → **仅当磁盘上真实存在 fp8 检查点**才作为降档台阶
           （risk=low）；量化包 mxfp8/int8_convrot/nvfp4 为加载期反量化、权重仍以
           fp16 驻留，**不是**省显存台阶，不参与降档
        3. BlockSwap 换出大部分块（约 50% 权重削减）→ 装得下则放行（risk=medium）
        4. 以上均不满足 → 报告 risk=high（由调用方决定拒绝还是放行）

    安全阈值 = 可用显存 × 0.9（预留 10% 安全余量）。

    Args:
        model_name: 模型名称，支持 "3b" / "7b" / "7b-sharp" / "7b_sharp"。
        input_width: 输入宽度（像素）。
        input_height: 输入高度（像素）。
        num_frames: 帧数，图像=1，视频=实际帧数。
        available_vram_gb: 可用显存（GB），None 时自动探测。
        available_precisions: 用户磁盘上真实存在的精度集合。None 表示未知/不限制
            （保持既有调用方与测试语义）；显式传入时，推荐结果只会落在该集合内。

    Returns:
        dict: 推荐参数组合，包含以下键：
            - precision (str): 推荐精度，"fp16" / "fp8" / "mxfp8" / "int8_convrot" / "nvfp4"
            - enable_blockswap (bool): 是否建议开启 BlockSwap
            - blocks_to_swap (int): 推荐换出块数（BlockSwap 开启时有效）
            - tile_size (int): 推荐 VAE tile 分块大小
            - vram_tile_overlap (int): 推荐 tile 重叠像素
            - estimated_vram_gb (float): 估算所需显存（GB）
            - available_vram_gb (float): 可用显存（GB）
            - risk (str): OOM 风险等级，"low" / "medium" / "high"
            - warning (str): 风险提示信息（空字符串表示无风险）
    """
    # 自动探测可用显存
    if available_vram_gb is None:
        info = get_gpu_memory_info()
        available_vram_gb = info["available_mb"] / 1024.0

    model_key = _normalize_model_name(model_name)
    base_table = _model_vram_base_gb()
    base_vram = base_table.get(model_key, base_table["3b"])
    num_blocks = _model_num_blocks().get(model_key, 36)

    # 精度可用性：None = 不掌握磁盘事实，沿用旧语义（fp16/fp8 都当作可用）
    unrestricted = available_precisions is None
    owned = set(available_precisions or ())
    # fp16 档展示用的实际精度标识：优先用户持有的非 fp8 精度（量化包驻留≈fp16）
    fp16_label = next((p for p in ("fp16", "mxfp8", "int8_convrot", "nvfp4") if unrestricted or p in owned), "fp16")

    # 估算各方案所需显存
    fp16_needed = estimate_vram_requirements(model_name, "fp16", input_width, input_height, num_frames)
    fp8_needed = estimate_vram_requirements(model_name, "fp8", input_width, input_height, num_frames)

    # fp8 只有在磁盘上真实存在时才是省显存台阶（量化包反量化后仍以 fp16 驻留，不算）
    prefer_fp8 = fp8_needed < fp16_needed and (unrestricted or "fp8" in owned)
    # BlockSwap 削减模型权重显存（按所选档位的权重基线削减约 50%）
    fp16_base = base_vram.get("fp16", 16.0)
    swap_base = base_vram.get("fp8", fp16_base / 2) if prefer_fp8 else fp16_base
    swap_precision = "fp8" if prefer_fp8 else fp16_label
    swap_needed = fp8_needed if prefer_fp8 else fp16_needed
    swap_with_blockswap = swap_needed - swap_base * _BLOCKSWAP_REDUCTION

    safe_threshold = available_vram_gb * _SAFE_THRESHOLD_RATIO

    warning = ""

    if fp16_needed <= safe_threshold:
        precision = fp16_label
        enable_blockswap = False
        estimated = fp16_needed
        risk = "low"
    elif prefer_fp8 and fp8_needed <= safe_threshold:
        precision = "fp8"
        enable_blockswap = False
        estimated = fp8_needed
        risk = "low"
    elif swap_with_blockswap <= available_vram_gb:
        precision = swap_precision
        enable_blockswap = True
        estimated = swap_with_blockswap
        risk = "medium"
        warning = (
            f"显存偏紧：按当前配置估算需 {fp16_needed:.1f}GB，可用 {available_vram_gb:.1f}GB。"
            f"建议开启 BlockSwap（换出 {num_blocks - 4} 块到 CPU，估算降至 {estimated:.1f}GB），"
            f"推理速度会明显变慢。"
        )
    else:
        precision = swap_precision
        enable_blockswap = True
        estimated = swap_with_blockswap
        risk = "high"
        warning = (
            f"VRAM 严重不足：估算 {estimated}GB（含 BlockSwap），可用 {available_vram_gb:.1f}GB。"
            f"建议降低分辨率、减少帧数或使用更小的模型。"
        )

    # BlockSwap 推荐换出块数（保留 4 块在 GPU，其余换出）
    blocks_to_swap = num_blocks - 4 if enable_blockswap else 0

    # VAE tile 分块推荐：按可用显存匹配 config.yaml gpu.vram_tile_tiers 档位（降序）
    tile_size = 256
    vram_tile_overlap = 64
    for tier in _tile_tiers():
        if available_vram_gb >= tier["min_available_gb"]:
            tile_size = int(tier["tile_size"])
            vram_tile_overlap = int(tier["tile_overlap"])
            break

    return {
        "precision": precision,
        "enable_blockswap": enable_blockswap,
        "blocks_to_swap": blocks_to_swap,
        "tile_size": tile_size,
        "vram_tile_overlap": vram_tile_overlap,
        "estimated_vram_gb": estimated,
        "available_vram_gb": round(available_vram_gb, 2),
        "risk": risk,
        "warning": warning,
    }


def get_system_memory_info() -> dict:
    """获取系统内存（RAM）信息

    使用 psutil 查询系统虚拟内存状态。

    Returns:
        dict: 包含以下键的内存信息字典：
            - total_mb (int): 总物理内存（MB）
            - available_mb (int): 可用内存（MB）
            - used_mb (int): 已用内存（MB）
            - utilization_pct (float): 内存利用率百分比（0-100）

        psutil 不可用时返回全 0 默认字典。
    """
    try:
        from app.integrated_app.engines._memory_utils import _get_system_memory

        mem = _get_system_memory()
        return {
            "total_mb": mem.total // (1024 * 1024),
            "available_mb": mem.available // (1024 * 1024),
            "used_mb": mem.used // (1024 * 1024),
            "utilization_pct": mem.percent,
        }
    except Exception:
        return {
            "total_mb": 0,
            "available_mb": 0,
            "used_mb": 0,
            "utilization_pct": 0,
        }


def get_full_system_info() -> dict:
    """获取完整系统信息（GPU + 内存 + 操作系统）

    聚合 GPU 显存、系统内存、OS 版本、Python 版本等信息，
    用于系统状态展示和问题诊断。

    Returns:
        dict: 包含以下键的系统信息字典：
            - os (str): 操作系统名称（Windows/Linux/Darwin）
            - os_version (str): 操作系统版本号
            - processor (str): 处理器信息
            - python_version (str): Python 版本号
            - gpu (dict): GPU 显存信息（来自 get_gpu_memory_info）
            - memory (dict): 系统内存信息（来自 get_system_memory_info）
    """
    gpu_info = get_gpu_memory_info()
    mem_info = get_system_memory_info()

    import platform

    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "gpu": gpu_info,
        "memory": mem_info,
    }
