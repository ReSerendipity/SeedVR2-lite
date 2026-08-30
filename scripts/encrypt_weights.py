#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""模型权重加密工具 — 将明文 safetensors 加密为 AES-GCM .encrypted 格式

用法:
    # 1. 生成机器绑定许可证 (写入 data/license.json, 该目录不入库)
    python scripts/encrypt_weights.py generate-license --user operator@example.com

    # 2. 加密 model/ 下全部权重 (生成 *.safetensors.encrypted, 明文文件保留由用户决定)
    python scripts/encrypt_weights.py encrypt --model-dir model

    # 3. 校验加密文件可被当前机器解密
    python scripts/encrypt_weights.py verify --model-dir model

运行时解密: 引擎加载权重时自动优先使用 <name>.encrypted（见
app/integrated_app/security/weight_encryption.py 的 resolve_weight_for_loading），
许可证从环境变量 SEEDVR2_LICENSE_KEY 或 data/license.json 读取。

安全提示:
    - 加密前确认磁盘剩余空间充足（加密过程需读写完整文件）
    - 确认运行正常后再手动删除明文权重
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.integrated_app.security.weight_encryption import (  # noqa: E402
    decrypt_to_memory,
    derive_encryption_key,
    encrypt_file,
    generate_license,
)


def cmd_generate_license(user: str) -> int:
    """生成机器绑定许可证并写入 data/license.json。"""
    import json

    info = generate_license(user)
    out_file = PROJECT_ROOT / "data" / "license.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        json.dumps(
            {
                "user": info.user,
                "machine_fingerprint": info.machine_fingerprint,
                "issued_at": info.issued_at,
                "license_key": info.license_key,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"许可证已写入: {out_file}")
    print(f"  机器指纹: {info.machine_fingerprint[:16]}...")
    print(f"  许可证密钥: {info.license_key[:16]}...")
    print("提示: 跨机器部署需在目标机器重新生成许可证并重新加密权重。")
    return 0


def _iter_weight_files(model_dir: Path):
    for p in sorted(model_dir.rglob("*.safetensors")):
        if p.name.endswith(".encrypted") or p.with_name(p.name + ".encrypted").exists():
            continue
        yield p


def cmd_encrypt(model_dir: Path) -> int:
    """加密目录下全部未加密的 safetensors。"""
    files = list(_iter_weight_files(model_dir))
    if not files:
        print(f"{model_dir} 下没有待加密的明文 safetensors（或均已存在 .encrypted）")
        return 0

    import json

    license_file = PROJECT_ROOT / "data" / "license.json"
    if not license_file.exists():
        print("错误: 未找到 data/license.json，请先运行 generate-license 子命令", file=sys.stderr)
        return 2
    license_key = json.loads(license_file.read_text(encoding="utf-8"))["license_key"]
    key = derive_encryption_key(license_key)

    for p in files:
        out = p.with_name(p.name + ".encrypted")
        encrypt_file(p, out, key)
        print(f"  ✓ {p.name} -> {out.name} ({p.stat().st_size} bytes)")
    print(f"已加密 {len(files)} 个文件。确认推理正常后可手动删除明文文件。")
    return 0


def cmd_verify(model_dir: Path) -> int:
    """校验全部 .encrypted 文件可被当前机器解密（哈希匹配）。"""
    import json

    license_file = PROJECT_ROOT / "data" / "license.json"
    if not license_file.exists():
        print("错误: 未找到 data/license.json", file=sys.stderr)
        return 2
    license_key = json.loads(license_file.read_text(encoding="utf-8"))["license_key"]
    key = derive_encryption_key(license_key)

    enc_files = sorted(model_dir.rglob("*.safetensors.encrypted"))
    if not enc_files:
        print(f"{model_dir} 下没有 .encrypted 文件")
        return 0
    failed = 0
    for p in enc_files:
        try:
            decrypt_to_memory(p, key)
            print(f"  ✓ {p.name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {p.name}: {type(e).__name__}: {e}")
    print(f"校验完成: {len(enc_files) - failed}/{len(enc_files)} 通过")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="模型权重 AES-GCM 加密工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gen = sub.add_parser("generate-license", help="生成机器绑定许可证")
    p_gen.add_argument("--user", required=True, help="用户标识（如邮箱）")

    p_enc = sub.add_parser("encrypt", help="加密 model/ 下的明文权重")
    p_enc.add_argument("--model-dir", type=Path, default=PROJECT_ROOT / "model")

    p_ver = sub.add_parser("verify", help="校验加密文件可被当前机器解密")
    p_ver.add_argument("--model-dir", type=Path, default=PROJECT_ROOT / "model")

    args = parser.parse_args()
    if args.cmd == "generate-license":
        return cmd_generate_license(args.user)
    if args.cmd == "encrypt":
        return cmd_encrypt(args.model_dir)
    if args.cmd == "verify":
        return cmd_verify(args.model_dir)
    return 2


if __name__ == "__main__":
    sys.exit(main())
