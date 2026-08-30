"""GPU 真实推理质量门禁的本地可测逻辑（数据治理扩展）。

本门禁最终在 gpu-smoke.yml 的自托管 GPU runner 上运行：真跑一次推理 →
用 ``compute_quality`` 复用应用内置 ``image_metrics`` 比对输出与输入保真度。
其中「指标计算 + 阈值判定」逻辑可在本机 CPU 上用 numpy/PIL 完整复现，故在此
锁定，使门禁本身有回归保护、且 CI 无 GPU 也能绿。

验收标准：
1. 阈值判定纯函数对各类指标正确放行/拦截；
2. ``compute_quality`` 端到端（本机 .venv 解释器 + 仓库内置 image_metrics）
   能对相同图给出高保真、对黑白差异图给出低保真并被门禁拦截。
"""

import importlib.util
import sys
from pathlib import Path

import numpy as np
from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SMOKE_PATH = _REPO_ROOT / "scripts" / "smoke_portable_bundle.py"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("smoke_portable_bundle", _SMOKE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # noqa: S301 - 受控本地脚本，非不可信输入
    return module


_smoke = _load_smoke()
passes_quality_gate = _smoke.passes_quality_gate
compute_quality = _smoke.compute_quality

PYTHON = sys.executable  # 本机 .venv 解释器（含 numpy/PIL）


class TestQualityGateDecision:
    def test_passes_when_above_thresholds(self):
        ok, why = passes_quality_gate({"psnr_db": 30.0, "ssim": 0.95}, 15.0, 0.5)
        assert ok is True
        assert "PSNR" in why

    def test_fails_on_low_psnr(self):
        ok, why = passes_quality_gate({"psnr_db": 5.0, "ssim": 0.9}, 15.0, 0.5)
        assert ok is False
        assert "PSNR" in why

    def test_fails_on_low_ssim(self):
        ok, why = passes_quality_gate({"psnr_db": 30.0, "ssim": 0.1}, 15.0, 0.5)
        assert ok is False
        assert "SSIM" in why

    def test_fails_on_eval_error(self):
        ok, why = passes_quality_gate({"error": "boom"}, 15.0, 0.5)
        assert ok is False
        assert "boom" in why


class TestComputeQuality:
    def _write_png(self, tmp_path: Path, arr: np.ndarray, name: str) -> Path:
        p = tmp_path / name
        Image.fromarray(arr.astype(np.uint8)).save(p)
        return p

    def test_identical_images_pass(self, tmp_path):
        img = np.random.default_rng(0).integers(0, 255, (64, 64, 3), dtype=np.uint8)  # nosec B311
        a = self._write_png(tmp_path, img, "a.png")
        b = self._write_png(tmp_path, img, "b.png")
        metrics = compute_quality(PYTHON, _REPO_ROOT, a, b)
        assert "error" not in metrics, metrics
        ok, _ = passes_quality_gate(metrics, 15.0, 0.5)
        assert ok is True

    def test_different_images_flagged(self, tmp_path):
        a = self._write_png(tmp_path, np.full((64, 64, 3), 255, dtype=np.uint8), "a.png")
        b = self._write_png(tmp_path, np.zeros((64, 64, 3), dtype=np.uint8), "b.png")
        metrics = compute_quality(PYTHON, _REPO_ROOT, a, b)
        assert "error" not in metrics, metrics
        # 纯黑白对比：PSNR 极低，门禁应判失败
        ok, _ = passes_quality_gate(metrics, 15.0, 0.5)
        assert ok is False

    def test_output_upscaled_to_input_size(self, tmp_path):
        """输出尺寸与输入不一致时，compute_quality 应自动缩放后再比较。"""
        rng = np.random.default_rng(1)  # nosec B311
        small = rng.integers(0, 255, (32, 32, 3), dtype=np.uint8)
        a = self._write_png(tmp_path, small, "a.png")
        b = self._write_png(tmp_path, small, "b_big.png")
        # b 是不同尺寸的同一内容（模拟上采样结果）
        Image.fromarray(small.astype(np.uint8)).resize((64, 64)).save(b)
        metrics = compute_quality(PYTHON, _REPO_ROOT, a, b)
        assert "error" not in metrics, metrics
        ok, _ = passes_quality_gate(metrics, 15.0, 0.5)
        assert ok is True
