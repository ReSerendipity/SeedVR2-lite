"""tests/test_e2e_navigation_guard.py —— E2E 导航防复发静态守卫（不启浏览器，纯文本结构断言）。

为什么需要这条守卫
------------------
`app.js` 每次页面载入都会 `new EventSource('/api/sse/events')`，而 api-mocks 的 SSE 用
`route.fulfill` 返回**有限**响应体：服务端关闭连接后 EventSource 依规范自动重连，形成重连
风暴。此时发起导航，firefox 会在「旧文档拆载 + 新文档 domcontentloaded」之间死锁，表现为
`page.reload` 或 `page.goto` 的 60000ms 超时（`navigationTimeout`）。对策是导航前先
`closeSseBeforeNavigation(page)`。

这个坑历史上修了三次、每次只覆盖当时看到的文件：
  - main push run #64/#65/#68/#69/#70/#71：`page.reload` 超时 → 只给
    `reloadApplyingClientState()` 加了关闭；
  - 其后把关闭提进 `BasePage.navigate()`（同一族的 goto 侧）。当时把触发实例记成
    「run#103 的 theme.spec.ts」——2026-09-25 逐份日志核对：run#103 的 firefox 有 31 条红，
    `theme.spec.ts:187` 那两条报的是 `locator.click: Timeout 30000ms`（`#agreementModal`
    的 overlay 拦住了 `#btnThemeToggle` 的点击，与导航无关），整份日志里 goto/reload 超时
    0 次；theme.spec.ts 报过的导航类超时只有 reload 族与 #79 的 beforeEach 测试超时；
  - PR #131：`performance.spec.ts` 的 10 处裸 goto → 只修了那一个文件；
  - CI run **#163**（#131 合并之后）：`network-conditions.spec.ts:104` 又卡
    `page.goto: Timeout 60000ms`——取样里唯一有日志实证的 goto 超时。
也就是说"下一次别再漏"完全靠人记，而它已经连续漏了三次。本守卫把它变成机械约束。

（上述编号取自 2026-09-25 的取样：25 份 firefox job 日志——2026-09-02→09-25 之间全部
12 个 firefox 红的 main push run、若干绿 run 作对照、PR run #103/#104/#105/#161/#163/
#164/#166；8 月及更早的红 run 未扫。改写这段前先按同样方法重取证据。）

判据是结构性的（不靠变量名猜）：Playwright 的 `Page.goto(url)` 必须带参数，而 6 个页面对象
把自己的入口声明为零参数 `async goto()`（内部 `await this.navigate(this.path)`，已受保护）——
所以「带参数的 `.goto(`」必然需要前置关闭；「零参数的 `.goto()`」必然是页面对象调用。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPECS = sorted((REPO_ROOT / "tests" / "specs").glob("*.ts"))
PAGES = sorted((REPO_ROOT / "tests" / "pages").glob("*.ts"))

GOTO_CALL = re.compile(r"^(?P<indent>\s*)(?:await\s+)?(?P<recv>[A-Za-z_][\w.]*)\.goto\((?P<arg>[^)]*)\)")
GOTO_DEF = re.compile(r"\basync\s+goto\s*\((?P<params>[^)]*)\)\s*:")
CLOSE_CALL = "closeSseBeforeNavigation"


def _is_comment(line: str) -> bool:
    return line.strip().startswith(("//", "*", "/*"))


def _read(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


class TestSpecsCloseSseBeforeRawGoto:
    """tests/specs/*.ts 里每一处带参数的 .goto( 前一行必须是同 receiver 的关闭调用。"""

    def test_specs_exist_and_are_scanned(self):
        assert len(SPECS) >= 10, f"只扫到 {len(SPECS)} 个 spec，守卫可能失效（路径变了？）"

    def test_every_raw_goto_is_preceded_by_sse_close(self):
        offenders: list[str] = []
        checked = 0
        for spec in SPECS:
            lines = _read(spec)
            for i, line in enumerate(lines):
                m = GOTO_CALL.match(line)
                if not m or _is_comment(line):
                    continue
                if m.group("arg").strip() == "":
                    continue  # 页面对象的零参数 goto()，经 BasePage.navigate() 已受保护
                checked += 1
                prev = lines[i - 1] if i else ""
                if f"{CLOSE_CALL}({m.group('recv')})" not in prev:
                    offenders.append(f"{spec.name}:{i + 1}: {line.strip()[:64]}")
        assert checked >= 50, f"只检查到 {checked} 处裸 goto，正则可能没匹配上（守卫形同虚设）"
        assert not offenders, (
            "以下导航调用没先关 SSE，firefox 下会偶发/稳定卡在 page.goto: Timeout 60000ms：\n  "
            + "\n  ".join(offenders)
            + f"\n修法：在调用前加 `await {CLOSE_CALL}(<同一个 receiver>);`"
            "（helper 在 tests/utils/wait-helpers.ts；页面对象请改走 navigate()）"
        )

    def test_files_using_the_helper_import_it(self):
        missing: list[str] = []
        for spec in SPECS:
            text = spec.read_text(encoding="utf-8")
            if CLOSE_CALL + "(" in text and CLOSE_CALL not in text.split("\n\n")[0] and "wait-helpers" not in text:
                missing.append(spec.name)
        assert not missing, f"用到了 {CLOSE_CALL} 却没从 @utils/wait-helpers 导入：{missing}"


class TestPageObjectsStayOnTheSafePath:
    """页面对象的 goto() 必须仍是零参数且经 navigate()——否则第 2 类保护就漏了。"""

    def test_base_navigate_closes_sse_first(self):
        lines = _read(REPO_ROOT / "tests" / "pages" / "base.page.ts")
        body = "\n".join(lines)
        assert "async navigate(" in body, "BasePage.navigate() 不见了"
        nav_at = next(i for i, ln in enumerate(lines) if "async navigate(" in ln)
        window = "\n".join(lines[nav_at : nav_at + 8])
        assert (
            CLOSE_CALL in window.split("page.goto")[0]
        ), "BasePage.navigate() 未在 page.goto 之前关闭 SSE —— 所有页面对象都会失去保护"

    def test_page_object_goto_is_no_arg_and_delegates_to_navigate(self):
        offenders: list[str] = []
        scanned = 0
        for page_file in PAGES:
            if page_file.name == "base.page.ts":
                continue
            text = page_file.read_text(encoding="utf-8")
            for m in GOTO_DEF.finditer(text):
                scanned += 1
                if m.group("params").strip():
                    offenders.append(f"{page_file.name}: goto({m.group('params').strip()[:24]}) 带参数")
                    continue
                line_no = text[: m.start()].count("\n")
                body = "\n".join(text.splitlines()[line_no : line_no + 6])
                if "this.navigate(" not in body:
                    offenders.append(f"{page_file.name}: goto() 未委托 this.navigate()")
        assert scanned >= 5, f"只扫到 {scanned} 个页面对象 goto()，正则可能失效"
        assert not offenders, "页面对象导航绕开了受保护路径：\n  " + "\n  ".join(offenders)
