#!/usr/bin/env python3
"""WinPython 便携 python 能力探测（GOTCHAS #98，CI 诊断用）。

独立于主构建链，快速回答：WinPython 3.12.10 能否 import cryptography、
能否生成/验证 Ed25519 签名（服务器启动自检的前置依赖）。
"""

from __future__ import annotations

import contextlib
import sys

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        with contextlib.suppress(OSError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    print(f"[probe] python {sys.version}")
    try:
        import cryptography

        print(f"[probe] CRYPTO_OK {cryptography.__version__}")
    except Exception as e:  # noqa: BLE001
        print(f"[probe] CRYPTO_FAIL {e!r}")
        return 1

    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: PLC0415

        key = ed25519.Ed25519PrivateKey.generate()
        sig = key.sign(b"x")
        key.public_key().verify(sig, b"x")
        print("[probe] ED25519_VERIFY_OK")
    except Exception as e:  # noqa: BLE001
        print(f"[probe] ED25519_VERIFY_FAIL {e!r}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
