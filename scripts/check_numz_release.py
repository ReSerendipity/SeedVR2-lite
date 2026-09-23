#!/usr/bin/env python3
"""权重源监控：HuggingFace 上的档位是否已被 config.yaml 纳入。

背景：本项目权重默认取自 `numz/SeedVR2_comfyUI`（fp16/fp8）、量化档取自
`Comfy-Org/SeedVR2`（int8_convrot/mxfp8/nvfp4）、GGUF 档可取自 `cmeka/SeedVR2-GGUF`。
上游一旦放出**新档位**（或撤掉旧档），本项目应当感知，而不是等用户下载失败才发现。

三条纪律（与 scripts/check_upstream_sync.py 同源）
----------------------------------------------
1. **已知答案自检先于结论**：``SELF_CHECKS`` 里钉死几条本次实测为真的仓库内容
   （例如 numz 必含 `seedvr2_ema_7b_sharp_fp8_e4m3fn.safetensors`、cmeka 必不含 `IQ1_S`）。
   自检不过 ⇒ 抓取/解析链路坏了 ⇒ 退出码 2 且**不出报告**。探测链路坏了却输出得像结论，
   是这类批处理脚本最贵的失败模式。
2. **网络不可用 ≠ 没有新档位**：本机境外访问为注入式阻断且带时段波动；失败一律走退出码 3，
   文案写「无法判定」，绝不写「一致」。
3. **只读**：不下载权重、不改 config.yaml。发现缺口只报告，补登记是人的决定
   （新增档位必须同时补 `sha256_*`，否则该档会静默失去 CWE-353 完整性校验）。

用法
----
    python scripts/check_numz_release.py                 # 拉清单、比对、出报告
    python scripts/check_numz_release.py --stdout
    python scripts/check_numz_release.py --json
    python scripts/check_numz_release.py --endpoint https://hf-mirror.com   # 国内镜像

退出码：0 无缺口 / 1 有新增或未登记 / 2 自检失败（结论不可信）/ 3 网络不可用（无法判定）
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        with contextlib.suppress(OSError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DEFAULT_REPORT_DIR = ROOT / "docs" / "reports"

# 监控的权重源。role 说明它在本项目里的身份，避免把不同源的字节当同一档互换。
REPOS: tuple[dict[str, str], ...] = (
    {"repo": "numz/SeedVR2_comfyUI", "role": "fp16/fp8 + VAE + 文本嵌入（_DEFAULT_REPO）"},
    {"repo": "Comfy-Org/SeedVR2", "role": "int8_convrot / mxfp8 / nvfp4（_COMFY_ORG_REPO，diffusion_models/ 子目录）"},
    {"repo": "cmeka/SeedVR2-GGUF", "role": "GGUF 档（尚未纳入本项目）"},
)

# 权重文件名之外的仓库附属文件，不算"档位"。
IGNORE_SUFFIX = (".gitattributes", "README.md", "LICENSE", "config.json", ".md", ".patch")

# 文件名归一化用的前缀 / 尺寸记号。前缀按长度倒序剥离，尺寸记号必须 7b_sharp 先于 7b。
_MODEL_PREFIXES = ("seedvr2_ema_", "seedvr2_", "ema_")
_SIZE_TOKENS = ("7b_sharp", "3b", "7b")


def parse_weight(name: str) -> tuple[str, str]:
    """把权重文件名拆成 (尺寸, 档位)。

    为什么按"档位"而不是按文件名比对：同一档位在两个源里有两套命名
    （numz 的 `seedvr2_ema_3b_fp16` vs Comfy-Org 的 `seedvr2_3b_fp16`，字节不同、哈希不同）。
    只比文件名的话，每次跑都会把 6 个"另一源的同档位"报成新档 —— 噪音一多，监控就会
    和"永远全红"的哈希门禁一样被忽略。

    Returns:
        (size, variant)：未知尺寸用 "-"；gguf 容器在档位后加 "@gguf" 以区分容器格式。
    """
    stem = Path(name).stem
    size = "-"
    for token in _SIZE_TOKENS:
        if token in stem:
            size = token
            stem = stem.replace(token, "", 1)
            break
    for prefix in _MODEL_PREFIXES:
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
            break
    variant = stem.strip("_-") or stem
    if name.lower().endswith(".gguf"):
        variant = f"{variant}@gguf"
    return size, variant


# 已知答案：本次核查（2026-09-22）实测为真。任一不符即说明链路坏了。
SELF_CHECKS: tuple[tuple[str, str, bool], ...] = (
    ("numz/SeedVR2_comfyUI", "seedvr2_ema_7b_sharp_fp8_e4m3fn.safetensors", True),
    ("numz/SeedVR2_comfyUI", "ema_vae_fp16.safetensors", True),
    ("numz/SeedVR2_comfyUI", "seedvr2_7b_mxfp8.safetensors", False),  # mxfp8 属 Comfy-Org
    ("Comfy-Org/SeedVR2", "seedvr2_7b_mxfp8.safetensors", True),
    ("cmeka/SeedVR2-GGUF", "seedvr2_ema_3b-Q4_K_M.gguf", True),
    ("cmeka/SeedVR2-GGUF", "seedvr2_ema_3b-IQ1_S.gguf", False),  # 该档位根本不存在
)

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_SELFCHECK_FAILED = 2
EXIT_NETWORK = 3


@dataclass
class RepoSnapshot:
    """一个 HF 仓库的权重文件清单快照。"""

    repo: str
    files: dict[str, int] = field(default_factory=dict)  # basename -> 字节数
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _load_config_filenames() -> dict[str, str]:
    """从 config.yaml 收集已登记的权重文件名 -> 归属说明。"""
    import yaml

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    models = (cfg.get("model") or {}).get("models") or {}
    for size, m in models.items():
        for key, val in (m or {}).items():
            if not isinstance(val, str):
                continue
            if key.startswith(("checkpoint_", "vae_checkpoint", "pos_emb", "neg_emb")):
                out.setdefault(Path(val).name, f"{size}/{key}")
    return out


def fetch_repo_files(repo: str, endpoint: str | None, timeout: int) -> tuple[dict[str, int], str]:
    """取仓库权重清单（basename -> 字节）。返回 (清单, 错误信息)；错误非空即本次未取得数据。

    两级传输，**都不关闭证书校验**：
    1. ``requests``（走 certifi）—— 正常机器上就够。
    2. 回退 ``curl --ssl-no-revoke`` —— 本机实测 certifi 验不过 huggingface.co 的证书链
       （``SSLCertVerificationError: unable to get local issuer certificate``），而 Windows
       根存储可以。该 flag 只跳过吊销检查、**不跳过链验证**，因此不是把 TLS 校验关掉。
       这也是 install 脚本既有的取数方式，不引入新依赖。
    """
    base = (endpoint or "https://huggingface.co").rstrip("/")
    url = f"{base}/api/models/{repo}"
    files, err = _fetch_via_requests(url, timeout)
    if not err:
        return files, ""
    curl_files, curl_err = _fetch_via_curl(url, timeout)
    if not curl_err:
        return curl_files, ""
    return {}, f"requests: {err} ｜ curl: {curl_err}"


def _parse_siblings(payload: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for sib in payload.get("siblings", []):
        name = Path(str(sib.get("rfilename", ""))).name
        if not name or name.endswith(IGNORE_SUFFIX):
            continue
        size = sib.get("size") or (sib.get("blobLfsSize") or {}).get("size") or 0
        with contextlib.suppress(TypeError, ValueError):
            out[name] = int(size)
    return out


def _fetch_via_requests(url: str, timeout: int) -> tuple[dict[str, int], str]:
    try:
        import requests

        res = requests.get(url, params={"blobs": "true"}, timeout=timeout)
        res.raise_for_status()
        return _parse_siblings(res.json()), ""
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {str(exc)[:160]}"


def _fetch_via_curl(url: str, timeout: int) -> tuple[dict[str, int], str]:
    import subprocess  # nosec B404（固定参数列表调用 curl，无 shell、URL 由本模块拼装）

    try:
        res = subprocess.run(  # nosec B603
            [
                "curl",
                "-sS",
                "--ssl-no-revoke",
                "--max-time",
                str(timeout),
                "-H",
                "Accept: application/json",
                f"{url}?blobs=true",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout + 15,
            check=False,
        )
    except FileNotFoundError:
        return {}, "curl 不在 PATH"
    except subprocess.TimeoutExpired:
        return {}, f"curl 超时 >{timeout}s"
    if res.returncode != 0:
        return {}, f"curl rc={res.returncode}: {(res.stderr or '').strip()[:160]}"
    try:
        return _parse_siblings(json.loads(res.stdout)), ""
    except ValueError as exc:
        return {}, f"curl 返回非 JSON: {exc}"


def run_self_checks(snapshots: dict[str, RepoSnapshot]) -> list[str]:
    """核对已知答案。上游**真的**撤掉某个文件时这里也会报，属预期：那条登记需要人工复核更新。"""
    failures: list[str] = []
    for repo, filename, should_exist in SELF_CHECKS:
        snap = snapshots.get(repo)
        if snap is None or not snap.ok:
            failures.append(f"{repo} 未取得清单，无法核对已知答案（{filename}）")
            continue
        exists = filename in snap.files
        if exists != should_exist:
            failures.append(f"{repo} 的 {filename} 期望存在={should_exist}，实测={exists}")
    return failures


def compare(snapshots: dict[str, RepoSnapshot], registered: dict[str, str]) -> list[dict[str, Any]]:
    """逐仓库比对缺口，判据是「尺寸:档位」而不是文件名。

    返回每仓库一行：
    - ``new_variants``：该源的 ``档位 -> [尺寸...]``，其「尺寸:档位」组合在 config.yaml 里
      **从未登记** —— 这才是需要人看的新档（新量化格式 / 新模型尺寸）。
    - ``naming_dupes``：档位已登记、只是文件名属于另一源命名 —— 既有设计，单列一档不报警。
    """
    reg_keys = {":".join(parse_weight(name)) for name in registered}
    rows: list[dict[str, Any]] = []
    for spec in REPOS:
        snap = snapshots.get(spec["repo"])
        if snap is None or not snap.ok:
            rows.append({"repo": spec["repo"], "status": "network_error", "detail": snap.error if snap else "未抓取"})
            continue
        new_by_variant: dict[str, list[str]] = {}
        dupes: list[str] = []
        for name in sorted(snap.files):
            size, variant = parse_weight(name)
            if f"{size}:{variant}" not in reg_keys:
                new_by_variant.setdefault(variant, []).append(size)
            elif name not in registered:
                dupes.append(name)
        rows.append(
            {
                "repo": spec["repo"],
                "role": spec["role"],
                "status": "ok",
                "total": len(snap.files),
                "new_variants": new_by_variant,
                "naming_dupes": dupes,
            }
        )
    return rows


def render_report(rows: list[dict[str, Any]], registered_count: int, endpoint: str | None) -> str:
    today = dt.date.today().strftime("%Y%m%d")
    lines = [
        f"# 权重源档位监控 numz_release_{today}",
        "",
        f"- 生成时间：{dt.datetime.now().isoformat(timespec='seconds')}",
        f"- 源端点：{endpoint or 'huggingface.co（默认）'}",
        f"- config.yaml 已登记权重文件名：{registered_count} 个",
        "",
        "| 仓库 | 状态 | 权重文件数 | 新档位（尺寸:档 未登记过） | 同档另一源命名 |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        if r["status"] != "ok":
            lines.append(f"| `{r['repo']}` | ⚠️ **未取得清单** | — | — | — |")
            continue
        new_count = sum(len(v) for v in r["new_variants"].values())
        lines.append(f"| `{r['repo']}` | ok | {r['total']} | {new_count} | {len(r['naming_dupes'])} |")
    lines.append("")

    for r in rows:
        if r["status"] != "ok":
            lines += [f"## {r['repo']}：未取得数据", "", f"错误：`{r.get('detail', '')}`", ""]
            continue
        lines += [f"## {r['repo']}", "", f"- 角色：{r['role']}"]
        if r["new_variants"]:
            lines += ["", "**新档位**（该「尺寸:档位」组合在 config.yaml 里从未登记）：", ""]
            lines += [
                f"- `{variant}` × {', '.join(sorted(sizes))}" for variant, sizes in sorted(r["new_variants"].items())
            ]
        else:
            lines += ["", "（无新档位）"]
        if r["naming_dupes"]:
            lines += [
                "",
                f"_同档位、另一源命名_（{len(r['naming_dupes'])} 个；属既有的双源命名分裂设计，不需处理）：",
                "",
                ", ".join(f"`{n}`" for n in r["naming_dupes"]),
            ]
        lines.append("")

    lines += [
        "---",
        "",
        "## 补登记时的硬要求",
        "",
        "- 新增档位必须**同时**在 `config.yaml` 写 `sha256_<档>`：`verify_checkpoint` 在期望哈希为空时会",
        "  `skip_if_empty=True` 直接返回通过（只记一条 debug 日志）——漏配哈希 = 该档静默失去 CWE-353 保护。",
        "- 不同源的同一模型**字节不同、哈希不可互用**（numz 的 `seedvr2_ema_*` vs Comfy-Org 的 `seedvr2_*`），",
        "  双哈希走 `sha256_<档>_alt` 机制。",
        "- 本脚本只读：不下载权重、不改 config.yaml。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HuggingFace 权重档位监控（详见模块 docstring）")
    parser.add_argument("--endpoint", default=None, help="HF 端点，如 https://hf-mirror.com")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    registered = _load_config_filenames()
    snapshots: dict[str, RepoSnapshot] = {}
    for spec in REPOS:
        files, err = fetch_repo_files(spec["repo"], args.endpoint, args.timeout)
        snapshots[spec["repo"]] = RepoSnapshot(repo=spec["repo"], files=files, error=err)

    unfetched = [s for s in snapshots.values() if not s.ok]
    if len(unfetched) == len(snapshots):
        # 一条都没抓到 ⇒ 是网络问题，不是探针问题；先判它，否则下面自检会把「无数据」误报成「链路坏了」
        for s in unfetched:
            print(f"[网络不可用] {s.repo}: {s.error}", file=sys.stderr)
        print(
            "[结论] **无法判定**（不是「无新增档位」）。本机境外阻断带时段波动，请稍后重试或加 --endpoint。",
            file=sys.stderr,
        )
        return EXIT_NETWORK
    failures = run_self_checks({k: v for k, v in snapshots.items() if v.ok})
    if failures:
        print(f"[自检门禁失败] {len(failures)} 条已知答案不符 —— 抓取/解析链路不可信，**不出报告**。", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        print("[提示] 先确认是链路坏了还是上游真的撤了档；后者须人工复核并更新 SELF_CHECKS。", file=sys.stderr)
        return EXIT_SELFCHECK_FAILED
    print(f"[自检门禁] {len(SELF_CHECKS)} 条已知答案全部命中，抓取链路可信。")

    rows = compare(snapshots, registered)
    new_items = sum(sum(len(v) for v in r.get("new_variants", {}).values()) for r in rows)

    if args.as_json:
        print(
            json.dumps(
                {"registered_count": len(registered), "repos": rows, "self_checks_passed": len(SELF_CHECKS)},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        report = render_report(rows, len(registered), args.endpoint)
        if args.stdout:
            print(report)
        else:
            args.report_dir.mkdir(parents=True, exist_ok=True)
            out = args.report_dir / f"numz_release_{dt.date.today().strftime('%Y%m%d')}.md"
            out.write_text(report, encoding="utf-8", newline="\n")
            print(f"[written] {out}")

    if unfetched:
        print(
            f"[结论] {len(unfetched)} 个源未取得清单（{', '.join(s.repo for s in unfetched)}）—— "
            f"这些源是「无法判定」，本轮结论不完整；已判定部分候选新档位 {new_items} 个。"
        )
        return EXIT_NETWORK
    if new_items:
        print(f"[结论] 候选新档位 {new_items} 个 —— 详见报告。")
        return EXIT_FINDINGS
    print("[结论] 上游档位与 config.yaml 登记一致。")
    return EXIT_CLEAN


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
