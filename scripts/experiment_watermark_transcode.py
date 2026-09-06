#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""水印转码鲁棒性诊断实验（评估报告 R2 + 后续建议 R1 量化基准，非 CI 门禁）。

测量 DCT 水印两种嵌入方案经 H.264 有损转码后的可验证存活率：
- 图像方案 (alpha=0.5, repeat=1)：PNG 无损保存路径
- 视频方案 (alpha=0.05, repeat=3)：视频帧路径（三通道等幅 + 重复码，2026-09-06 起）

实验结论（2026-09-06，本仓库开发机）：
    旧实现（单通道 alpha=0.5）：CRF14/23 均 0/16 帧存活——且与嵌入强度无关
    （step 33 仍 0%）。根因：单通道扰动经 RGB→YUV420 仅 0.299 进入全分辨率
    亮度通道，中频分量被色度下采样结构性破坏。
    新实现（三通道等幅 + 步长 20 + 重复码 3）：CRF14/18/23 全帧存活，BER≈0。
    代价：视频路径 PSNR ≈ 37.5dB（视觉透明档）。

用途：
- 水印算法调整后复跑本脚本量化鲁棒性变化
- 复现评估报告 R2 / 后续建议 R1 的实验证据

用法:
    python scripts/experiment_watermark_transcode.py [--frames 8] [--size 512]
    # ffmpeg 取 imageio-ffmpeg 自带二进制，或回退 PATH 中的 ffmpeg
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.integrated_app.security.watermark import (  # noqa: E402
    _VIDEO_ALPHA,
    _VIDEO_REPEAT,
    _WATERMARK_ALPHA,
    embed_watermark,
    verify_watermark,
)

_SCHEMES = {
    "image(0.5,R1)": (_WATERMARK_ALPHA, 1),
    "video(0.05,R3)": (_VIDEO_ALPHA, _VIDEO_REPEAT),
}


def _find_ffmpeg() -> str | None:
    """优先 imageio-ffmpeg 自带二进制，回退 PATH。"""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def _make_frame(size: int, seed: int) -> np.ndarray:
    """合成有内容结构的测试帧（渐变 + 噪声，避免纯色块的特殊性）。"""
    rng = np.random.default_rng(seed)
    base = np.tile(np.linspace(30, 220, size).astype(np.uint8), (size, 1))
    img = np.stack([base] * 3, axis=-1)
    noise = rng.integers(-10, 10, img.shape).astype(np.int16)
    return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def _safe_name(scheme: str) -> str:
    return scheme.replace("(", "_").replace(")", "_").replace(",", "_").replace(".", "_")


def _run(scheme: str, frames: int, size: int, crf: int, workdir: str, ffmpeg: str) -> tuple[int, int]:
    alpha, repeat = _SCHEMES[scheme]
    frames_dir = os.path.join(workdir, f"frames_{_safe_name(scheme)}")
    os.makedirs(frames_dir, exist_ok=True)
    for i in range(frames):
        img = embed_watermark(_make_frame(size, i), payload="task-transcode-experiment", alpha=alpha, repeat=repeat)
        cv2.imwrite(os.path.join(frames_dir, f"frame_{i:06d}.png"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))

    out_mp4 = os.path.join(workdir, f"out_{_safe_name(scheme)}_crf{crf}.mp4")
    proc = subprocess.run(
        [
            ffmpeg, "-y", "-framerate", "8",
            "-i", os.path.join(frames_dir, "frame_%06d.png"),
            "-c:v", "libx264", "-crf", str(crf), "-pix_fmt", "yuv420p", out_mp4,
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"ffmpeg 转码失败 (scheme={scheme}, crf={crf}): {proc.stderr[-400:]}", file=sys.stderr)
        return -1, -1

    cap = cv2.VideoCapture(out_mp4)
    passed = total = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        total += 1
        if verify_watermark(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)):
            passed += 1
    cap.release()
    return passed, total


def main() -> int:
    parser = argparse.ArgumentParser(description="水印转码鲁棒性诊断实验（图像/视频双方案）")
    parser.add_argument("--frames", type=int, default=8, help="合成帧数（默认 8）")
    parser.add_argument(
        "--size", type=int, default=512, help="帧边长（默认 512，需偶数；视频方案 R=3 需 ≥410px 容量）"
    )
    parser.add_argument(
        "--crfs", type=int, nargs="+", default=[18, 23], help="CRF 档位列表（18 为生产 compose_video 取值）"
    )
    args = parser.parse_args()

    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        print("未找到 ffmpeg（imageio-ffmpeg 与 PATH 均无）", file=sys.stderr)
        return 2

    overall_ok = True
    with tempfile.TemporaryDirectory() as td:
        for scheme in _SCHEMES:
            for crf in args.crfs:
                passed, total = _run(scheme, args.frames, args.size, crf, td, ffmpeg)
                if total < 0:
                    overall_ok = False
                    continue
                print(f"{scheme} CRF {crf}: {passed}/{total} 帧转码后仍可验证水印")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
