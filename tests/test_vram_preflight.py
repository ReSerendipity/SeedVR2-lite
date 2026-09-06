#!/usr/bin/env python3
"""显存预检门禁测试（成本治理 P1-2）。

覆盖评估报告改进建议 #1 的验收标准：
- vram_preflight_gate：开关关闭 / 媒体探测失败 / 无 GPU 时 fail-open 放行；
  估算需求超预算或 risk=high 时抛 InsufficientVramError（detail 含估算值、
  预算与推荐参数）；risk=medium 放行并携带 warning；risk=low 无 warning；
  预算 = mem_get_info 可用 + 本进程已分配（避免已加载权重二次扣减）。
- 路由接线：/api/restore/ 与 /api/restore/batch 提交链路在门禁拒绝时返回
  503 + INSUFFICIENT_VRAM 错误信封，medium 风险时响应携带 vram_warning。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from types import SimpleNamespace

import pytest

from app.integrated_app.exceptions import InsufficientVramError
from app.integrated_app.services import restore_service
from tests.conftest import csrf_post


def _gpu_info(available_mb: int = 1024, allocated_mb: int = 1024, total_mb: int = 8192) -> dict:
    return {
        "total_mb": total_mb,
        "allocated_mb": allocated_mb,
        "reserved_mb": allocated_mb,
        "available_mb": available_mb,
        "utilization_pct": 50.0,
    }


def _recommend(risk: str = "low", warning: str = "") -> dict:
    return {
        "precision": "fp8",
        "enable_blockswap": risk in ("medium", "high"),
        "blocks_to_swap": 28 if risk in ("medium", "high") else 0,
        "tile_size": 512,
        "vram_tile_overlap": 128,
        "estimated_vram_gb": 6.0,
        "available_vram_gb": 2.0,
        "risk": risk,
        "warning": warning,
    }


class TestVramPreflightGate:
    """vram_preflight_gate 单元测试。"""

    def test_skip_when_disabled(self):
        cfg = {"runtime": {"vram_preflight_enabled": False}}
        assert restore_service.vram_preflight_gate(cfg, "3b", "fp16", "whatever.png", "image") is None

    def test_skip_when_probe_fails(self, monkeypatch):
        monkeypatch.setattr(restore_service, "_probe_media_geometry", lambda p, t: None)
        assert restore_service.vram_preflight_gate({}, "3b", "fp16", "missing.png", "image") is None

    def test_skip_when_no_gpu(self, monkeypatch):
        monkeypatch.setattr(restore_service, "_probe_media_geometry", lambda p, t: (1920, 1080, 1))
        monkeypatch.setattr(restore_service, "get_gpu_memory_info", lambda: _gpu_info(total_mb=0))
        assert restore_service.vram_preflight_gate({}, "3b", "fp16", "a.png", "image") is None

    def test_reject_when_estimate_exceeds_budget(self, monkeypatch):
        """预算 = available + allocated（2GB），估算 100GB → 拒绝。"""
        monkeypatch.setattr(restore_service, "_probe_media_geometry", lambda p, t: (1920, 1080, 1))
        monkeypatch.setattr(restore_service, "get_gpu_memory_info", lambda: _gpu_info())
        monkeypatch.setattr(restore_service, "estimate_vram_requirements", lambda *a, **k: 100.0)
        monkeypatch.setattr(restore_service, "recommend_params", lambda *a, **k: _recommend("low"))
        with pytest.raises(InsufficientVramError) as exc_info:
            restore_service.vram_preflight_gate({}, "7b", "fp16", "a.png", "image")
        detail = exc_info.value.detail
        assert detail["estimated_vram_gb"] == 100.0
        assert detail["available_vram_gb"] == 2.0  # (1024 + 1024) / 1024
        assert detail["recommendation"]["risk"] == "low"
        assert "建议" in exc_info.value.message

    def test_reject_when_recommendation_high_risk(self, monkeypatch):
        """估算未超预算但任何降档组合都放不下（risk=high）→ 拒绝。"""
        monkeypatch.setattr(restore_service, "_probe_media_geometry", lambda p, t: (1920, 1080, 1))
        monkeypatch.setattr(restore_service, "get_gpu_memory_info", lambda: _gpu_info())
        monkeypatch.setattr(restore_service, "estimate_vram_requirements", lambda *a, **k: 1.5)
        monkeypatch.setattr(restore_service, "recommend_params", lambda *a, **k: _recommend("high"))
        with pytest.raises(InsufficientVramError):
            restore_service.vram_preflight_gate({}, "3b", "fp16", "a.png", "image")

    def test_medium_risk_passes_with_warning(self, monkeypatch):
        monkeypatch.setattr(restore_service, "_probe_media_geometry", lambda p, t: (1920, 1080, 1))
        monkeypatch.setattr(restore_service, "get_gpu_memory_info", lambda: _gpu_info())
        monkeypatch.setattr(restore_service, "estimate_vram_requirements", lambda *a, **k: 1.5)
        monkeypatch.setattr(
            restore_service, "recommend_params", lambda *a, **k: _recommend("medium", warning="VRAM 紧张")
        )
        result = restore_service.vram_preflight_gate({}, "3b", "fp8", "a.mp4", "video")
        assert result is not None
        assert result["risk"] == "medium"
        assert result["warning"] == "VRAM 紧张"
        assert (result["num_frames"], result["input_width"], result["input_height"]) == (1, 1920, 1080)

    def test_low_risk_passes_without_warning(self, monkeypatch):
        monkeypatch.setattr(restore_service, "_probe_media_geometry", lambda p, t: (800, 600, 1))
        monkeypatch.setattr(restore_service, "get_gpu_memory_info", lambda: _gpu_info())
        monkeypatch.setattr(restore_service, "estimate_vram_requirements", lambda *a, **k: 1.0)
        monkeypatch.setattr(restore_service, "recommend_params", lambda *a, **k: _recommend("low"))
        result = restore_service.vram_preflight_gate({}, "3b", "fp16", "a.png", "image")
        assert result is not None
        assert result["risk"] == "low"
        assert result["warning"] == ""

    def test_precision_none_falls_back_fp16_baseline(self, monkeypatch):
        """precision=None 时按 fp16 基线估算（量化变体加载期反量化语义）。"""
        monkeypatch.setattr(restore_service, "_probe_media_geometry", lambda p, t: (800, 600, 1))
        monkeypatch.setattr(restore_service, "get_gpu_memory_info", lambda: _gpu_info())
        captured: dict = {}

        def _fake_estimate(model, precision, w, h, frames):
            captured["precision"] = precision
            return 1.0

        monkeypatch.setattr(restore_service, "estimate_vram_requirements", _fake_estimate)
        monkeypatch.setattr(restore_service, "recommend_params", lambda *a, **k: _recommend("low"))
        restore_service.vram_preflight_gate({}, "3b", None, "a.png", "image")
        assert captured["precision"] == "fp16"


class TestProbeMediaGeometry:
    """_probe_media_geometry 媒体几何探测。"""

    def test_image_probe(self, tmp_path):
        from PIL import Image

        p = tmp_path / "in.png"
        Image.new("RGB", (64, 32)).save(p)
        assert restore_service._probe_media_geometry(str(p), "image") == (64, 32, 1)

    def test_missing_file_returns_none(self, tmp_path):
        assert restore_service._probe_media_geometry(str(tmp_path / "no.png"), "image") is None


@pytest.mark.integration
class TestVramPreflightRouteWiring:
    """提交链路接线测试：门禁拒绝 → 503 信封；medium 风险 → 响应带 vram_warning。"""

    @staticmethod
    def _make_png_folder(tmp_path):
        from PIL import Image

        folder = tmp_path / "media"
        folder.mkdir(exist_ok=True)
        Image.new("RGB", (8, 8)).save(folder / "in.png")
        return str(folder)

    @staticmethod
    def _bypass_disk_preflight(monkeypatch):
        """低磁盘环境（剩余 < disk_min_free_gb）下绕过磁盘预检，聚焦显存门禁接线。"""
        import app.integrated_app.routes.restore.common as common_module

        monkeypatch.setattr(common_module, "ensure_disk_space", lambda *a, **k: None)

    def test_upload_rejected_when_gate_raises(self, test_app, tmp_path, monkeypatch):
        import app.integrated_app.routes.restore.upload as upload_module

        monkeypatch.setattr(upload_module, "gpu_manager", SimpleNamespace(is_gpu_available=True))
        self._bypass_disk_preflight(monkeypatch)

        def _boom(*args, **kwargs):
            raise InsufficientVramError("预估显存 99.0GB 超过当前可用 2.0GB，任务大概率 OOM，已拒绝启动。")

        monkeypatch.setattr(upload_module, "vram_preflight_gate", _boom)
        resp = csrf_post(test_app, "/api/restore/", data={"folder_path": self._make_png_folder(tmp_path)})
        assert resp.status_code == 503
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INSUFFICIENT_VRAM"
        assert "拒绝启动" in body["error"]["message"]

    def test_upload_accepts_with_vram_warning(self, test_app, tmp_path, monkeypatch):
        import app.integrated_app.routes.restore.upload as upload_module

        monkeypatch.setattr(upload_module, "gpu_manager", SimpleNamespace(is_gpu_available=True))
        self._bypass_disk_preflight(monkeypatch)
        monkeypatch.setattr(
            upload_module,
            "vram_preflight_gate",
            lambda *a, **k: {"risk": "medium", "warning": "VRAM 紧张：已开启 BlockSwap，推理速度可能较慢"},
        )
        resp = csrf_post(test_app, "/api/restore/", data={"folder_path": self._make_png_folder(tmp_path)})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["vram_warning"] == "VRAM 紧张：已开启 BlockSwap，推理速度可能较慢"
        assert data["task_id"]

    def test_batch_rejected_when_gate_raises(self, test_app, tmp_path, monkeypatch):
        import app.integrated_app.routes.restore.batch as batch_module

        monkeypatch.setattr(batch_module, "gpu_manager", SimpleNamespace(is_gpu_available=True))
        self._bypass_disk_preflight(monkeypatch)

        def _boom(*args, **kwargs):
            raise InsufficientVramError("预估显存 99.0GB 超过当前可用 2.0GB，任务大概率 OOM，已拒绝启动。")

        monkeypatch.setattr(batch_module, "vram_preflight_gate", _boom)
        resp = csrf_post(test_app, "/api/restore/batch", data={"folder_path": self._make_png_folder(tmp_path)})
        assert resp.status_code == 503
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INSUFFICIENT_VRAM"

    def test_batch_accepts_with_vram_warning(self, test_app, tmp_path, monkeypatch):
        import app.integrated_app.routes.restore.batch as batch_module

        monkeypatch.setattr(batch_module, "gpu_manager", SimpleNamespace(is_gpu_available=True))
        self._bypass_disk_preflight(monkeypatch)
        monkeypatch.setattr(
            batch_module,
            "vram_preflight_gate",
            lambda *a, **k: {"risk": "medium", "warning": "VRAM 紧张：已开启 BlockSwap，推理速度可能较慢"},
        )
        resp = csrf_post(test_app, "/api/restore/batch", data={"folder_path": self._make_png_folder(tmp_path)})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["vram_warning"] == "VRAM 紧张：已开启 BlockSwap，推理速度可能较慢"
        assert data["batch_id"]
