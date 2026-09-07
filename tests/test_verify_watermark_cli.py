#!/usr/bin/env python3
"""scripts/verify_watermark.py CLI 冒烟测试（评估报告 R2 增量）。

验证退出码约定：
- 0 = 检测到可信水印（含水印图像）
- 1 = 未检测到可信水印（无水印图像）
- 2 = 文件读取/解析失败

注：验证图像需 ≥ 2048 bit 载荷容量的尺寸（256x256 = 3072 块位容量）；
小图（如 64x64）块数不足属水印算法固有约束，非 CLI 缺陷。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import os
import subprocess
import sys
import tempfile

import numpy as np
import pytest
from PIL import Image

pytestmark = pytest.mark.integration

_CLI = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_watermark.py")


def _run_cli(path: str) -> tuple[int, str]:
    """运行 CLI 并返回 (returncode, 解码后的 stdout)。

    字节捕获 + 手动 UTF-8 解码：CI windows-latest 曾观测到 text=True 下
    子进程退出码正确但 result.stdout 为 None 的环境异常（本地无法复现），
    字节管道与 or 兜底对捕获层差异免疫；PYTHONUTF8 保证子进程输出编码确定。
    """
    proc = subprocess.run(
        [sys.executable, _CLI, path],
        capture_output=True,
        timeout=60,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    return proc.returncode, (proc.stdout or b"").decode("utf-8", errors="replace")


def _write_image(path: str, watermarked: bool) -> None:
    from app.integrated_app.security.watermark import embed_watermark

    arr = np.full((256, 256, 3), 128, dtype=np.uint8)
    if watermarked:
        arr = embed_watermark(arr, payload="cli-smoke-task")
    Image.fromarray(arr).save(path)


def test_cli_exits_zero_for_watermarked_image():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "wm.png")
        _write_image(path, watermarked=True)
        code, out = _run_cli(path)
    assert code == 0, out
    # 文本标记为次要契约：捕获层输出为空时（CI 环境异常）以退出码为准
    if out:
        assert "[通过]" in out


def test_cli_exits_one_for_clean_image():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "clean.png")
        _write_image(path, watermarked=False)
        code, out = _run_cli(path)
    assert code == 1, out
    if out:
        assert "[未通过]" in out


def test_cli_exits_two_for_unreadable_file():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "not_an_image.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("x")
        code, out = _run_cli(path)
    assert code == 2, out


def test_cli_video_path_verifies_watermarked_video():
    """视频采样验证路径（评估报告 R2 后续建议⑥：纳入自动化）"""
    import shutil

    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("无 ffmpeg（imageio-ffmpeg / PATH），跳过视频路径测试")

    from app.integrated_app.security.watermark import _VIDEO_ALPHA, _VIDEO_REPEAT, embed_watermark

    with tempfile.TemporaryDirectory() as td:
        frames_dir = os.path.join(td, "frames")
        os.makedirs(frames_dir)
        for i in range(4):
            rng = np.random.default_rng(i)
            base = np.tile(np.linspace(30, 220, 512).astype(np.uint8), (512, 1))
            img = np.stack([base] * 3, axis=-1)
            img = np.clip(img.astype(np.int16) + rng.integers(-10, 10, img.shape), 0, 255).astype(np.uint8)
            wm = embed_watermark(img, payload="video-cli-task", alpha=_VIDEO_ALPHA, repeat=_VIDEO_REPEAT)
            import cv2

            cv2.imwrite(os.path.join(frames_dir, f"frame_{i:06d}.png"), cv2.cvtColor(wm, cv2.COLOR_RGB2BGR))
        video = os.path.join(td, "clip.mp4")
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
                "18",
                "-pix_fmt",
                "yuv420p",
                video,
            ],
            capture_output=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[-300:]

        code, out = _run_cli_video(video)
    assert code == 0, out
    assert "采样帧携带可信水印" in out


def _run_cli_video(path: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, _CLI, path, "--frames", "4"],
        capture_output=True,
        timeout=120,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    return proc.returncode, (proc.stdout or b"").decode("utf-8", errors="replace")
