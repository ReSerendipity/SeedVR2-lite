#!/usr/bin/env python3
"""W3C Trace Context 传播中间件。

对应云原生评估报告 P3-8「分布式追踪上下文传播」的零依赖实现：
- 解析入站 ``traceparent`` 头（W3C Trace Context 规范），提取 trace-id；
  缺失或非法时生成新 trace-id（32 hex）。
- trace-id 注入 Python logging 上下文（ContextVar + filter），使全链路
  日志携带 trace 字段，与 request_id 中间件同款机制。
- 响应回写 ``traceparent`` 头，供上游（Ingress / 网关 / 前端）串联调用链。

设计取舍：
- 仅做上下文传播，不采样、不上报——接入 OpenTelemetry SDK 时，本中间件
  提供的 trace-id 可直接作为 Resource/Link 桥接，不需要改造路由层。

注册位置：
    在 ``app_server.create_app()`` 末尾注册（LIFO 语义下最先执行，
    完整覆盖后续所有中间件与 handler 的日志上下文）。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import re
import threading
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
_trace_id_local = threading.local()

# W3C Trace Context：version(2)-trace-id(32 hex)-parent-id(16 hex)-flags(2)
_TRACEPATTERN = re.compile(r"^([0-9a-fA-F]{2})-([0-9a-fA-F]{32})-([0-9a-fA-F]{16})-([0-9a-fA-F]{2})$")


def get_trace_id() -> str:
    """获取当前异步上下文的 trace_id（未设置时返回空字符串）。"""
    return _trace_id_var.get()


def set_trace_id(trace_id: str) -> None:
    """发布 trace_id 到 ContextVar 与线程本地镜像（供后台线程日志关联）。"""
    _trace_id_var.set(trace_id)
    _trace_id_local.trace_id = trace_id


class TracingMiddleware(BaseHTTPMiddleware):
    """解析 / 生成 W3C trace-id 并回写 traceparent 响应头。"""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        trace_id = self._extract_trace_id(request.headers.get("traceparent", ""))
        span_id = uuid.uuid4().hex[:16]

        token = _trace_id_var.set(trace_id)
        _trace_id_local.trace_id = trace_id
        try:
            request.state.trace_id = trace_id
            response = await call_next(request)
            # 回写 traceparent，保持上下游调用链可串联（采样位置置 01）
            response.headers["traceparent"] = f"00-{trace_id}-{span_id}-01"
            return response
        finally:
            with contextlib.suppress(ValueError, LookupError):
                _trace_id_var.reset(token)
            _trace_id_local.trace_id = "-"

    @staticmethod
    def _extract_trace_id(raw: str) -> str:
        """从入站 traceparent 提取合法 trace-id，非法则生成新 ID。

        Args:
            raw: 入站 traceparent 头原文。

        Returns:
            32 位小写 hex trace-id。
        """
        match = _TRACEPATTERN.match(raw.strip())
        if match:
            version, trace_id = match.group(1), match.group(2).lower()
            # W3C 规范：version 0x00 时 trace-id 不得全零
            if version == "00" and set(trace_id) != {"0"}:
                return trace_id
        return uuid.uuid4().hex


class TraceLogFilter(logging.Filter):
    """日志过滤器：为每条 LogRecord 注入 trace_id（缺省 '-'）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            trace_id = _trace_id_var.get("")
            if not trace_id:
                trace_id = getattr(_trace_id_local, "trace_id", "-") or "-"
            record.trace_id = trace_id
        return True
