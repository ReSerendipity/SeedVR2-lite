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


def _run_cli(path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _CLI, path],
        capture_output=True,
        text=True,
        timeout=60,
    )


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
        result = _run_cli(path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[通过]" in result.stdout


def test_cli_exits_one_for_clean_image():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "clean.png")
        _write_image(path, watermarked=False)
        result = _run_cli(path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "[未通过]" in result.stdout


def test_cli_exits_two_for_unreadable_file():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "not_an_image.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("x")
        result = _run_cli(path)
    assert result.returncode == 2, result.stdout + result.stderr
