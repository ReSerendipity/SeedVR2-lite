#!/usr/bin/env python3
"""PathGuard Windows 特有攻击向量测试（评估报告 R7）。

历史缺口：test_path_guard.py 的 21 项用例均为 POSIX 式向量，
未覆盖 Windows 特有路径变形。本文件补齐（仅 win32 执行，CI
windows-latest 矩阵真实运行、ubuntu 自动跳过）：

- 保留设备名（CON/NUL/AUX/COM1...）与含扩展名变体
- UNC 路径（\\\\server\\share）与设备命名空间（\\\\.\\）
- 盘符绝对路径 / 盘符相对路径
- 尾部点 / 尾部空格（Win32 归一化剥除别名）
- 大小写变形（Windows 不敏感文件系统的合法访问不因大小写被破坏）

核心安全属性：任何向量要么被拒（False），要么 resolve() 后
仍落在白名单子树内——绝不发生「放行但实际越出白名单」。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import sys
from pathlib import Path

import pytest

from app.integrated_app.security.path_guard import PathGuard

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows 特有路径向量"),
    pytest.mark.integration,
]


@pytest.fixture
def guard(tmp_path: Path) -> tuple[PathGuard, Path]:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    return PathGuard([allowed]), allowed


def _assert_no_escape(guard: PathGuard, allowed: Path, vector: str) -> None:
    """安全属性断言：放行 ⇒ 解析后必在白名单子树内。"""
    if guard.is_safe_path(vector):
        resolved = Path(vector).resolve()
        assert resolved == allowed or allowed in resolved.parents, f"向量 {vector!r} 被放行但解析到白名单外: {resolved}"


# ---------- 保留设备名 ----------


@pytest.mark.parametrize("name", ["CON", "NUL", "AUX", "COM1", "LPT1", "CON.txt", "NUL.png"])
def test_reserved_device_names_rejected(guard, name):
    g, allowed = guard
    assert g.is_safe_path(name) is False
    _assert_no_escape(g, allowed, name)


# ---------- UNC / 设备命名空间 ----------


@pytest.mark.parametrize(
    "vector",
    [
        "\\\\server\\share\\secret.txt",
        "\\\\localhost\\c$\\Windows\\win.ini",
        "\\\\.\\C:\\Windows\\System32\\config\\sam",
        "\\\\?\\C:\\Windows\\win.ini",
    ],
)
def test_unc_and_device_namespace_rejected(guard, vector):
    g, allowed = guard
    assert g.is_safe_path(vector) is False
    _assert_no_escape(g, allowed, vector)


# ---------- 盘符绝对 / 相对路径 ----------


@pytest.mark.parametrize(
    "vector",
    [
        "C:\\Windows\\win.ini",
        "C:\\Windows",
        "C:Windows\\System32",  # 盘符相对路径（相对 C: 上一次 CWD）
    ],
)
def test_drive_paths_outside_whitelist_rejected(guard, vector):
    g, allowed = guard
    _assert_no_escape(g, allowed, vector)


# ---------- 尾部点 / 尾部空格（Win32 归一化别名） ----------


@pytest.mark.parametrize(
    "vector",
    [
        "allowed.....",  # 尾部点：Win32 会剥除后别名到 allowed 或保留为不存在路径
        "allowed ",  # 尾部空格：同上
        "allowed. . .",
    ],
)
def test_trailing_dot_space_no_escape(guard, vector):
    """尾部点/空格只允许两种结局：被拒，或别名解析回白名单内目标。"""
    g, allowed = guard
    _assert_no_escape(g, allowed, vector)


# ---------- 大小写变形（合法访问不因大小写折叠被误拒） ----------


def test_case_insensitive_legitimate_access(guard, tmp_path):
    g, allowed = guard
    target = allowed / "Sub" / "File.png"
    target.parent.mkdir()
    target.write_bytes(b"x")
    # 大小写变形路径应被放行（resolve 后与白名单树折叠匹配）
    assert g.is_safe_path(str(target).upper()) is True
    assert g.is_safe_path(str(target).swapcase()) is True


# ---------- 白名单根的越界方向 ----------


def test_parent_of_allowed_root_rejected(guard, tmp_path):
    g, allowed = guard
    # 白名单根的父目录（tmp_path）与兄弟目录都必须被拒
    assert g.is_safe_path(str(tmp_path)) is False
    sibling = tmp_path / "allowed_evil"
    sibling.mkdir()
    assert g.is_safe_path(str(sibling)) is False
