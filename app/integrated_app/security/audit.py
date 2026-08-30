# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""安全事件独立审计日志通道 (A09: Security Logging & Monitoring)。

与业务日志 (logs/app.log) 分离，专收安全相关事件，JSONL 结构化格式，
便于 SIEM/脚本消费与事后追溯。

使用方式:
    from app.integrated_app.security.audit import audit_event

    audit_event("CSRF_FAILURE", request=request, outcome="rejected")

事件字段（JSONL 每行一个对象）:
    ts:        ISO8601 本地时间
    event:     事件类型（CSRF_FAILURE / AUTH_FAILURE / AUTH_BAN /
               RATE_LIMITED / PATH_DENIED / INTEGRITY_FAILURE / ...）
    request_id: 请求追踪 ID（无请求上下文时为 null）
    ip:        客户端 IP（无请求上下文时为 null）
    其余 kwargs 展开为附加字段。

设计约束:
    - 审计写入失败绝不影响业务主流程（best-effort，异常吞掉只打 debug）
    - 单独 RotatingFileHandler（默认 10MB x 5），与业务日志互不干扰
    - 不记录密码/token 等凭据内容，调用方只传元数据
"""

import json
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 审计日志目录（与业务日志同目录，独立文件）
_AUDIT_LOG_DIR = Path(__file__).resolve().parents[3] / "logs"
_AUDIT_LOG_FILE = _AUDIT_LOG_DIR / "security_audit.log"

_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5

_audit_logger: logging.Logger | None = None


def _get_audit_logger() -> logging.Logger:
    """获取（惰性初始化的）审计 logger，独立文件 handler。"""
    global _audit_logger
    if _audit_logger is not None:
        return _audit_logger

    audit = logging.getLogger("seedvr2.security.audit")
    audit.setLevel(logging.INFO)
    audit.propagate = False  # 不进业务日志，避免双写
    if not audit.handlers:
        try:
            _AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                _AUDIT_LOG_FILE,
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            audit.addHandler(handler)
        except Exception as e:  # 只读文件系统等：审计降级为业务日志
            logger.debug(f"审计日志文件初始化失败，降级到业务日志: {e}")
    _audit_logger = audit
    return audit


def _request_metadata(request) -> dict:
    """从 Starlette/FastAPI 请求对象提取审计元数据（尽力而为）。"""
    meta: dict = {"request_id": None, "ip": None}
    if request is None:
        return meta
    try:
        meta["request_id"] = getattr(getattr(request, "state", None), "request_id", None)
        client = getattr(request, "client", None)
        meta["ip"] = getattr(client, "host", None) if client else None
        meta["method"] = request.method
        meta["path"] = request.url.path
    except Exception as e:  # 审计元数据提取失败不阻断
        logger.debug(f"审计元数据提取失败: {e}")
    return meta


def audit_event(event: str, request=None, **fields) -> None:
    """记录一条安全审计事件（JSONL，best-effort，绝不抛出）。

    Args:
        event: 事件类型常量（如 CSRF_FAILURE / AUTH_FAILURE / PATH_DENIED）。
        request: 当前请求对象（可选，用于自动附带 request_id/ip/method/path）。
        **fields: 附加审计字段（不得包含凭据明文）。
    """
    try:
        record = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event}
        record.update(_request_metadata(request))
        record.update(fields)
        _get_audit_logger().info(json.dumps(record, ensure_ascii=False, default=str))
    except Exception as e:  # noqa: BLE001 — 审计永不阻断业务
        logger.debug(f"审计事件写入失败: {e}")


def audit_log_path() -> Path:
    """返回审计日志文件路径（供运维检查/测试断言）。"""
    return _AUDIT_LOG_FILE
