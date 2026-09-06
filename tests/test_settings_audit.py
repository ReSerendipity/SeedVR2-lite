#!/usr/bin/env python3
"""配置热改审计测试（数据治理 P1-4）。

验收标准（评估报告 P1-4）：
1. POST /api/system/settings 变更成功 → CONFIG_UPDATE 审计事件（含变更键清单，
   安全敏感键 runtime.security.allowed_base_dirs 可追溯）
2. 空变更（无字段提供）→ 不产生审计事件
3. POST /api/system/locale → CONFIG_UPDATE 审计事件

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import app.integrated_app.routes.system.settings as settings_module
from tests.conftest import csrf_post


class TestConfigAudit:
    def test_settings_update_emits_audit_with_changed_keys(self, test_app, monkeypatch):
        events = []
        monkeypatch.setattr(settings_module, "audit_event", lambda event, **kw: events.append((event, kw)))

        resp = csrf_post(
            test_app,
            "/api/system/settings",
            json={"seed": 42, "allowed_base_dirs": ["outputs/", "data/uploads/"]},
        )

        assert resp.status_code == 200
        assert len(events) == 1
        event, fields = events[0]
        assert event == "CONFIG_UPDATE"
        assert "restore.seed" in fields["keys"]
        assert "runtime.security.allowed_base_dirs" in fields["keys"]

    def test_empty_update_emits_no_audit(self, test_app, monkeypatch):
        events = []
        monkeypatch.setattr(settings_module, "audit_event", lambda event, **kw: events.append(event))

        resp = csrf_post(test_app, "/api/system/settings", json={})

        assert resp.status_code == 200
        assert events == []

    def test_locale_change_emits_audit(self, test_app, monkeypatch):
        events = []
        monkeypatch.setattr(settings_module, "audit_event", lambda event, **kw: events.append((event, kw)))

        resp = csrf_post(test_app, "/api/system/locale", json={"locale": "en"})

        assert resp.status_code == 200
        assert len(events) == 1
        event, fields = events[0]
        assert event == "CONFIG_UPDATE"
        assert fields["keys"] == ["i18n.default_locale"]
