"""量化质量基线脚本的纯逻辑测试（MLOps P2-6，零网络/零 GPU）。

验收标准：
1. load_repo_utils 独立加载 image_metrics/golden_scenes（不触发 app 包链）；
2. build_scene_inputs 确定性产出 PNG；load_image_array 尺寸对齐（重采样路径）；
3. compute_precision_metrics：基准精度自身偏移为 None、其余精度有相对值、
   缺失基准时 reference_missing 标记；
4. merge_runs 追加 + MAX_RUNS 截尾；load_existing 损坏文件容错；
5. poll_task：终态返回 data / 超时返回 None（fake session + 假时钟）。
"""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "quant_quality_baseline", _REPO_ROOT / "perf" / "benchmark" / "quant_quality_baseline.py"
)
qqb = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules["quant_quality_baseline"] = qqb
_SPEC.loader.exec_module(qqb)


class TestLoadRepoUtils:
    def test_loads_both_modules(self):
        im, gs = qqb.load_repo_utils(_REPO_ROOT)
        assert callable(im.psnr) and callable(im.ssim)
        assert callable(gs.build_sources)

    def test_missing_src_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            qqb.load_repo_utils(tmp_path)


class TestSceneInputs:
    def test_build_and_reload_roundtrip(self, tmp_path):
        _, gs = qqb.load_repo_utils(_REPO_ROOT)
        paths = qqb.build_scene_inputs(gs, tmp_path, 64)
        assert paths and all(p.exists() for p in paths.values())
        arr = qqb.load_image_array(next(iter(paths.values())), np.zeros((64, 64, 3), dtype=np.uint8))
        assert arr.shape == (64, 64, 3) and arr.dtype == np.uint8

    def test_resize_alignment(self, tmp_path):
        from PIL import Image

        fp = tmp_path / "big.png"
        Image.new("RGB", (128, 128), (10, 20, 30)).save(fp)
        arr = qqb.load_image_array(fp, np.zeros((64, 64, 3), dtype=np.uint8))
        assert arr.shape == (64, 64, 3)
        # 纯色图重采样后仍是该纯色
        assert (arr == np.array([10, 20, 30], dtype=np.uint8)).all()


class TestComputeMetrics:
    def _mods(self):
        im, _ = qqb.load_repo_utils(_REPO_ROOT)
        return im

    def test_reference_self_none_others_relative(self):
        im = self._mods()
        source = np.zeros((32, 32, 3), dtype=np.uint8)
        source[:] = 128
        out_ref = source.copy()
        out_dev = source.copy()
        out_dev[::4] = 96  # 1/4 行偏暗
        rec = qqb.compute_precision_metrics(im, source, {"fp8": out_ref, "mxfp8": out_dev}, reference="fp8")
        assert rec["fp8"]["psnr_vs_reference"] is None
        assert rec["mxfp8"]["psnr_vs_reference"] is not None
        assert rec["mxfp8"]["psnr_vs_source"] < 99
        assert rec["fp8"]["psnr_vs_source"] == pytest.approx(99.0)  # 与源恒等

    def test_missing_reference_flagged(self):
        im = self._mods()
        source = np.full((32, 32, 3), 100, dtype=np.uint8)
        rec = qqb.compute_precision_metrics(im, source, {"mxfp8": source.copy()}, reference="fp16")
        assert rec["mxfp8"]["reference_missing"] is True
        assert rec["mxfp8"]["psnr_vs_reference"] is None


class TestMergeAndLoad:
    def test_append_and_trim(self):
        merged = {"runs": [{"i": n} for n in range(qqb.MAX_RUNS)]}
        out = qqb.merge_runs(merged, {"i": 999})
        assert len(out["runs"]) == qqb.MAX_RUNS
        assert out["runs"][-1] == {"i": 999}
        assert out["runs"][0] == {"i": 1}

    def test_first_run_creates_schema(self):
        out = qqb.merge_runs(None, {"x": 1})
        assert out == {"schema": 1, "runs": [{"x": 1}]}

    def test_load_existing_tolerant(self, tmp_path):
        assert qqb.load_existing(tmp_path / "nope.json") is None
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert qqb.load_existing(bad) is None
        ok = tmp_path / "ok.json"
        ok.write_text(json.dumps({"runs": []}), encoding="utf-8")
        assert qqb.load_existing(ok) == {"runs": []}


class _Resp:
    def __init__(self, payload, status_code=200, content=b""):
        self._payload = payload
        self.status_code = status_code
        self.content = content

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)

    def get(self, url, timeout=None):
        return self._responses.pop(0)


class TestPollTask:
    def test_terminal_returns_data(self, monkeypatch):
        monkeypatch.setattr(qqb.time, "sleep", lambda _s: None)
        s = _FakeSession(
            [
                _Resp({"data": {"status": "processing"}}),
                _Resp({"data": {"status": "completed", "seed_effective": 42}}),
            ]
        )
        data = qqb.poll_task(s, "http://x", "t1", timeout_s=100)
        assert data == {"status": "completed", "seed_effective": 42}

    def test_timeout_returns_none(self, monkeypatch):
        clock = iter([0.0, 0.0, 50.0, 500.0])
        monkeypatch.setattr(qqb.time, "monotonic", lambda: next(clock))
        monkeypatch.setattr(qqb.time, "sleep", lambda _s: None)

        class _Loop:
            def get(self, url, timeout=None):
                return _Resp({"data": {"status": "processing"}})

        assert qqb.poll_task(_Loop(), "http://x", "t1", timeout_s=100) is None
