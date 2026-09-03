"""前后端契约门禁（A 类：前端调用 / 后端缺失）。

起源：审计发现前端 ``cancelBatch()`` 一直 ``POST
/api/restore/batch/{batch_id}/cancel``，而该路径后端**从未注册**。缺陷完全静默——
404 被 ``.catch`` 吞掉后仍然 toast「任务已取消」，用户以为停了，GPU 却继续跑完
剩下所有文件。静态读代码看不见，控制台一闪而过。

因此把 ``scripts/audit_api_consistency.py`` 的抽取与差集逻辑固化成断言：
以后任何「前端引用了后端没有的路径」或「前端引用了磁盘上不存在的静态资源」
都会让本用例直接变红，而不是等用户报「按钮没反应」。

B 类（后端存在 / 前端无入口）不在此断言范围内：那是一份需要人工定性的清单
（对外 API 面 / 探针 / 待清理端点），跑 ``python scripts/audit_api_consistency.py orphans`` 查看。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import audit_api_consistency as audit  # noqa: E402


def _frontend_inventory() -> dict[str, list[tuple[str, str]]]:
    refs: dict[str, list[tuple[str, str]]] = {}
    for path, src in audit.frontend_sources():
        for url, method in audit.extract_urls(src):
            refs.setdefault(url, []).append((audit.short_name(path), method or "-"))
    return refs


def test_frontend_extractor_is_not_silently_broken():
    """抽取器本身不能静默失效：一旦抽不到任何路径，下面的差集断言会假绿。"""
    refs = _frontend_inventory()
    api_refs = [p for p in refs if p.startswith("/api/")]
    assert len(api_refs) >= 25, f"前端 API 引用数异常偏少（{len(api_refs)}），疑似抽取器失效"
    # 拼接式调用（'/api/restore/' + id + '/cancel'）必须被展开成参数化路径，
    # 这正是当初漏掉批量取消缺口的调用形态
    assert "/api/restore/{}/cancel" in refs
    assert "/api/restore/batch/{}/cancel" in refs


def test_no_frontend_call_without_backend_route(test_app):
    """A 类硬门禁：前端引用的每个后端路径都必须真实存在（方法也要覆盖）。"""
    res = audit.check_routes(test_app.app.openapi())
    assert not res["a_missing"], "前端调用了后端不存在的路径：" + "; ".join(
        f"{path} ← {', '.join(f for f, _ in places)}" for path, places in res["a_missing"]
    )
    assert not res["a_method"], "前端使用的 HTTP 方法后端未提供（会拿 405）：" + "; ".join(
        f"{path} 前端 {used} vs 后端 {allowed}" for path, used, allowed, _ in res["a_method"]
    )


def test_no_missing_static_assets(test_app):
    """前端引用的 /static/ 资源必须在磁盘上存在（拼错一个 css/js 路径是静默 404）。"""
    res = audit.check_routes(test_app.app.openapi())
    assert not res["static_missing"], "引用了不存在的静态资源：" + ", ".join(path for path, _ in res["static_missing"])


def test_no_form_field_is_silently_dropped(test_app):
    """前端提交的表单字段必须全部被后端 Form 签名接收。

    FastAPI 对未声明的字段是**静默丢弃**：用户调了参数、看到参数被保存、
    但推理时毫无效果，也没有任何报错。
    """
    ff = audit.check_form_fields(test_app.app.openapi())
    assert not ff["dropped"], f"这些参数后端不接收，用户调了没效果：{ff['dropped']}"


def test_inline_handlers_reference_defined_functions():
    """内联事件处理器（onclick=...）引用的函数必须有定义，否则点击即 ReferenceError。"""
    problems = audit.check_inline_handlers()
    assert not problems, "内联处理器绑定了未定义的函数：\n" + "\n".join(problems)


def test_batch_lifecycle_endpoints_are_wired_both_ends(test_app):
    """批量任务的两条生命周期控制链路必须前后端都接通。

    取消（后端曾缺失）与重试（前端曾无入口）成对守住，避免再次只修一半。
    """
    paths = test_app.app.openapi()["paths"]
    cancel = "/api/restore/batch/{batch_id}/cancel"
    retry = "/api/restore/batch/{batch_id}/retry"
    assert cancel in paths and "post" in paths[cancel], "后端缺少批量取消端点（前端一直在调它）"
    assert retry in paths and "post" in paths[retry], "后端缺少批量失败重试端点"

    refs = set(_frontend_inventory())
    assert {audit.norm_backend(cancel), audit.norm_backend(retry)} <= refs, "批量取消/重试必须在前端有真实调用点"
