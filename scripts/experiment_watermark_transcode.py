#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""水印转码鲁棒性诊断实验（评估报告 R2 证据采集，非 CI 门禁）。

测量 DCT 水印（security/watermark.py，alpha 默认 0.01，QIM 中频嵌入）
经 H.264 有损转码后的可验证存活率。合成帧 → 逐帧嵌水印 → PNG 序列 →
ffmpeg libx264 转码 → 逐帧 verify_watermark 统计。

基线结果（2026-09-06，本仓库开发机，16 帧 256x256 合成帧）:
    CRF 23 yuv420p:  0/16 帧存活
    CRF 14 yuv420p:  0/16 帧存活
结论：当前隐式水印经视频有损编码后不可验证，属已知限制。
RGB→YUV 4:2:0 色度抽样 + DCT 量化为主要破坏因素；增强算法
（提高强度 / 亮度分量嵌入 / 后验证重嵌）为后续算法层工作。

用途：
- 水印算法调整后复跑本脚本量化鲁棒性变化
- 复现评估报告 R2 的实验证据

用法:
    python scripts/experiment_watermark_transcode.py [--frames 16] [--size 256]
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

from app.integrated_app.security.watermark import embed_watermark, verify_watermark  # noqa: E402


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


def _run(frames: int, size: int, crf: int, workdir: str, ffmpeg: str) -> tuple[int, int]:
    frames_dir = os.path.join(workdir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    for i in range(frames):
        img = embed_watermark(_make_frame(size, i), payload="task-transcode-experiment")
        cv2.imwrite(os.path.join(frames_dir, f"frame_{i:06d}.png"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))

    out_mp4 = os.path.join(workdir, f"out_crf{crf}.mp4")
    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-framerate",
            "8",
            "-i",
            os.path.join(frames_dir, "frame_%06d.png"),
            "-c:v",
            "libx264",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            out_mp4,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(f"ffmpeg 转码失败 (crf={crf}): {proc.stderr[-400:]}", file=sys.stderr)
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
    parser = argparse.ArgumentParser(description="水印转码鲁棒性诊断实验")
    parser.add_argument("--frames", type=int, default=16, help="合成帧数（默认 16）")
    parser.add_argument("--size", type=int, default=256, help="帧边长（默认 256，需偶数）")
    parser.add_argument("--crfs", type=int, nargs="+", default=[23, 14], help="CRF 档位列表")
    args = parser.parse_args()

    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        print("未找到 ffmpeg（imageio-ffmpeg 与 PATH 均无）", file=sys.stderr)
        return 2

    overall_ok = True
    with tempfile.TemporaryDirectory() as td:
        for crf in args.crfs:
            passed, total = _run(args.frames, args.size, crf, td, ffmpeg)
            if total < 0:
                overall_ok = False
                continue
            print(f"CRF {crf}: {passed}/{total} 帧转码后仍可验证水印")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
