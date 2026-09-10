#!/usr/bin/env python3
"""显存预检门禁测试（成本治理 P1-2）。

覆盖评估报告改进建议 #1 的验收标准 + 「检测过于激进导致完全无法使用」修复：
- vram_preflight_gate：开关关闭 / 媒体探测失败 / 无 GPU 时 fail-open 放行；
  仅当**最大降级组合仍超预算**（risk=high 且 degraded_vram > 预算）时抛
  InsufficientVramError（detail 含估算值/降级值/预算/持有精度/推荐参数）；
  估算超预算但可降级容纳 → 放行并携带 warning；risk=medium → 转达推荐降档建议；
  预算 = available + allocated（避免已加载权重二次扣减）。
- 精度回退链复刻：用户所选精度文件不存在时（如只有 nvfp4），按**实际会加载的精度**
  估算，且推荐结果只会落在磁盘真实持有的精度集合内。
- 量化包（mxfp8/int8_convrot/nvfp4）显存语义：加载期反量化、权重以 fp16 驻留，
  估算与 fp16 同档（只有真 fp8 减半）。
- 路由接线：/api/restore/ 与 /api/restore/batch 在门禁拒绝时返回 503 +
  INSUFFICIENT_VRAM 错误信封，放行带 warning 时响应携带 vram_warning。

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


def _recommend(risk: str = "low", warning: str = "", precision: str = "fp8") -> dict:
    return {
        "precision": precision,
        "enable_blockswap": risk in ("medium", "high"),
        "blocks_to_swap": 28 if risk in ("medium", "high") else 0,
        "tile_size": 512,
        "vram_tile_overlap": 128,
        "estimated_vram_gb": 6.0,
        "available_vram_gb": 2.0,
        "risk": risk,
        "warning": warning,
    }


class _FakeManager:
    """只实现 check_model_exists 的假 ModelManager（按给定精度集合应答）。"""

    def __init__(self, owned: set[str]):
        self._owned = owned

    def check_model_exists(self, size: str, precision: str | None = None) -> bool:
        return precision in self._owned


class TestQuantizedPrecisionSemantics:
    """量化包的显存语义：加载期反量化 → 驻留≈fp16，估算不减半。"""

    def test_nvfp4_same_as_fp16(self):
        from app.integrated_app import gpu_utils

        fp16 = gpu_utils.estimate_vram_requirements("3b", "fp16", 1920, 1080)
        for p in ("nvfp4", "mxfp8", "int8_convrot"):
            assert gpu_utils.estimate_vram_requirements("3b", p, 1920, 1080) == fp16

    def test_real_fp8_still_lighter(self):
        from app.integrated_app import gpu_utils

        assert gpu_utils.estimate_vram_requirements("3b", "fp8", 1920, 1080) < gpu_utils.estimate_vram_requirements(
            "3b", "fp16", 1920, 1080
        )

    def test_blocks_to_swap_reduces_estimate(self):
        """BlockSwap 换出越多块，估算下降越多（按换出比例线性削减权重驻留）。"""
        from app.integrated_app import gpu_utils

        full = gpu_utils.estimate_vram_requirements("3b", "fp16", 2048, 2048)
        swapped = gpu_utils.estimate_vram_requirements("3b", "fp16", 2048, 2048, blocks_to_swap=28)
        assert swapped < full
        # 削减量 = 权重基线 × (换出块数 / 总块数)，与公共 helper 完全一致
        expected_reduction = gpu_utils.blockswap_reduction_gb("3b", "fp16", 28)
        assert full - swapped == pytest.approx(expected_reduction, abs=0.02)
        # 换出更多块应削减更多（P1-6 修正：不再固定砍 50%）
        swapped_fewer = gpu_utils.estimate_vram_requirements("3b", "fp16", 2048, 2048, blocks_to_swap=14)
        assert (full - swapped_fewer) < (full - swapped)
        # 且削减量近似与换出块数成正比（28/14 ≈ 2x）
        ratio = (full - swapped) / (full - swapped_fewer)
        assert ratio == pytest.approx(28 / 14, rel=0.05)


class TestRecommendParamsPrecisionAvailability:
    """recommend_params 只推荐磁盘真实持有的降档台阶。"""

    def test_fp8_not_recommended_when_absent(self):
        """只有量化包时：fp8 不是省显存台阶，改推 BlockSwap（risk=medium）。"""
        from app.integrated_app import gpu_utils

        result = gpu_utils.recommend_params(
            "3b", 1024, 1024, num_frames=1, available_vram_gb=10.75, available_precisions=["nvfp4"]
        )
        assert result["precision"] == "nvfp4"
        assert result["enable_blockswap"] is True
        assert result["risk"] == "medium"
        assert "建议开启 BlockSwap" in result["warning"]

    def test_fp8_recommended_when_owned(self):
        from app.integrated_app import gpu_utils

        result = gpu_utils.recommend_params(
            "3b", 1024, 1024, num_frames=1, available_vram_gb=10.75, available_precisions=["fp8", "nvfp4"]
        )
        assert result["precision"] == "fp8"
        assert result["risk"] == "low"
        assert result["warning"] == ""

    def test_none_keeps_legacy_semantics(self):
        """available_precisions=None（CI/未知磁盘）→ 沿用旧的 fp16/fp8 阶梯。"""
        from app.integrated_app import gpu_utils

        result = gpu_utils.recommend_params("3b", 1920, 1080, num_frames=1, available_vram_gb=10.0)
        assert result["precision"] == "fp8"
        assert result["risk"] == "low"


class TestEffectivePrecisionResolution:
    """加载期精度回退链复刻（与 model_manager._load_model_locked 对齐）。"""

    def test_requested_owned_is_kept(self):
        mgr = _FakeManager({"fp16", "nvfp4"})
        assert restore_service._resolve_effective_precision(mgr, "3b", "fp16", ["fp16", "nvfp4"]) == "fp16"

    def test_fp16_falls_back_to_fp8_counterpart(self):
        owned = ["fp8", "nvfp4"]
        assert restore_service._resolve_effective_precision(None, "3b", "fp16", owned) == "fp8"

    def test_missing_quantized_falls_back_to_first_owned(self):
        """只有 nvfp4 时，无论所选是 fp16 还是 mxfp8，实际加载都是 nvfp4。"""
        owned = ["nvfp4"]
        assert restore_service._resolve_effective_precision(None, "3b", "mxfp8", owned) == "nvfp4"

    def test_owned_none_means_unrestricted(self):
        assert restore_service._resolve_effective_precision(None, "3b", "fp16", None) == "fp16"

    def test_list_owned_precisions_swallows_probe_errors(self):
        class _Boom:
            def check_model_exists(self, size, precision=None):
                raise RuntimeError("磁盘探测失败")

        assert restore_service._list_owned_precisions(_Boom(), "3b") is None

    def test_list_owned_precisions_empty_returns_none(self):
        assert restore_service._list_owned_precisions(_FakeManager(set()), "3b") is None


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

    def test_passes_when_degraded_plan_fits_despite_over_budget_estimate(self, monkeypatch):
        """核心回归：估算 100GB 但降级方案 1GB 能装下 → 放行（旧实现直接 503）。"""
        monkeypatch.setattr(restore_service, "_probe_media_geometry", lambda p, t: (1920, 1080, 1))
        monkeypatch.setattr(restore_service, "get_gpu_memory_info", lambda: _gpu_info())
        monkeypatch.setattr(restore_service, "estimate_vram_requirements", lambda *a, **k: 100.0)
        monkeypatch.setattr(restore_service, "_max_degraded_fit", lambda *a, **k: 1.0)
        monkeypatch.setattr(
            restore_service, "recommend_params", lambda *a, **k: _recommend("medium", "建议开启 BlockSwap")
        )
        result = restore_service.vram_preflight_gate({}, "7b", "fp16", "a.png", "image")
        assert result is not None
        assert result["risk"] == "medium"
        assert "建议开启 BlockSwap" in result["warning"]

    def test_reject_when_degraded_plan_still_exceeds_budget(self, monkeypatch):
        """最大降级仍超预算 → InsufficientVramError（detail 含降级估算与持有精度）。"""
        monkeypatch.setattr(restore_service, "_probe_media_geometry", lambda p, t: (1920, 1080, 1))
        monkeypatch.setattr(restore_service, "get_gpu_memory_info", lambda: _gpu_info())
        monkeypatch.setattr(restore_service, "estimate_vram_requirements", lambda *a, **k: 100.0)
        monkeypatch.setattr(restore_service, "_max_degraded_fit", lambda *a, **k: 99.0)
        monkeypatch.setattr(restore_service, "recommend_params", lambda *a, **k: _recommend("high"))
        with pytest.raises(InsufficientVramError) as exc_info:
            restore_service.vram_preflight_gate({}, "7b", "fp16", "a.png", "image", model_manager=_FakeManager({"fp8"}))
        detail = exc_info.value.detail
        assert detail["estimated_vram_gb"] == 100.0
        assert detail["degraded_vram_gb"] == 99.0
        assert detail["available_vram_gb"] == 2.0  # (1024 + 1024) / 1024
        assert detail["owned_precisions"] == ["fp8"]
        assert "BlockSwap" in exc_info.value.message

    def test_medium_risk_passes_with_warning(self, monkeypatch):
        monkeypatch.setattr(restore_service, "_probe_media_geometry", lambda p, t: (1920, 1080, 1))
        monkeypatch.setattr(restore_service, "get_gpu_memory_info", lambda: _gpu_info())
        monkeypatch.setattr(restore_service, "estimate_vram_requirements", lambda *a, **k: 1.5)
        monkeypatch.setattr(restore_service, "_max_degraded_fit", lambda *a, **k: 1.0)
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
        monkeypatch.setattr(restore_service, "_max_degraded_fit", lambda *a, **k: 1.0)
        monkeypatch.setattr(restore_service, "recommend_params", lambda *a, **k: _recommend("low"))
        result = restore_service.vram_preflight_gate({}, "3b", "fp16", "a.png", "image")
        assert result is not None
        assert result["risk"] == "low"
        assert result["warning"] == ""

    def test_precision_none_falls_back_fp16_baseline(self, monkeypatch):
        """precision=None 且无 manager 时按 fp16 基线估算（量化变体加载期反量化语义）。"""
        monkeypatch.setattr(restore_service, "_probe_media_geometry", lambda p, t: (800, 600, 1))
        monkeypatch.setattr(restore_service, "get_gpu_memory_info", lambda: _gpu_info())
        captured: dict = {}

        def _fake_estimate(model, precision, w, h, frames, swap=0):
            captured["precision"] = precision
            captured["swap"] = swap
            return 1.0

        monkeypatch.setattr(restore_service, "estimate_vram_requirements", _fake_estimate)
        monkeypatch.setattr(restore_service, "_max_degraded_fit", lambda *a, **k: 1.0)
        monkeypatch.setattr(restore_service, "recommend_params", lambda *a, **k: _recommend("low"))
        restore_service.vram_preflight_gate({}, "3b", None, "a.png", "image", blocks_to_swap=16)
        assert captured["precision"] == "fp16"
        assert captured["swap"] == 16

    def test_estimates_with_effective_precision_and_warns_substitution(self, monkeypatch):
        """所选精度不存在时按实际精度估算，并把替换事实告诉用户（12GB + 仅 nvfp4 的真实场景）。"""
        monkeypatch.setattr(restore_service, "_probe_media_geometry", lambda p, t: (1024, 1024, 1))
        monkeypatch.setattr(restore_service, "get_gpu_memory_info", lambda: _gpu_info(11008, 0, 12227))
        monkeypatch.setattr(restore_service, "_max_degraded_fit", lambda *a, **k: 8.01)
        monkeypatch.setattr(
            restore_service, "recommend_params", lambda *a, **k: _recommend("medium", "建议开启 BlockSwap", "nvfp4")
        )
        result = restore_service.vram_preflight_gate(
            {}, "3b", "fp16", "a.png", "image", blocks_to_swap=32, model_manager=_FakeManager({"nvfp4"})
        )
        assert result is not None
        assert result["requested_precision"] == "fp16"
        assert result["effective_precision"] == "nvfp4"
        assert "自动改用已下载的 nvfp4" in result["warning"]
        # 真实估算链路：3b 量化包按 fp16 档基线 16GB，换出 32 块削减约 8GB → ≈8GB < 10.75GB 预算
        assert result["estimated_vram_gb"] < result["available_vram_gb"]


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
    """提交链路接线测试：门禁拒绝 → 503 信封；放行带 warning → 响应携带 vram_warning。"""

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
            raise InsufficientVramError("当前可用显存 2.0GB 不足以完成本次任务：即使开到最大 BlockSwap 仍需约 99.0GB。")

        monkeypatch.setattr(upload_module, "vram_preflight_gate", _boom)
        resp = csrf_post(test_app, "/api/restore/", data={"folder_path": self._make_png_folder(tmp_path)})
        assert resp.status_code == 503
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INSUFFICIENT_VRAM"

    def test_upload_accepts_with_vram_warning(self, test_app, tmp_path, monkeypatch):
        import app.integrated_app.routes.restore.upload as upload_module

        monkeypatch.setattr(upload_module, "gpu_manager", SimpleNamespace(is_gpu_available=True))
        self._bypass_disk_preflight(monkeypatch)
        monkeypatch.setattr(
            upload_module,
            "vram_preflight_gate",
            lambda *a, **k: {"risk": "medium", "warning": "显存偏紧：建议开启 BlockSwap，推理速度会明显变慢"},
        )
        resp = csrf_post(test_app, "/api/restore/", data={"folder_path": self._make_png_folder(tmp_path)})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["vram_warning"] == "显存偏紧：建议开启 BlockSwap，推理速度会明显变慢"
        assert data["task_id"]

    def test_upload_passes_blocks_to_swap_and_manager_to_gate(self, test_app, tmp_path, monkeypatch):
        """接线回归：门禁必须收到「实际生效的换出块数」与 model_manager，否则又会按零换出高估。"""
        import app.integrated_app.routes.restore.upload as upload_module

        monkeypatch.setattr(upload_module, "gpu_manager", SimpleNamespace(is_gpu_available=True))
        self._bypass_disk_preflight(monkeypatch)
        captured: dict = {}

        def _capture(*args, **kwargs):
            captured.update(kwargs)
            return {"risk": "low", "warning": ""}

        monkeypatch.setattr(upload_module, "vram_preflight_gate", _capture)
        resp = csrf_post(
            test_app,
            "/api/restore/",
            data={"folder_path": self._make_png_folder(tmp_path), "blocks_to_swap": "12"},
        )
        assert resp.status_code == 200
        assert captured["blocks_to_swap"] >= 12
        assert captured["model_manager"] is not None

    def test_batch_rejected_when_gate_raises(self, test_app, tmp_path, monkeypatch):
        import app.integrated_app.routes.restore.batch as batch_module

        monkeypatch.setattr(batch_module, "gpu_manager", SimpleNamespace(is_gpu_available=True))
        self._bypass_disk_preflight(monkeypatch)

        def _boom(*args, **kwargs):
            raise InsufficientVramError("当前可用显存 2.0GB 不足以完成本次任务：即使开到最大 BlockSwap 仍需约 99.0GB。")

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
            lambda *a, **k: {"risk": "medium", "warning": "显存偏紧：建议开启 BlockSwap，推理速度会明显变慢"},
        )
        resp = csrf_post(test_app, "/api/restore/batch", data={"folder_path": self._make_png_folder(tmp_path)})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["vram_warning"] == "显存偏紧：建议开启 BlockSwap，推理速度会明显变慢"
        assert data["batch_id"]
