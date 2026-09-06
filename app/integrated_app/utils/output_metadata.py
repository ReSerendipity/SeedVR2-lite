#!/usr/bin/env python3
"""输出文件生成参数元数据（数据治理 P2-5）。

修复产物脱离本系统后（被复制/转发/归档），历史库中的血缘随之失效。
本模块把生成参数写进输出文件自身的元数据，让文件自带「用什么参数生成」：

- PNG：tEXt 文本块 ``seedvr2_params``（无损，可与 eXIf 块共存）
- JPEG/WebP/TIFF：EXIF UserComment (0x9286)，并与源图 EXIF（拍摄信息）
  合并进同一次编码保存——避免二次有损压缩与 copy_exif 的相互覆盖

失败仅告警返回，绝不影响推理主流程。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

# PNG tEXt 块名 / EXIF UserComment tag（与 verify 工具约定）
METADATA_TAG = "seedvr2_params"
_EXIF_USER_COMMENT = 0x9286

# 元数据载荷上限（EXIF UserComment 过大部分工具解析异常；60KB 足够参数 JSON）
_MAX_PAYLOAD_BYTES = 60000


def generation_params_payload(params: dict) -> str:
    """把生成参数字典序列化为元数据 JSON 载荷。

    Args:
        params: 生成参数（推理配置字典，值可能含不可序列化对象）。

    Returns:
        JSON 字符串（超长截断）；空参数返回空串。
    """
    if not params:
        return ""
    try:
        payload = json.dumps(params, ensure_ascii=False, default=str, sort_keys=True)
    except (TypeError, ValueError) as e:
        logger.debug(f"生成参数序列化失败（跳过元数据嵌入）: {e}")
        return ""
    return payload[:_MAX_PAYLOAD_BYTES]


def _read_source_exif(source_path: str | None) -> bytes | None:
    """读取源图的 EXIF 原始字节（copy_exif 语义：源图无 EXIF 返回 None）。"""
    if not source_path or not os.path.exists(source_path):
        return None
    try:
        from PIL import Image

        with Image.open(source_path) as img:
            exif = img.info.get("exif")
        return exif if isinstance(exif, bytes) else None
    except Exception as e:  # noqa: BLE001 — 源图 EXIF 读取失败不阻断
        logger.debug(f"源图 EXIF 读取失败（跳过合并）: {source_path}: {e}")
        return None


def build_save_metadata_kwargs(
    ext: str,
    params: dict,
    source_path: str | None = None,
    copy_source_exif: bool = True,
) -> dict:
    """构建需并入 ``PIL.Image.save(**kwargs)`` 的元数据参数。

    生成参数始终尝试写入（PNG tEXt / EXIF UserComment）；源图 EXIF 在
    ``copy_source_exif`` 且源图携带 EXIF 时合并进同一次保存，调用方据
    返回的 ``exif_merged`` 标志跳过独立的 copy_exif 二次保存（避免覆盖
    与重复编码）。

    Args:
        ext: 目标扩展名（含点，如 ".png"/".jpg"）。
        params: 生成参数字典。
        source_path: 源图路径（无则不合并 EXIF）。
        copy_source_exif: 是否合并源图 EXIF（对应 postprocess.copy_exif 配置）。

    Returns:
        可直接 update 进 save_kwargs 的字典；可能含 pnginfo / exif / exif_merged。
    """
    result: dict = {}
    payload = generation_params_payload(params)
    source_exif = _read_source_exif(source_path) if copy_source_exif else None

    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".tiff"):
        return result  # BMP 等无元数据容量的格式跳过

    try:
        from PIL import Image
    except ImportError:
        return result

    # 生成参数 + 源图 EXIF 合并进同一个 EXIF 对象（PNG 写 eXIf 块，JPEG/WebP/TIFF 写标准 EXIF）
    exif_obj = Image.Exif()
    if source_exif:
        try:
            exif_obj.load(source_exif)
        except Exception as e:  # noqa: BLE001 — 源 EXIF 损坏时降级为仅生成参数
            logger.debug(f"源图 EXIF 解析失败（降级）: {e}")
            exif_obj = Image.Exif()
    if payload:
        exif_obj[_EXIF_USER_COMMENT] = payload

    if ext == ".png":
        try:
            from PIL.PngImagePlugin import PngInfo
        except ImportError:
            return result
        pnginfo = PngInfo()
        if payload:
            pnginfo.add_text(METADATA_TAG, payload)
        result["pnginfo"] = pnginfo
        if source_exif:
            result["exif"] = exif_obj
    else:
        if payload or source_exif:
            result["exif"] = exif_obj

    result["exif_merged"] = source_exif is not None
    return result
