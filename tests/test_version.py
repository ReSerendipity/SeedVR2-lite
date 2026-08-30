#!/usr/bin/env python3
"""get_app_version 单元测试：版本号单一事实来源（发布管理体系评估 P0-2）。"""

import re
import tomllib
from pathlib import Path

from app.integrated_app.version import get_app_version

_ROOT = Path(__file__).resolve().parents[1]


class TestGetAppVersion:
    def test_matches_pyproject_version(self):
        """源码运行时版本必须与 pyproject.toml 完全一致（根治硬编码 1.0.0 漂移）。"""
        expected = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        assert get_app_version() == expected

    def test_returns_valid_semver(self):
        """返回值必须是 MAJOR.MINOR.PATCH 形式，禁止 unknown/空串。"""
        value = get_app_version()
        assert value != "unknown" and value != ""
        assert re.fullmatch(r"\d+\.\d+\.\d+", value), f"非语义化版本: {value}"
