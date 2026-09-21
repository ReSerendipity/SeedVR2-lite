"""引用可用性门禁（scripts/check_local_only_refs.py）的本地可测逻辑。

该门禁的口径：追踪文件不得把读者指向「本机有、克隆里没有」的未分发文件（幻影引用），
除非就地写明可获取性说明或用文件级「本地未分发引用」声明收编。历史背景与规则见
docs/CODING_STANDARDS.md 第 5 节。

这里锁定的是判定的纯逻辑部分（取词 / 产物豁免 / 声明块解析 / 多义路径消歧），
使门禁本身有回归保护——门禁一旦被改松，幻影引用就会重新长回来。

本地未分发引用：AGENTS.md、docs/agents/、docs/project/、docs/README.md
（本文件是门禁的回归测试，上面这些路径是**用例里的夹具字符串**，不是供人打开的引用；
按门禁自身口径，被测试的行为必须能出现在测试里，故用文件级声明收编而非改写用例）
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CHECKER = _REPO_ROOT / "scripts" / "check_local_only_refs.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_local_only_refs", _CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # noqa: S301 - 仓库内受控脚本，非不可信输入
    return module


_gate = _load_checker()


def _init_git_repo(root: Path) -> Path:
    """在 `root`（tmp_path）里 git init 一个可控最小仓，作为判定用例的自足夹具。

    结构（均相对 `root`）：
    - 已追踪：.gitignore、README.md、docs/CODING_STANDARDS.md、docs/DOD.md
    - 被 .gitignore 排除且磁盘真实存在：AGENTS.md、docs/agents/GOTCHAS.md、docs/README.md

    这样「未分发幻影 / 目录前缀命中 / 同名优先追踪」三种磁盘状态都能在夹具里稳定复现，
    不再依赖本机真实仓库是否恰好存在这些本地治理文档（fresh clone / CI 里它们通常不存在，
    旧实现只能靠 pytest.skip 放行，等于门禁核心判定在 CI 上从没被验证过）。
    """

    def run(*args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)  # noqa: S603

    run("init", "-q")
    run("config", "user.email", "gate-test@example.com")
    run("config", "user.name", "local-ref-gate fixture")

    (root / ".gitignore").write_text("AGENTS.md\ndocs/agents/\ndocs/README.md\n", encoding="utf-8")
    (root / "README.md").write_text("# root readme (tracked)\n", encoding="utf-8")
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "CODING_STANDARDS.md").write_text("# tracked public standard\n", encoding="utf-8")
    (docs / "DOD.md").write_text("# tracked definition of done\n", encoding="utf-8")
    # 被忽略但磁盘真实存在——幻影引用应当指向它们
    (root / "AGENTS.md").write_text("# maintainer-local governance doc\n", encoding="utf-8")
    agents = docs / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "GOTCHAS.md").write_text("# maintainer-local gotchas\n", encoding="utf-8")
    (docs / "README.md").write_text("# maintainer-local docs readme\n", encoding="utf-8")

    run("add", ".gitignore", "README.md", "docs/CODING_STANDARDS.md", "docs/DOD.md")
    run("commit", "-q", "-m", "local-ref-gate fixture")
    return root


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """把 `check_local_only_refs` 的模块级 ROOT 改指到夹具仓。

    tracked_files() / ignored_paths() / resolve_candidates() 全部读该全局 ROOT，
    重定向后整套判定即在受控磁盘状态上运行，用例无需再探测真实仓库。
    """
    root = _init_git_repo(tmp_path)
    monkeypatch.setattr(_gate, "ROOT", root)
    return root


class TestContractWords:
    """口径关键词是文档与门禁之间的契约，改名等于把门禁拆了。"""

    def test_marker_covers_repo_standard_phrase(self):
        assert "未随仓库分发" in _gate.MARKERS
        assert "维护者本地" in _gate.MARKERS
        assert "maintainer-local" in _gate.MARKERS

    def test_declaration_anchor_is_documented(self):
        assert _gate.DECLARATION_ANCHOR == "本地未分发引用"

    def test_checker_itself_is_exempt(self):
        # 脚本正文逐条列出被忽略路径，属实现材料，不能自触发
        assert "scripts/check_local_only_refs.py" in _gate.SELF_EXEMPT
        # .gitignore 的内容就是排除清单，不是读者指针
        assert ".gitignore" in _gate.SELF_EXEMPT


class TestTokenExtraction:
    def test_picks_file_and_dir_tokens(self):
        line = "详见 `AGENTS.md` 与 docs/agents/GOTCHAS.md，或目录 docs/project/"
        found = set(_gate.TOKEN_RE.findall(line))
        assert "AGENTS.md" in found
        assert "docs/agents/GOTCHAS.md" in found
        assert "docs/project/" in found

    def test_ignores_url_and_placeholder_paths(self):
        # URL 片段前一个字符是 `/`，被 lookbehind 挡住；占位符含 < > 不在字符类里
        for line in ("https://example.com/static/app.js", "C:\\Users\\<用户名>\\x.md", "/home/<user>/docs/a.md"):
            assert "app.js" not in _gate.TOKEN_RE.findall(line)


class TestArtifactExemption:
    def test_runtime_and_build_dirs_are_not_references(self):
        for rel in (
            "desktop/node_modules/vue/index.js",
            "tests/playwright-report",
            "outputs/image/2026.png",
            "dist/bundles/SeedVR2.zip",
        ):
            assert _gate.is_artifact(rel), rel

    def test_doc_paths_are_not_artifacts(self):
        assert not _gate.is_artifact("docs/agents/GOTCHAS.md")
        assert not _gate.is_artifact("precheck.ps1")


class TestDeclaredRefs:
    def test_multi_line_declaration_is_read_as_one_block(self):
        lines = [
            "> **本地未分发引用**：以下条目里的 `AGENTS.md`、`docs/plans/`、",
            "> `docs/reports/`、`examples/`、`precheck.ps1` 均为维护者本地文件，未随仓库分发；",
            "> 对外可执行的口径见 `docs/CODING_STANDARDS.md` 第 5 节。",
            "",
            "## 1.5.8",
        ]
        declared = _gate.declared_refs(lines)
        assert "AGENTS.md" in declared
        assert "precheck.ps1" in declared
        assert "docs/plans" in declared
        assert "examples" in declared
        # 空行之后不再属于声明块
        assert "1.5.8" not in declared

    def test_no_declaration_yields_empty(self):
        assert _gate.declared_refs(["随便一句话，没有锚点。"]) == set()


class TestLocalOnlyResolution:
    """`hits_local_only` 只在「所有解析结果都未分发」时才判幻影。

    全部经 `git_repo` 夹具取 tracked/ignored：磁盘状态由 tmp_path 里的最小仓钉死，
    不再读真实仓库的 ignored_paths()，因此本机 / CI / fresh clone 下都是 0 skip、且
    真正锁住判定逻辑（把 hits_local_only 改松会立即让幻影用例转红）。
    """

    def test_ignored_file_is_flagged(self, git_repo):
        assert git_repo.is_dir()
        tracked = _gate.tracked_files()
        ignored = _gate.ignored_paths()
        assert _gate.hits_local_only("docs/DOD.md", "AGENTS.md", tracked, ignored) == "AGENTS.md"

    def test_file_under_ignored_dir_is_flagged(self, git_repo):
        assert git_repo.is_dir()
        tracked = _gate.tracked_files()
        ignored = _gate.ignored_paths()
        target = "docs/agents/GOTCHAS.md"
        assert _gate.hits_local_only("docs/DOD.md", target, tracked, ignored) == target

    def test_tracked_file_is_never_flagged(self, git_repo):
        assert git_repo.is_dir()
        tracked = _gate.tracked_files()
        ignored = _gate.ignored_paths()
        assert _gate.hits_local_only("docs/DOD.md", "docs/CODING_STANDARDS.md", tracked, ignored) is None

    def test_ambiguous_basename_prefers_tracked(self, git_repo):
        # 「根 README.md」被写成 README.md 时，同目录解释会命中未分发的 docs/README.md；
        # 只要有一种解释是已跟踪文件，就不能判幻影（否则 README 类引用全是假阳性）。
        assert git_repo.is_dir()
        tracked = _gate.tracked_files()
        ignored = _gate.ignored_paths()
        assert _gate.hits_local_only("docs/发布检查清单.md", "README.md", tracked, ignored) is None

    def test_unknown_path_is_not_reported(self, git_repo):
        assert git_repo.is_dir()
        tracked = _gate.tracked_files()
        ignored = _gate.ignored_paths()
        assert _gate.hits_local_only("docs/DOD.md", "no/such/file.md", tracked, ignored) is None
