#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""输出文件命名：产物名一律沿用输入/上传文件名（只换扩展名）。

用户按文件名辨认内容，任何改名（时间戳、模型档位、随机后缀、标识后缀）都会让产物
无法检索。同时这里也是**唯一被信任的清洗点**：客户端传来的 `multipart` 文件名与
磁盘上的输入路径都从这里出一个安全的名——直接 join 原始名会引入 `../` 穿越。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import os
import time

# Windows/Linux 文件名非法字符与控制字符（NUL 等留在名字里会写出无法访问的文件）；
# 上传原名的字符集完全由客户端决定，必须整体剥掉而非替换成下划线
_ILLEGAL_CHARS = '\\/:*?"<>|' + "".join(chr(o) for o in range(32)) + "\x7f"
_ILLEGAL_IN_FILENAME = str.maketrans(dict.fromkeys(_ILLEGAL_CHARS))

# 与上传缓存层的截断保持一致（cache.generate_unique_filename 同为 48）
_MAX_STEM_LEN = 48


def sanitize_stem(raw: str) -> str:
    """把任意输入名（含 Windows 风格路径、穿越序列、控制字符）收成安全的文件名词干。

    Returns:
        str: 不含分隔符、不含 `..`、非空的词干；无法提取时返回空串，由调用方兜底。
    """
    # 先归一反斜杠：Linux 容器收到 `C:\a\b.png` 时 basename 不会切分 '\'
    base = os.path.basename((raw or "").replace("\\", "/"))
    stem = os.path.splitext(base)[0].translate(_ILLEGAL_IN_FILENAME)
    while ".." in stem:
        stem = stem.replace("..", "_")
    return stem.strip().rstrip(". ")[:_MAX_STEM_LEN].rstrip(". ")


def build_output_name(source: str | None, ext: str) -> str:
    """由输入媒体路径或上传原始文件名构造输出文件名（只换扩展名）。

    Args:
        source: 输入媒体路径，或 `multipart` 上传的原始文件名。
        ext: 目标扩展名（含点号），如 ".png"、".mp4"。

    Returns:
        str: 形如 `photo_4k.png`；拿不到可用词干时退回时间戳名，
        绝不产出 `.png` 这类无名文件。

        取证/合规部署如需在名字里带 AI 标识，用 `SEEDVR2_EXPLICIT_AI_LABEL=1`
        显式开启（默认关闭：它会改变产物名）。
    """
    stem = sanitize_stem(source or "")
    if not stem:
        stem = time.strftime("%Y%m%d_%H%M%S")
    label = "_AI" if os.environ.get("SEEDVR2_EXPLICIT_AI_LABEL", "0") == "1" else ""
    return f"{stem}{label}{ext}"
