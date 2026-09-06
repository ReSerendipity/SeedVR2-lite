#!/usr/bin/env python3
"""SeedVR2 输出水印验证 CLI（数据治理 P2-5；视频支持为评估报告 R2 增量）。

为本项目补齐「输出图像/视频是否携带可信归属水印」的独立验证入口
（对齐 MiniMax-H3-lite 的 scripts/verify_watermark.py 能力；
此前水印仅在嵌入侧与单元测试中覆盖，无运维/取证工具）。

验证逻辑复用 app/integrated_app/security/watermark.py：
- 配置 .watermark_key 时：严格 HMAC 签名验证（仅签发端水印通过）
- 未配置密钥时：退化为品牌字符串包含检测（仅参考）

用法:
    python scripts/verify_watermark.py <image> [--show-payload]
    python scripts/verify_watermark.py <video> --frames 8   # 均匀采样 N 帧验证

视频判定口径：任一采样帧携带可信水印即通过（部分帧可能因编码量化
丢失水印，逐帧统计供鲁棒性评估；--require-all 可要求全部通过）。

退出码:
    0 — 检测到可信水印
    1 — 未检测到可信水印
    2 — 文件读取/解析失败
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app.integrated_app.security.watermark import extract_watermark, verify_watermark  # noqa: E402

_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}


def _verify_video(path: Path, sample_frames: int, require_all: bool) -> int:
    """视频验证：均匀采样 N 帧逐一验证，统计携带可信水印的帧数。"""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        print(f"[错误] 视频无法打开: {path}")
        return 2
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        if total <= 0:
            print(f"[错误] 无法读取视频帧数: {path}")
            return 2
        step = max(1, total // max(1, sample_frames))
        passed = 0
        sampled = 0
        idx = 0
        while sampled < sample_frames and idx < total:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok:
                break
            sampled += 1
            if verify_watermark(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)):
                passed += 1
            idx += step
    finally:
        cap.release()

    if sampled == 0:
        print(f"[错误] 视频无可采样帧: {path}")
        return 2
    verdict = (passed == sampled) if require_all else (passed > 0)
    print(
        f"[{'通过' if verdict else '未通过'}] {path} "
        f"({passed}/{sampled} 采样帧携带可信水印{'，要求全部通过' if require_all else ''})"
    )
    return 0 if verdict else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="SeedVR2 输出水印验证工具（图像/视频）")
    parser.add_argument("path", help="待验证的图像或视频文件路径")
    parser.add_argument("--show-payload", action="store_true", help="打印提取到的水印载荷（仅图像）")
    parser.add_argument("--frames", type=int, default=8, help="视频采样帧数（默认 8）")
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="要求视频全部采样帧通过（默认任一帧通过即可）",
    )
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"[错误] 文件不存在: {path}")
        return 2

    if os.path.splitext(path)[1].lower() in _VIDEO_EXTS:
        return _verify_video(path, args.frames, args.require_all)

    try:
        with Image.open(path) as img:
            arr = np.asarray(img.convert("RGB"))
    except Exception as e:
        print(f"[错误] 图像读取/解析失败: {e}")
        return 2

    payload = extract_watermark(arr)
    if args.show_payload and payload:
        print(f"提取载荷: {payload[:200]}")

    if verify_watermark(arr):
        print(f"[通过] 检测到可信 SeedVR2 归属水印: {path}")
        return 0
    print(f"[未通过] 未检测到可信水印: {path}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
