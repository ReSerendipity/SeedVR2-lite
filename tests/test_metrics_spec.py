"""指标口径规范测试（数据治理 P3-2）。

验收标准（对应评估报告 §9.2 P3-2）：
1. 规范文档存在，且明确定义 processing_time / fps / it/s / 显存四类口径；
2. 图像与视频两条管线的 metadata 均输出 steps_per_second / dit_seconds /
   stage_durations_ms（口径字段齐备）；
3. it/s 计算口径正确：以 DiT 采样耗时为分母（不含 VAE/IO），
   与端到端 processing_fps 明确区分。
"""

import re
from pathlib import Path

import pytest

SPEC_PATH = Path("docs/METRICS_SPEC.md")
PIPELINES = (
    "app/integrated_app/engines/_image_pipeline.py",
    "app/integrated_app/engines/_video_pipeline.py",
)


class TestMetricsSpecDocument:
    def test_spec_document_exists(self):
        """验收点 1：规范文档存在。"""
        assert SPEC_PATH.exists(), "缺少 docs/METRICS_SPEC.md"

    @pytest.mark.parametrize(
        "keyword",
        ["processing_time", "processing_fps", "steps_per_second", "max_memory_allocated", "PSNR", "SSIM"],
    )
    def test_spec_defines_all_metrics(self, keyword: str):
        """验收点 1：四类口径定义齐备。"""
        text = SPEC_PATH.read_text(encoding="utf-8")
        assert keyword in text

    def test_spec_forbids_mixing_scopes(self):
        """规范必须显式禁止 fps 与 it/s 混用。"""
        text = SPEC_PATH.read_text(encoding="utf-8")
        assert "不可混用" in text and "铁律" in text


class TestPipelineMetadataFields:
    @pytest.mark.parametrize("pipeline_path", PIPELINES)
    def test_metadata_emits_speed_fields(self, pipeline_path: str):
        """验收点 2：两条管线均输出统一口径字段。"""
        source = Path(pipeline_path).read_text(encoding="utf-8")
        assert '"steps_per_second"' in source
        assert '"dit_seconds"' in source
        assert '"stage_durations_ms"' in source

    def test_steps_per_second_uses_dit_stage_only(self):
        """验收点 3：it/s 分母是 dit_sample 阶段耗时，不是端到端耗时。"""
        for pipeline_path in PIPELINES:
            source = Path(pipeline_path).read_text(encoding="utf-8")
            # 分母取自 dit_sample 阶段
            assert 'stage_durations_ms.get("dit_sample"' in source
            # 分子是采样步数
            assert re.search(
                r"steps_per_second\s*=\s*round\(\s*float\(sample_steps\)\s*/\s*dit_seconds", source
            ), f"{pipeline_path}: it/s 计算口径与规范不一致"

    def test_video_keeps_end_to_end_fps_separate(self):
        """视频端保留端到端 processing_fps，与 it/s 并存不混用。"""
        source = Path("app/integrated_app/engines/_video_pipeline.py").read_text(encoding="utf-8")
        assert '"processing_fps"' in source
        assert '"avg_frame_time_ms"' in source
