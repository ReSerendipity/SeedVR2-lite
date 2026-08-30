#!/usr/bin/env python3
"""权重 Sidecar 元数据 —— 数据治理 P2-2。

解决的问题：权重文件旁没有任何"出身"信息，无法回答
"这份 checkpoint 用什么数据 / 什么配置 / 从哪个父权重训出来的"。

约定：每个权重文件 ``xxx.pt`` / ``xxx.safetensors`` 旁可放置一个同名
``xxx.meta.json`` sidecar（内容寻址 + 可选父级引用），结构:

{
  "schema": "seedvr2-weight-sidecar/1",
  "weight_file": "checkpoint_step_500.pt",
  "sha256": "...",          # 权重文件内容摘要
  "size_bytes": 12345,
  "created_at": "2026-08-30T12:00:00",
  "training": {             # 训练侧来源（可选）
      "step": 500, "epoch": 1, "world_size": 8,
      "dataset_sha256": "...", "dataset_root": "...",
      "hyperparameters": {...}
  },
  "parent": {               # 父权重引用（可选，用于续训链）
      "weight_file": "checkpoint_step_250.pt", "sha256": "..."
  }
}

工具函数:
- build_sidecar: 计算权重哈希并组装元数据
- write_sidecar / read_sidecar: 原子落盘与读回
- sidecar_path_for: 由权重路径推导 sidecar 路径
- verify_sidecar_hash: 校验权重文件与 sidecar 记录的哈希是否一致（防漂移）
- describe_provenance: 生成一行可读的溯源摘要（日志/UI 用）

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

SIDECAR_SCHEMA = "seedvr2-weight-sidecar/1"
SIDECAR_SUFFIX = ".meta.json"


def sidecar_path_for(weight_path: str) -> str:
    """由权重文件路径推导 sidecar 路径（``x.pt`` → ``x.meta.json``）。

    Args:
        weight_path: 权重文件路径。

    Returns:
        sidecar JSON 路径。
    """
    return f"{os.path.splitext(weight_path)[0]}{SIDECAR_SUFFIX}"


def sha256_file(path: str, chunk_size: int = 8 * 1024 * 1024) -> str:
    """分块计算权重文件 SHA-256（GB 级文件安全）。"""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def build_sidecar(
    weight_path: str,
    *,
    training: dict[str, Any] | None = None,
    parent: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict:
    """计算权重哈希并组装 sidecar 元数据。

    Args:
        weight_path: 权重文件路径（必须已存在）。
        training: 训练来源信息（step/epoch/dataset_sha256/hyperparameters 等）。
        parent: 父权重引用（{"weight_file": ..., "sha256": ...}）。
        extra: 其他自定义字段。

    Returns:
        sidecar 元数据 dict。
    """
    return {
        "schema": SIDECAR_SCHEMA,
        "weight_file": os.path.basename(weight_path),
        "sha256": sha256_file(weight_path),
        "size_bytes": os.path.getsize(weight_path),
        "created_at": datetime.now().isoformat(),
        "training": training or {},
        "parent": parent or {},
        "extra": extra or {},
    }


def write_sidecar(metadata: dict, weight_path: str) -> str:
    """原子写入 sidecar（临时文件 + replace，避免读到半截 JSON）。

    Args:
        metadata: build_sidecar 的返回值。
        weight_path: 对应权重文件路径（sidecar 与其同目录同名前缀）。

    Returns:
        写入的 sidecar 路径。
    """
    target = sidecar_path_for(weight_path)
    directory = os.path.dirname(os.path.abspath(target)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, target)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    logger.info("权重 sidecar 已写入: %s (sha256=%s...)", target, str(metadata.get("sha256", ""))[:12])
    return target


def read_sidecar(weight_path: str) -> dict | None:
    """读取权重旁的 sidecar 元数据。

    Args:
        weight_path: 权重文件路径。

    Returns:
        sidecar dict；不存在或解析失败返回 None。
    """
    path = sidecar_path_for(weight_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("读取权重 sidecar 失败: %s (%s)", path, e)
        return None
    return data if isinstance(data, dict) else None


def verify_sidecar_hash(weight_path: str, metadata: dict | None = None) -> bool:
    """校验权重文件当前内容是否与 sidecar 记录的哈希一致。

    用途：检测权重被替换/续训覆盖后 sidecar 漂移（数据治理证据链完整性）。

    Args:
        weight_path: 权重文件路径。
        metadata: 已加载的 sidecar；None 时自动读取。

    Returns:
        一致返回 True；无 sidecar / 无哈希记录返回 False。
    """
    metadata = metadata if metadata is not None else read_sidecar(weight_path)
    if not metadata:
        return False
    expected = metadata.get("sha256")
    if not expected or not os.path.exists(weight_path):
        return False
    return sha256_file(weight_path) == expected


def describe_provenance(metadata: dict | None) -> str:
    """生成一行可读的溯源摘要（日志/排障用）。

    Args:
        metadata: sidecar 元数据。

    Returns:
        形如 "checkpoint_step_500.pt <- step=500 epoch=1 dataset=abc123… (parent: 250)" 的字符串。
    """
    if not metadata:
        return "无 sidecar 元数据（权重来源不可追溯）"
    training = metadata.get("training") or {}
    parent = metadata.get("parent") or {}
    digest = (training.get("dataset_sha256") or "")[:12] or "-"
    base = (
        f"{metadata.get('weight_file', '?')} <- "
        f"step={training.get('step', '-')} epoch={training.get('epoch', '-')} "
        f"dataset={digest}"
    )
    if parent.get("weight_file"):
        base += f" (parent: {parent['weight_file']})"
    return base


__all__ = [
    "SIDECAR_SCHEMA",
    "SIDECAR_SUFFIX",
    "sidecar_path_for",
    "sha256_file",
    "build_sidecar",
    "write_sidecar",
    "read_sidecar",
    "verify_sidecar_hash",
    "describe_provenance",
]
