#!/usr/bin/env python3
"""A 线：把 security/ 核心安全模块 Cython 编译为 .pyd（闭源分发组件）。

背景（水印保密 A-2/A-3）：源码在 Apache-2.0 开源仓库公开，普通用户拿到
便携包里的 .py 就能照着注释定点拆水印。本脚本把 security/ 下模块编译为
二进制 .pyd（无 .py 源码、无 docstring/嵌入坐标注释），发布包不再包含
可读的水印实现；模块名与导入路径保持不变，运行行为不变。

产物目录结构（security/）：
    __init__.py                     保留（包导入）
    watermark.cp312-win_amd64.pyd   编译产物（替代 watermark.py）
    ... 其余模块同理 ...
    integrity_manifest.json(.sig...) 运行时数据，原样保留
    manifest_signing_public_key.pem  内置公钥，原样保留

用法：
    # 输出到目录（供本地/CI 注入便携包）
    python scripts/build_closed_components.py --out build/closed

    # 同时打包为 zip（build_portable_bundle.ps1 -ClosedComponentsZip 使用）
    python scripts/build_closed_components.py --out build/closed --zip build/closed.zip

    # 只编译指定模块（默认 security/ 全部 .py）
    python scripts/build_closed_components.py --modules watermark.py secret_key.py --out build/closed

注意：
    - 需要 MSVC 工具链（CI 用 windows-latest 自带；本机需 Visual Studio Build Tools）。
    - 编译产物只进发布包，开发目录保持 .py 源码。
    - 发布前必须用 A-6 重算完整性清单：.pyd 哈希与 .py 不同，旧清单会让自检误报篡改。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECURITY_DIR = PROJECT_ROOT / "app" / "integrated_app" / "security"
PYTHON = sys.executable


def _compile_module(module_py: Path, work_dir: Path, out_dir: Path) -> Path | None:
    """在隔离目录编译单个模块，返回生成的 .pyd 路径（失败返回 None）。"""
    work = work_dir / module_py.stem
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy2(module_py, work / module_py.name)

    # 隔离目录内用 cythonize -i：避免项目根 pyproject 触发 setuptools 包发现
    cythonize_exe = Path(PYTHON).parent / "cythonize.exe"
    cmd = [str(cythonize_exe), "-i", module_py.name, "-3"]
    proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print(f"  [FAIL] 编译失败: {module_py.name}\n{proc.stdout[-800:]}{proc.stderr[-800:]}")
        return None

    pyds = list(work.glob("*.cp*-win_amd64.pyd")) + list(work.glob("*.pyd"))
    if not pyds:
        print(f"  [FAIL] 未生成 .pyd: {module_py.name}\n{proc.stdout[-800:]}")
        return None
    pyd = pyds[0]
    shutil.copy2(pyd, out_dir / pyd.name)
    print(f"  [OK] {module_py.name} -> {pyd.name}")
    return pyd


def main() -> int:
    parser = argparse.ArgumentParser(description="编译 security/ 闭源组件（Cython .pyd）")
    parser.add_argument("--out", required=True, help="产物输出目录")
    parser.add_argument("--zip", default=None, help="可选：产物 zip 路径")
    parser.add_argument("--modules", default=None, help="逗号分隔的模块名（默认 security/ 全部 .py）")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    modules = [m.strip() for m in args.modules.split(",") if m.strip()] if args.modules else None

    targets: list[Path] = []
    for py in sorted(SECURITY_DIR.glob("*.py")):
        if py.name == "__init__.py":
            continue
        if modules is not None and py.name not in modules:
            continue
        targets.append(py)
    if not targets:
        print("[FAIL] 没有可编译的模块")
        return 1

    print(f"编译 {len(targets)} 个模块: {', '.join(t.name for t in targets)}")

    # 1) 复制运行时数据（非 .py 文件：清单/签名/公钥）
    for f in SECURITY_DIR.iterdir():
        if f.is_file() and f.suffix != ".py":
            shutil.copy2(f, out_dir / f.name)

    # 2) 逐模块隔离编译
    with tempfile.TemporaryDirectory(prefix="seedvr2_closed_") as td:
        work_dir = Path(td)
        compiled = 0
        for t in targets:
            pyd = _compile_module(t, work_dir, out_dir)
            if pyd is not None:
                compiled += 1
        if compiled != len(targets):
            print(f"[FAIL] 编译不完整: {compiled}/{len(targets)} 成功（请检查 MSVC 工具链）")
            return 1

    # 3) 写入替换清单（供构建脚本删除对应 .py：pyd 名 -> py 名）
    pyd_map = {}
    for py in targets:
        stem = py.stem
        matches = list(out_dir.glob(f"{stem}*.pyd"))
        if matches:
            pyd_map[matches[0].name] = py.name
    replace_manifest = out_dir / ".closed_replacements.json"
    replace_manifest.write_text(
        __import__("json").dumps(pyd_map, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    total = sum(f.stat().st_size for f in out_dir.iterdir() if f.is_file())
    print(f"[OK] 闭源组件产物: {out_dir}（{len(pyd_map)} 个 .pyd，{total / 1024 / 1024:.1f} MB）")

    if args.zip:
        zip_path = Path(args.zip)
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        import zipfile

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(out_dir.iterdir()):
                if f.is_file():
                    zf.write(f, f.name)
        print(f"[OK] 已打包: {zip_path}（{zip_path.stat().st_size / 1024 / 1024:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
