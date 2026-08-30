"""tests/test_doctor.py — scripts/doctor.py 单元测试（无 GPU / 无网络依赖）。"""

from __future__ import annotations

import shutil
import socket
import sys
from pathlib import Path

import yaml

from scripts.doctor import (
    FAIL,
    PASS,
    SKIP,
    WARN,
    _model_base_dir,
    check_dependencies,
    check_disk_space,
    check_model_files,
    check_port,
    check_python_version,
    check_venv,
    run_all,
)


class TestCheckPythonVersion:
    def test_current_interpreter_passes(self):
        result = check_python_version()
        assert result.status == PASS

    def test_unrealistic_minimum_fails(self):
        result = check_python_version(min_version=(99, 0))
        assert result.status == FAIL
        assert "低于最低要求" in result.detail


class TestCheckDependencies:
    def test_real_environment_core_packages_present(self):
        result = check_dependencies()
        # 测试环境装了完整 requirements，核心包必须全在
        assert result.status in (PASS, WARN)
        if result.status == WARN:
            assert "核心依赖缺失" not in result.detail

    def test_missing_core_package_fails(self):
        result = check_dependencies(core=["definitely_not_a_real_module_xyz"])
        assert result.status == FAIL
        assert "definitely_not_a_real_module_xyz" in result.detail

    def test_missing_optional_package_warns(self):
        result = check_dependencies(core=[], optional=["definitely_not_a_real_module_xyz"])
        assert result.status == WARN

    def test_empty_lists_pass(self):
        result = check_dependencies(core=[], optional=[])
        assert result.status == PASS


class TestCheckDiskSpace:
    def test_huge_threshold_fails(self, tmp_path: Path):
        result = check_disk_space(tmp_path, min_gb=10**9)
        assert result.status == FAIL

    def test_zero_threshold_passes(self, tmp_path: Path):
        result = check_disk_space(tmp_path, min_gb=0.0)
        assert result.status == PASS

    def test_near_threshold_warns(self, tmp_path: Path):
        # 取 min = free/1.5：free 落在 (min, 2×min) 开区间内 → WARN
        free_gb = shutil.disk_usage(tmp_path).free / (1024**3)
        result = check_disk_space(tmp_path, min_gb=free_gb / 1.5)
        assert result.status == WARN


class TestCheckPort:
    def test_random_free_port_passes(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            free_port = sock.getsockname()[1]
        result = check_port(free_port)
        assert result.status == PASS

    def test_bound_port_warns(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            bound_port = sock.getsockname()[1]
            sock.listen(1)
            result = check_port(bound_port)
            assert result.status == WARN
            assert "已被占用" in result.detail


class TestCheckVenv:
    def test_missing_venv_warns(self, tmp_path: Path):
        result = check_venv(tmp_path)
        assert result.status == WARN
        assert "uv sync" in result.detail or "install.bat" in result.detail

    def test_existing_venv_passes(self, tmp_path: Path):
        marker = ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python"
        (tmp_path / marker).parent.mkdir(parents=True)
        (tmp_path / marker).write_text("", encoding="utf-8")
        result = check_venv(tmp_path)
        assert result.status == PASS


def _write_config(tmp_path: Path, cfg: dict) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return config_path


class TestCheckModelFiles:
    def _entry(self) -> dict:
        return {
            "model": {
                "default_size": "3b",
                "default_precision": "fp16",
                "pretrained_dir": "model",
                "models": {
                    "3b": {
                        "checkpoint_fp16": "main.safetensors",
                        "vae_checkpoint": "vae.safetensors",
                        "pos_emb": "pos_emb.pt",
                        "neg_emb": "neg_emb.pt",
                    }
                },
            }
        }

    def test_all_files_present_passes(self, tmp_path: Path):
        base = tmp_path / "model"
        base.mkdir()
        for name in ("main.safetensors", "vae.safetensors", "pos_emb.pt", "neg_emb.pt"):
            (base / name).write_text("x", encoding="utf-8")
        config_path = _write_config(tmp_path, self._entry())
        result = check_model_files(config_path)
        assert result.status == PASS
        assert "4 个文件齐全" in result.detail

    def test_missing_files_warns_with_download_hint(self, tmp_path: Path):
        config_path = _write_config(tmp_path, self._entry())
        result = check_model_files(config_path)
        assert result.status == WARN
        assert "download_model.py" in result.detail

    def test_missing_config_skips(self, tmp_path: Path):
        result = check_model_files(tmp_path / "nonexistent.yaml")
        assert result.status == SKIP

    def test_shared_mode_uses_shared_root(self, tmp_path: Path):
        cfg = self._entry()
        cfg["model"]["model_source_mode"] = "shared"
        cfg["model"]["shared_models_root"] = "shared_root"
        base = tmp_path / "shared_root"
        base.mkdir()
        for name in ("main.safetensors", "vae.safetensors", "pos_emb.pt", "neg_emb.pt"):
            (base / name).write_text("x", encoding="utf-8")
        assert _model_base_dir(cfg["model"], tmp_path) == tmp_path / "shared_root"
        config_path = _write_config(tmp_path, cfg)
        result = check_model_files(config_path)
        assert result.status == PASS


class TestRunAll:
    def test_returns_all_checks_with_valid_status(self):
        results = run_all(min_free_gb=0.0)  # 磁盘用 0 阈值避免真实磁盘接近预检线的环境波动
        names = [name for name, _ in results]
        assert names == [
            "Python 版本",
            "核心依赖",
            "GPU / CUDA",
            "FFmpeg",
            "模型权重",
            "磁盘空间",
            "端口占用",
            ".venv",
        ]
        for _, res in results:
            assert res.status in (PASS, WARN, FAIL, SKIP)
