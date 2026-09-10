#!/usr/bin/env python3
"""便携 python 验签诊断（GOTCHAS #98，2026-09-10 CI 定位用）。

在解包后的便携包内用便携 python 直接调用与服务器启动自检相同的
verify_manifest_signature_ed25519，逐环节打印：
- cryptography 是否可 import 及版本；
- 清单/签名/公钥文件是否存在；
- 验签结果或异常。

用法:
    <portable-python> scripts/diag_portable_verify.py --app-dir <SeedVR2-Portable>
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        with contextlib.suppress(OSError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="便携 python 验签诊断")
    parser.add_argument("--app-dir", required=True, help="SeedVR2-Portable 根目录")
    args = parser.parse_args()

    app_dir = os.path.abspath(args.app_dir)
    sys.path.insert(0, app_dir)
    manifest = os.path.join(app_dir, "app", "integrated_app", "security", "integrity_manifest.json")
    sig = manifest + ".sig.ed25519"
    pub = os.path.join(app_dir, "app", "integrated_app", "security", "manifest_signing_public_key.pem")

    try:
        import cryptography

        print(f"[diag] cryptography {cryptography.__version__} import OK")
    except Exception as e:  # noqa: BLE001
        print(f"[diag] cryptography import FAIL: {e!r}")
        return 1

    for label, path in (("manifest", manifest), ("sig", sig), ("pub", pub)):
        print(f"[diag] {label} exists={os.path.exists(path)} path={path}")

    try:
        from app.integrated_app.security.secret_key import (  # noqa: PLC0415
            verify_manifest_signature_ed25519,
        )

        result = verify_manifest_signature_ed25519(manifest)
        print(f"[diag] PORTABLE_VERIFY={result}")
        return 0 if result else 2
    except Exception as e:  # noqa: BLE001
        print(f"[diag] VERIFY_EXC: {type(e).__name__}: {e!r}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
