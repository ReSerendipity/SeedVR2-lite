#!/usr/bin/env python3
"""帧级断点续跑测试（成本治理 P2）。

覆盖评估报告改进建议 #5 的验收标准：
- _segment_frames_complete：全帧在盘 → True；缺帧/空帧 → False；
  尺寸校验防护——降级重试改变分辨率时残留帧不被复用（防止 ffmpeg 合成
  混尺寸帧序列）；
- _rebuild_tail_from_disk：从盘上重建段间混合尾帧；缺帧/零重叠返回 None；
- 接线哨兵：单文件与批量重试路径都注入 resume_frames，且
  _get_inference_config 白名单放行该参数（引擎能实际收到）。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from pathlib import Path

import pytest

from app.integrated_app.engines._video_pipeline import _VideoPipelineMixin

cv2 = pytest.importorskip("cv2")


def _write_frame(frames_dir: Path, idx: int, size: tuple[int, int] = (64, 32)) -> None:
    import numpy as np

    h, w = size
    img = np.zeros((h, w, 3), dtype="uint8")
    cv2.imwrite(str(frames_dir / f"frame_{idx:06d}.png"), img)


class TestSegmentFramesComplete:
    def test_all_frames_present(self, tmp_path):
        for i in range(4, 9):
            _write_frame(tmp_path, i)
        assert _VideoPipelineMixin._segment_frames_complete(str(tmp_path), 4, 9) is True

    def test_missing_frame(self, tmp_path):
        for i in (4, 5, 7):
            _write_frame(tmp_path, i)
        assert _VideoPipelineMixin._segment_frames_complete(str(tmp_path), 4, 8) is False

    def test_empty_frame_file(self, tmp_path):
        _write_frame(tmp_path, 0)
        (tmp_path / "frame_000001.png").write_bytes(b"")
        assert _VideoPipelineMixin._segment_frames_complete(str(tmp_path), 0, 2) is False

    def test_size_mismatch_rejects_reuse(self, tmp_path):
        """降级重试改变分辨率 → 残留帧尺寸不符 → 整段重算。"""
        _write_frame(tmp_path, 0, size=(64, 32))
        assert _VideoPipelineMixin._segment_frames_complete(str(tmp_path), 0, 1, expected_hw=(128, 64)) is False
        assert _VideoPipelineMixin._segment_frames_complete(str(tmp_path), 0, 1, expected_hw=(64, 32)) is True


class TestRebuildTailFromDisk:
    def test_rebuild_returns_rgb_array(self, tmp_path):
        import numpy as np

        for i in range(6):
            _write_frame(tmp_path, i)
        tail = _VideoPipelineMixin._rebuild_tail_from_disk(str(tmp_path), 6, 2)
        assert tail is not None
        assert tail.shape == (2, 64, 32, 3)  # (n, h, w, 3)，_write_frame(size=(64,32)) 即 h=64, w=32
        assert tail.dtype == np.uint8

    def test_missing_tail_frame_returns_none(self, tmp_path):
        _write_frame(tmp_path, 4)
        _write_frame(tmp_path, 6)  # 缺 frame_000005
        assert _VideoPipelineMixin._rebuild_tail_from_disk(str(tmp_path), 6, 2) is None

    def test_zero_overlap_or_first_segment_returns_none(self, tmp_path):
        _write_frame(tmp_path, 0)
        assert _VideoPipelineMixin._rebuild_tail_from_disk(str(tmp_path), 1, 0) is None
        assert _VideoPipelineMixin._rebuild_tail_from_disk(str(tmp_path), 0, 4) is None


class TestWiringSentinels:
    """重试链路接线哨兵：resume_frames 从编排层到引擎白名单全链路可达。"""

    def test_inference_config_whitelists_resume_frames(self):
        """_get_inference_config 是白名单 dict，未登记的 kwargs 会被静默丢弃。"""
        from app.integrated_app.engines.seedvr2_engine import SeedVR2Engine

        engine = SeedVR2Engine.__new__(SeedVR2Engine)
        engine.config = {"inference": {}, "model": {}, "restore": {}}
        inf = engine._get_inference_config(resume_frames=True)
        assert inf.get("resume_frames") is True
        assert engine._get_inference_config().get("resume_frames") is False

    def test_single_task_retry_sets_resume_flag(self):
        from app.integrated_app.services import restore_service

        source = Path(restore_service.__file__).read_text(encoding="utf-8")
        assert 'resume_frames_flag["flag"] = True' in source, "单文件重试 _on_retry 必须打开续跑标志"
        assert 'resume_frames=resume_frames_flag["flag"]' in source

    def test_batch_retry_passes_resume_frames(self):
        from app.integrated_app.services import restore_service

        source = Path(restore_service.__file__).read_text(encoding="utf-8")
        assert 'infer_kwargs["resume_frames"] = True' in source, "批量重试必须注入 resume_frames"

    def test_engine_metadata_records_resumed_segments(self):
        source = Path(_VideoPipelineMixin.__module__.replace(".", "/") + ".py").read_text(encoding="utf-8")
        assert '"resumed_segments": resumed_segments' in source
