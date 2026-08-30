"""云原生容器化相关端点测试。

覆盖范围（对应云原生评估报告 P1-P3 落地项）：
- /api/system/ready 就绪探针（P1-3）：200/503 语义
- /metrics Prometheus 文本格式暴露（P2-6）
- W3C traceparent 上下文传播（P3-8）
- SEEDVR2_ALLOWED_ORIGINS 环境变量 CORS 覆盖（P3-8）
- LOG_FORMAT=json 结构化 JSON 日志（P3-8）
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app.integrated_app.app_server import JSONLogFormatter, create_app
from app.integrated_app.config import load_config

pytestmark = pytest.mark.integration


class TestPrometheusMetricsEndpoint:
    """GET /metrics Prometheus 文本格式测试（报告 P2-6）"""

    def test_metrics_returns_prom_text_format(self, test_app):
        response = test_app.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert "version=0.0.4" in response.headers["content-type"]

    def test_metrics_contains_expected_metric_families(self, test_app):
        """核心指标族必须齐全：uptime / gpu / inference counter / cache"""
        body = test_app.get("/metrics").text
        for metric in (
            "seedvr2_uptime_seconds",
            "seedvr2_gpu_available",
            "seedvr2_inferences_total",
            "seedvr2_inference_successes_total",
            "seedvr2_cache_bytes",
        ):
            assert f"# TYPE {metric} " in body, f"missing metric family: {metric}"

    def test_metrics_values_are_parseable_floats(self, test_app):
        """每条指标行的值必须是合法浮点（非法值应被 _fmt 回退为 0.0）"""
        body = test_app.get("/metrics").text
        value_lines = [ln for ln in body.splitlines() if ln and not ln.startswith("#")]
        assert value_lines, "no metric samples found"
        for line in value_lines:
            _name, raw_value = line.rsplit(" ", 1)
            float(raw_value)  # 解析失败即断言失败

    def test_metrics_counters_reflect_recorded_inference(self, test_app):
        """记录一次成功推理后 counter 应同步 +1"""
        from app.integrated_app.metrics import metrics_collector

        before = test_app.get("/metrics").text
        base = next(ln for ln in before.splitlines() if ln.startswith("seedvr2_inferences_total "))
        base_value = float(base.rsplit(" ", 1)[1])

        metrics_collector.record_inference(success=True, duration=1.5, model_size="3b")

        after = test_app.get("/metrics").text
        now_value = float(
            next(ln for ln in after.splitlines() if ln.startswith("seedvr2_inferences_total ")).rsplit(" ", 1)[1]
        )
        assert now_value == pytest.approx(base_value + 1)


class TestReadinessProbe:
    """GET /api/system/ready 就绪探针测试（报告 P1-3）"""

    def test_ready_returns_200_when_not_loading(self, test_app):
        """非加载中状态应返回 200 ready，并暴露 model_loaded / gpu_available"""
        response = test_app.get("/api/system/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert isinstance(data["model_loaded"], bool)
        assert isinstance(data["gpu_available"], bool)

    def test_ready_returns_503_while_model_loading(self, test_app, monkeypatch):
        """模型加载中应返回 503 + Retry-After（startupProbe 重试窗口）"""
        manager = test_app.app.state.model_manager
        manager.get_status.return_value = {"model_loaded": False, "load_in_progress": True}
        try:
            response = test_app.get("/api/system/ready")
        finally:
            manager.get_status.return_value = {"loaded": False, "model_name": None}
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unavailable"
        assert data["reason"] == "model_loading"
        assert response.headers.get("retry-after") == "5"

    def test_ready_survives_model_status_exception(self, test_app, monkeypatch):
        """get_status 抛异常时探针应降级为未加载而非 5xx 崩溃"""
        manager = test_app.app.state.model_manager

        def _boom():
            raise RuntimeError("registry unavailable")

        manager.get_status.side_effect = _boom
        try:
            response = test_app.get("/api/system/ready")
        finally:
            manager.get_status.side_effect = None
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
        assert response.json()["model_loaded"] is False

    def test_ready_is_excluded_from_csrf(self, test_app):
        """就绪探针为 GET，应无需 CSRF token 即可访问（编排探针无 cookie 上下文）"""
        response = test_app.get("/api/system/ready")
        assert response.status_code == 200


class TestTracingMiddleware:
    """W3C traceparent 传播测试（报告 P3-8）"""

    def test_inbound_trace_id_is_preserved_and_echoed(self, test_app):
        """合法入站 traceparent：trace-id 保留并回写响应头"""
        trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
        response = test_app.get(
            "/api/system/ping",
            headers={"traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"},
        )
        assert response.status_code == 200
        echoed = response.headers["traceparent"]
        assert echoed.startswith(f"00-{trace_id}-")

    def test_missing_traceparent_generates_new_trace_id(self, test_app):
        """缺失 traceparent：生成 32 位 hex 新 trace-id 并回写"""
        response = test_app.get("/api/system/ping")
        echoed = response.headers["traceparent"]
        version, trace_id, span_id, flags = echoed.split("-")
        assert version == "00"
        assert len(trace_id) == 32
        int(trace_id, 16)  # 必须是合法 hex
        assert len(span_id) == 16

    def test_invalid_traceparent_is_replaced(self, test_app):
        """非法 traceparent（trace-id 全零违反 W3C 规范）：替换为新 ID"""
        response = test_app.get(
            "/api/system/ping",
            headers={"traceparent": "00-00000000000000000000000000000000-00f067aa0ba902b7-01"},
        )
        echoed = response.headers["traceparent"].split("-")[1]
        assert echoed != "0" * 32
        assert len(echoed) == 32


class TestCorsEnvOverride:
    """SEEDVR2_ALLOWED_ORIGINS 环境变量覆盖测试（报告 P3-8 / 反模式 #5）"""

    def _build_app(self, tmp_path):
        config = load_config()
        config.setdefault("history", {})["db_path"] = str(tmp_path / "history.db")
        config.setdefault("model", {})["auto_load"] = False
        config.setdefault("server", {})["auto_open_browser"] = False
        return create_app(config)

    def test_env_origins_override_config(self, tmp_path, monkeypatch):
        """环境变量白名单应覆盖 config.yaml，preflight 放行注入的域名"""
        monkeypatch.setenv("SEEDVR2_ALLOWED_ORIGINS", "https://seedvr2.example.com")
        app = self._build_app(tmp_path)
        with TestClient(app) as client:
            response = client.options(
                "/api/system/ping",
                headers={
                    "Origin": "https://seedvr2.example.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert response.headers.get("access-control-allow-origin") == "https://seedvr2.example.com"

    def test_env_origins_reject_unknown_origin(self, tmp_path, monkeypatch):
        """不在环境变量白名单内的来源应被 CORS 拒绝"""
        monkeypatch.setenv("SEEDVR2_ALLOWED_ORIGINS", "https://seedvr2.example.com")
        app = self._build_app(tmp_path)
        with TestClient(app) as client:
            response = client.options(
                "/api/system/ping",
                headers={
                    "Origin": "https://evil.example.net",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert "access-control-allow-origin" not in response.headers


class TestJSONLogFormatter:
    """LOG_FORMAT=json 结构化日志测试（报告 P3-8 / 12-Factor XI）"""

    def _make_record(self, msg: str) -> logging.LogRecord:
        return logging.LogRecord(
            name="seedvr2.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=42,
            msg=msg,
            args=(),
            exc_info=None,
        )

    def test_output_is_single_line_json_with_context_fields(self):
        import json as _json

        from app.integrated_app.middleware.request_id import RequestIDLogFilter
        from app.integrated_app.middleware.tracing import TraceLogFilter

        record = self._make_record("你好 SeedVR2")
        RequestIDLogFilter().filter(record)
        TraceLogFilter().filter(record)
        line = JSONLogFormatter().format(record)
        payload = _json.loads(line)  # 必须是合法 JSON（单行事件流）
        assert payload["msg"] == "你好 SeedVR2"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "seedvr2.test"
        assert "trace_id" in payload and "request_id" in payload

    def test_unserializable_message_falls_back_to_string(self):
        import json as _json

        record = self._make_record("bad %s")
        record.args = object()  # 不可格式化对象
        line = JSONLogFormatter().format(record)
        assert isinstance(_json.loads(line), dict)  # 不得抛异常，须降级为合法 JSON
