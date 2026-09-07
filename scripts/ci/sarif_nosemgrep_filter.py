#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""SARIF nosemgrep 过滤器（评估报告 R4 收尾：GitHub 告警面板收敛）。

背景（2026-09-06 实证）：semgrep 1.173.0 的 `--sarif` 输出**不应用 nosemgrep
抑制**（被标注的 finding 仍出现在 SARIF 中，且 result 无 level/properties
severity）；GitHub code scanning 于是按规则 `defaultConfiguration.level`
（多为 error）给这些条目建 error 告警，每次扫描重复上报 → 面板永不自愈，
与 `--severity ERROR` 门禁步骤（正确过滤）的语义矛盾。

本脚本以 `--json` 扫描结果（正确应用抑制与严重级）为事实源，
按 (ruleId, path, startLine) 三元组裁掉 SARIF 中 JSON 未认定的结果，
保留全部未抑制 finding 的告警可见性。方向保守：只删不增。

用法（CI）:
    semgrep scan --config auto --sarif --output semgrep.sarif
    semgrep scan --config auto --json  --output semgrep.findings.json
    python scripts/ci/sarif_nosemgrep_filter.py semgrep.sarif semgrep.findings.json semgrep.sarif
退出码: 0 成功（打印删除计数）；2 输入文件缺失/解析失败。
"""

import json
import os
import sys


def _key(rule_id: str, path: str, line) -> tuple:
    norm = os.path.normcase(os.path.normpath(str(path).lstrip("./\\")))
    return (str(rule_id), norm, int(line or 0))


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    sarif_path, json_path, out_path = sys.argv[1:4]
    try:
        with open(sarif_path, encoding="utf-8") as f:
            sarif = json.load(f)
        with open(json_path, encoding="utf-8") as f:
            findings = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[sarif-filter] 输入读取失败: {e}", file=sys.stderr)
        return 2

    # JSON 扫描已应用 nosemgrep 抑制与 --json 语义：其结果集即“真实检出”
    live = {_key(r["check_id"], r["path"], (r.get("start") or {}).get("line")) for r in findings.get("results", [])}

    removed = 0
    for run in sarif.get("runs", []):
        results = run.get("results") or []
        kept = []
        for res in results:
            loc = ((res.get("locations") or [{}])[0]).get("physicalLocation") or {}
            uri = (loc.get("artifactLocation") or {}).get("uri", "")
            line = (loc.get("region") or {}).get("startLine")
            if _key(res.get("ruleId", ""), uri, line) in live:
                kept.append(res)
            else:
                removed += 1
        run["results"] = kept

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(sarif, f, ensure_ascii=False)
    print(
        f"[sarif-filter] 过滤 SARIF：删除 {removed} 条未抑制/不存在项（JSON 事实源 "
        f"{len(live)} 条），输出 {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
