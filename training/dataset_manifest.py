#!/usr/bin/env python3
"""训练数据集清单（Dataset Manifest）—— 数据治理 P2-1。

解决的问题：训练产出权重无法回答"用了哪批数据"。本模块为训练数据目录
生成内容寻址清单，并把清单摘要写入实验追踪（ExperimentTracker），
从而建立「训练数据 → 模型权重」的可追溯链。

清单结构（schema: seedvr2-dataset-manifest/1）:
{
  "schema": str,
  "root": 绝对根目录,
  "created_at": ISO 时间,
  "total_files": int,
  "total_bytes": int,
  "dataset_sha256": 数据集整体摘要（按 path 排序后逐文件 sha256 再哈希）,
  "files": [{"path": 相对路径, "size": 字节, "sha256": hex}, ...]
}

设计要点:
- 摘要对**内容**寻址：文件内容不变则 dataset_sha256 不变，与目录位置无关；
- 文件顺序按相对路径排序，保证跨机器/跨平台结果一致；
- 支持 manifest 落盘（write_manifest）与摘要比对（digest_of）；
- 不依赖 torch，纯标准库，可在任意环境运行与测试。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

MANIFEST_SCHEMA = "seedvr2-dataset-manifest/1"

# 大文件清单默认上限：超出后只记录前 N 个文件并在清单中标注 truncated
DEFAULT_MAX_FILES = 200_000


def digest_of(manifest: dict) -> str:
    """返回清单的数据集摘要（优先读 dataset_sha256，缺失时按 files 重算）。

    Args:
        manifest: build_manifest / load_manifest 返回的清单 dict。

    Returns:
        数据集摘要 hex 字符串；空清单返回 sha256("")。
    """
    digest = manifest.get("dataset_sha256")
    if digest:
        return str(digest)
    return _dataset_digest(manifest.get("files") or [])


def _dataset_digest(files: list[dict]) -> str:
    """按相对路径排序后把所有文件 sha256 串联再哈希，得到数据集整体摘要。"""
    ordered = sorted(files or [], key=lambda item: item.get("path", ""))
    hasher = hashlib.sha256()
    for item in ordered:
        hasher.update(item.get("path", "").encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(item.get("sha256", "").encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def build_manifest(
    root: str,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    exclude_dirs: tuple[str, ...] = (".git", "__pycache__"),
) -> dict:
    """为训练数据目录生成内容寻址清单。

    Args:
        root: 训练数据根目录（递归扫描）。
        max_files: 最多记录的文件数，超出则截断并标注 truncated=True。
        exclude_dirs: 扫描时跳过的目录名。

    Returns:
        清单 dict；目录不存在时返回空清单（files=[]，dataset_sha256 为 sha256("")）。
    """
    abs_root = os.path.abspath(root)
    files: list[dict] = []
    truncated = False

    if not os.path.isdir(abs_root):
        logger.warning("数据集目录不存在，返回空清单: %s", abs_root)
        return _empty_manifest(abs_root)

    for dirpath, dirnames, filenames in os.walk(abs_root):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for filename in filenames:
            if len(files) >= max_files:
                truncated = True
                break
            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, abs_root).replace(os.sep, "/")
            try:
                size = os.path.getsize(full_path)
                file_hash = _sha256_file(full_path)
            except OSError as e:
                logger.warning("跳过无法读取的文件: %s (%s)", full_path, e)
                continue
            files.append({"path": rel_path, "size": size, "sha256": file_hash})
        if truncated:
            break

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "root": abs_root,
        "created_at": datetime.now().isoformat(),
        "total_files": len(files),
        "total_bytes": sum(item["size"] for item in files),
        "truncated": truncated,
        "files": files,
    }
    manifest["dataset_sha256"] = _dataset_digest(files)
    return manifest


def _empty_manifest(abs_root: str) -> dict:
    """构造空清单（目录不存在/无文件场景）。"""
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "root": abs_root,
        "created_at": datetime.now().isoformat(),
        "total_files": 0,
        "total_bytes": 0,
        "truncated": False,
        "files": [],
    }
    manifest["dataset_sha256"] = _dataset_digest([])
    return manifest


def _sha256_file(path: str, chunk_size: int = 8 * 1024 * 1024) -> str:
    """分块计算文件 SHA-256。"""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_manifest(manifest: dict, output_path: str) -> str:
    """把清单写入 JSON 文件（父目录自动创建）。

    Args:
        manifest: 清单 dict。
        output_path: 输出 JSON 路径。

    Returns:
        实际写入的路径。
    """
    parent = os.path.dirname(os.path.abspath(output_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    logger.info(
        "数据集清单已写入: %s (files=%d, digest=%s...)",
        output_path,
        manifest.get("total_files", 0),
        digest_of(manifest)[:12],
    )
    return os.path.abspath(output_path)


def load_manifest(path: str) -> dict:
    """从 JSON 文件加载清单。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


__all__ = [
    "MANIFEST_SCHEMA",
    "build_manifest",
    "write_manifest",
    "load_manifest",
    "digest_of",
]
