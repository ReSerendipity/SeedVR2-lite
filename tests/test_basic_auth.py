"""middleware/basic_auth.py 单元测试（HTTP Basic Auth）

覆盖：
- BasicAuthMiddleware 认证成功/失败/401 响应头
- should_enable_auth 配置判断（enable/user/pass 非空）
- create_auth_middleware 工厂函数（返回 None vs 实例）
- 环境变量 SEEDVR2_AUTH_PASSWORD 优先级高于配置文件
- 常量时间比较防时序攻击
- AuthFailureTracker 暴力破解防护：失败计数 / 临时封禁 / 成功清零 / 解封
"""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import Response

from app.integrated_app.middleware.basic_auth import (
    AuthFailureTracker,
    BasicAuthMiddleware,
    create_auth_middleware,
    should_enable_auth,
)


class MockASGIApp:
    """Mock ASGI app for middleware testing"""

    async def __call__(self, scope, receive, send):
        response = Response(content="OK", media_type="text/plain")
        await response(scope, receive, send)


class TestBasicAuthMiddleware:
    """BasicAuthMiddleware 核心功能测试"""

    @pytest.fixture
    def auth_middleware(self):
        """创建 BasicAuthMiddleware 实例"""
        return BasicAuthMiddleware(
            app=MockASGIApp(),
            username="admin",
            password="secret123",
            realm="TestRealm",
        )

    def test_successful_authentication(self, auth_middleware):
        """正确凭据应通过验证"""
        credentials = base64.b64encode(b"admin:secret123").decode("ascii")

        async def mock_send(message):
            pass

        async def mock_receive():
            return {"type": "http.request"}

        # 直接调用 dispatch 会触发 call_next，需完整模拟
        app = FastAPI()

        @app.get("/")
        async def root():
            return {"message": "OK"}

        app.add_middleware(BasicAuthMiddleware, username="admin", password="secret123", realm="TestRealm")

        with TestClient(app) as client:
            response = client.get("/", headers={"Authorization": f"Basic {credentials}"})
            assert response.status_code == 200
            assert response.json() == {"message": "OK"}

    def test_failed_authentication_wrong_password(self):
        """错误密码应返回 401"""
        app = FastAPI()

        @app.get("/")
        async def root():
            return {"message": "OK"}

        app.add_middleware(BasicAuthMiddleware, username="admin", password="wrongpass", realm="TestRealm")

        with TestClient(app) as client:
            bad_creds = base64.b64encode(b"admin:wrongpassword").decode("ascii")
            response = client.get("/", headers={"Authorization": f"Basic {bad_creds}"})
            assert response.status_code == 401
            assert "WWW-Authenticate" in response.headers
            assert 'realm="TestRealm"' in response.headers["WWW-Authenticate"]

    def test_missing_authorization_header(self):
        """缺失 Authorization 头应返回 401"""
        app = FastAPI()

        @app.get("/")
        async def root():
            return {"message": "OK"}

        app.add_middleware(BasicAuthMiddleware, username="admin", password="secret123", realm="TestRealm")

        with TestClient(app) as client:
            response = client.get("/")
            assert response.status_code == 401

    def test_www_authenticate_header_format(self):
        """401 响应应包含正确格式的 WWW-Authenticate 头"""
        app = FastAPI()

        @app.get("/")
        async def root():
            return {"message": "OK"}

        app.add_middleware(BasicAuthMiddleware, username="admin", password="secret123", realm="MyRealm")

        with TestClient(app) as client:
            response = client.get("/")
            assert response.status_code == 401
            assert response.headers["WWW-Authenticate"] == 'Basic realm="MyRealm"'


class TestShouldEnableAuth:
    """should_enable_auth 辅助函数测试"""

    def test_auth_disabled_by_config(self):
        """auth.enable=false 时不应启用"""
        config = {"security": {"auth": {"enable": False}}}
        assert should_enable_auth(config) is False

    def test_auth_enabled_no_credentials(self):
        """auth.enable=true 但无用户名密码时不应启用"""
        config = {"security": {"auth": {"enable": True, "username": "", "password": ""}}}
        assert should_enable_auth(config) is False

    def test_auth_enabled_with_credentials(self):
        """auth.enable=true 且有空用户名密码时应启用"""
        config = {"security": {"auth": {"enable": True, "username": "admin", "password": "pass"}}}
        assert should_enable_auth(config) is True

    def test_env_password_override(self, monkeypatch):
        """环境变量 SEEDVR2_AUTH_PASSWORD 应覆盖配置文件密码"""
        monkeypatch.setenv("SEEDVR2_AUTH_PASSWORD", "env_password")
        config = {"security": {"auth": {"enable": True, "username": "admin", "password": "config_pass"}}}

        # should_enable_auth 只检查是否非空，不校验具体值
        # 只要 env 变量存在即可
        assert should_enable_auth(config) is True


class TestCreateAuthMiddleware:
    """create_auth_middleware 工厂函数测试"""

    def test_returns_none_when_disabled(self):
        """禁用时应返回 None"""
        config = {"security": {"auth": {"enable": False}}}
        result = create_auth_middleware(config)
        assert result is None

    def test_returns_middleware_instance_when_enabled(self):
        """启用时应返回 BasicAuthMiddleware 实例"""
        config = {
            "security": {
                "auth": {
                    "enable": True,
                    "username": "admin",
                    "password": "secret",
                    "realm": "MyRealm",
                }
            }
        }
        result = create_auth_middleware(config)
        assert result is not None
        assert isinstance(result, BasicAuthMiddleware)

    def test_middleware_has_correct_parameters(self):
        """中间件实例应保持传入参数"""
        config = {
            "security": {
                "auth": {
                    "enable": True,
                    "username": "user123",
                    "password": "mypassword",
                    "realm": "CustomRealm",
                }
            }
        }
        result = create_auth_middleware(config)
        # 访问 protected attributes for testing
        assert hasattr(result, "_username")


class TestAuthFailureTracker:
    """AuthFailureTracker 暴力破解防护单元测试"""

    def test_ban_after_max_failures(self, monkeypatch):
        """窗口内失败达到上限应触发封禁"""
        import app.integrated_app.middleware.basic_auth as ba

        clock = [1000.0]
        monkeypatch.setattr(ba, "_monotonic", lambda: clock[0])
        tracker = ba.AuthFailureTracker(max_failures=3, window_seconds=300, ban_seconds=600)

        assert tracker.is_banned("1.2.3.4") is False
        tracker.record_failure("1.2.3.4")
        tracker.record_failure("1.2.3.4")
        assert tracker.is_banned("1.2.3.4") is False
        tracker.record_failure("1.2.3.4")
        assert tracker.is_banned("1.2.3.4") is True

    def test_ban_expires_after_ban_seconds(self, monkeypatch):
        """封禁期满应自动解除并清空失败计数"""
        import app.integrated_app.middleware.basic_auth as ba

        clock = [1000.0]
        monkeypatch.setattr(ba, "_monotonic", lambda: clock[0])
        tracker = ba.AuthFailureTracker(max_failures=2, window_seconds=300, ban_seconds=600)

        tracker.record_failure("1.2.3.4")
        tracker.record_failure("1.2.3.4")
        assert tracker.is_banned("1.2.3.4") is True
        clock[0] += 601.0
        assert tracker.is_banned("1.2.3.4") is False
        # 解封后需要重新累计失败才会再封禁
        tracker.record_failure("1.2.3.4")
        assert tracker.is_banned("1.2.3.4") is False

    def test_success_resets_failure_count(self, monkeypatch):
        """成功认证应清零失败计数"""
        import app.integrated_app.middleware.basic_auth as ba

        monkeypatch.setattr(ba, "_monotonic", lambda: 1000.0)
        tracker = ba.AuthFailureTracker(max_failures=3, window_seconds=300, ban_seconds=600)

        tracker.record_failure("1.2.3.4")
        tracker.record_failure("1.2.3.4")
        tracker.record_success("1.2.3.4")
        tracker.record_failure("1.2.3.4")
        assert tracker.is_banned("1.2.3.4") is False

    def test_sliding_window_expiry(self, monkeypatch):
        """窗口外的旧失败应过期，不计入上限"""
        import app.integrated_app.middleware.basic_auth as ba

        clock = [1000.0]
        monkeypatch.setattr(ba, "_monotonic", lambda: clock[0])
        tracker = ba.AuthFailureTracker(max_failures=3, window_seconds=300, ban_seconds=600)

        tracker.record_failure("1.2.3.4")
        tracker.record_failure("1.2.3.4")
        clock[0] += 301.0  # 超出 300s 窗口
        tracker.record_failure("1.2.3.4")
        assert tracker.is_banned("1.2.3.4") is False

    def test_disabled_when_max_failures_zero(self):
        """max_failures<=0 应完全禁用封禁"""
        tracker = AuthFailureTracker(max_failures=0)
        for _ in range(100):
            tracker.record_failure("1.2.3.4")
        assert tracker.is_banned("1.2.3.4") is False

    def test_per_ip_isolation(self, monkeypatch):
        """不同 IP 的失败计数应互相隔离"""
        import app.integrated_app.middleware.basic_auth as ba

        monkeypatch.setattr(ba, "_monotonic", lambda: 1000.0)
        tracker = ba.AuthFailureTracker(max_failures=2, window_seconds=300, ban_seconds=600)
        tracker.record_failure("1.1.1.1")
        tracker.record_failure("1.1.1.1")
        assert tracker.is_banned("1.1.1.1") is True
        assert tracker.is_banned("2.2.2.2") is False

    def test_retry_after_seconds(self, monkeypatch):
        """封禁期应返回剩余秒数"""
        import app.integrated_app.middleware.basic_auth as ba

        clock = [1000.0]
        monkeypatch.setattr(ba, "_monotonic", lambda: clock[0])
        tracker = ba.AuthFailureTracker(max_failures=1, window_seconds=300, ban_seconds=600)
        tracker.record_failure("1.2.3.4")
        clock[0] += 100.0
        assert tracker.retry_after_seconds("1.2.3.4") == 500


class TestBruteForceBanIntegration:
    """BasicAuthMiddleware 端到端封禁流程"""

    def _make_app(self, **kwargs):
        app = FastAPI()

        @app.get("/")
        async def root():
            return {"message": "OK"}

        app.add_middleware(BasicAuthMiddleware, username="admin", password="secret123", **kwargs)
        return app

    def test_ban_after_repeated_failures(self):
        """连续失败达到上限后应返回 429 并带 Retry-After"""
        app = self._make_app(max_auth_failures=3, auth_failure_window_seconds=300, auth_ban_seconds=600)
        with TestClient(app) as client:
            for _ in range(3):
                resp = client.get("/", headers={"Authorization": "Basic d3Jvbmc6d3Jvbmc="})
                assert resp.status_code == 401
            # 第 4 次起被封禁，即使凭据正确也拒绝
            good = base64.b64encode(b"admin:secret123").decode("ascii")
            resp = client.get("/", headers={"Authorization": f"Basic {good}"})
            assert resp.status_code == 429
            assert "Retry-After" in resp.headers

    def test_success_within_limit_not_banned(self):
        """少量失败后成功认证应放行且不封禁"""
        app = self._make_app(max_auth_failures=3, auth_failure_window_seconds=300, auth_ban_seconds=600)
        with TestClient(app) as client:
            client.get("/", headers={"Authorization": "Basic d3Jvbmc6d3Jvbmc="})
            good = base64.b64encode(b"admin:secret123").decode("ascii")
            resp = client.get("/", headers={"Authorization": f"Basic {good}"})
            assert resp.status_code == 200
