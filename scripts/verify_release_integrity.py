#!/usr/bin/env python3
"""发布前完整性门禁：校验核心模块完整性清单与目标代码/分发文件严格一致。

背景（2026-09-10，GOTCHAS #113 / #114）：
发布版 `config.yaml` 为 `integrity_enforce: true`（由 `scripts/build_portable_bundle.ps1`
构建期注入），因此**清单一旦与代码不一致，用户端会直接拒绝启动**，而不是只打个告警。
v1.5.6 定版提交改过 `model_manager.py` 却未重签清单，发布包启动自检 11 项中 1 项失败——
若该包作为增量更新发出去，已安装用户（`config.yaml` 被 updater 保留为 true）将无法启动。

本脚本把「发布前必须校验」变成机械强制：任何异常都以非零退出码结束。

用法：
    # 仓库态（提交前 / CI）
    python scripts/verify_release_integrity.py

    # 发布态：便携包构建后的 payload、或 staging（App 根 = 含 app/ 包的目录）
    python scripts/verify_release_integrity.py --app-root dist/tauri-release/staging/App

    # 发布态额外要求强制模式（与便携包口径一致）
    python scripts/verify_release_integrity.py --app-root <App 根> --require-enforce

退出码：
    0  通过
    1  校验失败（清单漂移 / 签名无效 / 条目缺失 / 数量不符 / enforce 未开）
    2  用法或环境错误（路径不存在、导入失败等）
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import os
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        with contextlib.suppress(OSError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")


def _load_selfcheck(app_root: Path):
    """从指定 App 根加载完整性自检模块，保证门禁判定与运行时判定同源。"""
    if not (app_root / "app" / "integrated_app").is_dir():
        print(f"[FAIL] 目标不是有效 App 根（缺少 app/integrated_app）: {app_root}")
        return None

    sys.path.insert(0, str(app_root))
    # 清掉可能已缓存的 app.* 模块，避免把别的 App 根的实现带进来
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[name]

    try:
        module = importlib.import_module("app.integrated_app.security.integrity_selfcheck")
    except Exception as exc:  # pragma: no cover - 环境问题
        print(f"[FAIL] 无法导入完整性自检模块（{app_root}）: {type(exc).__name__}: {exc}")
        return None
    return module


def _read_enforce(config_path: Path) -> bool | None:
    """读取 config.yaml 的 runtime.security.integrity_enforce；读不到返回 None。"""
    if not config_path.is_file():
        return None
    try:
        import yaml
    except ImportError:
        # 无 PyYAML 时退回正则（仅需这一个布尔位）
        import re

        text = config_path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^\s*integrity_enforce:\s*(true|false|True|False)\s*$", text, re.M)
        return None if not m else m.group(1).lower() == "true"

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    node = (data.get("runtime") or {}).get("security") or {}
    value = node.get("integrity_enforce")
    return None if value is None else bool(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="发布前完整性门禁")
    parser.add_argument(
        "--app-root",
        default=None,
        help="App 根（含 app/ 包的目录）；默认本仓库根。发布态传 staging\\App 或 payload 内 App 根。",
    )
    parser.add_argument(
        "--require-enforce",
        action="store_true",
        help="额外要求 config.yaml 的 integrity_enforce 为 true（便携包发布口径）。",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="config.yaml 路径；默认取 <app-root>/config.yaml。",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    app_root = Path(args.app_root).resolve() if args.app_root else repo_root

    print("=" * 68)
    print("发布前完整性门禁（verify_release_integrity）")
    print(f"  App 根: {app_root}")
    print("=" * 68)

    module = _load_selfcheck(app_root)
    if module is None:
        return 2

    core_count = len(getattr(module, "_CORE_MODULES", []))

    try:
        result = module.run_startup_selfcheck(enforce=False)
    except Exception as exc:  # pragma: no cover - 环境问题
        print(f"[FAIL] 自检执行异常: {type(exc).__name__}: {exc}")
        return 2

    total = int(result.get("total", 0))
    passed = int(result.get("passed", 0))
    failed = int(result.get("failed", 0))
    skipped = int(result.get("skipped", 0))
    signed = bool(result.get("manifest_signed", False))
    failed_files = list(result.get("failed_files", []))

    print(f"  条目: total={total} passed={passed} failed={failed} skipped={skipped}")
    print(f"  清单签名有效: {signed}")
    print(f"  核心模块应有条目数: {core_count}")

    problems: list[str] = []

    if failed > 0:
        problems.append(f"清单哈希与实际文件不一致: {', '.join(failed_files)}")
    if not signed:
        problems.append(
            "清单缺少有效签名（先跑 scripts/generate_integrity_manifest.py 再跑 "
            "scripts/sign_integrity_manifest.py）"
        )
    if total != core_count:
        problems.append(
            f"清单条目数 {total} ≠ 核心模块数 {core_count}"
            "（可能是闭源注入态清单与实际 payload 不匹配）"
        )
    if skipped > 0:
        problems.append(f"有 {skipped} 个模块被跳过（文件缺失或清单条目为空）")
    if passed != core_count and failed == 0 and skipped == 0:
        problems.append(f"通过数 {passed} ≠ 核心模块数 {core_count}")

    config_path = Path(args.config).resolve() if args.config else app_root / "config.yaml"
    enforce = _read_enforce(config_path)
    print(f"  config.yaml integrity_enforce: {enforce}（{config_path.name}）")
    if args.require_enforce and enforce is not True:
        problems.append(
            f"要求发布态强制模式，但 integrity_enforce={enforce}（期望 true）：{config_path}"
        )

    print("-" * 68)
    if problems:
        print("[FAIL] 完整性门禁未通过：")
        for p in problems:
            print(f"  - {p}")
        print("=" * 68)
        return 1

    print("[PASS] 完整性门禁通过：清单与目标代码/分发文件一致，签名有效。")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
