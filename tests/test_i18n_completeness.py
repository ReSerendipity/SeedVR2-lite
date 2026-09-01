"""i18n 取词完整性回归测试（2026-08-31 UI 精修 Stage A 引入）。

覆盖三类此前无任何门禁、只能靠肉眼截图发现的缺陷：

1. **模板裸键泄漏**：`t()` 未命中时返回键本身（真值），因此模板里
   `{{ t('x') or '中文' }}` 的回退永不生效，缺键会直接把 `restore.output_format`
   这类字符串渲染到页面上。
2. **前端取词漏登记**：base.html 曾以手写白名单向 `window.__I18N__` 暴露翻译键，
   JS 实际引用的键若忘了登记就静默退化成硬编码中文。现改为整表导出，
   本测试守住「JS 引用的每个键都能在注入表里取到」。
3. **版本号漂移**：模板里硬编码 v1.0.0 会与 pyproject 脱钩，
   本测试守住页面版本串跟随 get_app_version()。

运行：pytest tests/test_i18n_completeness.py -q
"""

import json
import re
from pathlib import Path

import pytest

LOCALES_DIR = Path(__file__).resolve().parent.parent / "app/integrated_app/locales"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app/integrated_app/templates"
LANGS = ["zh", "zh-TW", "en", "ja", "fr"]

PAGES = ["/", "/restore", "/history", "/system-status", "/settings"]

# 模板里 t('namespace.key') 形式的调用
T_CALL = re.compile(r"""\bt\(\s*['"]([A-Za-z0-9_.\-]+)['"]""")
# JS 里 I['namespace.key'] / I["key"] 形式的取词。
# 负向预查排除 I['prefix.' + expr] 这类动态拼接取词：字面量前缀无法静态校验，
# 由运行时的 || 兜底保证不崩。不排掉就会把 'status.' 当成键误报。
JS_LOOKUP = re.compile(r"""\bI\[\s*['"]([A-Za-z0-9_.\-]+)['"]""")


def _static_lookup_keys(source: str) -> set[str]:
    r"""只保留静态字面量取词，剔除动态拼接的字面量前缀。

    I['status.' + r.status] 这类写法会被正则抓出 'status.'，而合法的命名空间键
    永远不会以 '.' 结尾，据此判定为动态取词并跳过（由运行时的 || 兜底保证不崩）。
    注意别改用 \s*(?!\+) 排除：\s* 可回溯，匹配零个空格即可绕过 lookahead。"""
    return {k for k in JS_LOOKUP.findall(source) if not k.endswith(".")}


# 渲染结果中的裸键（命名空间.蛇形名），用于扫描可见文本
BARE_KEY = re.compile(r"\b[a-z]{3,}(?:_[a-z0-9]+){0,2}\.[a-z][a-z0-9_]{2,}\b")
# 可见文本里合法出现的花括号点号串（文件名/域名等），按需白名单
TEXT_ALLOWLIST = {
    "config.yaml",
    "app.log",
    "index.html",
    "style.css",
    "start.bat",
    "install.bat",  # 关于页快速开始正当提及安装脚本文件名，非 i18n 泄漏
    "package.json",
    "python.exe",
    "requirements.txt",
    "history.db",
    "model_lib",
}


def _load_all() -> dict[str, dict]:
    return {lang: json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8")) for lang in LANGS}


def _resolves(data: dict, key: str) -> bool:
    """按 i18n._resolve_key 的语义判断键是否可解析为字符串。"""
    if key in data and isinstance(data[key], str):
        return True
    node = data
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False
    return isinstance(node, str)


@pytest.mark.parametrize("lang", LANGS)
def test_template_keys_resolve_in_every_locale(lang: str) -> None:
    """模板引用的每个翻译键，都必须在全部 5 个语言文件里可解析。

    缺任何一个语言，该语言用户就会看到裸键（t() 未命中返回键名，
    而 `or '中文'` 回退因键名为真值而永不触发）。
    """
    data = _load_all()[lang]
    missing: list[str] = []
    for tpl in sorted(TEMPLATES_DIR.glob("*.html")):
        for key in T_CALL.findall(tpl.read_text(encoding="utf-8")):
            if not _resolves(data, key) and key not in missing:
                missing.append(key)
    assert not missing, f"{lang}.json 缺少模板用到的键: {sorted(missing)}"


def test_no_hardcoded_chinese_only_fallback_via_or_idiom() -> None:
    """禁止再写 `{{ t('x') or '中文' }}`。

    t() 未命中返回键本身（非空字符串），`or` 分支永远取不到，
    这个写法只会掩盖缺键、并在页面上泄漏裸键。
    需要兜底请用 `t('x', default='…')`，或者把键补进 5 个词表。
    """
    offenders: list[str] = []
    pattern = re.compile(r"""\{\{\s*t\(\s*['"][^'"]+['"]\s*\)\s+or\s+""")
    for tpl in sorted(TEMPLATES_DIR.glob("*.html")):
        for lineno, line in enumerate(tpl.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{tpl.name}:{lineno}")
    assert not offenders, f"失效的 or 回退写法（应改为补键或 t(k, default=…)）: {offenders}"


def test_js_referenced_keys_are_injected(test_app) -> None:
    """JS 里 I['…'] 取用的每个键，都必须出现在整表注入的 __I18N__ 中。

    这是旧手写白名单最容易出问题的地方：漏登记不会报错，只会静默显示中文。
    """
    client = test_app
    html = client.get("/restore").text
    start = html.find("window.__I18N__ = ")
    assert start != -1, "页面未注入 window.__I18N__"

    injected = set(re.findall(r'"([A-Za-z0-9_.\-]+)":', html[start : start + 200_000]))

    used: set[str] = set()
    for src in list(TEMPLATES_DIR.glob("*.html")) + list((TEMPLATES_DIR.parent / "static/js").glob("*.js")):
        used |= _static_lookup_keys(src.read_text(encoding="utf-8"))

    missing = sorted(k for k in used if k not in injected)
    assert not missing, f"JS 取词但 __I18N__ 未提供的键: {missing}"


@pytest.mark.parametrize("page", PAGES)
def test_rendered_pages_have_no_bare_key_in_visible_text(test_app, page: str) -> None:
    """页面可见文本里不得出现裸翻译键。"""
    resp = test_app.get(page)
    assert resp.status_code == 200, f"{page} -> HTTP {resp.status_code}"

    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", resp.text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", body)
    leaks = sorted({m.group(0) for m in BARE_KEY.finditer(text)} - TEXT_ALLOWLIST)
    assert not leaks, f"{page} 可见文本泄漏裸键: {leaks}"


def test_templates_have_balanced_attribute_quotes() -> None:
    """模板每行的双引号必须成对——未闭合的属性值会吞掉后续标签。

    浏览器解析器遇到 `title="系统状态>` 这类漏引号时不会报错，而是一路吞到
    下一个 `"` 才闭合，导致中间的标签（图标 <i>/<svg> 等）被当成属性消失，
    JS 的 querySelector 也永远拿不到它。控制台零输出，只能靠静态检查发现。
    """
    offenders: list[str] = []
    for tpl in sorted(TEMPLATES_DIR.glob("*.html")):
        for lineno, line in enumerate(tpl.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            # 注释行与 Jinja 控制行不参与属性解析，跳过避免误报
            if not stripped or stripped.startswith("<!--") or stripped.startswith("{%"):
                continue
            if stripped.count('"') % 2 == 1:
                offenders.append(f"{tpl.name}:{lineno}: {stripped[:100]}")
    assert not offenders, "属性引号未闭合（会静默吞掉后续标签）:\n" + "\n".join(offenders)


def test_page_version_follows_pyproject(test_app) -> None:
    """关于页与状态条的版本串必须来自 version.py，不得再硬编码。"""
    from app.integrated_app.version import get_app_version

    version = get_app_version()
    assert version and version != "unknown", f"get_app_version() 返回异常值 {version!r}"

    about = test_app.get("/settings").text
    assert f"v{version}" in about, f"关于页未展示当前版本 v{version}（疑似硬编码回潮）"
    assert "v1.0.0" not in about, "关于页仍残留硬编码 v1.0.0"


def test_history_stat_label_matches_aggregated_field() -> None:
    """统计卡标签必须与后端字段口径一致。

    /api/system/history/statistics 的 total_records 统计的是全部记录（含失败），
    标签若写「完成任务总数」就是自相矛盾（两条失败记录会显示「2 完成任务总数」）。
    """
    data = _load_all()
    for lang in LANGS:
        label = data[lang]["history"]["stat_total_records"]
        assert (
            "完成" not in label and "Completed" not in label and "terminées" not in label
        ), f"{lang}: stat_total_records 标签 {label!r} 仍把全量记录说成完成数"
