"""GPU 后端管理器单元测试

覆盖 GPUBackend 枚举、GPUInfo 数据类、
GPUBackendManager 管理器的检测/查询/模型加载预检功能。
使用 mock torch.cuda 模拟 GPU 环境。

支持三后端：NVIDIA CUDA / AMD ROCm / Apple Silicon MPS。
"""

from unittest.mock import patch

import torch

from app.integrated_app.gpu_backend import (
    GPUBackend,
    GPUBackendManager,
    GPUInfo,
    _CUDAStrategy,
    _is_rocm_build,
    _MPSStrategy,
    _ROCMStrategy,
)

# ---------------------------------------------------------------------------
# GPUBackend 枚举
# ---------------------------------------------------------------------------


class TestGPUBackend:
    """GPUBackend 枚举测试"""

    def test_cuda_value(self):
        assert GPUBackend.CUDA.value == "cuda"

    def test_unavailable_value(self):
        assert GPUBackend.UNAVAILABLE.value == "unavailable"


# ---------------------------------------------------------------------------
# GPUInfo 数据类
# ---------------------------------------------------------------------------


class TestGPUInfo:
    """GPUInfo 数据类测试"""

    def test_defaults(self):
        info = GPUInfo(
            backend=GPUBackend.CUDA,
            name="Test GPU",
            total_vram_mb=8192,
            available_vram_mb=4096,
            utilization_pct=50.0,
        )
        assert info.driver_version == ""
        assert info.cuda_version == ""

    def test_with_versions(self):
        info = GPUInfo(
            backend=GPUBackend.CUDA,
            name="Test",
            total_vram_mb=8192,
            available_vram_mb=4096,
            utilization_pct=50.0,
            driver_version="535.98",
            cuda_version="12.2",
        )
        assert info.driver_version == "535.98"
        assert info.cuda_version == "12.2"


# ---------------------------------------------------------------------------
# _CUDAStrategy
# ---------------------------------------------------------------------------


class TestCUDAStrategy:
    """_CUDAStrategy 策略测试"""

    def test_device_str(self):
        strategy = _CUDAStrategy()
        assert strategy.device_str() == "cuda"

    def test_detect_with_torch_available(self):
        strategy = _CUDAStrategy()
        with patch("torch.cuda.is_available", return_value=True):
            assert strategy.detect() is True

    def test_detect_with_torch_unavailable(self):
        strategy = _CUDAStrategy()
        with patch("torch.cuda.is_available", return_value=False):
            assert strategy.detect() is False

    def test_is_available_true(self):
        strategy = _CUDAStrategy()
        with patch("torch.cuda.is_available", return_value=True):
            assert strategy.is_available() is True

    def test_is_available_false(self):
        strategy = _CUDAStrategy()
        with patch("torch.cuda.is_available", return_value=False):
            assert strategy.is_available() is False

    def test_get_process_group_backend(self):
        strategy = _CUDAStrategy()
        assert strategy.get_process_group_backend() == "nccl"


# ---------------------------------------------------------------------------
# GPUBackendManager (GPU 不可用场景)
# ---------------------------------------------------------------------------


class TestGPUBackendManagerUnavailable:
    """GPUBackendManager 测试 — GPU 不可用场景"""

    def test_unavailable_when_no_gpu(self):
        with patch.object(_CUDAStrategy, "detect", return_value=False):
            manager = GPUBackendManager()
            assert manager.is_gpu_available is False
            assert manager.backend == GPUBackend.UNAVAILABLE
            assert manager.device_str == "cpu"

    def test_can_load_model_false_when_unavailable(self):
        with patch.object(_CUDAStrategy, "detect", return_value=False):
            manager = GPUBackendManager()
            assert manager.can_load_model(required_vram_mb=100) is False

    def test_get_gpu_info_unavailable(self):
        with patch.object(_CUDAStrategy, "detect", return_value=False):
            manager = GPUBackendManager()
            info = manager.get_gpu_info()
            assert info.backend == GPUBackend.UNAVAILABLE

    def test_device_name_unavailable(self):
        with patch.object(_CUDAStrategy, "detect", return_value=False):
            manager = GPUBackendManager()
            assert "GPU" in manager.device_name or "NVIDIA" in manager.device_name


# ---------------------------------------------------------------------------
# GPUBackendManager (CUDA 可用场景)
# ---------------------------------------------------------------------------


class TestGPUBackendManagerCUDA:
    """GPUBackendManager 测试 — CUDA 可用场景"""

    @patch.object(_CUDAStrategy, "detect", return_value=True)
    @patch.object(
        _CUDAStrategy,
        "get_info",
        return_value={
            "name": "RTX 4090",
            "total_vram": 25769803776,
            "available_vram_mb": 20000,
            "utilization": 22.0,
            "cuda_version": "12.4",
        },
    )
    def test_detects_cuda_when_available(self, _mock_info, _mock_detect):
        manager = GPUBackendManager()
        assert manager.is_gpu_available is True
        assert manager.backend == GPUBackend.CUDA
        assert manager.device_str == "cuda"

    @patch.object(_CUDAStrategy, "detect", return_value=True)
    @patch.object(
        _CUDAStrategy,
        "get_info",
        return_value={
            "name": "RTX 4090",
            "total_vram": 25769803776,
            "available_vram_mb": 20000,
            "utilization": 22.0,
            "cuda_version": "12.4",
        },
    )
    def test_can_load_model_sufficient_vram(self, _mock_info, _mock_detect):
        manager = GPUBackendManager()
        assert manager.can_load_model(required_vram_mb=8000) is True

    @patch.object(_CUDAStrategy, "detect", return_value=True)
    @patch.object(
        _CUDAStrategy,
        "get_info",
        return_value={
            "name": "GT 710",
            "total_vram": 2147483648,
            "available_vram_mb": 500,
            "utilization": 75.0,
            "cuda_version": "11.8",
        },
    )
    def test_can_load_model_insufficient_vram(self, _mock_info, _mock_detect):
        manager = GPUBackendManager()
        assert manager.can_load_model(required_vram_mb=2000) is False

    @patch.object(_CUDAStrategy, "detect", return_value=True)
    @patch.object(
        _CUDAStrategy,
        "get_info",
        return_value={
            "name": "RTX 4090",
            "total_vram": 25769803776,
            "available_vram_mb": 20000,
            "utilization": 22.0,
            "cuda_version": "12.4",
        },
    )
    def test_get_gpu_info_available(self, _mock_info, _mock_detect):
        manager = GPUBackendManager()
        info = manager.get_gpu_info()
        assert info.backend == GPUBackend.CUDA
        assert info.name == "RTX 4090"

    @patch.object(_CUDAStrategy, "detect", return_value=True)
    @patch.object(
        _CUDAStrategy,
        "get_info",
        return_value={
            "name": "RTX 4090",
            "total_vram": 25769803776,
            "available_vram_mb": 20000,
            "utilization": 22.0,
            "cuda_version": "12.4",
        },
    )
    def test_get_gpu_info_caches(self, _mock_info, _mock_detect):
        manager = GPUBackendManager()
        info1 = manager.get_gpu_info()
        info2 = manager.get_gpu_info()
        assert info1 == info2
        # get_info should only be called once during detect + once for info
        # due to caching


# ---------------------------------------------------------------------------
# ROCm 识别（AMD GPU / torch.version.hip 非空）
# ---------------------------------------------------------------------------


class TestRocmBuildDetection:
    """ROCm 构建识别测试（HIP 版 PyTorch 以 cuda API 暴露设备）"""

    def test_is_rocm_build_with_hip_version(self):
        with patch.object(torch.version, "hip", "6.2"):
            assert _is_rocm_build() is True

    def test_is_rocm_build_without_hip(self):
        with patch.object(torch.version, "hip", None):
            assert _is_rocm_build() is False

    def test_cuda_strategy_excludes_rocm_build(self):
        """CUDA 策略在 HIP 构建下必须返回 False（由 ROCM 策略接管）"""
        strategy = _CUDAStrategy()
        with patch("torch.cuda.is_available", return_value=True), patch.object(torch.version, "hip", "6.2"):
            assert strategy.detect() is False

    def test_rocm_strategy_detects_hip_build(self):
        strategy = _ROCMStrategy()
        with patch("torch.cuda.is_available", return_value=True), patch.object(torch.version, "hip", "6.2"):
            assert strategy.detect() is True
            assert strategy.device_str() == "cuda"
            assert strategy.get_process_group_backend() == "nccl"

    def test_rocm_strategy_negative_without_hip(self):
        strategy = _ROCMStrategy()
        with patch("torch.cuda.is_available", return_value=True), patch.object(torch.version, "hip", None):
            assert strategy.detect() is False


# ---------------------------------------------------------------------------
# MPS 策略（Apple Silicon）
# ---------------------------------------------------------------------------


class TestMPSStrategy:
    """Apple Silicon MPS 策略测试"""

    def _mps_patch(self):
        return (
            patch("torch.backends.mps.is_available", return_value=True),
            patch("torch.backends.mps.is_built", return_value=True),
        )

    def test_device_str(self):
        strategy = _MPSStrategy()
        assert strategy.device_str() == "mps"

    def test_detect_available(self):
        strategy = _MPSStrategy()
        with self._mps_patch()[0], self._mps_patch()[1]:
            assert strategy.detect() is True

    def test_detect_unavailable(self):
        strategy = _MPSStrategy()
        with patch("torch.backends.mps.is_available", return_value=False):
            assert strategy.detect() is False

    def test_get_process_group_backend(self):
        strategy = _MPSStrategy()
        assert strategy.get_process_group_backend() == "gloo"


# ---------------------------------------------------------------------------
# GPUBackendManager（ROCm 可用场景）
# ---------------------------------------------------------------------------


class TestGPUBackendManagerROCM:
    """GPUBackendManager 测试 — AMD ROCm 可用场景"""

    @patch.object(_CUDAStrategy, "detect", return_value=False)  # CUDA 策略排除 HIP 构建
    @patch.object(_MPSStrategy, "detect", return_value=False)
    @patch.object(
        _ROCMStrategy,
        "get_info",
        return_value={
            "name": "AMD Radeon RX 7900 XTX",
            "total_vram": 25769803776,
            "available_vram_mb": 20000,
            "utilization": 22.0,
            "cuda_version": "ROCm/HIP 6.2",
        },
    )
    def test_detects_rocm_when_available(self, _mock_info, _mock_mps, _mock_cuda):
        with patch.object(_ROCMStrategy, "detect", return_value=True):
            manager = GPUBackendManager()
            assert manager.is_gpu_available is True
            assert manager.backend == GPUBackend.ROCM
            assert manager.device_str == "cuda"
            info = manager.get_gpu_info()
            assert info.backend == GPUBackend.ROCM
            assert "ROCm" in info.cuda_version


# ---------------------------------------------------------------------------
# GPUBackendManager（MPS 可用场景）
# ---------------------------------------------------------------------------


class TestGPUBackendManagerMPS:
    """GPUBackendManager 测试 — Apple Silicon MPS 可用场景"""

    @patch.object(_CUDAStrategy, "detect", return_value=False)
    @patch.object(_ROCMStrategy, "detect", return_value=False)
    @patch.object(
        _MPSStrategy,
        "get_info",
        return_value={
            "name": "Apple M3 (MPS)",
            "total_vram": 17179869184,  # 16GB 统一内存
            "available_vram_mb": 12000,
            "utilization": 30.0,
            "cuda_version": "",
        },
    )
    def test_detects_mps_when_available(self, _mock_info, _mock_rocm, _mock_cuda):
        with patch.object(_MPSStrategy, "detect", return_value=True):
            manager = GPUBackendManager()
            assert manager.is_gpu_available is True
            assert manager.backend == GPUBackend.MPS
            assert manager.device_str == "mps"
            info = manager.get_gpu_info()
            assert info.backend == GPUBackend.MPS
            assert info.total_vram_mb == 16384

    def test_mps_check_health_false_when_unavailable(self):
        strategy = _MPSStrategy()
        with patch("torch.backends.mps.is_available", return_value=False):
            assert strategy.check_health() is False
