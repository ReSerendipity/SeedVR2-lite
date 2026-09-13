#!/usr/bin/env python3
"""权重文件名别名解析（numz / Comfy-Org 双命名兼容）。

同一份 SeedVR2 权重在两套社区仓里有不同的文件名，**字节不同但模型结构一致**
（实测 3B 权重参数键 635/635 完全命中，仅多出 2 个无关 conditioning 键）：

- numz 版（HuggingFace ``numz/SeedVR2_comfyUI``）::

      seedvr2_ema_3b_fp16.safetensors
      seedvr2_ema_3b_fp8_e4m3fn.safetensors

- Comfy-Org 版（ModelScope ``Comfy-Org/SeedVR2``）::

      seedvr2_3b_fp16.safetensors
      seedvr2_3b_fp8_e4m3fn.safetensors

两者仅差 ``seedvr2_`` 之后是否带 ``ema_`` 段。``config.yaml`` 只登记其中一套，
用户若下载的是另一套，加载器 / 完整性校验会误判为「文件不存在」并抛
``FileNotFoundError``（现象：明明下了 fp8 却报「已尝试 fp16, fp8 均无对应文件」）。

本模块把「命名等价」这一约定收敛为**单一实现**，供模型加载、哈希白名单、
完整性校验与下载脚本共用，避免各处各写一份前缀判断而漂移。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import os
from collections.abc import Mapping

_EMA_PREFIX = "seedvr2_ema_"
_PLAIN_PREFIX = "seedvr2_"

# VAE 权重在 Comfy-Org 仓存在同内容的重复命名（见 docs/LICENSE_COMPLIANCE.md §3.2），
# 与本项目 config.yaml 引用的名字互为别名。它们不带 ``seedvr2_`` 前缀，故单独登记。
_EXTRA_ALIASES: dict[str, str] = {
    "ema_vae_fp16.safetensors": "seedvr2_ema_vae_fp16.safetensors",
}


def weight_filename_aliases(filename: str) -> list[str]:
    """返回同一权重在两套命名约定下的等价文件名（原文件名优先级最高）。

    Args:
        filename: 配置中登记的权重文件名（可含目录前缀，取 basename 参与推导）。

    Returns:
        list[str]: 去重保序的候选文件名列表；首项恒为传入名的 basename，
            其后是另一套命名约定的等价名。无等价名时仅返回原文件名。
            传入空串返回空列表。
    """
    if not filename:
        return []
    base = os.path.basename(filename)
    aliases = [base]
    if base.startswith(_EMA_PREFIX):
        aliases.append(_PLAIN_PREFIX + base[len(_EMA_PREFIX) :])
    elif base.startswith(_PLAIN_PREFIX):
        aliases.append(_EMA_PREFIX + base[len(_PLAIN_PREFIX) :])
    else:
        extra = _EXTRA_ALIASES.get(base)
        if extra:
            aliases.append(extra)

    seen: set[str] = set()
    out: list[str] = []
    for alias in aliases:
        if alias not in seen:
            seen.add(alias)
            out.append(alias)
    return out


def is_comfy_org_name(filename: str) -> bool:
    """判断文件名是否属于 Comfy-Org 转包/量化命名（``seedvr2_`` 前缀且无 ``ema_``）。

    Args:
        filename: 权重文件名（可含目录前缀）。

    Returns:
        bool: 属于 Comfy-Org 命名返回 True。
    """
    base = os.path.basename(filename or "")
    return base.startswith(_PLAIN_PREFIX) and not base.startswith(_EMA_PREFIX)


def find_weight_file(pretrained_dir: str | os.PathLike[str], filename: str) -> str | None:
    """按别名优先级返回第一个真实存在的权重文件路径。

    用 ``os.path.exists`` 判断（而非 ``Path.exists``），以保持与既有单测
    ``@patch("os.path.exists")`` 的兼容。

    Args:
        pretrained_dir: 权重根目录。
        filename: 配置登记的权重文件名。

    Returns:
        str | None: 首个存在的文件路径（``pretrained_dir`` 与文件名拼接）；均不存在返回 None。
    """
    root = os.fspath(pretrained_dir) if pretrained_dir is not None else ""
    for alias in weight_filename_aliases(filename):
        path = os.path.join(root, alias)
        if os.path.exists(path):
            return path
    return None


def weight_hash_candidates(model_cfg: Mapping[str, object] | None, suffix: str) -> list[str]:
    """返回某个权重可接受的期望 SHA256 列表（主哈希 + ``sha256_<suffix>_alt``）。

    同一权重两套命名的字节不同 → 哈希不同。config.yaml 主键登记一套，
    可选 ``_alt`` 键登记另一套；两者任一命中即视为校验通过。

    Args:
        model_cfg: 单个模型的配置字典（config.yaml ``model.models.<size>``）。
        suffix: 哈希键后缀，如 ``"fp16"`` / ``"fp8"`` / ``"vae"`` / ``"pos_emb"`` / ``"neg_emb"``。

    Returns:
        list[str]: 去重后的小写期望哈希列表；未配置任何哈希时返回空列表。
    """
    cfg = model_cfg or {}
    out: list[str] = []
    for key in (f"sha256_{suffix}", f"sha256_{suffix}_alt"):
        val = cfg.get(key)
        if isinstance(val, str) and val.strip():
            digest = val.strip().lower()
            if digest not in out:
                out.append(digest)
    return out
