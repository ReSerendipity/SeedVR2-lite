#!/usr/bin/env python3
"""性能基线归档机制测试（成本治理 P2：性能基线留档）。

覆盖评估报告改进建议 #3 的验收标准：
- bench_restore_api 默认归档到项目根 .benchmarks/（而非 outputs/ 内——
  outputs/ 受保留策略周期清理，基线档案放进去会被误删）；
- flash_attention_benchmark._archive_report 统一写入 .benchmarks/，
  文件名含 GPU 标识，内容可 JSON 解析且含 hardware 上下文键。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestBenchRestoreApiArchive:
    def test_default_archive_under_benchmarks_dir(self):
        from perf.benchmark import bench_restore_api

        archive = bench_restore_api.DEFAULT_ARCHIVE
        assert archive.is_absolute(), "归档路径应锚定项目根（不随 cwd 漂移）"
        assert ".benchmarks" in archive.parts
        assert "outputs" not in archive.parts, "归档不得落在 outputs/（会被保留策略清理误删）"
        assert archive.suffix == ".jsonl"

    def test_benchmarks_dir_outside_outputs(self):
        outputs_root = (PROJECT_ROOT / "outputs").resolve()
        benchmarks_root = (PROJECT_ROOT / ".benchmarks").resolve()
        assert outputs_root not in benchmarks_root.parents


class TestFlashAttentionArchiveReport:
    def test_archive_report_writes_benchmarks_json(self):
        from perf.benchmark import flash_attention_benchmark as fab

        report = {
            "generated_at": "2026-09-06T00:00:00",
            "hardware": {"gpu_name": "TEST GPU", "torch_version": "test"},
            "flash_attn_available": False,
            "results": [],
            "precision": None,
        }
        out_path = fab._archive_report(report)
        try:
            assert out_path.parent == PROJECT_ROOT / ".benchmarks"
            assert "TEST_GPU" in out_path.name
            data = json.loads(out_path.read_text(encoding="utf-8"))
            assert data["hardware"]["gpu_name"] == "TEST GPU"
            assert "generated_at" in data
        finally:
            out_path.unlink(missing_ok=True)
