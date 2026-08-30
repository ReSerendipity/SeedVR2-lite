#!/usr/bin/env python3
"""生成 Golden 退化基准对数据集（数据治理 P1-3）。

把"源图 → 退化图"基准对落盘到 tests/golden/，并写出 manifest.json
（记录每个场景的退化参数与实测 PSNR/SSIM 基线），用途：
- 人工核查：肉眼比对 source / degraded 是否符合预期退化强度；
- 基线留存：manifest 记录本次生成的指标，作为退化流水线变更的参照；
- CI 门禁：tests/test_golden_quality.py 使用同一生成器在内存中构建
  基准对并断言质量门槛（不依赖本脚本产物，保证无状态可重现）。

用法：
    python scripts/generate_golden_dataset.py [--size 128] [--output tests/golden]

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# 保证从任意 cwd 执行都能导入 app 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.integrated_app.utils.golden_scenes import (  # noqa: E402
    DEFAULT_DEGRADATION_SEED,
    build_golden_scenes,
)
from app.integrated_app.utils.image_metrics import psnr, ssim  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Golden 退化基准对数据集")
    parser.add_argument("--size", type=int, default=128, help="图像边长（默认 128）")
    parser.add_argument("--output", default="tests/golden", help="输出目录（默认 tests/golden）")
    parser.add_argument(
        "--skip-images",
        action="store_true",
        help="只写 manifest，不落盘 PNG（CI 快速校验场景）",
    )
    args = parser.parse_args()

    scenes = build_golden_scenes(args.size)
    if not scenes:
        print("[WARN] 未生成任何场景（可能缺少 torch），退出码 1")
        return 1

    os.makedirs(args.output, exist_ok=True)

    manifest: dict = {
        "schema": "seedvr2-golden/1",
        "size": args.size,
        "degradation_seed": DEFAULT_DEGRADATION_SEED,
        "scenes": [],
    }

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow 缺失时仅写 manifest
        Image = None  # noqa: N806 - 沿用 PIL 惯例的类名引用

    for scene in scenes:
        metrics = {
            "psnr_db": round(psnr(scene.source, scene.degraded), 4),
            "ssim": round(ssim(scene.source, scene.degraded), 6),
        }
        entry = {
            "name": scene.name,
            "params": scene.params_desc,
            "degraded_vs_source": metrics,
        }
        manifest["scenes"].append(entry)

        if Image is not None and not args.skip_images:
            Image.fromarray(scene.source).save(os.path.join(args.output, f"{scene.name}_source.png"))
            Image.fromarray(scene.degraded).save(os.path.join(args.output, f"{scene.name}_degraded.png"))

    manifest_path = os.path.join(args.output, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"Golden 数据集已生成: {len(manifest['scenes'])} 个场景 -> {args.output}")
    for entry in manifest["scenes"]:
        m = entry["degraded_vs_source"]
        print(f"  - {entry['name']}: PSNR {m['psnr_db']:.2f} dB, SSIM {m['ssim']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
