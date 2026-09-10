#!/usr/bin/env python3
"""增量包打包（把《增量更新发布手册》的手工流程机械化）。

设计要点（2026-09-10）：**门禁先行**。第一步就跑 `verify_release_integrity.py` 校验
staging 的完整性清单与实际分发文件一致；不过门禁就绝不产出 zip。这样「发布前必须校验」
不再是靠人记的纪律，而是脚本的硬前置。

发布版 config.yaml 为 `integrity_enforce: true`，清单一旦漂移，已安装用户更新后会**拒绝启动**
（GOTCHAS #114）。v1.5.6→v1.5.7 那次正是靠打包前的这个校验才发现 `model_manager.py` 清单漂移。

用法：
    python scripts/build_app_increment.py --version 1.5.7
    python scripts/build_app_increment.py --version 1.5.7 --dry-run

退出码：
    0  成功（或 dry-run 通过）
    1  门禁失败 / 版本不一致 / 打包失败
    2  用法或环境错误
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        with contextlib.suppress(OSError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STAGING = REPO_ROOT / "dist" / "tauri-release" / "staging" / "App"
DEFAULT_OUT_DIR = REPO_ROOT / "dist" / "tauri-release" / "installer"

# 与《增量更新发布手册》§2 一致：增量包只含应用代码，永不含运行时/模型/数据/日志。
ZIP_EXCLUDES = [
    "-xr!runtime",
    "-xr!model",
    "-xr!data",
    "-xr!logs",
    "-xr!__pycache__",
    "-xr!ffmpeg.exe",
    "-xr!ffprobe.exe",
]

DEFAULT_7ZA_CANDIDATES = [
    Path(r"C:\Users\Doro\Tools\7z-extra\x64\7za.exe"),
]


def find_7za(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    for cand in DEFAULT_7ZA_CANDIDATES:
        if cand.is_file():
            return cand
    for name in ("7za", "7z"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_staging_version(staging: Path) -> str | None:
    vj = staging / "version.json"
    if not vj.is_file():
        return None
    try:
        return json.loads(vj.read_text(encoding="utf-8")).get("version")
    except Exception:
        return None


def read_pyproject_version(staging: Path) -> str | None:
    pp = staging / "pyproject.toml"
    if not pp.is_file():
        return None
    for line in pp.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("version"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="增量包打包（门禁先行）")
    parser.add_argument("--version", required=True, help="版本号，如 1.5.7")
    parser.add_argument("--staging", default=str(DEFAULT_STAGING), help="增量包源目录（默认 staging\\App）")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="输出目录（默认 dist\\tauri-release\\installer）")
    parser.add_argument("--7za", dest="seven_zip", default=None, help="7za.exe 路径；缺省自动探测")
    parser.add_argument("--python", default=sys.executable, help="用于跑门禁的解释器（需 cryptography）")
    parser.add_argument("--skip-gate", action="store_true", help="[危险] 跳过完整性门禁，仅供调试")
    parser.add_argument("--dry-run", action="store_true", help="只跑门禁与校验，不打包")
    args = parser.parse_args()

    version = args.version.lstrip("v")
    staging = Path(args.staging).resolve()
    out_dir = Path(args.out_dir).resolve()
    zip_name = f"app-v{version}.zip"
    zip_path = out_dir / zip_name
    sha_path = out_dir / f"{zip_name}.sha256"

    print("=" * 68)
    print(f"增量包打包  version={version}")
    print(f"  源目录: {staging}")
    print(f"  输出  : {zip_path}")
    print("=" * 68)

    if not staging.is_dir():
        print(f"[FAIL] 源目录不存在: {staging}")
        return 2

    # ── 步骤 1/5：完整性门禁（硬前置）──────────────────────────────
    if args.skip_gate:
        print("[1/5] 完整性门禁：**已按 --skip-gate 跳过**（仅调试用，不得用于正式发布）")
    else:
        print("[1/5] 完整性门禁 ...")
        gate = subprocess.run(
            [args.python, str(REPO_ROOT / "scripts" / "verify_release_integrity.py"), "--app-root", str(staging)],
            cwd=str(REPO_ROOT),
        )
        if gate.returncode != 0:
            print()
            print("[FAIL] 完整性门禁未通过 —— 已中止，未产出任何 zip。")
            print("       修复：scripts/generate_integrity_manifest.py → 以仓库态字节覆写工作区")
            print("             → scripts/sign_integrity_manifest.py（详见发布手册 §8）。")
            return 1
        print("      门禁通过：清单与 staging 分发文件一致且签名有效。")

    # ── 步骤 2/5：版本号一致性 ──────────────────────────────────────
    print("[2/5] 版本号一致性 ...")
    vj_ver = read_staging_version(staging)
    pp_ver = read_pyproject_version(staging)
    print(f"      staging/version.json   -> {vj_ver}")
    print(f"      staging/pyproject.toml -> {pp_ver}")
    mismatch = [n for n, v in (("version.json", vj_ver), ("pyproject.toml", pp_ver)) if v != version]
    if mismatch:
        print(f"[FAIL] 版本号不一致：{', '.join(mismatch)} 与 --version {version} 不符")
        print("       请先按发布手册 §1 更新 staging 的版本文件（两份都要改）。")
        return 1
    print("      一致。")

    if args.dry_run:
        print("[3/5] dry-run：跳过打包。")
        print("[DRY-RUN OK] 门禁与版本校验均通过。")
        return 0

    # ── 步骤 3/5：打包 ─────────────────────────────────────────────
    seven = find_7za(args.seven_zip)
    if seven is None:
        print("[FAIL] 找不到 7za；用 --7za 指定路径")
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    if sha_path.exists():
        sha_path.unlink()

    print(f"[3/5] 打包（{seven}）...")
    cmd = [str(seven), "a", "-tzip", "-mx=9", str(zip_path), "*", *ZIP_EXCLUDES]
    pack = subprocess.run(cmd, cwd=str(staging), capture_output=True, text=True, errors="replace")
    tail = "\n".join((pack.stdout or "").strip().splitlines()[-4:])
    print("\n".join(f"      {ln}" for ln in tail.splitlines()))
    if pack.returncode != 0 or not zip_path.is_file():
        print(f"[FAIL] 7za 打包失败（exit={pack.returncode}）")
        print(pack.stderr or "")
        return 1

    # ── 步骤 4/5：SHA256 ───────────────────────────────────────────
    print("[4/5] 生成 SHA256 ...")
    digest = sha256_file(zip_path)
    # updater 的 fetch_sha256 取第一个 64 位 hex，"<hex>  <文件名>" 兼容
    sha_path.write_text(f"{digest}  {zip_name}\n", encoding="ascii", newline="\n")
    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"      {zip_name}  {size_mb:.2f} MB")
    print(f"      sha256 = {digest}")

    # ── 步骤 5/5：下一步 ───────────────────────────────────────────
    notes_name = "release-" + version.replace(".", "") + ".md"
    print("[5/5] 下一步（发布为正式版，绝不能加 --prerelease）：")
    print()
    print(f"  gh release create v{version} --target main \\")
    print(f'    --title "SeedVR2 v{version}" \\')
    print(f'    --notes-file "{out_dir / notes_name}" \\')
    print(f'    "{zip_path}" "{sha_path}"')
    print()
    print("  发布后按手册 §5 跑验证清单（尤其 ⑥⑦：资产构成 + Portable Release 运行排查）。")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
