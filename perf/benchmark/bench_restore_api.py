#!/usr/bin/env python3
"""SeedVR2 修复耗时基准脚本（走真实 HTTP API）。

用法：
    python perf/benchmark/bench_restore_api.py --file <media> --label <名字> \
        [--dit-model 3b_fp16|3b_fp8] [--task-type image|video] [--resolution 1024] \
        [--param key=value ...]

    # 查看历史趋势（最近 10 条）
    python perf/benchmark/bench_restore_api.py --trend 10

说明：
    1. 需要先在本地启动 SeedVR2 服务（app/clean_launch.py）。
    2. 通过 multipart/form-data 上传文件 + 参数，POST /api/restore/。
    3. 轮询 /api/restore/{task_id}/result 直到终态，打印：
       - submit: 上传+建任务耗时
       - processing: 后台推理耗时（本文中对比的核心指标）
       - total: 总耗时
    4. 自动处理 CSRF（Double Submit Cookie：先 GET 拿 cookie，POST 带 X-CSRF-Token）。
    5. 首次运行同一模型会触发 torch.compile（若开启），耗时偏大属正常，应以第 2 次（稳态）为准。
    6. 结果自动归档到 .benchmarks/bench_restore_api.jsonl（JSONL 追加），
       支持跨次运行的趋势对比（--trend）；--no-archive 可跳过归档（P2-3）。
       归档位置在 outputs/ 之外——outputs/ 受保留策略周期清理（见
       docs/输出保留策略.md），基线档案放进去会被 14 天规则误删（成本治理 P1-1）。
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import requests

BASE = "http://127.0.0.1:7870"
TERMINAL = ("completed", "failed", "cancelled", "timeout")
# 项目根/.benchmarks/：锚定脚本位置而非 cwd，任意工作目录启动均归档到同一基线库
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE = _PROJECT_ROOT / ".benchmarks" / "bench_restore_api.jsonl"


def _fetch_gpu_info(session: requests.Session) -> dict:
    """拉取 GPU 名称与档位信息，失败返回空 dict（不阻塞基准）。"""
    try:
        r = session.get(f"{BASE}/api/system/health", timeout=10)
        gpu = (r.json().get("gpu") or {}) if r.status_code == 200 else {}
        return {"gpu_name": gpu.get("device_name", "")}
    except Exception:
        return {}


def bench(file_path: str, label: str, form: dict) -> dict:
    """执行一次基准并返回结构化结果（含归档所需上下文）。"""
    s = requests.Session()
    gpu_info = _fetch_gpu_info(s)
    s.get(f"{BASE}/", timeout=30)  # 拿 csrf_token cookie
    csrf = s.cookies.get("csrf_token", "")
    headers = {"X-CSRF-Token": csrf} if csrf else {}

    record: dict = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "form": dict(form),
        **gpu_info,
    }

    with open(file_path, "rb") as f:
        fname = file_path.rsplit("\\", 1)[-1]
        files = {"file": (fname, f, "application/octet-stream")}
        t0 = time.time()
        resp = s.post(f"{BASE}/api/restore/", files=files, data=form, headers=headers, timeout=30)
        t1 = time.time()
        if resp.status_code != 200:
            print(f"[{label}] POST failed: {resp.status_code} {resp.text[:300]}")
            record.update({"status": "submit_failed", "submit_s": None, "processing_s": None, "total_s": None})
            return record
        task_id = resp.json()["data"]["task_id"]
        print(f"[{label}] task_id={task_id} submit={t1 - t0:.1f}s")
        record["task_id"] = task_id

    t_submit = t1 - t0
    record["submit_s"] = round(t_submit, 2)
    t_start = time.time()
    while True:
        r = s.get(f"{BASE}/api/restore/{task_id}/result", timeout=30)
        j = r.json()
        data = j.get("data", {})
        st = data.get("status") or j.get("status")
        if st in TERMINAL:
            elapsed = time.time() - t_start
            print(f"[{label}] status={st} processing={elapsed:.1f}s total={t_submit + elapsed:.1f}s")
            record.update(
                {
                    "status": st,
                    "processing_s": round(elapsed, 2),
                    "total_s": round(t_submit + elapsed, 2),
                }
            )
            if st == "failed":
                print(f"[{label}] error: {data.get('error')}")
                record["error"] = data.get("error")
            # 后端真实耗时（含模型加载）优先，前端轮询计时兜底
            backend_time = data.get("processing_time")
            if isinstance(backend_time, (int, float)) and backend_time > 0:
                record["backend_processing_s"] = round(float(backend_time), 2)
            return record
        time.sleep(2.0)


def archive_result(record: dict, archive_path: Path) -> None:
    """把结果追加到 JSONL 归档（目录自动创建，写入失败仅警告）。"""
    try:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with open(archive_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"[archive] {archive_path}")
    except OSError as e:
        print(f"[archive] 归档失败（不影响基准结果）: {e}")


def show_trend(archive_path: Path, last_n: int) -> int:
    """打印最近 N 条归档记录的趋势对比。"""
    if not archive_path.exists():
        print(f"暂无归档: {archive_path}")
        return 0
    records = []
    with open(archive_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    records = records[-last_n:]
    if not records:
        print("归档为空")
        return 0

    print(f"{'timestamp':<20} {'label':<24} {'model':<12} {'status':<15} {'backend_s':>9} {'poll_s':>8}")
    print("-" * 92)
    for r in records:
        model = (r.get("form") or {}).get("dit_model", "")
        backend = r.get("backend_processing_s")
        backend_str = f"{backend:.1f}" if isinstance(backend, (int, float)) else "--"
        poll = r.get("processing_s")
        poll_str = f"{poll:.1f}" if isinstance(poll, (int, float)) else "--"
        print(
            f"{str(r.get('timestamp', '')):<20} {str(r.get('label', '')):<24} "
            f"{model:<12} {str(r.get('status', '')):<15} {backend_str:>9} {poll_str:>8}"
        )
    return len(records)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file", help="待修复的图片或视频路径")
    p.add_argument("--label", default="run", help="本次运行标签")
    p.add_argument("--dit-model", default="3b_fp16")
    p.add_argument("--task-type", default="image", choices=["image", "video"])
    p.add_argument("--resolution", type=int, default=1024)
    p.add_argument("--param", action="append", default=[], help="额外表单字段 key=value，可多次")
    p.add_argument(
        "--trend",
        type=int,
        default=0,
        metavar="N",
        help="不跑基准，仅打印归档中最近 N 条记录的趋势对比",
    )
    p.add_argument(
        "--archive-path",
        default=str(DEFAULT_ARCHIVE),
        help="归档 JSONL 路径（默认 .benchmarks/bench_restore_api.jsonl）",
    )
    p.add_argument("--no-archive", action="store_true", help="跳过结果归档")
    args = p.parse_args()

    archive_path = Path(args.archive_path)

    if args.trend > 0:
        show_trend(archive_path, args.trend)
        return

    if not args.file:
        p.error("--file 为必填（除非使用 --trend）")

    form = {
        "task_type": args.task_type,
        "dit_model": args.dit_model,
        "resolution": str(args.resolution),
        "max_resolution": "0",
        "blocks_to_swap": "32",
        "batch_size": "5",
    }
    for kv in args.param:
        if "=" in kv:
            k, v = kv.split("=", 1)
            form[k] = v

    record = bench(args.file, args.label, form)
    if not args.no_archive:
        archive_result(record, archive_path)


if __name__ == "__main__":
    main()
