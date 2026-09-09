"""轮换 SeedVR2 水印签名密钥（K-1：密钥轮换 + 历史验证保留）。

轮换流程：
    1. 当前密钥备份为 data/.watermark_key.old（若已有 .old，先按 .old.1/.old.2 滚动，保留 2 份历史）；
    2. 生成新密钥写入 data/.watermark_key；
    3. 打印提醒：旧密钥仍可验证历史产物（verify_watermark 会同时尝试新旧密钥）。

用法:
    python scripts/rotate_watermark_key.py

注意:
    - 密钥文件仅本机持有，切勿提交仓库或随便携包分发；
    - 轮换后请离线备份新密钥，旧密钥备份（.old）也建议离线保存以维持取证链；
    - 正式签发场景（环境变量 SEEDVR2_WATERMARK_KEY 注入）不受本脚本影响。
"""

import secrets
from pathlib import Path

KEY_FILE = Path(__file__).resolve().parent.parent / "data" / ".watermark_key"
OLD_BACKUPS = 2


def _rotate_backups() -> None:
    """把已存在的 .old 链向后滚动（.old -> .old.1 -> .old.2），保留 OLD_BACKUPS 份。"""
    oldest = Path(str(KEY_FILE) + f".old.{OLD_BACKUPS}")
    if oldest.exists():
        oldest.unlink()
    for i in range(OLD_BACKUPS, 1, -1):
        cur = Path(str(KEY_FILE) + f".old.{i - 1}")
        nxt = Path(str(KEY_FILE) + f".old.{i}")
        if cur.exists():
            cur.rename(nxt)
    old = Path(str(KEY_FILE) + ".old")
    if old.exists():
        old.rename(Path(str(KEY_FILE) + ".old.1"))


def main() -> None:
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if KEY_FILE.exists():
        existing = KEY_FILE.read_text(encoding="utf-8").strip()
        if not existing:
            print(f"当前密钥文件为空，跳过备份: {KEY_FILE}")
        else:
            _rotate_backups()
            backup = Path(str(KEY_FILE) + ".old")
            KEY_FILE.rename(backup)
            print(f"旧密钥已备份: {backup}")
    else:
        print("未发现现有密钥，直接生成新密钥（不涉及轮换）")

    KEY_FILE.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
    print(f"新水印签名密钥已生成: {KEY_FILE}")
    print("注意:")
    print("  1. 验证链同时尝试 当前密钥 / 旧位置密钥 / .old 备份密钥，历史产物仍可验证；")
    print("  2. 请离线备份新密钥与 .old 备份（密钥遗失后对应区间的水印将无法验证）；")
    print("  3. 打包便携包时务必排除全部密钥文件。")
    print("  4. 若通过环境变量 SEEDVR2_WATERMARK_KEY 注入签发密钥，此脚本生成的本地密钥仅作兜底。")


if __name__ == "__main__":
    main()
