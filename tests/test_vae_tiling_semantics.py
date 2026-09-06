#!/usr/bin/env python3
"""VAE tiling 语义统一测试（成本治理 P1-3）。

覆盖评估报告改进建议 #4 的验收标准：
- 偏好默认与引擎对齐：UserPreferences.vae_tiling_enabled 默认 True
  （引擎 tiled VAE 默认开启 + OOM 自动回退），且旧配置显式 false 仍被尊重；
- config.yaml 的 user_preferences.vae_tiling_enabled 与代码默认一致；
- 模板哨兵：sv-param-check label 全部正确闭合（历史上 6 处缺 `>` 导致
  checkbox 被 HTML 解析器吞进属性，锁定/偏好链路实际由 hidden input 承担）；
- 死字段复活哨兵：legacy 偏好迁移包含 vae_tiling_enabled → encode_tiled；
- i18n：档位提示 key 在四语言字典中齐备。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESTORE_HTML = PROJECT_ROOT / "app" / "integrated_app" / "templates" / "restore.html"
APP_JS = PROJECT_ROOT / "app" / "integrated_app" / "static" / "js" / "app.js"
CONFIG_YAML = PROJECT_ROOT / "config.yaml"


class TestPreferenceEngineAlignment:
    def test_default_prefers_tiling_true(self):
        from app.integrated_app.optimization.webui_enhancement import UserPreferences

        assert (
            UserPreferences().vae_tiling_enabled is True
        ), "偏好默认应与引擎一致（引擎 tiled VAE 默认开启 + OOM 回退）"

    def test_from_dict_respects_explicit_false(self):
        from app.integrated_app.optimization.webui_enhancement import UserPreferences

        prefs = UserPreferences.from_dict({"vae_tiling_enabled": False})
        assert prefs.vae_tiling_enabled is False, "旧配置显式关闭仍应生效（用户选择优先）"

    def test_config_yaml_matches_default(self):
        cfg = yaml.safe_load(CONFIG_YAML.read_text(encoding="utf-8"))
        assert cfg["user_preferences"]["vae_tiling_enabled"] is True


class TestTemplateSentinels:
    def test_param_check_labels_are_closed(self):
        """回归哨兵：label 缺 '>' 会让 HTML 解析器把 checkbox 吞进属性。"""
        unclosed = [
            lineno
            for lineno, line in enumerate(RESTORE_HTML.read_text(encoding="utf-8").splitlines(), start=1)
            if 'sv-param-check" data-tooltip' in line and not line.rstrip().endswith(">")
        ]
        assert unclosed == [], f"以下行的 sv-param-check label 未闭合: {unclosed}"

    def test_legacy_vae_tiling_pref_migrated(self):
        text = RESTORE_HTML.read_text(encoding="utf-8")
        assert "legacy.vae_tiling_enabled" in text, "死偏好字段必须在 legacy 迁移中映射到表单 encode_tiled"

    def test_tier_hint_element_and_loader_exist(self):
        text = RESTORE_HTML.read_text(encoding="utf-8")
        assert 'id="vaeTilingTierHint"' in text
        assert "_showVaeTilingTierHint" in text


class TestI18nSentinels:
    def test_tier_hint_key_present_in_all_locales(self):
        text = APP_JS.read_text(encoding="utf-8")
        count = text.count("'restore.vae_tiling_tier_hint'")
        assert count >= 4, f"档位提示 key 应覆盖四种语言，当前 {count} 处"
