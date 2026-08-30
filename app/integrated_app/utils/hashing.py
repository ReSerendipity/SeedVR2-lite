#!/usr/bin/env python3
"""内容寻址哈希工具（数据治理 P1-1 源文件血缘）。

提供文件/字节内容的 SHA-256 摘要计算，供历史记录 input_sha256 列、
批量任务落账等血缘场景使用。分块读取，支持 GB 级大文件。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import hashlib
import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024  # 与 security/integrity_check.compute_sha256 对齐的分块大小


def compute_bytes_sha256(data: bytes) -> str:
    """计算字节内容的 SHA-256 摘要（hex 小写）。

    Args:
        data: 原始字节内容。

    Returns:
        64 位 hex 字符串。
    """
    return hashlib.sha256(data).hexdigest()


def compute_file_sha256(path: str, chunk_size: int = _DEFAULT_CHUNK_SIZE) -> str:
    """计算磁盘文件内容的 SHA-256 摘要（hex 小写），分块读取。

    Args:
        path: 文件路径。
        chunk_size: 读取分块大小（字节），默认 8MB。

    Returns:
        64 位 hex 字符串；文件不存在或不可读时返回空串（调用方兜底，
        血缘缺失不应阻断推理主流程）。
    """
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(chunk_size):
                digest.update(chunk)
    except OSError as e:
        # 血缘计算失败不阻断业务：返回空串，历史记录该列留空
        logger.warning(f"计算文件 SHA-256 失败（血缘留空）: {path}: {e}")
        return ""
    return digest.hexdigest()


def file_exists_and_readable(path: str) -> bool:
    """判断文件是否存在且可读（哈希计算前的快速预检）。"""
    return os.path.isfile(path) and os.access(path, os.R_OK)


__all__ = ["compute_bytes_sha256", "compute_file_sha256", "file_exists_and_readable"]
