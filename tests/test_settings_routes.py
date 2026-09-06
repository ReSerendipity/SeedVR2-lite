"""系统设置路由测试 (routes/system/settings.py) — browse-dir / validate_path / open-explorer。"""

import pytest
from fastapi import HTTPException

from app.integrated_app.routes.system.settings import validate_path

pytestmark = pytest.mark.integration

# ---------- validate_path ----------


def test_validate_path_empty():
    with pytest.raises(HTTPException) as e:
        validate_path("")
    assert e.value.status_code == 400


def test_validate_path_dotdot():
    with pytest.raises(HTTPException) as e:
        validate_path("../etc/passwd")
    assert e.value.status_code == 400


def test_validate_path_outside_allowed_roots(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(HTTPException) as e:
        validate_path(str(outside), allowed_roots=[str(tmp_path / "allowed")])
    assert e.value.status_code == 403


def test_validate_path_inside_allowed_root(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    sub = allowed / "sub"
    sub.mkdir()
    resolved = validate_path(str(sub), allowed_roots=[str(allowed)])
    assert resolved == str(sub.resolve())


def test_validate_path_no_roots_ok(tmp_path):
    sub = tmp_path / "x"
    sub.mkdir()
    assert validate_path(str(sub), allowed_roots=[]) == str(sub.resolve())


def test_validate_path_sibling_prefix_forbidden(tmp_path):
    # R6 回归：历史 startswith 前缀匹配会把兄弟目录 allowed_evil 误放行
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    evil = tmp_path / "allowed_evil"
    evil.mkdir()
    with pytest.raises(HTTPException) as e:
        validate_path(str(evil), allowed_roots=[str(allowed)])
    assert e.value.status_code == 403


# ---------- browse_directory ----------


def _allow(client, *extra_dirs):
    """把目录追加进测试应用的路径白名单。

    get_config 依赖返回 app.state.config 同一 dict，原地修改即可生效。
    """
    cfg = client.app.state.config
    sec = cfg.setdefault("runtime", {}).setdefault("security", {})
    roots = list(sec.get("allowed_base_dirs", []))
    roots.extend(str(d) for d in extra_dirs)
    sec["allowed_base_dirs"] = roots


def test_browse_directory_empty_returns_whitelist_roots(test_app):
    resp = test_app.get("/api/system/browse-dir")
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_path"] == ""
    assert "items" in data
    # 根视图返回白名单根目录（而非盘符枚举），不泄漏白名单外文件系统结构
    assert len(data["items"]) >= 1
    assert all(item["type"] == "directory" for item in data["items"])


def test_browse_directory_lists_dir(test_app, tmp_path):
    _allow(test_app, tmp_path)
    sub = tmp_path / "subdir"
    sub.mkdir()
    f = tmp_path / "note.txt"
    f.write_text("hi")
    resp = test_app.get("/api/system/browse-dir", params={"path": str(tmp_path)})
    assert resp.status_code == 200
    items = resp.json()["items"]
    names = {i["name"] for i in items}
    assert "subdir" in names
    # show_files=False 时不返回文件
    assert "note.txt" not in names


def test_browse_directory_show_files(test_app, tmp_path):
    _allow(test_app, tmp_path)
    f = tmp_path / "data.bin"
    f.write_bytes(b"\x00" * 10)
    resp = test_app.get("/api/system/browse-dir", params={"path": str(tmp_path), "show_files": True})
    assert resp.status_code == 200
    files = [i for i in resp.json()["items"] if i["type"] == "file"]
    assert any(i["name"] == "data.bin" and i.get("size") == 10 for i in files)


def test_browse_directory_outside_whitelist_forbidden(test_app, tmp_path):
    # 未加入白名单的目录 → 403（评估报告 R1：目录枚举收敛到白名单）
    resp = test_app.get("/api/system/browse-dir", params={"path": str(tmp_path)})
    assert resp.status_code == 403


def test_browse_directory_sibling_prefix_forbidden(test_app, tmp_path):
    # R6 回归：allowed 为白名单根时，兄弟目录 allowed_evil 不得因
    # 字符串前缀相同而被放行（历史 startswith 匹配缺陷）
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    evil = tmp_path / "allowed_evil"
    evil.mkdir()
    _allow(test_app, allowed)
    resp = test_app.get("/api/system/browse-dir", params={"path": str(evil)})
    assert resp.status_code == 403


def test_browse_directory_not_found(test_app, tmp_path):
    _allow(test_app, tmp_path)
    resp = test_app.get("/api/system/browse-dir", params={"path": str(tmp_path / "missing_dir_xyz")})
    assert resp.status_code == 404


def test_browse_directory_not_a_dir(test_app, tmp_path):
    _allow(test_app, tmp_path)
    f = tmp_path / "file.txt"
    f.write_text("x")
    resp = test_app.get("/api/system/browse-dir", params={"path": str(f)})
    assert resp.status_code == 400


def test_browse_directory_dotdot_rejected(test_app):
    resp = test_app.get("/api/system/browse-dir", params={"path": "../../"})
    assert resp.status_code == 400


def test_browse_directory_parent_path(test_app, tmp_path):
    _allow(test_app, tmp_path)
    sub = tmp_path / "child"
    sub.mkdir()
    resp = test_app.get("/api/system/browse-dir", params={"path": str(sub)})
    assert resp.status_code == 200
    parent = resp.json()["parent_path"]
    assert parent == str(tmp_path.resolve())


def test_browse_directory_parent_clamped_to_roots(test_app, tmp_path):
    # 白名单根的父目录越出白名单 → parent_path 收敛为空（回到根视图）
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    _allow(test_app, allowed)
    resp = test_app.get("/api/system/browse-dir", params={"path": str(allowed)})
    assert resp.status_code == 200
    assert resp.json()["parent_path"] == ""


# ---------- open-explorer ----------


def _csrf_post(client, url, **kwargs):
    """带 CSRF token 的 POST（与 conftest.csrf_post 逻辑一致）。"""
    client.get("/")
    token = client.cookies.get("csrf_token")
    headers = kwargs.pop("headers", {})
    if token:
        headers["X-CSRF-Token"] = token
    return client.post(url, headers=headers, **kwargs)


def test_open_explorer_invalid_path(test_app):
    # 空路径 -> 400
    resp = _csrf_post(test_app, "/api/system/open-explorer", json={"path": "  "})
    assert resp.status_code == 400
    # 含 .. -> 400 或 403（取决于 realpath 顺序）
    resp2 = _csrf_post(test_app, "/api/system/open-explorer", json={"path": "..."})
    assert resp2.status_code in (400, 403)


def test_open_explorer_valid_dir(test_app, tmp_path):
    import sys

    import app.integrated_app.routes.system.settings as sm

    _allow(test_app, tmp_path)
    if sys.platform == "win32":
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(sm.os, "startfile") as mock_sf:
            resp = _csrf_post(test_app, "/api/system/open-explorer", json={"path": str(tmp_path)})
        assert mock_sf.called
    else:
        with __import__("unittest.mock", fromlist=["patch"]).patch.object(sm.subprocess, "Popen") as mock_popen:
            resp = _csrf_post(test_app, "/api/system/open-explorer", json={"path": str(tmp_path)})
        assert mock_popen.called
    assert resp.status_code == 200


def test_open_explorer_outside_whitelist_forbidden(test_app, tmp_path):
    # 未加入白名单 → 403（评估报告 R1）
    resp = _csrf_post(test_app, "/api/system/open-explorer", json={"path": str(tmp_path)})
    assert resp.status_code == 403


def test_open_explorer_file_path_rejected(test_app, tmp_path):
    # 收敛为仅目录：对文件 os.startfile 会以默认程序打开（潜在执行原语）
    _allow(test_app, tmp_path)
    f = tmp_path / "doc.txt"
    f.write_text("x")
    resp = _csrf_post(test_app, "/api/system/open-explorer", json={"path": str(f)})
    assert resp.status_code == 400
