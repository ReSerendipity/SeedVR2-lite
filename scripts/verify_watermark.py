#!/usr/bin/env python3
"""SeedVR2 输出水印验证 CLI（数据治理 P2-5）。

为本项目补齐「输出图像是否携带可信归属水印」的独立验证入口
（对齐 MiniMax-H3-lite 的 scripts/verify_watermark.py 能力；
此前水印仅在嵌入侧与单元测试中覆盖，无运维/取证工具）。

验证逻辑复用 app/integrated_app/security/watermark.py：
- 配置 .watermark_key 时：严格 HMAC 签名验证（仅签发端水印通过）
- 未配置密钥时：退化为品牌字符串包含检测（仅参考）

用法:
    python scripts/verify_watermark.py <image> [--show-payload]

退出码:
    0 — 检测到可信水印
    1 — 未检测到可信水印
    2 — 文件读取/解析失败
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app.integrated_app.security.watermark import extract_watermark, verify_watermark  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="SeedVR2 输出水印验证工具")
    parser.add_argument("image", help="待验证的图像文件路径")
    parser.add_argument("--show-payload", action="store_true", help="打印提取到的水印载荷")
    args = parser.parse_args()

    path = Path(args.image)
    if not path.exists():
        print(f"[错误] 文件不存在: {path}")
        return 2
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
