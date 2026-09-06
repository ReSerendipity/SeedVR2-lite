#!/usr/bin/env python3
"""量化质量基线（MLOps 评估 P2-6）：跨精度 PSNR/SSIM 对比留档。

目的：把「三种 Comfy-Org 量化变体加载期反量化是否损伤画质」从人工评级
（README ★ 表）变成**可留档、可趋势对比**的数字基线，检出「量化质量回退」。

用法（需本机已启动 SeedVR2 服务且对应精度权重已在 model/）：

    python perf/benchmark/quant_quality_baseline.py \
        --base-url http://127.0.0.1:7870 \
        --model 3b --precisions fp8 mxfp8 --reference fp8 \
        --scene gradient --resolution 512 --seed 42

输出：`.benchmarks/quant_baseline.json`（追加运行记录，保留最近 20 次），
并打印人读摘要。退出码 0=全部精度成功；非 0=存在 failed/skipped。

设计纪律（与执行对照表 D13 留痕一致）：
- 输入用 `golden_scenes.build_sources` 确定性源图——**不依赖任何私有图片**，跨机器可复现；
- 指标复用项目唯一实现 `image_metrics`（PSNR/SSIM）。**不引入 LPIPS/torchmetrics**：
  项目未声明该依赖，铁律禁止为评估工具新增第三方包；PSNR+SSIM 足以识别「可见降质」；
- 本脚本零模型/GPU 代码（requests + PIL + numpy），真机跑法见 BENCHMARK_GUIDE.md
  「量化质量基线」节；CI 自动接线待 smoke 服务提供「启动后常驻」模式（记录在案，非缺失）。
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import requests

# 项目根/.benchmarks/：锚定脚本位置而非 cwd（与 bench_restore_api 同口径）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = _PROJECT_ROOT / ".benchmarks" / "quant_baseline.json"
TERMINAL = ("completed", "failed", "cancelled", "timeout")
MAX_RUNS = 20


def load_repo_utils(src_dir: Path) -> tuple[Any, Any]:
    """按文件路径独立加载 image_metrics / golden_scenes。

    便携包/CI 场景下不触发 app 包级 import 链（避免拉起 FastAPI/torch 全栈），
    与 smoke_portable_bundle.py 的 --metrics-module 同一策略。
    """
    mods = {}
    for name in ("image_metrics", "golden_scenes"):
        path = src_dir / "app" / "integrated_app" / "utils" / f"{name}.py"
        if not path.exists():
            raise FileNotFoundError(f"工具模块不存在: {path}")
        spec = importlib.util.spec_from_file_location(f"qbaseline_{name}", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载 {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        mods[name] = module
    return mods["image_metrics"], mods["golden_scenes"]


def build_scene_inputs(golden_scenes: Any, out_dir: Path, size: int) -> dict[str, Path]:
    """确定性 golden 源图 → PNG 落盘，返回 {场景名: 路径}。"""
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, arr in golden_scenes.build_sources(size=size).items():
        fp = out_dir / f"golden_src_{name}.png"
        Image.fromarray(arr).save(fp)
        paths[name] = fp
    return paths


def new_session(base_url: str) -> requests.Session:
    """建会话并预取 CSRF cookie（Double Submit Cookie，与 bench_restore_api 同法）。"""
    s = requests.Session()
    with contextlib.suppress(requests.RequestException):
        s.get(f"{base_url}/", timeout=30)  # 首页失败不致命：提交时若缺 CSRF 会被 403 拦下并记录
    return s


def session_headers(s: requests.Session) -> dict[str, str]:
    csrf = s.cookies.get("csrf_token", "")
    return {"X-CSRF-Token": csrf} if csrf else {}


def submit_restore(
    s: requests.Session,
    base_url: str,
    png_path: Path,
    dit_model: str,
    resolution: int,
    seed: int,
) -> str | None:
    """提交单张 golden 源图修复，返回 task_id；提交失败返回 None。"""
    form = {
        "task_type": "image",
        "dit_model": dit_model,
        "resolution": str(resolution),
        "max_resolution": "0",
        "blocks_to_swap": "32",
        "batch_size": "5",
        "seed": str(seed),
        "output_format": "png",
    }
    with open(png_path, "rb") as f:
        resp = s.post(
            f"{base_url}/api/restore/",
            files={"file": (png_path.name, f, "image/png")},
            data=form,
            headers=session_headers(s),
            timeout=60,
        )
    if resp.status_code != 200:
        print(f"[quant-baseline] 提交失败 {dit_model}: HTTP {resp.status_code} {resp.text[:200]}")
        return None
    return resp.json()["data"]["task_id"]


def poll_task(
    s: requests.Session, base_url: str, task_id: str, timeout_s: float, interval_s: float = 3.0
) -> dict | None:
    """轮询至终态，返回 data dict；超时返回 None。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            data = (s.get(f"{base_url}/api/restore/{task_id}/result", timeout=30).json() or {}).get("data", {})
        except requests.RequestException:
            data = {}
        if data.get("status") in TERMINAL:
            return data
        time.sleep(interval_s)
    return None


def download_output(s: requests.Session, base_url: str, task_id: str, dest: Path) -> bool:
    """下载任务输出 PNG；失败返回 False（调用方记 skipped）。"""
    try:
        resp = s.get(f"{base_url}/api/restore/{task_id}/download", timeout=60)
        if resp.status_code != 200 or not resp.content:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return True
    except requests.RequestException:
        return False


def load_image_array(png_path: Path, like: np.ndarray) -> np.ndarray:
    """读 PNG 为 uint8 RGB；尺寸与参考不一致时重采样对齐（PSNR/SSIM 要求同形状）。"""
    from PIL import Image

    img = Image.open(png_path).convert("RGB")
    if img.size != (like.shape[1], like.shape[0]):
        img = img.resize((like.shape[1], like.shape[0]), Image.BILINEAR)
    return np.asarray(img, dtype=np.uint8)


def compute_precision_metrics(
    image_metrics: Any,
    source: np.ndarray,
    outputs: dict[str, np.ndarray],
    reference: str,
) -> dict[str, dict[str, Any]]:
    """逐精度计算 PSNR/SSIM（vs 源图）与相对基准精度的偏移。

    Args:
        image_metrics: 项目 image_metrics 模块（psnr/ssim 唯一实现）。
        source: golden 源图 (H, W, 3) uint8。
        outputs: {精度: 输出图数组}。
        reference: 基准精度名（如 "fp8"）；不存在于 outputs 时偏移项为 None。
    """
    ref_arr = outputs.get(reference)
    records: dict[str, dict[str, Any]] = {}
    for prec, arr in outputs.items():
        rec: dict[str, Any] = {
            "psnr_vs_source": round(image_metrics.psnr(source, arr), 2),
            "ssim_vs_source": round(image_metrics.ssim(source, arr), 4),
        }
        if prec == reference:
            rec["psnr_vs_reference"] = None
            rec["ssim_vs_reference"] = None
        elif ref_arr is not None:
            rec["psnr_vs_reference"] = round(image_metrics.psnr(ref_arr, arr), 2)
            rec["ssim_vs_reference"] = round(image_metrics.ssim(ref_arr, arr), 4)
        else:
            rec["psnr_vs_reference"] = None
            rec["ssim_vs_reference"] = None
            rec["reference_missing"] = True
        records[prec] = rec
    return records


def merge_runs(existing: dict | None, run: dict) -> dict:
    """把本次运行记录追加进基线 JSON 结构，保留最近 MAX_RUNS 条。"""
    runs = list((existing or {}).get("runs", []))
    runs.append(run)
    return {"schema": 1, "runs": runs[-MAX_RUNS:]}


def load_existing(output: Path) -> dict | None:
    if not output.exists():
        return None
    try:
        data = json.loads(output.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (ValueError, OSError):
        print(f"[quant-baseline] 既有基线文件不可解析，将重建: {output}")
        return None


def print_summary(run: dict) -> None:
    print(f"\n== 量化质量基线 {run['model']} / scene={run['scene']} / ref={run['reference']} ==")
    for prec, rec in run["precisions"].items():
        line = f"  {prec:<14} status={rec.get('status', '?')}"
        if isinstance(rec.get("psnr_vs_source"), (int, float)):
            line += (
                f"  PSNR(src)={rec['psnr_vs_source']}  SSIM(src)={rec['ssim_vs_source']}"
                f"  PSNR(ref)={rec.get('psnr_vs_reference')}  SSIM(ref)={rec.get('ssim_vs_reference')}"
            )
        print(line)


def run_baseline(args: argparse.Namespace) -> int:
    image_metrics, golden_scenes = load_repo_utils(Path(args.src_dir))
    base_url = args.base_url.rstrip("/")
    out_dir = Path(args.output)

    work_dir = Path(args.work_dir or (out_dir.parent / "quant-baseline-work"))
    scene_paths = build_scene_inputs(golden_scenes, work_dir, args.resolution)
    if args.scene == "all":
        scenes = list(scene_paths)
    elif args.scene in scene_paths:
        scenes = [args.scene]
    else:
        print(f"[quant-baseline] 未知场景 {args.scene}，可选: {sorted(scene_paths)} + all")
        return 2
    scene = scenes[0]

    s = new_session(base_url)
    source = load_image_array(scene_paths[scene], np.zeros((args.resolution, args.resolution, 3), dtype=np.uint8))

    outputs: dict[str, np.ndarray] = {}
    statuses: dict[str, dict[str, Any]] = {}
    failures = 0
    for prec in args.precisions:
        dit_model = f"{args.model}_{prec}"
        print(f"[quant-baseline] 提交 {dit_model} ...")
        task_id = submit_restore(s, base_url, scene_paths[scene], dit_model, args.resolution, args.seed)
        if not task_id:
            statuses[prec] = {"status": "submit_failed"}
            failures += 1
            continue
        data = poll_task(s, base_url, task_id, args.timeout)
        st = (data or {}).get("status")
        if st != "completed":
            statuses[prec] = {"status": st or "timeout", "error": (data or {}).get("error")}
            failures += 1
            continue
        dest = work_dir / f"out_{dit_model}.png"
        if not download_output(s, base_url, task_id, dest):
            statuses[prec] = {"status": "download_failed"}
            failures += 1
            continue
        outputs[prec] = load_image_array(dest, source)
        statuses[prec] = {
            "status": "completed",
            "task_id": task_id,
            "seed_effective": (data or {}).get("seed_effective"),
        }

    metrics = compute_precision_metrics(image_metrics, source, outputs, args.reference)
    for prec, st in statuses.items():
        if prec in metrics:
            metrics[prec].update({k: v for k, v in st.items() if k not in metrics[prec]})
        else:
            metrics[prec] = st

    run = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_url": base_url,
        "model": args.model,
        "scene": scene,
        "resolution": args.resolution,
        "seed": args.seed,
        "reference": args.reference,
        "precisions": metrics,
    }
    merged = merge_runs(load_existing(out_dir), run)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    out_dir.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[quant-baseline] 已归档 {out_dir}（共 {len(merged['runs'])} 条运行记录）")
    print_summary(run)
    return 1 if failures else 0


def main() -> None:
    p = argparse.ArgumentParser(description="量化质量基线（PSNR/SSIM 跨精度对比留档）")
    p.add_argument("--base-url", default="http://127.0.0.1:7870")
    p.add_argument("--model", default="3b", choices=["3b", "7b", "7b_sharp"], help="模型档位")
    p.add_argument(
        "--precisions",
        nargs="+",
        default=["fp8", "mxfp8"],
        help="要评估的精度列表（各精度权重须已在 model/，可用 scripts/download_model.py --precisions 拉取）",
    )
    p.add_argument("--reference", default="fp8", help="相对偏移的基准精度（默认 fp8：便携包自带、零额外下载）")
    p.add_argument("--scene", default="gradient", help="golden 场景名或 all（默认 gradient）")
    p.add_argument("--resolution", type=int, default=512, help="golden 源图与修复输出边长（正方形）")
    p.add_argument("--seed", type=int, default=42, help="钉死种子（跨次运行可比）")
    p.add_argument("--timeout", type=float, default=1800, help="单任务轮询超时秒数")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p.add_argument("--work-dir", default="", help="临时输入/输出目录（默认 .benchmarks/quant-baseline-work/）")
    p.add_argument(
        "--src-dir", default=str(_PROJECT_ROOT), help="含 app/integrated_app/utils 的仓库根（独立加载工具模块）"
    )
    args = p.parse_args()
    raise SystemExit(run_baseline(args))


if __name__ == "__main__":
    main()
