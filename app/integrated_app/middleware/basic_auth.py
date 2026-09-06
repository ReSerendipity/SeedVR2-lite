# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""HTTP Basic Auth 中间件

为 SeedVR2 提供 HTTP Basic Authentication 保护，
防止公网部署时的未授权访问 (CWE-306)。

启用方式 (config.yaml):
    security:
      auth:
        enable: true          # 启用 Basic Auth
        username: admin       # 用户名
        password: 'your-password'  # 明文密码 (建议使用环境变量注入)
        realm: SeedVR2        # WWW-Authenticate realm

安全建议:
    - 仅在 server.host 非 127.0.0.1 时启用 (公网/局域网部署)
    - 密码应通过环境变量 SEEDVR2_AUTH_PASSWORD 注入，避免明文存配置
    - 生产环境建议配合 HTTPS 使用 (Basic Auth 明文传输 Base64)
    - 静态资源 (CSS/JS/图片) 也受保护，浏览器会缓存凭据
"""

import base64
import hmac
import logging
import math
import os
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.integrated_app.utils.response import respond_error

logger = logging.getLogger(__name__)

# 单测可替换的单调时钟
_monotonic = time.monotonic


class AuthFailureTracker:
    """认证失败滑动窗口计数器 + 临时 IP 封禁。

    防止对 Basic Auth 端点的无限制暴力破解 (CWE-307):
    窗口期内失败次数达到上限后，该 IP 被临时封禁 ban_seconds 秒；
    期间所有认证请求直接拒绝（不再比对凭据）。成功认证清零该 IP 计数。

    实现与 rate_limit.py 同模式：内存滑动窗口，单事件循环下无锁；
    IP 条目超限时清理过期条目防内存无界增长。

    Attributes:
        max_failures: 触发封禁的窗口内失败次数上限。
        window_seconds: 失败计数滑动窗口时长（秒）。
        ban_seconds: 封禁时长（秒）。
    """

    _MAX_TRACKED_IPS = 1024

    def __init__(
        self,
        max_failures: int = 5,
        window_seconds: float = 300.0,
        ban_seconds: float = 600.0,
    ):
        """初始化失败追踪器。

        Args:
            max_failures: 触发封禁的窗口内失败次数上限，<=0 表示禁用。
            window_seconds: 失败计数滑动窗口时长（秒）。
            ban_seconds: 封禁时长（秒）。
        """
        self.max_failures = int(max_failures)
        self.window_seconds = float(window_seconds)
        self.ban_seconds = float(ban_seconds)
        # IP -> 窗口内失败时间戳队列
        self._failures: dict[str, deque[float]] = {}
        # IP -> 封禁起始时间
        self._banned: dict[str, float] = {}

    def _sweep(self, now: float) -> None:
        """内存护栏：条目超过阈值时清理过期失败记录与已解封条目。"""
        if len(self._failures) + len(self._banned) <= self._MAX_TRACKED_IPS:
            return
        stale = [ip for ip, hits in self._failures.items() if not hits or hits[-1] <= now - self.window_seconds]
        for ip in stale:
            del self._failures[ip]
        stale_bans = [ip for ip, t in self._banned.items() if now - t >= self.ban_seconds]
        for ip in stale_bans:
            del self._banned[ip]

    def is_banned(self, client_ip: str) -> bool:
        """判断 IP 是否处于封禁期。"""
        if self.max_failures <= 0:
            return False
        now = _monotonic()
        ban_start = self._banned.get(client_ip)
        if ban_start is not None:
            if now - ban_start < self.ban_seconds:
                return True
            # 封禁期满：解除并清空失败记录
            del self._banned[client_ip]
            self._failures.pop(client_ip, None)
        self._sweep(now)
        return False

    def record_failure(self, client_ip: str) -> None:
        """记录一次认证失败；达到上限时实施封禁。"""
        if self.max_failures <= 0:
            return
        now = _monotonic()
        hits = self._failures.setdefault(client_ip, deque())
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        hits.append(now)
        if len(hits) >= self.max_failures and client_ip not in self._banned:
            self._banned[client_ip] = now
            self._failures.pop(client_ip, None)
            logger.error(
                f"[SECURITY] 认证失败达 {self.max_failures} 次，IP {client_ip} " f"被封禁 {int(self.ban_seconds)}s"
            )
        self._sweep(now)

    def record_success(self, client_ip: str) -> None:
        """认证成功：清零该 IP 的失败计数与封禁状态。"""
        self._failures.pop(client_ip, None)
        self._banned.pop(client_ip, None)

    def retry_after_seconds(self, client_ip: str) -> int:
        """封禁剩余秒数（向上取整，最小 1）。"""
        ban_start = self._banned.get(client_ip)
        if ban_start is None:
            return 1
        return max(1, int(math.ceil(self.ban_seconds - (_monotonic() - ban_start))))


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """HTTP Basic Auth 中间件

    在 FastAPI 应用上注册后，所有请求需携带正确的
    Authorization: Basic <base64(username:password)> 头。

    安全特性:
        - 使用 hmac.compare_digest 常量时间比较，防止时序攻击
        - 密码可通过环境变量 SEEDVR2_AUTH_PASSWORD 覆盖配置文件值
        - 401 响应包含 WWW-Authenticate 头，触发浏览器凭据对话框
        - 认证失败滑动窗口计数 + 临时封禁，防暴力破解 (CWE-307)
    """

    def __init__(
        self,
        app,
        username: str,
        password: str,
        realm: str = "SeedVR2",
        max_auth_failures: int = 5,
        auth_failure_window_seconds: float = 300.0,
        auth_ban_seconds: float = 600.0,
    ):
        """初始化 Basic Auth 中间件。

        Args:
            app: ASGI 应用实例。
            username: 允许访问的用户名。
            password: 允许访问的密码。
            realm: WWW-Authenticate realm 值，用于浏览器对话框标题。
            max_auth_failures: 触发封禁的窗口内认证失败次数上限（<=0 禁用）。
            auth_failure_window_seconds: 失败计数滑动窗口时长（秒）。
            auth_ban_seconds: 认证封禁时长（秒）。
        """
        super().__init__(app)
        self._username = username
        # 环境变量优先级高于配置文件 (避免密码明文存配置)
        self._password = os.environ.get("SEEDVR2_AUTH_PASSWORD", password)
        self._realm = realm
        # 预计算期望的 Authorization 头值 (常量时间比较的基准)
        expected = f"{username}:{self._password}"
        self._expected_b64 = base64.b64encode(expected.encode("utf-8")).decode("ascii")
        self._failure_tracker = AuthFailureTracker(
            max_failures=max_auth_failures,
            window_seconds=auth_failure_window_seconds,
            ban_seconds=auth_ban_seconds,
        )
        logger.info(f"Basic Auth 已启用 (realm={realm}, user={username})")

    def _client_ip(self, request: Request) -> str:
        """提取客户端 IP（与 rate_limit 同策略：默认不信 XFF）。"""
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        """中间件分发方法，验证每个请求的 Authorization 头。

        Args:
            request: Starlette 请求对象。
            call_next: 下一个中间件/路由处理函数。

        Returns:
            Response: 验证通过则继续处理；封禁期返回 429；
                否则返回 401 Unauthorized。
        """
        client_ip = self._client_ip(request)

        # 封禁期内直接拒绝，不再比对凭据（先封禁判断，防绕过）
        if self._failure_tracker.is_banned(client_ip):
            retry_after = self._failure_tracker.retry_after_seconds(client_ip)
            logger.warning(
                f"认证封禁生效: {request.method} {request.url.path} from {client_ip} " f"(剩余 {retry_after}s)"
            )
            from app.integrated_app.security.audit import audit_event

            audit_event("AUTH_BAN", request=request, client_ip=client_ip, retry_after=retry_after)
            response = respond_error(
                code="AUTH_BANNED",
                message="认证失败次数过多，已被临时封禁",
                status=429,
                detail={"retry_after_seconds": retry_after},
            )
            response.headers["Retry-After"] = str(retry_after)
            response.headers["WWW-Authenticate"] = f'Basic realm="{self._realm}"'
            return response

        # 提取 Authorization 头
        auth_header = request.headers.get("Authorization", "")

        if auth_header.startswith("Basic "):
            # 提取 Base64 编码的凭据
            provided_b64 = auth_header[6:]
            # 常量时间比较，防止时序攻击
            if hmac.compare_digest(provided_b64, self._expected_b64):
                self._failure_tracker.record_success(client_ip)
                return await call_next(request)

        # 验证失败，记录并返回 401
        self._failure_tracker.record_failure(client_ip)
        logger.warning(f"未授权访问: {request.method} {request.url.path} from {client_ip}")
        from app.integrated_app.security.audit import audit_event

        audit_event("AUTH_FAILURE", request=request, client_ip=client_ip)
        response = respond_error(
            code="UNAUTHORIZED",
            message="SeedVR2 requires authentication.",
            status=401,
        )
        response.headers["WWW-Authenticate"] = f'Basic realm="{self._realm}"'
        return response


def resolve_auth_settings(config: dict) -> dict:
    """汇总生效的 Basic Auth 配置（环境变量优先于 config.yaml）。

    环境变量快捷通道（评估报告 R1 最小修复）：同时注入
    ``SEEDVR2_AUTH_USERNAME`` 与 ``SEEDVR2_AUTH_PASSWORD`` 即视为启用，
    容器/编排部署无需为镜像单独维护 config.yaml 的 security.auth 节。

    Args:
        config: 应用配置字典。

    Returns:
        dict: 归一化后的鉴权配置（enable/username/password/realm/防爆破参数）。
    """
    auth_cfg = config.get("security", {}).get("auth", {})
    env_username = os.environ.get("SEEDVR2_AUTH_USERNAME", "").strip()
    env_password = os.environ.get("SEEDVR2_AUTH_PASSWORD", "").strip()

    username = env_username or auth_cfg.get("username", "")
    # 环境变量优先级高于配置文件 (避免密码明文存配置)
    password = env_password or auth_cfg.get("password", "")

    enable = bool(auth_cfg.get("enable", False))
    if env_username and env_password:
        enable = True

    return {
        "enable": enable,
        "username": username,
        "password": password,
        "realm": auth_cfg.get("realm", "SeedVR2"),
        "max_auth_failures": int(auth_cfg.get("max_auth_failures", 5)),
        "auth_failure_window_seconds": float(auth_cfg.get("auth_failure_window_seconds", 300)),
        "auth_ban_seconds": float(auth_cfg.get("auth_ban_seconds", 600)),
    }


def should_enable_auth(config: dict) -> bool:
    """根据配置判断是否应启用 Basic Auth。

    启用条件:
        1. config.security.auth.enable == True，或同时注入
           SEEDVR2_AUTH_USERNAME + SEEDVR2_AUTH_PASSWORD 环境变量（容器部署快捷通道）
        2. 用户名和密码均非空

    Args:
        config: 应用配置字典。

    Returns:
        bool: True 表示应启用 Basic Auth。
    """
    settings = resolve_auth_settings(config)
    if not settings["enable"]:
        return False

    if not settings["username"] or not settings["password"]:
        logger.warning("Basic Auth 已配置 enable=true 但用户名或密码为空，跳过启用")
        return False

    return True


# 视为"容器/编排暴露部署"的环境标记：
# - Dockerfile 注入 SEEDVR2_DEPLOYMENT=container（权威来源，k8s+containerd 不创建 /.dockerenv）
# - 运行时标记为兜底探测（Docker / Podman）
_CONTAINER_MARKERS = ("/.dockerenv", "/run/.containerenv")
_ALLOW_UNAUTH_VALUES = {"1", "true", "yes"}


def is_containerized() -> bool:
    """探测当前进程是否运行在容器/编排环境中。

    Returns:
        bool: True 表示容器环境（镜像 ENV 声明或容器运行时标记存在）。
    """
    if os.environ.get("SEEDVR2_DEPLOYMENT", "").strip().lower() == "container":
        return True
    return any(os.path.exists(marker) for marker in _CONTAINER_MARKERS)


def ensure_exposure_auth(config: dict, *, containerized: bool | None = None) -> None:
    """暴露部署下的鉴权 fail-closed 检查（评估报告 R1）。

    容器/编排环境中服务必然绑定 0.0.0.0（config 校验器的回环强制管不到
    gunicorn --bind），无鉴权 + 端口映射/Ingress 即向局域网开放全部 API
    （GPU 推理 / 上传 / 目录端点）。未配置鉴权且未显式豁免时拒绝启动，
    并给出可操作的修复指引——fail-closed 优于仅靠文档警示。

    Args:
        config: 应用配置字典。
        containerized: 显式指定容器环境（测试注入用）；None 时自动探测。

    Raises:
        RuntimeError: 容器环境、未配置鉴权且未显式豁免。
    """
    if containerized is None:
        containerized = is_containerized()
    if not containerized:
        return

    optout = os.environ.get("SEEDVR2_ALLOW_UNAUTHENTICATED", "").strip().lower()
    if optout in _ALLOW_UNAUTH_VALUES:
        logger.warning(
            "[SECURITY] SEEDVR2_ALLOW_UNAUTHENTICATED=1：已在容器内显式豁免 Basic Auth。"
            "仅当端口映射严格限定回环（如 127.0.0.1:7870:7870）时才安全。"
        )
        return

    auth = resolve_auth_settings(config)
    if auth["enable"] and auth["username"] and auth["password"]:
        return

    raise RuntimeError(
        "[SECURITY] 检测到容器/编排部署（SEEDVR2_DEPLOYMENT=container 或容器运行时标记），"
        "但 Basic Auth 未生效。容器内服务绑定 0.0.0.0，无鉴权将向网络开放全部 API。"
        "修复方式（任选其一）：\n"
        "  1) 注入环境变量 SEEDVR2_AUTH_USERNAME 与 SEEDVR2_AUTH_PASSWORD（强密码，推荐）；\n"
        "  2) 在 config.yaml 配置 security.auth.enable=true / username / password；\n"
        "  3) 若端口映射严格限定回环（127.0.0.1:7870:7870），可设置 "
        "SEEDVR2_ALLOW_UNAUTHENTICATED=1 显式豁免。"
    )


def create_auth_middleware(config: dict):
    """根据配置创建 BasicAuthMiddleware 实例（工厂函数）。

    Args:
        config: 应用配置字典。

    Returns:
        BasicAuthMiddleware | None: 配置启用时返回中间件类，否则 None。
    """
    if not should_enable_auth(config):
        return None

    settings = resolve_auth_settings(config)
    return BasicAuthMiddleware(
        app=None,  # 由 add_middleware 填充
        username=settings["username"],
        password=settings["password"],
        realm=settings["realm"],
    )
