#!/usr/bin/env python3
"""权重文件名别名解析测试（numz / Comfy-Org 双命名兼容）。

背景：同一份 SeedVR2 权重在两套社区仓里文件名不同（仅差 ``seedvr2_`` 之后是否带
``ema_`` 段），字节不同但模型结构一致。config.yaml 只登记一套，用户放另一套时
加载器会误判「文件不存在」。本测试锁定别名解析约定。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import importlib.util
from pathlib import Path

import yaml

from app.integrated_app.utils.weight_names import (
    find_weight_file,
    is_comfy_org_name,
    weight_filename_aliases,
    weight_hash_candidates,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


class TestWeightFilenameAliases:
    """文件名别名推导"""

    def test_numz_name_yields_comfy_org_alias(self):
        assert weight_filename_aliases("seedvr2_ema_3b_fp8_e4m3fn.safetensors") == [
            "seedvr2_ema_3b_fp8_e4m3fn.safetensors",
            "seedvr2_3b_fp8_e4m3fn.safetensors",
        ]

    def test_comfy_org_name_yields_numz_alias(self):
        assert weight_filename_aliases("seedvr2_3b_fp8_e4m3fn.safetensors") == [
            "seedvr2_3b_fp8_e4m3fn.safetensors",
            "seedvr2_ema_3b_fp8_e4m3fn.safetensors",
        ]

    def test_primary_name_stays_first(self):
        """原文件名必须优先，保证 config 登记的那套是首选。"""
        for name in ("seedvr2_ema_7b_sharp_fp16.safetensors", "seedvr2_7b_sharp_fp16.safetensors"):
            assert weight_filename_aliases(name)[0] == name

    def test_all_precisions_roundtrip(self):
        """各尺寸 / 各精度前缀推导自洽（去 ema 再加回来等价）。"""
        for name in (
            "seedvr2_ema_3b_fp16.safetensors",
            "seedvr2_ema_3b_fp8_e4m3fn.safetensors",
            "seedvr2_3b_mxfp8.safetensors",
            "seedvr2_3b_int8_convrot.safetensors",
            "seedvr2_3b_nvfp4.safetensors",
            "seedvr2_ema_7b_sharp_fp8_e4m3fn.safetensors",
        ):
            aliases = weight_filename_aliases(name)
            assert len(aliases) == 2
            assert set(weight_filename_aliases(aliases[1])) == set(aliases)

    def test_directory_prefix_is_stripped(self):
        assert weight_filename_aliases("model/seedvr2_ema_3b_fp16.safetensors") == [
            "seedvr2_ema_3b_fp16.safetensors",
            "seedvr2_3b_fp16.safetensors",
        ]

    def test_vae_duplicate_naming_alias(self):
        """VAE 在 Comfy-Org 仓有同内容重复命名（docs/LICENSE_COMPLIANCE.md §3.2）。"""
        assert weight_filename_aliases("ema_vae_fp16.safetensors") == [
            "ema_vae_fp16.safetensors",
            "seedvr2_ema_vae_fp16.safetensors",
        ]

    def test_unrelated_name_has_no_alias(self):
        assert weight_filename_aliases("dit_3b.safetensors") == ["dit_3b.safetensors"]
        assert weight_filename_aliases("pos_emb.pt") == ["pos_emb.pt"]

    def test_empty_name_returns_empty_list(self):
        assert weight_filename_aliases("") == []


class TestIsComfyOrgName:
    """Comfy-Org 命名判定"""

    def test_comfy_org(self):
        assert is_comfy_org_name("seedvr2_3b_mxfp8.safetensors") is True

    def test_numz_is_not_comfy_org(self):
        assert is_comfy_org_name("seedvr2_ema_3b_fp16.safetensors") is False

    def test_vae_is_not_comfy_org(self):
        assert is_comfy_org_name("ema_vae_fp16.safetensors") is False

    def test_empty_is_not_comfy_org(self):
        assert is_comfy_org_name("") is False


class TestFindWeightFile:
    """按别名优先级解析真实存在的文件"""

    def test_prefers_configured_name(self, tmp_path: Path):
        (tmp_path / "seedvr2_ema_3b_fp8_e4m3fn.safetensors").write_bytes(b"numz")
        (tmp_path / "seedvr2_3b_fp8_e4m3fn.safetensors").write_bytes(b"comfy")

        found = find_weight_file(tmp_path, "seedvr2_ema_3b_fp8_e4m3fn.safetensors")
        assert found is not None
        assert Path(found).name == "seedvr2_ema_3b_fp8_e4m3fn.safetensors"

    def test_falls_back_to_alias(self, tmp_path: Path):
        """config 登记 numz 名，磁盘只有 Comfy-Org 名 → 必须命中（本次修复的核心场景）。"""
        (tmp_path / "seedvr2_3b_fp8_e4m3fn.safetensors").write_bytes(b"comfy")

        found = find_weight_file(tmp_path, "seedvr2_ema_3b_fp8_e4m3fn.safetensors")
        assert found is not None
        assert Path(found).name == "seedvr2_3b_fp8_e4m3fn.safetensors"

    def test_returns_none_when_absent(self, tmp_path: Path):
        assert find_weight_file(tmp_path, "seedvr2_ema_3b_fp8_e4m3fn.safetensors") is None

    def test_empty_name_returns_none(self, tmp_path: Path):
        assert find_weight_file(tmp_path, "") is None


class TestWeightHashCandidates:
    """期望哈希候选集"""

    def test_primary_and_alt_collected(self):
        cfg = {"sha256_fp8": "AAAA", "sha256_fp8_alt": "BBBB"}
        assert weight_hash_candidates(cfg, "fp8") == ["aaaa", "bbbb"]

    def test_alt_optional(self):
        assert weight_hash_candidates({"sha256_fp16": "AA"}, "fp16") == ["aa"]

    def test_empty_config(self):
        assert weight_hash_candidates({}, "fp8") == []
        assert weight_hash_candidates(None, "fp8") == []

    def test_blank_values_ignored(self):
        assert weight_hash_candidates({"sha256_vae": "  ", "sha256_vae_alt": ""}, "vae") == []

    def test_duplicate_deduped(self):
        cfg = {"sha256_fp8": "AA", "sha256_fp8_alt": "aa"}
        assert weight_hash_candidates(cfg, "fp8") == ["aa"]


class TestDownloadRoutingConsistency:
    """``download_model`` 的来源路由必须与加载器的命名判定保持一致。

    ``scripts/download_model.py`` 是**独立脚本**（要能在尚未安装 app 依赖的环境里
    运行，用于首次下载），因此自带一份 ``_is_comfy_org`` 前缀判断而不 import 本模块
    —— 这是有意的解耦，不是遗漏。但两者功能上是**耦合**的：脚本按前缀决定去
    HuggingFace 还是 ModelScope 取文件，加载器按同一约定解析磁盘文件名。
    一旦漂移，就会出现「脚本下回来的文件名加载器解析不到」（或反之），
    故用本用例把两份实现钉死在同一约定上。
    """

    @staticmethod
    def _load_download_script():
        spec = importlib.util.spec_from_file_location("download_model", _REPO_ROOT / "scripts" / "download_model.py")
        assert spec and spec.loader, "无法加载 scripts/download_model.py"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # noqa: S301 - 受控本地脚本，非不可信输入
        return module

    def test_routing_predicate_agrees_with_loader(self):
        mod = self._load_download_script()
        samples = [
            "seedvr2_ema_3b_fp16.safetensors",
            "seedvr2_ema_3b_fp8_e4m3fn.safetensors",
            "seedvr2_ema_7b_sharp_fp16.safetensors",
            "seedvr2_3b_fp16.safetensors",
            "seedvr2_3b_fp8_e4m3fn.safetensors",
            "seedvr2_3b_int8_convrot.safetensors",
            "seedvr2_3b_mxfp8.safetensors",
            "seedvr2_3b_nvfp4.safetensors",
            "seedvr2_7b_mxfp8.safetensors",
            "ema_vae_fp16.safetensors",
            "pos_emb.pt",
            "neg_emb.pt",
        ]
        for name in samples:
            assert mod._is_comfy_org(name) == is_comfy_org_name(name), (
                f"{name}: download_model._is_comfy_org={mod._is_comfy_org(name)} "
                f"但 weight_names.is_comfy_org_name={is_comfy_org_name(name)}"
            )

    def test_routing_matches_config_registered_names(self):
        """config.yaml 登记的 5 个精度文件名，路由判定须与命名约定自洽。

        同时锁住「量化三精度（int8_convrot/mxfp8/nvfp4）用 Comfy-Org 名、
        fp16/fp8 用 numz 名」这一事实 —— 前者仅 Comfy-Org 提供，故无需 ``_alt`` 哈希。
        """
        mod = self._load_download_script()
        cfg = yaml.safe_load((_REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
        seen = 0
        for size, model_cfg in cfg["model"]["models"].items():
            for prec in ("fp16", "fp8", "int8_convrot", "mxfp8", "nvfp4"):
                name = model_cfg.get(f"checkpoint_{prec}")
                if not name:
                    continue
                seen += 1
                assert mod._is_comfy_org(name) == is_comfy_org_name(name), f"{size}/{prec}: {name}"
                # 别名集合必须包含自身，且两套命名互认
                assert name in weight_filename_aliases(name)
                assert (
                    len(weight_filename_aliases(name)) == 2
                ), f"{size}/{prec}: {name} 未推导出等价命名，双命名兼容会失效"
        assert seen >= 10, f"登记文件名过少（{seen}），疑似配置被删减"
