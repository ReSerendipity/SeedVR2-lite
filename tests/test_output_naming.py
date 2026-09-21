#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""输出文件命名口径测试：输入叫什么，产物就叫什么。

用户靠文件名辨认图像/视频内容，任何时间戳、模型标签或随机后缀都等于让产物无法检索；
单文件 / 批量 / 图像 / 视频四条路径统一沿用输入名，重名只追加序号，不改名。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import os

import pytest

pytest.importorskip("torch", reason="命名助手位于引擎管线模块内")

from app.integrated_app.engines._image_pipeline import _resolve_unique_path
from app.integrated_app.utils.output_names import build_output_name  # noqa: E402


class TestBuildOutputName:
    def test_keeps_input_stem_and_swaps_extension(self):
        assert build_output_name("/data/in/photo_4k.jpg", ".png") == "photo_4k.png"

    def test_keeps_chinese_and_spaces(self):
        assert build_output_name("/下载/猫 图 特写.jpeg", ".mp4") == "猫 图 特写.mp4"

    def test_keeps_dots_inside_stem(self):
        """v2.final 这类中间点不能被当成扩展名切掉。"""
        assert build_output_name("/in/scene.v2.final.png", ".png") == "scene.v2.final.png"

    def test_windows_style_path_on_posix(self):
        assert build_output_name(r"C:\Users\me\Downloads\镜头A.jpeg", ".png") == "镜头A.png"

    def test_strips_illegal_characters(self):
        assert build_output_name("/in/a<b>c|d?.png", ".png") == "abcd.png"

    @pytest.mark.parametrize("bad", [None, "", "/", "hidden/.png", "/in/.hidden"])
    def test_unusable_input_name_falls_back_to_timestamp(self, bad):
        """拿不到可用名字时退回时间戳，绝不产出 '.png' 这种无名文件。"""
        name = build_output_name(bad, ".png")
        assert name != ".png"
        assert name.endswith(".png")
        assert len(name) > len(".png")

    def test_no_ai_label_by_default(self, monkeypatch):
        monkeypatch.delenv("SEEDVR2_EXPLICIT_AI_LABEL", raising=False)
        assert build_output_name("/in/a.png", ".png") == "a.png"

    def test_ai_label_is_opt_in_only(self, monkeypatch):
        """文件名级显式标识会改变产物名，故默认关闭，对外部署需显式开启。"""
        monkeypatch.setenv("SEEDVR2_EXPLICIT_AI_LABEL", "1")
        assert build_output_name("/in/a.png", ".png") == "a_AI.png"


class TestSanitizeIsTheTrustBoundary:
    """产物名的输入是**客户端可控的** `multipart` 文件名，清洗点是唯一信任边界。

    引擎拿到 output_name 后会 `os.path.join(output_dir, filename)`，所以名字里绝不能
    留下分隔符或 `..`——否则一个上传文件名就能把产物写到 outputs/ 之外。
    """

    @pytest.mark.parametrize(
        "raw",
        ["../../evil.png", "..\\..\\evil.png", "/etc/passwd", "a/../../b.png", "....//x.png", "C:\\Windows\\sys32.png"],
    )
    def test_never_escapes_output_dir(self, raw):
        name = build_output_name(raw, ".png")
        assert "/" not in name and "\\" not in name, name
        assert ".." not in name, name
        assert os.path.basename(name) == name, name

    def test_control_chars_and_long_names_are_tamed(self):
        assert build_output_name("a\x00b.png", ".png") == "ab.png"
        assert len(build_output_name("x" * 200, ".png")) <= 200 + len(".png")

    def test_dot_only_names_fall_back_not_produce_hidden_file(self):
        # "..." / ".hidden" 这类名字剥完后为空 → 退回时间戳，而不是产出隐藏文件或空名
        assert build_output_name("...", ".png") != ".png"
        assert build_output_name("", ".png") != ".png"


class TestResolveUniquePath:
    def test_same_input_restored_twice_does_not_overwrite(self, tmp_path):
        """沿用输入名后重名变常见：第二份追加序号，第一份不被覆盖。"""
        first = _resolve_unique_path(str(tmp_path), "photo.png")
        assert os.path.basename(first) == "photo.png"
        with open(first, "wb") as f:
            f.write(b"first")

        second = _resolve_unique_path(str(tmp_path), "photo.png")
        assert second != first
        assert os.path.basename(second) == "photo_1.png"
        with open(first, "rb") as f:
            assert f.read() == b"first", "重名兜底不得覆盖上一版产物"
