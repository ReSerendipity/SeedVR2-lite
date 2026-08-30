# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""启动时核心模块完整性自检

在应用启动时计算核心安全模块的 SHA256 哈希值并与预期值比对，
检测文件是否被篡改或注入后门 (CWE-912 供应链投毒防御)。

覆盖的核心模块:
    - app_server.py         (应用入口)
    - security/path_guard.py (路径白名单守卫)
    - middleware/csrf.py     (CSRF 中间件)
    - middleware/basic_auth.py (Basic Auth 中间件)
    - engines/seedvr2_engine.py (推理引擎)
    - security/integrity_check.py (完整性校验)
    - security/watermark.py (数字水印)

使用方式:
    from app.integrated_app.security.integrity_selfcheck import run_startup_selfcheck

    results = run_startup_selfcheck()
    if results["failed"]:
        print("WARNING: 核心模块完整性校验失败！")

哈希清单文件:
    哈希值存储在 `app/integrated_app/security/integrity_manifest.json` 中。
    首次运行或代码更新后，运行 `python scripts/generate_integrity_manifest.py` 重新生成。
    若清单文件不存在，自检跳过并提示生成命令。
"""

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

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

# 清单文件路径
_MANIFEST_FILENAME = "integrity_manifest.json"


class SelfCheckResult(NamedTuple):
    """自检结果。"""

    total: int
    passed: int
    failed: int
    skipped: int
    failed_files: list[str]


def _get_manifest_path() -> Path:
    """获取清单文件路径。"""
    return Path(__file__).parent / _MANIFEST_FILENAME


def verify_manifest_signature(manifest_path: Path | str) -> bool:
    """校验完整性清单的 HMAC-SHA256 签名（数据治理 P3-3）。

    Args:
        manifest_path: 清单文件路径。

    Returns:
        签名存在且匹配返回 True；无密钥/无签名/不匹配返回 False。
    """
    try:
        from app.integrated_app.security.secret_key import verify_file_signature
    except Exception as e:  # noqa: BLE001 — 模块不可用时按"未签名"处理
        logger.debug("[SELF-CHECK] 签名校验模块不可用: %s", e)
        return False
    return verify_file_signature(manifest_path)


def _compute_file_sha256(filepath: Path) -> str:
    """计算文件 SHA256。"""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def run_startup_selfcheck(enforce: bool = False) -> dict:
    """执行启动时核心模块完整性自检。

    流程:
        1. 读取 integrity_manifest.json 清单文件
        2. 若清单不存在，跳过自检并提示生成命令
        3. 对每个核心模块计算当前 SHA256
        4. 与清单中的预期哈希比对
        5. 不一致的文件记录为失败

    Args:
        enforce: True 时校验失败抛出 RuntimeError 阻断启动（fail-fast）；
            清单缺失仍跳过不阻断（避免误伤首次部署）。

    Returns:
        dict: 包含 total/passed/failed/skipped/failed_files 字段。

    Raises:
        RuntimeError: enforce=True 且存在校验失败的文件。
    """
    manifest_path = _get_manifest_path()
    app_dir = Path(__file__).parent.parent  # app/integrated_app/

    # 读取清单
    if not manifest_path.exists():
        logger.info(
            "[SELF-CHECK] 完整性清单不存在，跳过自检。"
            " 运行 `python scripts/generate_integrity_manifest.py` 生成清单以启用启动自检。"
        )
        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": len(_CORE_MODULES),
            "failed_files": [],
        }

    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[SELF-CHECK] 清单文件读取失败: {e}")
        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": len(_CORE_MODULES),
            "failed_files": [],
        }

    # 数据治理 P3-3：校验清单本身的 HMAC 签名。
    # 清单与被校验代码同目录，"能改代码就能同步改清单"是原方案的结构性弱点；
    # 签名把信任根外移到密钥文件（data/.seedvr2_secret，权限 0600）。
    signature_ok = verify_manifest_signature(manifest_path)
    if not signature_ok:
        message = (
            "[SELF-CHECK] 完整性清单缺少有效签名（运行 `python scripts/sign_integrity_manifest.py` 生成）。"
            "未签名清单无法防御'同步篡改代码与清单'的投毒路径"
        )
        logger.warning(message)
        if enforce:
            raise RuntimeError(message + "；已开启 enforce，拒绝启动")

    expected_hashes = manifest.get("files", {})
    total = 0
    passed = 0
    failed = 0
    skipped = 0
    failed_files = []

    for module_rel in _CORE_MODULES:
        module_path = app_dir / module_rel
        if not module_path.exists():
            logger.warning(f"[SELF-CHECK] 核心模块不存在: {module_path}")
            skipped += 1
            continue

        total += 1
        expected = expected_hashes.get(module_rel, "")

        if not expected:
            skipped += 1
            total -= 1
            continue

        try:
            actual = _compute_file_sha256(module_path)
        except OSError as e:
            logger.error(f"[SELF-CHECK] 无法读取 {module_path}: {e}")
            failed += 1
            failed_files.append(module_rel)
            continue

        if actual == expected:
            passed += 1
            logger.debug(f"[SELF-CHECK] ✓ {module_rel}")
        else:
            failed += 1
            failed_files.append(module_rel)
            logger.error(
                f"[SECURITY WARNING] 核心模块完整性校验失败: {module_rel}\n"
                f"    期望 SHA256: {expected}\n"
                f"    实际 SHA256: {actual}\n"
                f"    该文件可能已被篡改！请检查代码完整性。"
            )

    # 输出汇总
    if failed > 0:
        logger.error(
            "=" * 60 + "\n"
            "[SECURITY] ⚠️  核心模块完整性自检失败！\n"
            f"    通过: {passed}/{total}, 失败: {failed}, 跳过: {skipped}\n"
            f"    失败文件: {', '.join(failed_files)}\n"
            "    请检查上述文件是否被篡改，或运行 "
            "`python scripts/generate_integrity_manifest.py` 更新清单。\n" + "=" * 60
        )
        from app.integrated_app.security.audit import audit_event

        audit_event("INTEGRITY_FAILURE", kind="selfcheck", failed_files=list(failed_files))
    elif passed > 0:
        logger.info(f"[SELF-CHECK] 核心模块完整性自检通过: {passed}/{total} ✓")

    if enforce and failed > 0:
        raise RuntimeError(f"核心模块完整性校验失败（enforce 模式，拒绝启动）: {', '.join(failed_files)}")

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "failed_files": failed_files,
    }


async def periodic_selfcheck_loop(interval_seconds: int) -> None:
    """运行时周期性重检核心模块完整性（协程，需作为 asyncio 后台任务运行）。

    每 interval_seconds 秒重跑一次 run_startup_selfcheck（enforce 语义：
    仅记录 error 日志并触发审计日志，不中断运行中的推理任务）。

    Args:
        interval_seconds: 重检间隔；<=0 时立即返回（调用方无需创建任务）。
    """
    if interval_seconds <= 0:
        return
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            result = await asyncio.to_thread(run_startup_selfcheck)
            if result["failed"] > 0:
                logger.error(
                    f"[SECURITY] 运行时周期完整性重检失败 {result['failed']} 项: "
                    f"{', '.join(result['failed_files'])}"
                )
        except asyncio.CancelledError:
            break
        except Exception as e:  # 单次重检异常不终止循环
            logger.warning(f"[SELF-CHECK] 周期完整性重检异常: {e}")
