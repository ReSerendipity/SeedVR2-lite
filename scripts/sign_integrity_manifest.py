#!/usr/bin/env python3
"""为核心模块完整性清单生成/校验 HMAC-SHA256 签名（数据治理 P3-3）。

背景：integrity_manifest.json 与被校验代码同目录，攻击者若能改代码就能
同步改清单，"启动自检"即被绕过。本脚本把信任根外移到密钥文件
（data/.seedvr2_secret，权限 0600），清单旁生成 integrity_manifest.json.sig。

用法：
    # 代码更新后：重新生成清单 → 签名
    python scripts/generate_integrity_manifest.py
    python scripts/sign_integrity_manifest.py

    # 校验（CI / 运维巡检）
    python scripts/sign_integrity_manifest.py --verify

退出码：
    0 成功；1 校验失败或文件缺失。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.integrated_app.security.secret_key import (  # noqa: E402
    get_secret_key,
    sign_file,
    signature_path_for,
    verify_file_signature,
)

MANIFEST_PATH = os.path.join("app", "integrated_app", "security", "integrity_manifest.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="完整性清单签名 / 校验")
    parser.add_argument("--manifest", default=MANIFEST_PATH, help="清单文件路径")
    parser.add_argument("--verify", action="store_true", help="校验模式（默认签名模式）")
    args = parser.parse_args()

    if not os.path.exists(args.manifest):
        print(f"[FAIL] 清单文件不存在: {args.manifest}")
        print("       先运行 `python scripts/generate_integrity_manifest.py` 生成")
        return 1

    if args.verify:
        if verify_file_signature(args.manifest):
            print(f"[PASS] 清单签名有效: {args.manifest}")
            return 0
        sig_path = signature_path_for(args.manifest)
        if not os.path.exists(sig_path):
            print(f"[FAIL] 缺少签名文件 {sig_path}，请先执行签名")
        else:
            print(f"[FAIL] 清单签名无效（内容已变更或密钥不匹配）: {args.manifest}")
        return 1

    key = get_secret_key()
    if not key:
        print("[FAIL] 无法获取签名密钥（环境变量 SEEDVR2_SECRET_KEY 或 data/.seedvr2_secret）")
        return 1

    sig_path = sign_file(args.manifest, key)
    if not sig_path:
        print("[FAIL] 签名写入失败")
        return 1
    print(f"[OK] 已签名: {args.manifest} -> {sig_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
