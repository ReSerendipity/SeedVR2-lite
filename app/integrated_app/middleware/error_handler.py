"""FastAPI 全局异常处理器。

为所有自定义异常和标准 Python 异常注册 FastAPI exception handler，
返回统一结构的 JSON 响应，并区分普通 API 请求与 HTMX 增强请求。

统一错误信封（P0-1 响应契约统一）:
    成功: {"success": true, "data": ...}          （utils/response.respond_success）
    失败: {"success": false, "error": {"code": <错误码>, "message": <消息>, "detail": <详情>}}
    覆盖范围: RestoreError 体系 / HTTPException / 请求校验错误 / ValueError /
              MemoryError / FileNotFoundError / 兜底 Exception / CSRF·限流中间件

响应策略:
    - 普通 API 请求：返回统一 JSON 结构 {success: false, error: {code, message, detail}}
    - HTMX 请求：返回 HX-Trigger 头触发前端 Toast 提示，无响应体

安全策略:
    - 兜底异常处理器不向客户端泄露异常类型、堆栈或内部路径
    - 仅返回通用错误消息，异常详情通过 logger.exception 记录到服务端日志
    - 请求校验错误仅回传 loc/msg/type，不回显原始输入值
    - 防止攻击者通过错误信息指纹识别框架和库版本

设计模式:
    - 责任链模式：按异常类型匹配对应的处理器，未匹配则走兜底
    - 策略模式：根据请求类型（普通/HTMX）选择不同的响应格式
"""

import json
import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.integrated_app.exceptions import RestoreError
from app.integrated_app.utils.response import respond_error

logger = logging.getLogger(__name__)

# HTTP 状态码 → 业务错误码映射（覆盖常见状态；未列出的回退为 HTTP_<status>）
_STATUS_ERROR_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
    507: "INSUFFICIENT_STORAGE",
}


def _error_code_for_status(status: int) -> str:
    """按 HTTP 状态码取业务错误码，未映射的状态回退为 HTTP_<status>。"""
    return _STATUS_ERROR_CODES.get(status, f"HTTP_{status}")


def _build_error_body(exc: RestoreError) -> dict:
    """构建 RestoreError 的统一错误响应体。

    Args:
        exc: RestoreError 异常实例，包含 code、message、detail 属性

    Returns:
        dict: 结构化错误体 {success: false, error: {code, message, detail}}
    """
    return {
        "success": False,
        "error": {
            "code": exc.code,
            "message": exc.message,
            "detail": exc.detail,
        },
    }


def _is_htmx_request(request: Request) -> bool:
    """判断请求是否来自 HTMX 增强的前端。

    HTMX 请求会携带 HX-Request: true 头。

    Args:
        request: FastAPI 请求对象

    Returns:
        bool: HTMX 请求返回 True
    """
    return request.headers.get("HX-Request") == "true"


def _htmx_error_response(message: str, status: int = 400) -> Response:
    """为 HTMX 请求返回带 HX-Trigger 的错误响应，触发前端 Toast 提示。

    HTMX 会自动解析 HX-Trigger 头中的 JSON 并触发对应的客户端事件。

    Args:
        message: 错误提示消息
        status: HTTP 状态码，默认 400 Bad Request

    Returns:
        Response: 空响应体，通过 HX-Trigger 头传递错误信息
    """
    return Response(
        status_code=status,
        headers={
            "HX-Trigger": json.dumps(
                {"showToast": {"message": message, "type": "error"}},
                ensure_ascii=False,
            )
        },
    )


def _error_response(
    request: Request,
    *,
    code: str,
    message: str,
    status: int,
    detail: dict | list | None = None,
) -> Response:
    """统一错误响应出口：普通请求走 respond_error 信封，HTMX 请求走 HX-Trigger。

    所有处理器（RestoreError / HTTPException / 校验错误 / 内建异常 / 兜底）
    统一经由此函数构造响应，保证错误信封单一来源（P0-1）。

    Args:
        request: FastAPI 请求对象。
        code: 业务错误码字符串。
        message: 面向用户的错误消息（不得含堆栈/内部路径）。
        status: HTTP 状态码。
        detail: 附加详情字典或列表（默认空字典）。

    Returns:
        Response: JSONResponse 或 HTMX 触发响应。
    """
    if _is_htmx_request(request):
        return _htmx_error_response(message, status)
    return respond_error(code=code, message=message, status=status, detail=detail)


async def _restore_error_handler(request: Request, exc: RestoreError) -> Response:
    """处理所有 RestoreError 子类异常。

    RestoreError 是应用自定义业务异常基类，包含错误码和 HTTP 状态码映射。

    Args:
        request: FastAPI 请求对象
        exc: RestoreError 异常实例

    Returns:
        Response: 结构化错误响应
    """
    status = exc.http_status()
    logger.warning("RestoreError [%s] %s — %s", exc.code, exc.message, exc.detail)
    return _error_response(request, code=exc.code, message=exc.message, status=status, detail=exc.detail)


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """处理 HTTPException（含 FastAPI/Starlette 两类），统一进错误信封。

    P0-1 之前此类异常走 FastAPI 默认 {"detail": ...} 格式，与其它错误
    格式不一致；现在统一映射为 {success: false, error: {code, message, detail}}。

    Args:
        request: FastAPI 请求对象。
        exc: HTTPException 实例（detail 可为 str / dict / 其它）。

    Returns:
        Response: 结构化错误响应。
    """
    status = exc.status_code
    code = _error_code_for_status(status)
    headers = getattr(exc, "headers", None)
    detail = exc.detail
    if isinstance(detail, str):
        message = detail
        detail_out: dict | list = {}
    elif isinstance(detail, dict):
        message = str(detail.get("message") or code)
        detail_out = detail
    else:
        message = str(detail) if detail is not None else code
        detail_out = {}
    logger.info("HTTPException [%s] %s — %s %s", status, code, request.url.path, message)
    response = _error_response(request, code=code, message=message, status=status, detail=detail_out)
    if headers:
        response.headers.update(headers)
    return response


async def _request_validation_handler(request: Request, exc: RequestValidationError) -> Response:
    """处理请求参数校验错误（422），统一进错误信封。

    安全策略：仅回传 loc/msg/type，不回显用户原始输入值（input 字段丢弃），
    防止大体积输入或敏感字段被原样回显到响应与日志。

    Args:
        request: FastAPI 请求对象。
        exc: RequestValidationError 实例。

    Returns:
        Response: 422 结构化错误响应。
    """
    errors = [
        {"loc": list(err.get("loc", [])), "msg": str(err.get("msg", "")), "type": str(err.get("type", ""))}
        for err in exc.errors()
    ]
    logger.info("请求校验失败 [422]: %s — %d 处", request.url.path, len(errors))
    return _error_response(
        request,
        code="VALIDATION_ERROR",
        message="请求参数校验失败",
        status=422,
        detail={"errors": errors},
    )


async def _value_error_handler(request: Request, exc: ValueError) -> Response:
    """将 ValueError 包装为结构化响应。

    通常用于参数验证失败、配置错误等场景，返回 422 状态码。

    Args:
        request: FastAPI 请求对象
        exc: ValueError 异常实例

    Returns:
        Response: 结构化错误响应
    """
    logger.warning("ValueError: %s", exc)
    return _error_response(request, code="VALUE_ERROR", message=str(exc), status=422)


async def _memory_error_handler(request: Request, exc: MemoryError) -> Response:
    """将 MemoryError 包装为结构化响应。

    内存不足时返回友好提示，建议用户降低分辨率或缩小输入尺寸。

    Args:
        request: FastAPI 请求对象
        exc: MemoryError 异常实例

    Returns:
        Response: 结构化错误响应
    """
    logger.error("MemoryError: %s", exc)
    message = "系统内存不足，请尝试降低分辨率或缩小输入尺寸"
    return _error_response(request, code="INSUFFICIENT_RAM", message=message, status=422)


async def _file_not_found_error_handler(request: Request, exc: FileNotFoundError) -> Response:
    """将 FileNotFoundError 包装为结构化响应。

    文件不存在时返回 404 状态码。

    Args:
        request: FastAPI 请求对象
        exc: FileNotFoundError 异常实例

    Returns:
        Response: 结构化错误响应
    """
    logger.warning("FileNotFoundError: %s", exc)
    return _error_response(request, code="FILE_NOT_FOUND", message=str(exc), status=404)


async def _generic_error_handler(request: Request, exc: Exception) -> Response:
    """兜底处理所有未捕获的异常。

    安全策略 (D10):
        - 不向客户端泄露异常类型、堆栈或内部路径
        - 仅返回通用错误消息"服务器内部错误，请稍后重试"
        - 异常详情通过 logger.exception 记录完整堆栈到服务端日志
        - 不再返回 exception_type 字段，防止攻击者指纹识别框架和库版本

    Args:
        request: FastAPI 请求对象
        exc: 未被前面处理器捕获的任意异常

    Returns:
        Response: 通用 500 错误响应
    """
    logger.exception("未处理的异常 [%s]: %s", type(exc).__name__, exc)
    return _error_response(request, code="INTERNAL_ERROR", message="服务器内部错误，请稍后重试", status=500)


def register_error_handlers(app) -> None:
    """向 FastAPI 应用注册所有异常处理器。

    注册顺序：先注册具体异常类型，最后注册兜底 Exception。
    FastAPI 按注册顺序匹配，具体类型优先于通用类型。
    注意：StarletteHTTPException 处理器会接管 FastAPI 默认的
    {"detail": ...} 格式，请求校验错误同理（P0-1 统一信封）。

    Args:
        app: FastAPI 应用实例
    """
    app.add_exception_handler(RestoreError, _restore_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _request_validation_handler)

    app.add_exception_handler(ValueError, _value_error_handler)
    app.add_exception_handler(MemoryError, _memory_error_handler)
    app.add_exception_handler(FileNotFoundError, _file_not_found_error_handler)

    app.add_exception_handler(Exception, _generic_error_handler)
