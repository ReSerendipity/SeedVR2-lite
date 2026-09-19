#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""水印嵌入失败处置策略（评估报告 R2：从 fail-open 到策略化兜底）。

历史行为：水印嵌入异常仅 debug 日志后照常输出无水印文件（fail-open），
静默缺失来源标识无法被发现，构成《AI 生成合成内容标识办法》合规缺口。
本模块把失败处置策略化（runtime.security.watermark_on_failure）：

- ``mark_metadata``（默认）：重试 1 次仍失败 → error 日志 + 审计事件，
  并由调用方在产物落盘后写 ``<输出名>.provenance.json`` 侧车
  （显式元数据标识，可被审计发现）
- ``block``：直接抛出 :class:`WatermarkEmbedError`，产出不落盘（严格合规档）
- ``ignore``：仅 debug 日志，保持历史行为

两段失败都走同一策略口径：

- **嵌入失败**：:func:`embed_with_retry` → :func:`handle_watermark_failure`
- **落盘后水印丢失**：:func:`output_carries_watermark` 重读产物验签 →
  :func:`handle_watermark_loss`（``block`` 档此时会删除已落盘产物）。
  嵌入成功 ≠ 产物带水印——2026-09-19 实测无损档水印经 JPEG q95 即不可验证，
  故图像输出按格式选档（:func:`select_image_embed_tier`）并一律落盘复验。

设计约束：
- 本模块不持有任何 GPU/torch 依赖；embed 经延迟导入，便于无 GPU 环境单测。
- 视频逐帧调用时由调用方聚合失败并只报告一次，避免审计日志刷屏。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

import numpy as np

from app.integrated_app.security.audit import audit_event

logger = logging.getLogger(__name__)

# 处置策略取值（与 RuntimeSecurityConfig.watermark_on_failure 校验一致）
WATERMARK_FAILURE_POLICIES = ("mark_metadata", "block", "ignore")
DEFAULT_WATERMARK_FAILURE_POLICY = "mark_metadata"

# 侧车文件后缀（<输出名>.provenance.json）
_PROVENANCE_SIDECAR_SUFFIX = ".provenance.json"

# 有损图像输出格式：这些编码器会抹掉无损档水印（2026-09-19 实测：图像档
# alpha=0.5 经 JPEG q95 即不可验证），因此嵌入前必须按格式换鲁棒档。
LOSSY_IMAGE_EXTS = (".jpg", ".jpeg", ".webp")


class WatermarkEmbedError(RuntimeError):
    """block 策略下水印嵌入失败（产出被阻断）。"""


def resolve_watermark_failure_policy(config: dict) -> str:
    """从应用配置解析水印失败处置策略。

    Args:
        config: 应用配置字典（get_config / engine self.config）。

    Returns:
        str: 合法策略名；未配置或配置非法时回退默认值（非法值记 warning）。
    """
    raw = config.get("runtime", {}).get("security", {}).get("watermark_on_failure")
    if raw is None:
        return DEFAULT_WATERMARK_FAILURE_POLICY
    if raw not in WATERMARK_FAILURE_POLICIES:
        logger.warning(
            f"watermark_on_failure 非法值 {raw!r}（合法：{WATERMARK_FAILURE_POLICIES}），"
            f"回退默认 {DEFAULT_WATERMARK_FAILURE_POLICY!r}"
        )
        return DEFAULT_WATERMARK_FAILURE_POLICY
    return raw


def embed_with_retry(
    image_np: np.ndarray,
    *,
    payload: str | None = None,
    alpha: float | None = None,
    repeat: int = 1,
) -> tuple[np.ndarray, bool, str | None]:
    """嵌入水印，失败自动重试 1 次。纯函数，无副作用。

    Args:
        image_np: 输入图像 (H x W x C, uint8)。
        payload: 水印载荷（约定绑定 task_id，见 P3-1）。
        alpha: 嵌入强度；None 用模块默认（图像路径 0.5）。视频帧传 0.05。
        repeat: 重复码次数；视频帧传 3（有损编码鲁棒档）。

    Returns:
        (image_np, embedded, error) 三元组：
        - embedded=True 时 error 为 None，image 为含水印结果；
        - embedded=False 时 image 为原图回传，error 为末次异常描述。
    """
    from app.integrated_app.security import watermark as wm

    kwargs: dict = {"payload": payload}
    if alpha is not None:
        kwargs["alpha"] = alpha
    if repeat != 1:
        kwargs["repeat"] = repeat
    try:
        return wm.embed_watermark(image_np, **kwargs), True, None
    except Exception as first_err:  # noqa: BLE001 — 失败处置交由策略层
        try:
            return wm.embed_watermark(image_np, **kwargs), True, None
        except Exception as second_err:  # noqa: BLE001
            return image_np, False, f"{type(second_err).__name__}: {second_err} (首次: {first_err})"


_key_missing_audited = False


def report_missing_watermark_key() -> bool:
    """生产路径的密钥可用性上报；返回密钥是否可用。

    没有密钥时水印只能嵌入不可证伪的未签名载荷（任何人都能伪造出「看起来带水印」
    的产物），归属举证随之失效——属安全降级，必须进审计通道而非只留业务日志。
    审计只记一次，避免批量/逐帧任务刷屏取证日志。
    """
    global _key_missing_audited

    from app.integrated_app.security import watermark as wm

    if wm.watermark_key_available():
        return True
    if not _key_missing_audited:
        _key_missing_audited = True
        logger.error("[SECURITY] 水印签名密钥缺失，产物水印降级为不可证伪的弱水印")
        audit_event("WATERMARK_KEY_MISSING", detail="水印签名密钥缺失，产物降级为未签名弱水印（不可证伪）")
    return False


def select_image_embed_tier(ext: str) -> tuple[float, int]:
    """按图像输出格式选择水印档位（自适应：无损保画质，有损保溯源）。

    Args:
        ext: 目标扩展名（含点，如 ".png"/".jpg"）。

    Returns:
        (alpha, repeat)：有损格式返回鲁棒档，其余返回无损档。
    """
    from app.integrated_app.security.watermark import _VIDEO_ALPHA, _VIDEO_REPEAT, _WATERMARK_ALPHA

    if ext.lower() in LOSSY_IMAGE_EXTS:
        return _VIDEO_ALPHA, _VIDEO_REPEAT
    return _WATERMARK_ALPHA, 1


def output_carries_watermark(path: str) -> bool:
    """重读落盘产物验签：水印是否真的活过了编码器，只有这么测才算数。

    读取/解码失败一律按「未携带」处理——产物不可证时宁可判失败，
    让调用方走策略处置，而不是继续假装水印在。
    """
    from app.integrated_app.security.watermark import verify_watermark

    try:
        import numpy as np
        from PIL import Image

        with Image.open(path) as img:
            arr = np.asarray(img.convert("RGB"))
        return bool(verify_watermark(arr))
    except Exception as e:  # noqa: BLE001 — 验签不应打断产出，失败交由策略处置
        logger.debug(f"落盘水印验签读取失败: {path}: {e}")
        return False


def handle_watermark_loss(*, policy: str, output_path: str, error: str, payload: str | None = None) -> None:
    """产物已落盘、但水印经编码/后处理抹掉时的策略化处置（落盘验签失败路径）。

    与 :func:`handle_watermark_failure`（嵌入环节就失败）的区别：这里文件已在盘上，
    ``block`` 档必须把产物删掉才算兑现「阻断」，否则严格合规模式形同虚设。
    """
    if policy == "block":
        audit_event("WATERMARK_LOSS_BLOCKED", detail=error, payload=payload)
        try:
            os.remove(output_path)
            logger.error(f"[SECURITY] 产物水印不可验证且 watermark_on_failure=block，已删除产出: {output_path}")
        except OSError as e:  # 删不掉更要报，避免留下"看着成功其实无溯源"的产物
            logger.error(f"[SECURITY] 产物水印不可验证，但删除产出失败: {e}")
        raise WatermarkEmbedError(f"产物水印落盘后不可验证，产出已阻断: {error}")
    if policy == "ignore":
        logger.debug(f"产物水印落盘后不可验证（ignore 策略，照常输出）: {error}")
        return
    logger.error(f"[SECURITY] 产物水印落盘后不可验证，已按 mark_metadata 策略降级为元数据标识: {error}")
    audit_event("WATERMARK_LOSS_DEGRADED", detail=error, payload=payload)
    try:
        sidecar_path = write_provenance_sidecar(output_path, payload=payload, reason=error)
        logger.warning(f"产物水印不可验证，已写溯源侧车: {sidecar_path}")
    except OSError as e:
        logger.error(f"[SECURITY] 水印缺失且溯源侧车写入失败: {e}")
        audit_event("WATERMARK_SIDECAR_FAILED", detail=str(e), payload=payload)


def handle_watermark_failure(*, policy: str, error: str, payload: str | None = None) -> None:
    """水印嵌入失败的策略化处置副作用。

    Args:
        policy: :data:`WATERMARK_FAILURE_POLICIES` 之一。
        error: embed_with_retry 返回的失败描述。
        payload: 水印载荷（审计溯源用，不含密钥）。

    Raises:
        WatermarkEmbedError: policy 为 ``block`` 时。
    """
    if policy == "block":
        audit_event("WATERMARK_EMBED_BLOCKED", detail=error, payload=payload)
        raise WatermarkEmbedError(f"水印嵌入失败且 watermark_on_failure=block，产出已阻断: {error}")
    if policy == "ignore":
        logger.debug(f"水印嵌入失败（ignore 策略，照常输出无水印文件）: {error}")
        return
    # mark_metadata（默认）：显式降级，绝不静默
    logger.error(f"[SECURITY] 水印嵌入失败，已按 mark_metadata 策略降级为元数据标识: {error}")
    audit_event("WATERMARK_EMBED_DEGRADED", detail=error, payload=payload)


def write_provenance_sidecar(output_path: str, *, payload: str | None = None, reason: str | None = None) -> str:
    """产物旁写 ``<输出名>.provenance.json`` 侧车（水印缺失时的显式元数据标识）。

    Args:
        output_path: 已落盘的产物路径（图像或视频）。
        payload: 水印载荷（即 task_id，可反查产生任务）。
        reason: 缺失原因；None 时用「嵌入失败」默认描述。

    Returns:
        str: 侧车文件路径。

    Raises:
        OSError: 写盘失败由调用方决定是否记审计（不静默吞）。
    """
    sidecar_path = os.path.splitext(output_path)[0] + _PROVENANCE_SIDECAR_SUFFIX
    body: dict[str, Any] = {
        "tool": "SeedVR2",
        "watermark_embedded": False,
        "reason": reason or "watermark embedding failed; provenance marked via sidecar metadata",
        "payload": payload,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=2)
    return sidecar_path
