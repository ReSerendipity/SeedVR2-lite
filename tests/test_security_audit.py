"""security/audit.py 独立安全审计日志通道测试

覆盖：
- JSONL 结构化写入与字段完整性
- request 元数据（request_id/ip/method/path）自动提取
- 不可序列化字段的异常安全（审计绝不抛出）
- CSRF 失败事件真实接入（端到端产生审计记录）
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.integrated_app.app_server import create_app
from app.integrated_app.security.audit import audit_event, audit_log_path


class FakeClient:
    host = "9.9.9.9"


class FakeUrl:
    path = "/api/restore/"


class FakeRequest:
    client = FakeClient()
    url = FakeUrl()
    method = "POST"

    def __init__(self):
        # 模拟 request.state.request_id（Starlette State 属性访问语义）
        self.state = SimpleNamespace(request_id="req-abc")


def _last_record() -> dict:
    lines = audit_log_path().read_text(encoding="utf-8").strip().splitlines()
    return json.loads(lines[-1])


class TestAuditEvent:
    """audit_event 核心行为"""

    def test_jsonl_write_and_fields(self, tmp_path):
        """事件应写入 JSONL 且字段完整"""
        audit_event("SELFTEST_BASIC", request=None, custom="v")
        rec = _last_record()
        assert rec["event"] == "SELFTEST_BASIC"
        assert rec["custom"] == "v"
        assert "ts" in rec and rec["request_id"] is None and rec["ip"] is None

    def test_request_metadata_extraction(self):
        """带 request 时应自动提取 request_id/ip/method/path"""
        audit_event("SELFTEST_META", request=FakeRequest())
        rec = _last_record()
        assert rec["ip"] == "9.9.9.9"
        assert rec["request_id"] == "req-abc"
        assert rec["method"] == "POST"
        assert rec["path"] == "/api/restore/"

    def test_never_raises_on_unserializable(self):
        """不可序列化字段不应抛异常（best-effort 语义）"""
        audit_event("SELFTEST_WEIRD", payload=object())
        assert _last_record()["event"] == "SELFTEST_WEIRD"


class TestAuditIntegration:
    """安全中间件真实接入审计"""

    def test_csrf_failure_writes_audit(self):
        """无 CSRF token 的 POST 应产生 CSRF_FAILURE 审计记录"""
        app = create_app({})
        with TestClient(app) as client:
            client.post("/api/restore/", files={"file": ("a.png", b"x", "image/png")})
        rec = _last_record()
        assert rec["event"] == "CSRF_FAILURE"
        assert rec["path"] == "/api/restore/"
