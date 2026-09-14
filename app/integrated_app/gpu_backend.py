"""GPU 后端抽象层模块 - SeedVR2 视频修复项目

本模块提供 GPU 后端的抽象与统一管理接口，采用 Strategy 设计模式实现后端分发，
避免冗长的 if/elif 条件链。支持三种计算后端：

- NVIDIA CUDA（``cuda``）：主力计算后端
- AMD ROCm/HIP（``rocm``）：ROCm 版 PyTorch 以 ``cuda`` 设备字符串呈现，通过
  ``torch.version.hip`` 区分
- Apple Silicon MPS（``mps``）：Metal Performance Shaders，统一内存架构

未检测到可用 GPU 时进入降级模式（推理功能不可用）。

所属项目: SeedVR2 (基于 ComfyUI-SeedVR2_VideoUpscaler 独立重构)
核心技术栈: PyTorch, CUDA/ROCm/MPS, Strategy Pattern, ABC 抽象基类

注意事项:
    - SeedVR2 模型支持 NVIDIA CUDA / AMD ROCm / Apple Silicon MPS 推理
      （上游 ComfyUI-SeedVR2_VideoUpscaler 明确支持三后端）
    - 启动时自动检测可用 GPU 后端，检测失败时记录警告并进入降级模式
    - 全局单例 `gpu_manager` 供应用各模块统一调用
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from app.integrated_app.optimization.gpu.nvml_monitor import query_gpu_utilization

logger = logging.getLogger(__name__)

_GPU_INFO_CACHE_TTL = 0.5
_MODEL_LOAD_CACHE_TTL = 0.5
_VRAM_SAFETY_MARGIN = 1.1


class GPUBackend(Enum):
    """支持的 GPU 后端类型枚举

    Attributes:
        CUDA: NVIDIA CUDA 后端（主力计算后端）
        ROCM: AMD ROCm/HIP 后端（ROCm 版 PyTorch）
        MPS: Apple Silicon MPS 后端（Metal Performance Shaders）
        UNAVAILABLE: 未检测到可用 GPU（降级模式，推理不可用）
    """

    CUDA = "cuda"  # NVIDIA GPUs
    ROCM = "rocm"  # AMD GPUs (ROCm/HIP)
    MPS = "mps"  # Apple Silicon (Metal)
    UNAVAILABLE = "unavailable"  # 未检测到可用 GPU（降级模式）


@dataclass
class GPUInfo:
    """GPU 硬件信息数据类

    存储 GPU 设备的完整硬件信息，包括显存、利用率、驱动版本等，
    用于系统状态展示、模型加载预检和兼容性判断。

    Attributes:
        backend: GPU 后端类型
        name: GPU 设备名称（如 "NVIDIA GeForce RTX 4090" / "AMD Radeon RX 7900 XTX"）
        total_vram_mb: 总显存大小（MB；MPS 为统一内存，取系统物理内存）
        available_vram_mb: 当前可用显存（MB）
        utilization_pct: 当前显存利用率百分比（0-100）
        driver_version: GPU 驱动版本号（暂未实现）
        cuda_version: CUDA 运行时版本号（ROCm 下为 HIP 版本信息）
        sm_utilization_pct: SM 真实利用率（P2-1，仅 NVIDIA nvidia-smi 可查询）；
            查询不可用或非 NVIDIA 后端时为 None
        temperature_c: GPU 温度（摄氏度，P2-1）；查询不可用或非 NVIDIA 后端时为 None
    """

    backend: GPUBackend
    name: str
    total_vram_mb: int
    available_vram_mb: int
    utilization_pct: float
    driver_version: str = ""
    cuda_version: str = ""
    sm_utilization_pct: float | None = None
    temperature_c: float | None = None


class _GPUStrategy(ABC):
    """GPU 后端策略抽象基类

    定义所有 GPU 后端策略必须实现的统一接口，遵循 Strategy 设计模式。
    每个具体后端（如 CUDA / ROCM / MPS）需继承此类并实现所有抽象方法。

    Note:
        这是内部抽象基类，不应直接实例化，应通过 GPUBackendManager 使用。
    """

    @abstractmethod
    def detect(self) -> bool:
        """检测此后端在当前系统中是否可用

        Returns:
            bool: 后端可用返回 True，否则返回 False
        """
        ...

    @abstractmethod
    def device_str(self) -> str:
        """返回 PyTorch 设备字符串标识

        Returns:
            str: PyTorch 设备字符串，如 "cuda"、"mps"、"cpu"
        """
        ...

    @abstractmethod
    def get_info(self) -> dict:
        """获取 GPU 硬件详细信息字典

        Returns:
            dict: 包含设备名称、显存、CUDA版本等信息的字典
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """检查此后端当前是否可用（运行时检查）

        Returns:
            bool: 当前可用返回 True，否则返回 False
        """
        ...

    def synchronize(self) -> None:
        """同步当前设备，等待所有 GPU 操作完成

        Raises:
            NotImplementedError: 默认实现抛出异常，子类需覆盖
        """
        raise NotImplementedError

    def check_health(self) -> bool:
        """运行时健康探测（评估 P2-4：能力可用 ≠ 上下文健康）

        默认实现返回 False（未检测到可探测的后端）。就绪探针仅在
        is_gpu_available 为 True 时才消费本结果，此时具体策略必然存在。

        Returns:
            bool: 后端运行时健康返回 True，上下文损坏/探测异常返回 False
        """
        return False

    def get_process_group_backend(self) -> str:
        """获取分布式训练进程组通信后端

        Returns:
            str: 进程组后端名称，默认返回 "gloo"（CPU 通信后端）
        """
        return "gloo"


def _is_rocm_build() -> bool:
    """判断当前 PyTorch 是否为 ROCm（HIP）构建。

    ROCm 版 PyTorch 的 ``torch.cuda.is_available()`` 同样返回 True（HIP 模拟
    CUDA API），需通过 ``torch.version.hip`` 非空来区分 NVIDIA CUDA 与 AMD ROCm。

    Returns:
        bool: ROCm 构建返回 True；否则返回 False
    """
    try:
        import torch

        return bool(getattr(torch.version, "hip", None))
    except ImportError:
        return False


class _CUDAStrategy(_GPUStrategy):
    """NVIDIA CUDA 后端具体策略实现

    实现 NVIDIA CUDA GPU 的检测、设备管理和信息查询功能。
    使用 PyTorch CUDA API 与 GPU 交互。ROCm 构建由 _ROCMStrategy 接管。
    """

    def detect(self) -> bool:
        """检测 CUDA 后端是否可用

        尝试导入 torch 并调用 torch.cuda.is_available() 检测；
        ROCm（HIP）构建由 _ROCMStrategy 接管，不在此判定。

        Returns:
            bool: CUDA 可用返回 True，torch 未安装或 CUDA 不可用返回 False
        """
        try:
            import torch

            return torch.cuda.is_available() and not _is_rocm_build()
        except ImportError:
            return False

    def device_str(self) -> str:
        """返回 CUDA 设备字符串

        Returns:
            str: 固定返回 "cuda"
        """
        return "cuda"

    def get_info(self) -> dict:
        """获取 CUDA GPU 详细硬件信息

        使用 PyTorch CUDA API 查询设备 0 的显存、名称、利用率等信息。

        Returns:
            dict: 包含以下键的字典:
                - name (str): GPU 设备名称
                - total_vram (int): 总显存（字节）
                - available_vram_mb (int): 可用显存（MB）
                - utilization (float): 显存利用率百分比
                - cuda_version (str): CUDA 版本字符串

        Raises:
            ImportError: PyTorch 未安装
            RuntimeError: CUDA 运行时错误或设备不可访问
            AssertionError: CUDA 设备断言失败
        """
        try:
            import torch
        except ImportError as e:
            logger.error(f"PyTorch 未安装，无法获取 CUDA 信息: {e}")
            raise

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA 当前不可用")

        try:
            name = torch.cuda.get_device_name(0)
        except RuntimeError as e:
            logger.error(f"获取 CUDA 设备名称失败: {e}")
            raise RuntimeError(f"无法获取 GPU 设备名称: {e}") from e

        try:
            props = torch.cuda.get_device_properties(0)
            total_vram = props.total_memory
        except RuntimeError as e:
            logger.error(f"获取 CUDA 设备属性失败: {e}")
            raise RuntimeError(f"无法获取 GPU 设备属性: {e}") from e

        try:
            free_memory, total_memory = torch.cuda.mem_get_info(0)
        except RuntimeError as e:
            logger.error(f"获取 CUDA 显存信息失败: {e}")
            raise RuntimeError(f"无法获取 GPU 显存信息: {e}") from e

        used = total_memory - free_memory
        available_vram_mb = free_memory // (1024 * 1024)
        utilization = (used / total_memory) * 100 if total_memory > 0 else 0
        cuda_version = torch.version.cuda or ""

        return {
            "name": name,
            "total_vram": total_vram,
            "available_vram_mb": available_vram_mb,
            "utilization": utilization,
            "cuda_version": cuda_version,
        }

    def is_available(self) -> bool:
        """运行时检查 CUDA 是否可用

        Returns:
            bool: CUDA 当前可用返回 True
        """
        try:
            import torch

            return torch.cuda.is_available() and not _is_rocm_build()
        except ImportError:
            return False

    def check_health(self) -> bool:
        """运行时健康探测：CUDA 上下文可响应且显存可查询（评估 P2-4）

        torch.cuda.is_available() 在 CUDA 上下文损坏（OOM 后驱动状态异常、
        驱动崩溃恢复中）时可能仍返回 True，表现为「健康但所有任务失败」的
        假阳性。mem_get_info 需要与驱动真实交互，能暴露这类损坏。

        Returns:
            bool: 上下文健康返回 True；探测异常返回 False（探针永不抛异常）
        """
        try:
            import torch

            if not torch.cuda.is_available():
                return False
            torch.cuda.mem_get_info(0)
            return True
        except Exception as e:  # noqa: BLE001 — 探针必须无异常收敛为 False
            logger.warning(f"CUDA 健康探测失败，判定 GPU 不健康: {e}")
            return False

    def synchronize(self) -> None:
        """同步 CUDA 设备，阻塞等待所有 GPU 操作完成

        用于计时或确保内存操作完成的精确同步点。
        """
        import torch

        torch.cuda.synchronize()

    def get_process_group_backend(self) -> str:
        """获取 CUDA 对应的分布式进程组后端

        Returns:
            str: 返回 "nccl"（NVIDIA Collective Communications Library）
        """
        return "nccl"


class _ROCMStrategy(_GPUStrategy):
    """AMD ROCm/HIP 后端具体策略实现

    实现 AMD GPU 的检测、设备管理和信息查询功能。ROCm 版 PyTorch 将 HIP
    设备以 CUDA API 形式暴露（torch.cuda.*），设备字符串同样为 "cuda"，
    因此显存/名称查询与 CUDA 策略共用 PyTorch API，仅在检测与文案上区分。
    """

    def detect(self) -> bool:
        """检测 ROCm 后端是否可用

        ROCm 构建的 PyTorch 中 torch.cuda.is_available() 返回 True，
        且 torch.version.hip 非空。

        Returns:
            bool: ROCm 可用返回 True，否则返回 False
        """
        try:
            import torch

            return torch.cuda.is_available() and _is_rocm_build()
        except ImportError:
            return False

    def device_str(self) -> str:
        """返回 ROCm 设备字符串

        Returns:
            str: 返回 "cuda"（ROCm 版 PyTorch 的设备标识为 cuda）
        """
        return "cuda"

    def get_info(self) -> dict:
        """获取 AMD GPU 详细硬件信息（经 PyTorch HIP API）

        Returns:
            dict: 与 CUDA 策略同构的信息字典（name/total_vram/
                available_vram_mb/utilization/cuda_version）。

        Raises:
            ImportError: PyTorch 未安装
            RuntimeError: ROCm 运行时错误或设备不可访问
        """
        try:
            import torch
        except ImportError as e:
            logger.error(f"PyTorch 未安装，无法获取 ROCm 信息: {e}")
            raise

        if not torch.cuda.is_available():
            raise RuntimeError("ROCm 当前不可用")

        try:
            name = torch.cuda.get_device_name(0)
        except RuntimeError as e:
            logger.error(f"获取 AMD 设备名称失败: {e}")
            raise RuntimeError(f"无法获取 GPU 设备名称: {e}") from e

        try:
            props = torch.cuda.get_device_properties(0)
            total_vram = props.total_memory
        except RuntimeError as e:
            logger.error(f"获取 AMD 设备属性失败: {e}")
            raise RuntimeError(f"无法获取 GPU 设备属性: {e}") from e

        try:
            free_memory, total_memory = torch.cuda.mem_get_info(0)
        except RuntimeError as e:
            logger.error(f"获取 AMD 显存信息失败: {e}")
            raise RuntimeError(f"无法获取 GPU 显存信息: {e}") from e

        used = total_memory - free_memory
        available_vram_mb = free_memory // (1024 * 1024)
        utilization = (used / total_memory) * 100 if total_memory > 0 else 0
        hip_version = getattr(torch.version, "hip", None) or ""

        return {
            "name": name,
            "total_vram": total_vram,
            "available_vram_mb": available_vram_mb,
            "utilization": utilization,
            "cuda_version": f"ROCm/HIP {hip_version}" if hip_version else "",
        }

    def is_available(self) -> bool:
        """运行时检查 ROCm 是否可用

        Returns:
            bool: ROCm 当前可用返回 True
        """
        try:
            import torch

            return torch.cuda.is_available() and _is_rocm_build()
        except ImportError:
            return False

    def check_health(self) -> bool:
        """运行时健康探测：HIP 上下文可响应且显存可查询

        Returns:
            bool: 上下文健康返回 True；探测异常返回 False（探针永不抛异常）
        """
        try:
            import torch

            if not torch.cuda.is_available():
                return False
            torch.cuda.mem_get_info(0)
            return True
        except Exception as e:  # noqa: BLE001 — 探针必须无异常收敛为 False
            logger.warning(f"ROCm 健康探测失败，判定 GPU 不健康: {e}")
            return False

    def synchronize(self) -> None:
        """同步 HIP 设备，阻塞等待所有 GPU 操作完成"""
        import torch

        torch.cuda.synchronize()

    def get_process_group_backend(self) -> str:
        """获取 ROCm 对应的分布式进程组后端

        Returns:
            str: 返回 "nccl"（ROCm 构建中以 RCCL 形式提供）
        """
        return "nccl"


class _MPSStrategy(_GPUStrategy):
    """Apple Silicon MPS 后端具体策略实现

    实现 Apple Silicon（M1/M2/M3/M4 等）MPS 后端的检测、设备管理和信息查询。
    MPS 是统一内存架构（UMA），无独立显存 API：
    - ``torch.mps.current_allocated_memory()`` 可查询 PyTorch 已分配内存
    - 总内存/可用内存以系统物理内存近似
    """

    def detect(self) -> bool:
        """检测 MPS 后端是否可用

        Returns:
            bool: MPS 可用返回 True，否则返回 False
        """
        try:
            import torch

            return bool(
                hasattr(torch.backends, "mps") and torch.backends.mps.is_available() and torch.backends.mps.is_built()
            )
        except ImportError:
            return False

    def device_str(self) -> str:
        """返回 MPS 设备字符串

        Returns:
            str: 固定返回 "mps"
        """
        return "mps"

    def get_info(self) -> dict:
        """获取 Apple Silicon MPS 硬件信息

        统一内存架构下以系统物理内存近似总显存/可用显存，
        以 torch.mps.current_allocated_memory() 计算已占用。

        Returns:
            dict: 与 CUDA 策略同构的信息字典（name/total_vram/
                available_vram_mb/utilization/cuda_version）。

        Raises:
            RuntimeError: MPS 不可用或系统内存查询失败
        """
        try:
            import platform

            import torch

            if not self.is_available():
                raise RuntimeError("MPS 当前不可用")
        except ImportError as e:
            logger.error(f"PyTorch 未安装，无法获取 MPS 信息: {e}")
            raise

        try:
            import psutil

            total_bytes = psutil.virtual_memory().total
            avail_bytes = psutil.virtual_memory().available
        except Exception:
            total_bytes = 0
            avail_bytes = 0

        allocated = 0
        try:
            if hasattr(torch.mps, "current_allocated_memory"):
                allocated = torch.mps.current_allocated_memory()
        except Exception:  # noqa: BLE001 — MPS 内存 API 不可用时忽略
            allocated = 0

        total_vram = total_bytes or allocated
        available_vram_mb = (avail_bytes if avail_bytes > 0 else max(total_vram - allocated, 0)) // (1024 * 1024)
        utilization = ((total_vram - available_vram_mb * (1024 * 1024)) / total_vram) * 100 if total_vram > 0 else 0

        chip = getattr(platform, "processor", lambda: "")() or "Apple Silicon"
        return {
            "name": f"{chip} (MPS)",
            "total_vram": total_vram,
            "available_vram_mb": available_vram_mb,
            "utilization": utilization,
            "cuda_version": "",
        }

    def is_available(self) -> bool:
        """运行时检查 MPS 是否可用

        Returns:
            bool: MPS 当前可用返回 True
        """
        try:
            import torch

            return bool(
                hasattr(torch.backends, "mps") and torch.backends.mps.is_available() and torch.backends.mps.is_built()
            )
        except ImportError:
            return False

    def check_health(self) -> bool:
        """运行时健康探测：MPS 上下文可执行基本张量操作

        Returns:
            bool: 上下文健康返回 True；探测异常返回 False（探针永不抛异常）
        """
        try:
            import torch

            if not self.is_available():
                return False
            x = torch.zeros(1, dtype=torch.float32, device="mps")
            _ = (x + 1).cpu()
            return True
        except Exception as e:  # noqa: BLE001 — 探针必须无异常收敛为 False
            logger.warning(f"MPS 健康探测失败，判定 GPU 不健康: {e}")
            return False

    def synchronize(self) -> None:
        """同步 MPS 设备，阻塞等待所有 GPU 操作完成"""
        import torch

        if hasattr(torch.mps, "synchronize"):
            torch.mps.synchronize()

    def get_process_group_backend(self) -> str:
        """获取 MPS 对应的分布式进程组后端

        Returns:
            str: 返回 "gloo"（MPS 无专用通信库）
        """
        return "gloo"


# 策略映射表：后端类型 -> 策略实例
_STRATEGY_MAP: dict[GPUBackend, _GPUStrategy] = {
    GPUBackend.CUDA: _CUDAStrategy(),
    GPUBackend.ROCM: _ROCMStrategy(),
    GPUBackend.MPS: _MPSStrategy(),
}

# 后端检测优先级顺序：NVIDIA CUDA → AMD ROCm → Apple MPS
_DETECTION_ORDER = [
    GPUBackend.CUDA,
    GPUBackend.ROCM,
    GPUBackend.MPS,
]


class GPUBackendManager:
    """GPU 后端统一管理器

    使用 Strategy 模式自动检测可用 GPU 后端并提供统一 API，
    封装不同后端的差异，为上层应用提供一致的 GPU 访问接口。

    支持 NVIDIA CUDA / AMD ROCm / Apple Silicon MPS，不支持纯 CPU 推理。
    未检测到 GPU 时进入降级模式。

    Usage:
        manager = GPUBackendManager()
        if manager.is_gpu_available:
            backend = manager.backend
            device = manager.device_str
            info = manager.get_gpu_info()
            can_load = manager.can_load_model(required_vram_mb=8000)
    """

    def __init__(self):
        """初始化 GPU 后端管理器并自动检测可用后端"""
        # _detect_backend() 在构造末尾必定赋值；初始值用 UNAVAILABLE 而非 None，
        # 保证 backend 始终为非空枚举。
        self._backend: GPUBackend = GPUBackend.UNAVAILABLE
        self._strategy: _GPUStrategy | None = None
        self._device_name: str = ""
        self._total_vram: int = 0
        self._gpu_info_cache: GPUInfo | None = None
        self._gpu_info_cache_time: float = 0.0
        self._can_load_cache: dict[int, tuple[float, bool]] = {}
        self._detect_backend()

    def _detect_backend(self):
        """自动检测可用的 GPU 后端

        按 `_DETECTION_ORDER` 优先级顺序遍历策略，选择第一个检测成功的后端
        （NVIDIA CUDA → AMD ROCm → Apple MPS）。
        如果均未检测到，设置为 UNAVAILABLE 降级模式并记录警告。

        检测流程：
            1. 按优先级尝试每个后端策略的 detect() 方法
            2. 检测成功后获取 GPU 信息（名称、显存）
            3. 信息获取失败时使用默认值但继续使用该后端
            4. 全部失败则进入降级模式
        """
        for backend_type in _DETECTION_ORDER:
            strategy = _STRATEGY_MAP[backend_type]
            try:
                if strategy.detect():
                    self._backend = backend_type
                    self._strategy = strategy
                    try:
                        info = strategy.get_info()
                        self._device_name = info.get("name", str(backend_type.value))
                        self._total_vram = info.get("total_vram", 0)
                    except Exception as e:
                        logger.debug(f"获取 {backend_type.name} 信息失败: {e}")
                        self._device_name = str(backend_type.value)
                        self._total_vram = 0
                    logger.info(f"检测到 {backend_type.name} 后端: {self._device_name}")
                    return
            except Exception as e:
                logger.debug(f"检测 {backend_type.name} 后端失败: {e}")
                continue

        # 未检测到可用 GPU，进入降级模式
        self._backend = GPUBackend.UNAVAILABLE
        self._strategy = None
        self._device_name = "未检测到可用 GPU"
        self._total_vram = 0
        logger.warning("未检测到 NVIDIA CUDA / AMD ROCm / Apple MPS GPU。" "应用将以降级模式启动，推理功能不可用。")

    @property
    def backend(self) -> GPUBackend:
        """获取当前 GPU 后端类型

        Returns:
            GPUBackend: 当前后端枚举值（CUDA / ROCM / MPS 或 UNAVAILABLE）
        """
        return self._backend

    @property
    def device_name(self) -> str:
        """获取 GPU 设备名称

        Returns:
            str: GPU 设备名称字符串，降级模式下返回提示信息
        """
        return self._device_name

    @property
    def is_gpu_available(self) -> bool:
        """检查 GPU 是否可用（任一计算后端已激活）

        Returns:
            bool: GPU 可用返回 True，降级模式返回 False
        """
        return self._backend != GPUBackend.UNAVAILABLE

    def check_health(self) -> bool:
        """运行时健康探测（评估 P2-4）

        委托当前策略探测后端上下文健康。调用方语义约定：
        - is_gpu_available=True 时调用本方法，False 表示 GPU 已损坏，应停止接流；
        - is_gpu_available=False（降级模式）时无需调用——服务本就不承诺推理能力。

        Returns:
            bool: GPU 运行时健康返回 True；无后端或探测异常返回 False
        """
        if self._strategy is None:
            return False
        try:
            return self._strategy.check_health()
        except Exception as e:  # noqa: BLE001 — 探针必须无异常收敛为 False
            logger.warning(f"GPU 健康探测异常，判定不健康: {e}")
            return False

    @property
    def device_str(self) -> str:
        """返回 PyTorch 设备字符串

        Returns:
            str: 可用时返回策略对应的设备字符串（如 "cuda" / "mps"），
                降级时返回 "cpu" 并记录警告
        """
        if self._strategy is not None:
            return self._strategy.device_str()
        logger.warning(
            "GPU 不可用，device_str 返回 'cpu'。" "SeedVR2 模型需要 CUDA/ROCm/MPS GPU 推理，CPU 模式下推理功能不可用。"
        )
        return "cpu"

    def get_gpu_info(self) -> GPUInfo:
        """获取当前 GPU 完整硬件信息

        优先从策略获取实时信息，失败或降级模式下返回已缓存的信息或空信息。
        查询结果会缓存 0.5 秒，避免频繁调用底层 API 造成开销。

        Returns:
            GPUInfo: 包含设备名称、显存、利用率、CUDA版本等的数据类实例
        """
        current_time = time.time()
        if self._gpu_info_cache is not None and current_time - self._gpu_info_cache_time < _GPU_INFO_CACHE_TTL:
            return self._gpu_info_cache

        result: GPUInfo
        if self._strategy is not None and self._backend != GPUBackend.UNAVAILABLE:
            try:
                info = self._strategy.get_info()
                result = GPUInfo(
                    backend=self._backend,
                    name=info.get("name", self._device_name),
                    total_vram_mb=info.get("total_vram", self._total_vram) // (1024 * 1024),
                    available_vram_mb=info.get("available_vram_mb", 0),
                    utilization_pct=info.get("utilization", 0.0),
                    driver_version="",
                    cuda_version=info.get("cuda_version", ""),
                )
                # P2-1: 仅 NVIDIA 后端叠加 SM 真实利用率与温度（nvidia-smi 查询；
                # 非 NVIDIA 后端 nvidia-smi 不存在，query_gpu_utilization 返回 None）
                if self._backend == GPUBackend.CUDA:
                    nvml_info = query_gpu_utilization()
                    if nvml_info is not None:
                        result.sm_utilization_pct = nvml_info.get("sm_utilization_pct")
                        result.temperature_c = nvml_info.get("temperature_c")
            except ImportError as e:
                logger.error(f"PyTorch 未安装，无法获取 GPU 信息: {e}")
                result = self._get_unavailable_info()
            except RuntimeError as e:
                logger.error(f"GPU 运行时错误，无法获取 GPU 信息: {e}")
                result = self._get_unavailable_info()
            except Exception as e:
                logger.error(f"获取 GPU 信息时发生未知错误: {e}", exc_info=True)
                result = self._get_unavailable_info()
        else:
            result = self._get_unavailable_info()

        self._gpu_info_cache = result
        self._gpu_info_cache_time = current_time
        return result

    def _get_unavailable_info(self) -> GPUInfo:
        """返回降级模式下的 GPU 信息

        Returns:
            GPUInfo: 降级模式信息对象
        """
        return GPUInfo(
            backend=self._backend,
            name=self._device_name,
            total_vram_mb=self._total_vram // (1024 * 1024) if self._total_vram else 0,
            available_vram_mb=0,
            utilization_pct=0.0,
        )

    def can_load_model(self, required_vram_mb: int) -> bool:
        """检查当前 GPU 是否有足够显存加载指定大小的模型

        模型加载预检：在实际加载模型前检查可用显存，避免 OOM。
        查询结果会缓存 0.5 秒，并要求可用显存比需求多 10% 作为安全边际，
        防止刚好卡阈值导致运行时 OOM。

        Args:
            required_vram_mb: 模型所需显存大小（MB）

        Returns:
            bool: 可用显存 >= 所需显存 * 1.1 返回 True，否则返回 False；
                  GPU 不可用时始终返回 False
        """
        if self._backend == GPUBackend.UNAVAILABLE:
            return False

        current_time = time.time()
        cache_entry = self._can_load_cache.get(required_vram_mb)
        if cache_entry is not None:
            cache_time, cache_result = cache_entry
            if current_time - cache_time < _MODEL_LOAD_CACHE_TTL:
                return cache_result

        info = self.get_gpu_info()
        required_with_margin = int(required_vram_mb * _VRAM_SAFETY_MARGIN)
        result = info.available_vram_mb >= required_with_margin

        self._can_load_cache[required_vram_mb] = (current_time, result)

        if len(self._can_load_cache) > 32:
            oldest_key = min(self._can_load_cache.keys(), key=lambda k: self._can_load_cache[k][0])
            del self._can_load_cache[oldest_key]

        return result

    def get_recommended_model_size(self) -> str:
        """根据当前 GPU 显存大小推荐合适的模型规格

        推荐策略（MPS 统一内存按系统物理内存近似）：
            - 24GB+ 显存：推荐 7B 模型
            - 16GB+ 显存：推荐 3B 模型
            - 低于 16GB：仍推荐 3B 模型但需配合 BlockSwap 等优化

        Returns:
            str: 推荐模型大小标识（"3b" 或 "7b"）
        """
        info = self.get_gpu_info()
        if info.total_vram_mb >= 24000:  # 24GB+
            return "7b"
        elif info.total_vram_mb >= 16000:  # 16GB+
            return "3b"
        else:
            return "3b"  # 显存不足也推荐3b，但会警告

    def get_device(self) -> str:
        """获取 PyTorch 设备字符串（同 device_str 属性）

        Returns:
            str: PyTorch 设备字符串
        """
        return self.device_str

    def synchronize(self) -> None:
        """同步当前 GPU 设备，等待所有操作完成

        GPU 可用时调用策略的 synchronize() 方法，降级模式下无操作。
        """
        if self._strategy is not None:
            self._strategy.synchronize()

    def get_process_group_backend(self) -> str:
        """获取分布式训练进程组通信后端

        Returns:
            str: CUDA/ROCm 返回 "nccl"，MPS/降级模式返回 "gloo"
        """
        if self._strategy is not None:
            return self._strategy.get_process_group_backend()
        return "gloo"


# 全局单例实例：应用启动时自动创建，各模块通过此实例访问 GPU 功能
gpu_manager = GPUBackendManager()
