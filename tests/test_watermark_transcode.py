#!/usr/bin/env python3
"""视频水印转码存活集成测试（后续建议 R1 量化基准的 CI 化）。

验证视频帧鲁棒档（alpha=0.05, repeat=3, 三通道等幅）经生产参数
（libx264 CRF18 yuv420p，见 video_processor.py compose_video）转码后，
verify_watermark 仍可检出可信签名水印。

历史背景：旧实现（单通道 alpha=0.5）转码后 0% 存活（色度下采样结构性
破坏），本测试锁定新方案的鲁棒性回归基线。ffmpeg 取 imageio-ffmpeg
自带二进制；不可用时跳过（CI 无 ffmpeg 的环境）。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.integrated_app.security.watermark import (
    _VIDEO_ALPHA,
    _VIDEO_REPEAT,
    embed_watermark,
    verify_watermark,
)

pytestmark = pytest.mark.integration

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def _make_frame(size: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = np.tile(np.linspace(30, 220, size).astype(np.uint8), (size, 1))
    img = np.stack([base] * 3, axis=-1)
    return np.clip(img.astype(np.int16) + rng.integers(-10, 10, img.shape), 0, 255).astype(np.uint8)


def test_video_scheme_survives_production_transcode():
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        pytest.skip("imageio-ffmpeg / PATH 中无 ffmpeg，跳过转码存活测试")

    size = 512  # 视频方案 R=3 需 ≥410px 容量（签名载荷 ~840 bit × 3）
    with tempfile.TemporaryDirectory() as td:
        frames_dir = os.path.join(td, "frames")
        os.makedirs(frames_dir)
        for i in range(4):
            wm = embed_watermark(
                _make_frame(size, i), payload="ci-transcode-task", alpha=_VIDEO_ALPHA, repeat=_VIDEO_REPEAT
            )
            cv2.imwrite(os.path.join(frames_dir, f"frame_{i:06d}.png"), cv2.cvtColor(wm, cv2.COLOR_RGB2BGR))

        out_mp4 = os.path.join(td, "out.mp4")
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
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                out_mp4,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[-400:]

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

    assert total > 0
    # 鲁棒档实测全帧存活；允许至多 1 帧采样损伤（避免单帧编解码边界的偶发抖动误报）
    assert passed >= total - 1, f"转码后水印存活率不足: {passed}/{total}"
