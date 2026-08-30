#!/usr/bin/env python3
"""GPU 可观测性单元测试（成本治理 P2-1）。

覆盖评估报告 P2-1 的验收标准：
- nvidia-smi 查询解析（正常 / 异常输出 / 失败返回码）
- TTL 缓存与失败冷却语义
- GPUInfo 新增 SM 利用率/温度字段默认值

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import subprocess
from unittest import mock

from app.integrated_app.gpu_backend import GPUBackend, GPUInfo
from app.integrated_app.optimization.gpu import nvml_monitor


class TestNvidiaSmiParsing:
    """nvidia-smi 输出解析。"""

    def test_normal_output_parsed(self):
        fake = mock.Mock(returncode=0, stdout="35, 45\n")
        with mock.patch.object(nvml_monitor.subprocess, "run", return_value=fake):
            data = nvml_monitor._query_nvidia_smi()
        assert data == {"sm_utilization_pct": 35.0, "temperature_c": 45.0}

    def test_nonzero_returncode_returns_none(self):
        fake = mock.Mock(returncode=1, stdout="")
        with mock.patch.object(nvml_monitor.subprocess, "run", return_value=fake):
            assert nvml_monitor._query_nvidia_smi() is None

    def test_garbage_output_returns_none(self):
        fake = mock.Mock(returncode=0, stdout="not,a number\n")
        with mock.patch.object(nvml_monitor.subprocess, "run", return_value=fake):
            assert nvml_monitor._query_nvidia_smi() is None

    def test_missing_binary_returns_none(self):
        with mock.patch.object(nvml_monitor.subprocess, "run", side_effect=OSError("nvidia-smi not found")):
            assert nvml_monitor._query_nvidia_smi() is None

    def test_timeout_returns_none(self):
        with mock.patch.object(
            nvml_monitor.subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=3)
        ):
            assert nvml_monitor._query_nvidia_smi() is None


class TestQueryCacheAndCooldown:
    """TTL 缓存与失败冷却。"""

    def setup_method(self):
        nvml_monitor.reset_cache()

    def teardown_method(self):
        nvml_monitor.reset_cache()

    def test_success_cached_within_ttl(self):
        calls = []

        def fake_query():
            calls.append(1)
            return {"sm_utilization_pct": 10.0, "temperature_c": 40.0}

        with mock.patch.object(nvml_monitor, "_query_nvidia_smi", side_effect=fake_query):
            first = nvml_monitor.query_gpu_utilization()
            second = nvml_monitor.query_gpu_utilization()

        assert first == second == {"sm_utilization_pct": 10.0, "temperature_c": 40.0}
        assert len(calls) == 1  # 第二次命中缓存

    def test_failure_cooldown_skips_requery(self):
        calls = []

        def fake_query():
            calls.append(1)
            return None

        with mock.patch.object(nvml_monitor, "_query_nvidia_smi", side_effect=fake_query):
            assert nvml_monitor.query_gpu_utilization() is None
            assert nvml_monitor.query_gpu_utilization() is None

        assert len(calls) == 1  # 冷却期内不重复拉起子进程

    def test_force_bypasses_cache(self):
        calls = []

        def fake_query():
            calls.append(1)
            return {"sm_utilization_pct": 1.0, "temperature_c": 30.0}

        with mock.patch.object(nvml_monitor, "_query_nvidia_smi", side_effect=fake_query):
            nvml_monitor.query_gpu_utilization()
            nvml_monitor.query_gpu_utilization(force=True)

        assert len(calls) == 2


class TestGPUInfoFields:
    """GPUInfo 新字段。"""

    def test_new_fields_default_none(self):
        info = GPUInfo(
            backend=GPUBackend.CUDA,
            name="RTX Test",
            total_vram_mb=16384,
            available_vram_mb=8192,
            utilization_pct=50.0,
        )
        assert info.sm_utilization_pct is None
        assert info.temperature_c is None
