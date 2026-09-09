#!/usr/bin/env python3
"""生成核心模块完整性清单 (integrity_manifest.json)

用于启动时核心模块 SHA256 完整性自检 (CWE-912 防御)。

用法:
    python scripts/generate_integrity_manifest.py
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

# 核心安全模块清单 (相对于 app/integrated_app/)
_CORE_MODULES = [
    "app_server.py",
    "config.py",
    "model_manager.py",
    "security/path_guard.py",
    "security/integrity_check.py",
    "security/watermark.py",
    "security/integrity_selfcheck.py",
    "middleware/csrf.py",
    "middleware/basic_auth.py",
    "middleware/rate_limit.py",
    "engines/seedvr2_engine.py",
]


def compute_sha256(filepath: Path) -> str:
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def resolve_manifest_entry(app_dir: Path, module_rel: str):
    """源码态返回 .py 条目；闭源注入态（Cython 编译后 .py 已被删除）回退同名 .pyd。

    清单必须覆盖 security/ 的编译产物：否则发布包重算清单时该模块从清单消失，
    篡改 .pyd 将无法被启动自检验出（GOTCHAS #92，2026-09-09 实测）。
    返回 (清单相对路径, 实际文件 Path)；找不到返回 (None, None)。
    """
    py_path = app_dir / module_rel
    if py_path.exists():
        return module_rel, py_path
    # security/path_guard.py → security/path_guard.cp312-win_amd64.pyd
    pyd_glob = module_rel[: -len(".py")] + ".*.pyd"
    candidates = sorted(app_dir.glob(pyd_glob))
    if len(candidates) == 1:
        return str(candidates[0].relative_to(app_dir)), candidates[0]
    return None, None


def main():
    import argparse

    parser = argparse.ArgumentParser(description="生成核心模块完整性清单")
    parser.add_argument(
        "--app-dir",
        default=None,
        help="app/integrated_app 目录；默认项目内。构建期传便携包内的对应目录，"
        "确保清单哈希基于实际分发文件（闭源注入 A-6）。",
    )
    args = parser.parse_args()

    if args.app_dir:
        app_dir = Path(args.app_dir)
    else:
        project_root = Path(__file__).parent.parent
        app_dir = project_root / "app" / "integrated_app"
    manifest_path = app_dir / "security" / "integrity_manifest.json"

    files = {}
    for module_rel in _CORE_MODULES:
        rel, module_path = resolve_manifest_entry(app_dir, module_rel)
        if module_path is not None:
            files[rel] = compute_sha256(module_path)
            print(f"  [OK] {rel}: {files[rel][:16]}...")
        else:
            print(f"  [FAIL] {module_rel}: NOT FOUND (.py/.pyd 均不存在)")

    manifest = {
        "generated_at": datetime.now().isoformat(),
        "generator": "scripts/generate_integrity_manifest.py",
        "description": "核心安全模块 SHA256 完整性清单，用于启动时自检",
        "files": files,
    }

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\n已生成: {manifest_path} ({len(files)} 个模块)")


if __name__ == "__main__":
    main()
