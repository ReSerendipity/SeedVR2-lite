#!/usr/bin/env python3
"""上游 vendored 树同步探针：上游 `models/**` (ByteDance-Seed/SeedVR) ↔ 本地 `model_lib/**`。

为什么不用文件哈希
------------------
`model_lib/` 不是逐字 vendored 副本，而是**改过名、加过中文 docstring、现代化过类型注解的 fork**。
实测 39 个共有文件的 SHA256 **100% 不相等**，但去掉 docstring / 注解 / import 后只剩 25 个真有
符号级差异、14 个完全相同。按文件哈希建门禁会永久输出「全部分叉」，然后被所有人忽略 —— 探针于是
失去信号价值。本脚本因此**按顶层符号**比对。

四种状态
--------
- ``same``            归一化后符号体一致（可经 confirmed 别名配对，见 ``ALIAS_MAP``）。
- ``upstream_only``   上游有、本地没有 —— **真待同步信号**。
- ``local_only``      本地有、上游没有 —— **资产不是债**。例：``causal_inflation_lib.py`` 里本地
                      新增的 NVIDIA conv3d 内存缺陷规避，上游至今没有。
- ``body_differs``    两边都有但符号体不同 —— 需人工判定（本地适配 / 上游修了 bug 皆可能）。

三条设计约束（缺一条这个探针就会骗人）
--------------------------------------
1. **自检门禁先于出报告**：``SELF_CHECKS`` 硬断言若干「已知答案」的符号状态。断言不过 ⇒ 比对链路
   本身坏了 ⇒ 退出码 2 且**拒绝出报告**。批量探测最常见的严重误判都是「链路坏了、输出却看着很像
   结论」，这一条是唯一防线。
2. **网络不可用 ≠ 代码已分叉**：本机境外访问为注入式阻断且**带时段波动**，同一仓库两次探测可能一
   通一不通。网络失败单独走退出码 3，输出「无法判定」，绝不写「无差异」。
3. **别名只认逐条按签名核对过的**：``ALIAS_MAP`` 每条带 status。``confirmed`` 参与 same 判定；
   ``candidate`` 只用于并排展示并标注待复核，**不**被当成一致 —— 按名字相似度自动配对会把真实漂移
   掩成「已同步」，那是比误报更糟的失效方向。

用法
----
    python scripts/check_upstream_sync.py             # 拉上游、比对、出报告
    python scripts/check_upstream_sync.py --offline   # 复用缓存，报告标注「可能过期」
    python scripts/check_upstream_sync.py --stdout    # 只打印不落盘
    python scripts/check_upstream_sync.py --json      # 机器可读，供未来 CI 消费

退出码：0 无待同步 / 1 有待同步或待复核 / 2 自检门禁失败（结论不可信）/ 3 网络不可用（无法判定）
/ 4 本地缓存损坏（与 3 分开：删缓存重来 vs 换出口重试，处置动作相反）

对 `model_lib/` **只读**；唯一写入是 `docs/reports/` 下的报告（该目录不随仓库分发）。
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import datetime as dt
import hashlib
import json
import subprocess  # nosec B404（仅以参数列表调用 git，无 shell=True）
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 报告文本含中文与 ⚠ 等字符：Windows 控制台默认 GBK，一打印就 UnicodeEncodeError，门禁于是拿栈
# 回溯顶替可读报告（仍然非零退出，但人看不到该修什么）。
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        with contextlib.suppress(OSError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent

DEFAULT_UPSTREAM_URL = "https://github.com/ByteDance-Seed/SeedVR.git"
DEFAULT_REV = "main"
# 默认放系统临时目录：不污染仓库，也不假设 /tmp 存在（本机是 Win32）。
DEFAULT_CACHE_DIR = Path(tempfile.gettempdir()) / "seedvr2_upstream_sync"
DEFAULT_REPORT_DIR = ROOT / "docs" / "reports"

# 本地树名 -> 上游树相对路径
TREES: dict[str, str] = {
    "dit": "models/dit",
    "dit_v2": "models/dit_v2",
    "video_vae_v3": "models/video_vae_v3",
}

# 本地为「让目录成为包」而新增、上游根本没有的文件：不参与比对。
LOCAL_ONLY_FILES: frozenset[str] = frozenset(
    {
        "dit/__init__.py",
        "dit_v2/__init__.py",
        "video_vae_v3/__init__.py",
        "video_vae_v3/modules/__init__.py",
    }
)

# 已登记的本地主动改动（口径与 model_lib/SOURCE.md 的 Modifications 一致）。命中此表的差异不计入
# 「未登记」提醒，但仍照常报告内容。
KNOWN_LOCAL_CHANGES: dict[str, str] = {
    "dit_v2/window.py": "本地新增 win / win_by_size 两个 get_window_op 分支与 5 个未引用辅助函数",
    "dit_v2/normalization.py": "本地新增 RMSNorm",
    "dit/attention.py": "本地新增 _sdpa_varlen_fallback（无 flash_attn 时的 SDPA 退化路径）",
    "dit_v2/attention.py": "本地新增 _sdpa_varlen_fallback",
    "video_vae_v3/modules/causal_inflation_lib.py": "本地新增 NVIDIA conv3d 内存缺陷规避（上游无）",
    "dit_v2/na.py": "本地只保留 flatten/unflatten/concat_idx/repeat_concat_idx/window_idx，未搬其余上游辅助函数",
}

# 别名表：文件 -> {上游符号: (本地符号, status)}。status ∈ {"confirmed", "candidate"}。
ALIAS_MAP: dict[str, dict[str, tuple[str, str]]] = {
    "dit/nablocks/__init__.py": {
        # 两边同为 `(block_type: str)` 的工厂函数，职责一致。
        "get_nablock": ("get_na_block", "confirmed"),
    },
    "dit/patch.py": {
        # 上游 NaPatchIn/NaPatchOut 是 nn.Module，本地 PatchifyEmbed/UnPatchify 也是；但本地另有
        # 模块级 patchify/unpatchify 函数承担上游 PatchIn/PatchOut 的角色，关系未逐行确认。
        "NaPatchIn": ("PatchifyEmbed", "candidate"),
        "NaPatchOut": ("UnPatchify", "candidate"),
    },
    "dit/rope.py": {
        # 上游是 RotaryEmbeddingBase → RotaryEmbedding3d → NaRotaryEmbedding3d 三档层次，本地压平成
        # 单个 rotary_emb + apply_rope。**这是接口收窄不是改名**，故永不标 confirmed。
        "RotaryEmbedding3d": ("rotary_emb", "candidate"),
    },
}

# 自检门禁：(文件, 符号, 期望状态)。全部由 2026-09-22 的上机核查确立。
SELF_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("dit_v2/window.py", "make_720Pwindows_bysize", "same"),
    ("dit_v2/window.py", "make_shifted_720Pwindows_bysize", "same"),
    ("dit_v2/window.py", "get_window_op", "body_differs"),
    ("dit_v2/window.py", "window_partition", "local_only"),
    ("dit/rope.py", "NaRotaryEmbedding3d", "upstream_only"),
    ("dit/na.py", "window_idx", "upstream_only"),
    ("dit/nablocks/__init__.py", "get_nablock", "same"),
    ("video_vae_v3/modules/causal_inflation_lib.py", "var:NVIDIA_CONV3D_MEMORY_BUG_WORKAROUND", "local_only"),
)

STATE_SAME = "same"
STATE_NEEDS_SYNC = "upstream_only"
STATE_LOCAL_AHEAD = "local_only"
STATE_REVIEW = "body_differs"

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_SELFCHECK_FAILED = 2
EXIT_NETWORK = 3
EXIT_CACHE_INVALID = 4


class UpstreamLayoutError(RuntimeError):
    """上游缓存里找不到预期的树目录 —— 通常是上游重构了布局，需人工更新 ``TREES``。"""


class UpstreamCacheError(RuntimeError):
    """缓存目录本身不可用（半删除 / 中断克隆留下的残骸）。

    必须与"网络不可用"分开：两者处置动作相反 —— 前者删缓存重来，后者换出口重试。
    把本地缓存损坏报成网络问题，会让人对着一个永远重试不好的错误去查远程。
    """


def _repo_usable(cache: Path, timeout: int) -> bool:
    """.git 目录存在 ≠ 仓库可用：缺 HEAD/config 的残骸会让 git 直接判 not a git repository。"""
    return _git(["-C", str(cache), "rev-parse", "HEAD"], timeout).returncode == 0


class _StripStyle(ast.NodeTransformer):
    """抹掉不影响语义的写法差异：注解、返回类型、装饰器、Generic 基类下标、纯注解赋值。"""

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.annotation = None
        node.type_comment = None
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        self.generic_visit(node)
        node.returns = None
        node.type_comment = None
        node.decorator_list = []
        return node

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815 —— ast 按方法名派发，名字不可改

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        self.generic_visit(node)
        node.decorator_list = []
        node.bases = [b.value if isinstance(b, ast.Subscript) else b for b in node.bases]
        node.keywords = [k for k in node.keywords if k.arg != "metaclass"]
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST | None:
        self.generic_visit(node)
        if node.value is None:
            return None
        return ast.Assign(targets=[node.target], value=node.value)


def _drop_docstrings(tree: ast.AST) -> None:
    """原地移除所有模块/类/函数体首处的字符串常量。"""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(getattr(body[0], "value", None), ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body.pop(0)


def _symbol_hash(node: ast.AST) -> str:
    # 顶层符号名归一化：ALIAS_MAP 问的是「是不是同一份实现」，而名字不同正是它要容纳的情况。
    # 不归一的话每个 confirmed 别名都会必然判 body_differs，别名表等于永久失效。
    # ⚠ 只能在**副本**上改名：调用侧用 `out[node.name] = _symbol_hash(node)` 取键，改在原节点上
    #   会让同文件所有函数塌成同一个键（右值先于键求值）。
    reparsed = ast.parse(ast.unparse(node)).body[0]
    if isinstance(getattr(reparsed, "name", None), str):
        reparsed.name = "__symbol__"
    return hashlib.sha256(ast.dump(_StripStyle().visit(reparsed)).encode("utf-8")).hexdigest()[:12]


def collect_symbols(path: Path) -> dict[str, str]:
    """解析一个 .py，返回 {符号名: 归一化哈希}。模块级变量以 ``var:`` 前缀计入，import 不计。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    _drop_docstrings(tree)
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = _symbol_hash(node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[f"var:{target.id}"] = _symbol_hash(node)
    return out


@dataclass
class FileDiff:
    """单个文件的符号级比对结果。符号一律以**上游名**记账，便于自检直接查表。"""

    rel: str
    same: list[str] = field(default_factory=list)
    upstream_only: list[str] = field(default_factory=list)
    local_only: list[str] = field(default_factory=list)
    body_differs: list[str] = field(default_factory=list)
    aliased: list[tuple[str, str, str]] = field(default_factory=list)  # (上游名, 本地名, status)

    @property
    def has_findings(self) -> bool:
        return bool(self.upstream_only or self.local_only or self.body_differs)

    @property
    def needs_attention(self) -> bool:
        return bool(self.upstream_only or self.body_differs)

    def state_of(self, symbol: str) -> str | None:
        for state, bucket in (
            (STATE_SAME, self.same),
            (STATE_NEEDS_SYNC, self.upstream_only),
            (STATE_LOCAL_AHEAD, self.local_only),
            (STATE_REVIEW, self.body_differs),
        ):
            if symbol in bucket:
                return state
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.rel,
            "same": self.same,
            "upstream_only": self.upstream_only,
            "local_only": self.local_only,
            "body_differs": self.body_differs,
            "aliased": [{"upstream": u, "local": loc, "status": s} for u, loc, s in self.aliased],
            "known_local_change": self.rel in KNOWN_LOCAL_CHANGES,
        }


def compare_file(rel: str, up_path: Path, local_path: Path) -> FileDiff:
    """比对一对文件。confirmed 别名允许「上游名 ≠ 本地名」仍判 same；candidate 别名不参与判定。"""
    up_syms = collect_symbols(up_path)
    lo_syms = collect_symbols(local_path)
    alias = ALIAS_MAP.get(rel, {})
    diff = FileDiff(rel=rel)
    consumed_local: set[str] = set()

    for name, digest in sorted(up_syms.items()):
        # 只有 confirmed 别名参与配对；candidate 仅供并排展示 —— 按名字相似度猜配对会把真实漂移
        # 掩成「已同步」，那是比误报更糟的失效方向。上游名在本地也存在时同样以同名为准。
        partner = alias.get(name)
        if partner is None or partner[1] != "confirmed" or name in lo_syms or partner[0] not in lo_syms:
            partner = None
        local_name = partner[0] if partner else (name if name in lo_syms else None)
        if local_name is None:
            diff.upstream_only.append(name)
            continue
        consumed_local.add(local_name)
        bucket = diff.same if lo_syms[local_name] == digest else diff.body_differs
        bucket.append(name)

    diff.local_only = [n for n in sorted(lo_syms) if n not in consumed_local and n not in up_syms]
    for name, (local_name, status) in sorted(alias.items()):
        if name in up_syms and local_name in lo_syms:
            diff.aliased.append((name, local_name, status))
    return diff


def _git(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603（固定可执行名 + 参数列表，无 shell、无用户输入拼接）
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _short_head(cache: Path, timeout: int) -> str:
    res = _git(["-C", str(cache), "rev-parse", "--short", "HEAD"], timeout)
    return res.stdout.strip() if res.returncode == 0 else "?"


def ensure_upstream(cache: Path, url: str, rev: str, *, offline: bool, timeout: int) -> tuple[bool, str]:
    """备好上游工作副本。返回 (是否可用, 状态说明)；说明会写进报告头部。"""
    if (cache / ".git").is_dir():
        if not _repo_usable(cache, timeout):
            raise UpstreamCacheError(
                f"缓存目录不可用：{cache} 有 .git 但 git 不认（多半是中断的克隆或被打断的清理）。"
                "删掉该目录、或换 --cache-dir 后重跑 —— 重试网络不会解决它。"
            )
        if offline:
            return True, f"复用本地缓存（--offline，**可能过期**）：HEAD={_short_head(cache, timeout)}"
        fetched = _git(["-C", str(cache), "fetch", "--depth", "1", "origin", rev], timeout)
        if fetched.returncode != 0:
            return False, (
                f"git fetch 失败（rc={fetched.returncode}）：{(fetched.stderr or '').strip()[:200] or '无输出'}"
                f"｜缓存 HEAD={_short_head(cache, timeout)}"
            )
        checked = _git(["-C", str(cache), "checkout", "--quiet", "--detach", "FETCH_HEAD"], timeout)
        if checked.returncode != 0:
            return False, f"git checkout 失败（rc={checked.returncode}）：{(checked.stderr or '').strip()[:200]}"
        return True, f"已更新到 {rev}：{_short_head(cache, timeout)}"

    if offline:
        return False, f"--offline 但缓存不存在：{cache}（先不带 --offline 跑一次以克隆）"

    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists() and any(cache.iterdir()) and not (cache / ".git").is_dir():
        raise UpstreamCacheError(
            f"缓存目录非空且不是 git 仓库：{cache}。删掉它或换 --cache-dir 后重跑 —— 这不是网络问题。"
        )
    if cache.exists() and not any(cache.iterdir()):
        cache.rmdir()
    for clone_args in (
        ["clone", "--depth", "1", "--branch", rev, url, str(cache)],
        ["clone", "--depth", "1", url, str(cache)],
    ):
        res = _git(clone_args, timeout)
        if res.returncode == 0:
            return True, f"已克隆：{_short_head(cache, timeout)}"
        last_err = (res.stderr or "").strip()[:300] or "无输出"
    return False, f"git clone 失败：{last_err}"


def resolve_upstream_trees(cache: Path) -> dict[str, Path] | None:
    """定位缓存里的各棵上游树；缺任一棵即视为上游目录布局变了，需人工更新 TREES。"""
    out: dict[str, Path] = {}
    for key, up_rel in TREES.items():
        path = cache / up_rel
        if not path.is_dir():
            return None
        out[key] = path
    return out


def compare_pair(rel: str, up_path: Path, local_path: Path) -> FileDiff:
    """上游没有这个文件时整文件记为本地新增，而不是让 read_text 抛 FileNotFoundError。"""
    if up_path.is_file():
        return compare_file(rel, up_path, local_path)
    return FileDiff(rel=rel, local_only=["<整个文件为本地新增>"])


def _build_lookup(cache: Path) -> dict[str, FileDiff]:
    """扫描三棵树，返回 {相对路径: FileDiff}（LOCAL_ONLY_FILES 不参与比对）。"""
    trees = resolve_upstream_trees(cache)
    if trees is None:
        raise UpstreamLayoutError(f"缓存里找不到预期的上游树：{cache / 'models'}")
    lookup: dict[str, FileDiff] = {}
    for key, up_tree in trees.items():
        local_tree = ROOT / "model_lib" / key
        for local_path in sorted(local_tree.rglob("*.py")):
            rel = f"{key}/{local_path.relative_to(local_tree).as_posix()}"
            if rel in LOCAL_ONLY_FILES:
                continue
            lookup[rel] = compare_pair(rel, up_tree / local_path.relative_to(local_tree).as_posix(), local_path)
    return lookup


def run_self_checks(lookup: dict[str, FileDiff]) -> list[str]:
    """核对「已知答案」。返回失败清单；非空即比对链路不可信，调用侧必须中止且不出报告。"""
    failures: list[str] = []
    for rel, symbol, expected in SELF_CHECKS:
        entry = lookup.get(rel)
        if entry is None:
            failures.append(f"{rel}::{symbol} 期望 {expected}，实际 未纳入比对（文件缺失或被排除）")
            continue
        actual = entry.state_of(symbol)
        if actual != expected:
            failures.append(f"{rel}::{symbol} 期望 {expected}，实际 {actual}")
    return failures


def render_report(*, url: str, rev: str, note: str, stale: bool, lookup: dict[str, FileDiff]) -> str:
    diffs = [d for d in lookup.values() if d.has_findings]
    clean = len(lookup) - len(diffs)
    needs = [d for d in diffs if d.upstream_only]
    review = [d for d in diffs if d.body_differs]
    ahead = [d for d in diffs if d.local_only]
    aliased = [(d.rel, u, loc, s) for d in diffs for u, loc, s in d.aliased]
    today = dt.date.today().strftime("%Y%m%d")

    lines = [
        f"# 上游同步核查报告 upstream_sync_{today}",
        "",
        f"- 生成时间：{dt.datetime.now().isoformat(timespec='seconds')}",
        f"- 上游：`{url}` @ `{rev}` — {note}",
        f"- 范围：`model_lib/{{{', '.join(TREES)}}}` ↔ 上游 `models/{{{', '.join(TREES)}}}`",
        "- 方法：去 docstring / 去注解 / 去装饰器 / 去 import 后**按顶层符号**取哈希（非文件哈希，理由见脚本头注释）",
    ]
    if stale:
        lines.append("- ⚠ **本次使用缓存（--offline），基线可能过期** —— 本报告不得作为「上游无变更」的证据")
    lines += [
        "",
        "## 摘要",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 纳入比对文件 | {len(lookup)} |",
        f"| 归一化后完全相同 | {clean} |",
        f"| 有差异 | {len(diffs)} |",
        f"| **待同步**（上游有 / 本地缺） | {len(needs)} |",
        f"| 待人工判定（同名符号内容不同） | {len(review)} |",
        f"| 本地领先（上游无此符号） | {len(ahead)} |",
        "",
    ]

    def table(title: str, items: list[FileDiff], attr: str, empty_note: str) -> list[str]:
        out = [f"## {title}", ""]
        if not items:
            return out + [f"（无）— {empty_note}", ""]
        out += ["| 文件 | 符号 | 已登记本地改动 |", "|---|---|---|"]
        for d in items:
            names = ", ".join(f"`{s}`" for s in getattr(d, attr))
            reg = KNOWN_LOCAL_CHANGES.get(d.rel, "")
            mark = f"是：{reg}" if reg else "**否（需登记或跟进）**"
            out.append(f"| `{d.rel}` | {names} | {mark} |")
        out.append("")
        return out

    lines += table("待同步：上游有、本地缺", needs, "upstream_only", "本轮上游没有本地缺失的符号")
    lines += table(
        "待人工判定：同名符号内容不同", review, "body_differs", "可能是本地适配（正常），也可能是上游修了 bug（该跟进）"
    )
    lines += table("本地领先：上游没有的符号", ahead, "local_only", "属资产，勿当分叉处理")

    if aliased:
        lines += ["## 别名映射命中", "", "| 文件 | 上游名 | 本地名 | status | 处理 |", "|---|---|---|---|---|"]
        for rel, up, loc, status in aliased:
            action = "已按同一符号比对" if status == "confirmed" else "**待人工复核，未按 same 判定**"
            lines.append(f"| `{rel}` | `{up}` | `{loc}` | {status} | {action} |")
        lines.append("")

    unregistered = sorted(d.rel for d in diffs if d.rel not in KNOWN_LOCAL_CHANGES)
    lines += ["## 未登记的本地主动改动", ""]
    if unregistered:
        lines.append("以下文件有差异但不在 `KNOWN_LOCAL_CHANGES`：请判定是「上游变了该跟进」还是「本地改过该登记」。")
        lines.append("")
        lines += [f"- `{rel}`" for rel in unregistered]
    else:
        lines.append("（无）所有差异文件均已在 `KNOWN_LOCAL_CHANGES` 登记")
    lines += ["", "---", "", "> 本探针只读：不写 `model_lib/`。任何修复建议须走禁区授权流程。", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="上游 vendored 树符号级同步探针（详见模块 docstring）")
    parser.add_argument("--upstream-url", default=DEFAULT_UPSTREAM_URL)
    parser.add_argument("--rev", default=DEFAULT_REV, help="上游分支或 commit（默认 main）")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--timeout", type=int, default=900, help="单次 git 操作超时（秒）")
    parser.add_argument("--offline", action="store_true", help="不联网、复用缓存；报告会标注可能过期")
    parser.add_argument("--stdout", action="store_true", help="只打印报告，不写文件")
    parser.add_argument("--json", action="store_true", dest="as_json", help="输出机器可读 JSON")
    args = parser.parse_args(argv)

    try:
        ok, note = ensure_upstream(
            args.cache_dir, args.upstream_url, args.rev, offline=args.offline, timeout=args.timeout
        )
    except subprocess.TimeoutExpired:
        print(f"[网络不可用] git 操作超时（>{args.timeout}s）。本机境外阻断带时段波动，请稍后重试。", file=sys.stderr)
        return EXIT_NETWORK
    except UpstreamCacheError as exc:
        print(f"[缓存损坏] {exc}", file=sys.stderr)
        print("[结论] 无法判定 —— 这是本地缓存问题，不是网络问题，重试出口无用。", file=sys.stderr)
        return EXIT_CACHE_INVALID
    if not ok:
        print(f"[网络不可用] 无法取得上游基线：{note}", file=sys.stderr)
        print("[结论] **无法判定**（不是「无差异」）。请稍后重试或换出口。", file=sys.stderr)
        return EXIT_NETWORK

    try:
        lookup = _build_lookup(args.cache_dir)
    except UpstreamLayoutError as exc:
        print(f"[结构不符] {exc}", file=sys.stderr)
        print("[结论] 无法判定 —— 上游可能重构了目录布局，需人工更新 TREES 映射。", file=sys.stderr)
        return EXIT_NETWORK

    failures = run_self_checks(lookup)
    if failures:
        print(
            f"[自检门禁失败] {len(failures)}/{len(SELF_CHECKS)} 条已知答案不符 —— 比对链路不可信，**不出报告**。",
            file=sys.stderr,
        )
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        print("[提示] 先修探针本身（归一化规则 / 别名表 / 期望值），再谈同步结论。", file=sys.stderr)
        return EXIT_SELFCHECK_FAILED
    print(f"[自检门禁] {len(SELF_CHECKS)} 条已知答案全部命中，比对链路可信。")

    diffs = [d for d in lookup.values() if d.has_findings]
    findings = sum(1 for d in diffs if d.needs_attention)

    if args.as_json:
        payload = {
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "upstream": {"url": args.upstream_url, "rev": args.rev, "note": note, "stale": args.offline},
            "summary": {
                "compared_files": len(lookup),
                "identical_files": len(lookup) - len(diffs),
                "diverged_files": len(diffs),
                "files_needing_attention": findings,
                "self_checks_passed": len(SELF_CHECKS),
            },
            "files": [d.to_dict() for d in sorted(diffs, key=lambda x: x.rel)],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        report = render_report(url=args.upstream_url, rev=args.rev, note=note, stale=args.offline, lookup=lookup)
        if args.stdout:
            print(report)
        else:
            args.report_dir.mkdir(parents=True, exist_ok=True)
            out = args.report_dir / f"upstream_sync_{dt.date.today().strftime('%Y%m%d')}.md"
            out.write_text(report, encoding="utf-8", newline="\n")
            print(f"[written] {out}")

    if findings:
        print(f"[结论] {findings} 个文件存在待同步或待复核符号 —— 详见报告。")
        return EXIT_FINDINGS
    print("[结论] 上游无待同步符号；所有差异均已登记为本地主动改动。")
    return EXIT_CLEAN


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
