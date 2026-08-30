#!/usr/bin/env python3
"""GPU 真实利用率与温度查询模块（成本治理 P2-1）。

历史实现中 /api/system/gpu 的"利用率"实为显存占用比（无 NVML 依赖），
无法反映 SM 计算单元的真实忙碌程度，导致"空闲判定 / 缩容"类决策失去依据。

本模块通过 nvidia-smi 子进程查询真实 SM 利用率与温度：
- 无 pynvml / NVML 库依赖（Windows 便携包环境友好）
- 结果带 2s TTL 缓存，避免频繁拉起子进程
- 查询失败进入 30s 冷却期，冷却期内直接返回 None（回退显存占比语义）

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import logging
import subprocess
import time

logger = logging.getLogger(__name__)

# 查询结果缓存时长（秒）：nvidia-smi 子进程冷启动开销大，不宜每次调用都执行
_QUERY_TTL_SECONDS = 2.0
# 查询失败后的冷却期（秒）：nvidia-smi 不存在/无 GPU 的机器上避免反复无效拉起
_FAIL_COOLDOWN_SECONDS = 30.0

_cache: dict = {"ts": 0.0, "data": None, "fail_ts": 0.0}


def query_gpu_utilization(force: bool = False) -> dict | None:
    """查询 GPU SM 真实利用率与温度（带缓存）。

    Args:
        force: 忽略缓存强制查询（测试用）。

    Returns:
        dict | None: {"sm_utilization_pct": float, "temperature_c": float}；
        查询失败（无 nvidia-smi / 超时 / 无 GPU）时返回 None。
    """
    now = time.time()
    if not force and _cache["data"] is not None and now - _cache["ts"] < _QUERY_TTL_SECONDS:
        return _cache["data"]
    if not force and _cache["data"] is None and now - _cache["fail_ts"] < _FAIL_COOLDOWN_SECONDS:
        return None  # 冷却期内跳过，避免反复无效拉起子进程

    data = _query_nvidia_smi()
    if data is not None:
        _cache["ts"] = now
        _cache["data"] = data
    else:
        _cache["fail_ts"] = now
        _cache["data"] = None
    return data


def reset_cache() -> None:
    """清空查询缓存（测试用）。"""
    _cache["ts"] = 0.0
    _cache["data"] = None
    _cache["fail_ts"] = 0.0


def _query_nvidia_smi() -> dict | None:
    """执行一次 nvidia-smi 查询。

    Returns:
        dict | None: 成功返回利用率与温度字典，失败返回 None。
    """
    try:
        result = subprocess.run(  # nosemgrep: subprocess-shell-True - 参数为固定字面量列表，无注入面
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
                "-i",
                "0",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug(f"nvidia-smi 查询不可用: {e}")
        return None

    if result.returncode != 0:
        logger.debug(f"nvidia-smi 查询失败: rc={result.returncode}")
        return None

    parts = [p.strip() for p in (result.stdout or "").strip().split(",")]
    if len(parts) < 2:
        return None
    try:
        return {
            "sm_utilization_pct": float(parts[0]),
            "temperature_c": float(parts[1]),
        }
    except ValueError:
        logger.debug(f"nvidia-smi 输出解析失败: {result.stdout!r}")
        return None
