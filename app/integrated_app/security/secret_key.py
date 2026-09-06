# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""服务端密钥管理模块 - 持久化 SECRET_KEY。

首次启动时生成随机密钥并持久化到 data/.seedvr2_secret，
后续重启复用同一密钥，保证 CSRF token 跨重启有效。

安全策略:
    - 密钥使用 secrets.token_bytes(32) 生成（256 位熵）
    - 持久化文件权限限制为 0o600（仅所有者可读写）
    - 密钥以 hex 编码存储，方便 YAML 引用
    - 支持从 config.yaml 指定密钥，覆盖自动生成的密钥
    - 支持环境变量 SEEDVR2_SECRET_KEY 覆盖（容器化/多实例部署场景）

数据治理 P3-3 新增能力:
    - harden_secret_file_permissions: 密钥文件权限收紧（POSIX 0600 / Windows ACL）
    - sign_bytes / sign_file / verify_file_signature: 任意文件的 HMAC-SHA256
      签名与校验，用于给 integrity_manifest.json 这类"与被校验对象同目录"
      的信任文件加签名，堵住"同步篡改代码与清单"的投毒路径。

使用方式:
    from app.integrated_app.security.secret_key import get_secret_key

    key = get_secret_key()  # 返回 bytes，32 字节
"""

import contextlib
import hashlib
import hmac
import logging
import os
import secrets
import stat
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# 默认密钥持久化路径（相对于项目根目录）
_DEFAULT_KEY_FILE = "data/.seedvr2_secret"

# 密钥字节数
_KEY_BYTES = 32

# 环境变量覆盖（优先级最高，容器化部署用）
_SECRET_KEY_ENV = "SEEDVR2_SECRET_KEY"

# 签名文件后缀（manifest.json → manifest.json.sig）
SIGNATURE_SUFFIX = ".sig"

# 按密钥文件路径分键的缓存：不同 key_file 各自独立，绝不串用同一密钥。
# （历史缺陷：单例 _cached_key 忽略 key_file 参数，第二个不同路径的调用会
# 拿到第一个路径的密钥——ubuntu 全量套件曾以 FileNotFoundError 暴露。）
# 锁保护"读文件/生成持久化"临界区，兑现模块文档的线程安全承诺。
_cache_lock = threading.Lock()
_cached_keys: dict[Path, bytes] = {}


def _default_key_file() -> Path:
    """默认密钥文件路径（项目根 data/.seedvr2_secret）。"""
    project_root = Path(__file__).resolve().parents[3]
    return project_root / _DEFAULT_KEY_FILE


def get_secret_key(key_file: str | os.PathLike | None = None) -> bytes:
    """获取服务端持久化密钥。

    优先级：环境变量 SEEDVR2_SECRET_KEY → 密钥文件 → 生成并持久化。
    按密钥文件路径分键缓存（线程安全），同一文件路径重复调用返回同一密钥；
    不同路径互不影响。

    Args:
        key_file: 密钥文件路径，为 None 时使用默认路径 data/.seedvr2_secret。

    Returns:
        32 字节随机密钥（bytes）。

    Raises:
        RuntimeError: 所有密钥来源均不可用时抛出（不应静默降级为弱密钥）。
    """
    env_key = os.environ.get(_SECRET_KEY_ENV, "").strip()
    if env_key:
        # 环境变量不进缓存：避免同一进程内环境变量变更被缓存掩盖
        try:
            return bytes.fromhex(env_key)
        except ValueError:
            return env_key.encode("utf-8")

    resolved = _default_key_file() if key_file is None else Path(key_file)
    cache_key = resolved.resolve()

    with _cache_lock:
        cached = _cached_keys.get(cache_key)
        if cached is not None:
            return cached

        if resolved.exists():
            try:
                hex_str = resolved.read_text(encoding="utf-8").strip()
                key = bytes.fromhex(hex_str)
                if len(key) != _KEY_BYTES:
                    logger.warning("密钥文件内容长度异常，重新生成密钥")
                    key = _generate_and_persist(resolved)
                # P3-3：读取时自愈历史部署的过宽权限（最佳实践-effort）
                harden_secret_file_permissions(resolved)
                logger.debug("从持久化文件加载服务端密钥")
                _cached_keys[cache_key] = key
                return key
            except Exception as e:
                logger.warning(f"读取密钥文件失败，重新生成: {e}")

        key = _generate_and_persist(resolved)
        _cached_keys[cache_key] = key
        return key


def _generate_and_persist(key_file: Path) -> bytes:
    """生成新密钥并持久化到文件。

    Args:
        key_file: 密钥文件路径。

    Returns:
        新生成的 32 字节密钥。
    """
    key = secrets.token_bytes(_KEY_BYTES)

    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(key.hex(), encoding="utf-8")

    # P3-3：权限收紧（0o600，Windows 下尽力收紧 ACL）
    harden_secret_file_permissions(key_file)

    logger.info(f"已生成并持久化服务端密钥: {key_file}")
    return key


def reset_cached_key() -> None:
    """清空全部密钥缓存（仅用于测试）。

    下次调用 get_secret_key 时会重新从文件读取或生成。
    """
    with _cache_lock:
        _cached_keys.clear()


# ---------------------------------------------------------------------------
# P3-3：密钥文件权限收紧
# ---------------------------------------------------------------------------


def harden_secret_file_permissions(path: str | os.PathLike) -> bool:
    """收紧密钥文件权限（POSIX 0600；Windows 尽力收紧 ACL）。

    Windows 上 chmod 只能影响只读位，因此额外尝试用 icacls 移除继承并
    仅保留当前用户完全控制；icacls 不可用时仅记录 debug 日志。

    Args:
        path: 密钥文件路径。

    Returns:
        收紧成功返回 True；文件不存在或失败返回 False（不抛异常）。
    """
    p = Path(path)
    if not p.exists():
        return False

    try:
        if os.name == "nt":
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
            # 临时目录内不跑 icacls：修改 ACL 会破坏 pytest 临时目录回收（WinError 5）
            temp_root = os.path.realpath(os.environ.get("TEMP", "") or os.environ.get("TMP", "") or "")
            if temp_root and os.path.realpath(str(p)).startswith(temp_root):
                return True
            try:
                import subprocess

                # KNOWN_ISSUES #76：icacls 在中文 Windows 输出 GBK 文本，而
                # PYTHONUTF8=1 环境（本仓 WinPython 默认）的 text=True 会按
                # UTF-8 解码 → _readerthread 崩线程、stdout 静默丢失。显式
                # encoding + errors=replace 保证解码永不中断（输出仅作 debug 日志）
                result = subprocess.run(
                    [
                        "icacls",
                        str(p),
                        "/inheritance:r",
                        "/grant:r",
                        f"{os.environ.get('USERNAME', '*')}:F",
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                    check=False,
                )
                if result.returncode != 0:
                    logger.debug("icacls 收紧密钥权限未成功（可忽略）: %s", (result.stderr or "").strip()[:120])
            except Exception as e:  # noqa: BLE001 - 平台能力缺失不阻断
                logger.debug("Windows 密钥权限收紧跳过: %s", e)
            return True

        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
        return True
    except Exception as e:  # noqa: BLE001 - 权限收紧失败不阻断业务
        logger.warning("密钥文件权限收紧失败: %s (%s)", p, e)
        return False


# ---------------------------------------------------------------------------
# P3-3：文件 HMAC-SHA256 签名 / 校验
# ---------------------------------------------------------------------------


def signature_path_for(file_path: str | os.PathLike) -> Path:
    """由被签名文件路径推导签名文件路径（追加 .sig）。"""
    return Path(f"{Path(file_path)}{SIGNATURE_SUFFIX}")


def sign_bytes(data: bytes, key: bytes) -> str:
    """对字节内容计算 HMAC-SHA256 十六进制摘要。"""
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def sign_file(file_path: str | os.PathLike, key: bytes | None = None) -> Path | None:
    """为文件生成 HMAC-SHA256 签名（写入同名 .sig 文件）。

    Args:
        file_path: 待签名文件。
        key: 密钥；None 时自动获取（失败返回 None）。

    Returns:
        签名文件路径；文件不存在或无法获取密钥时返回 None。
    """
    p = Path(file_path)
    if not p.exists():
        return None
    try:
        key_bytes = key if key is not None else get_secret_key()
    except Exception as e:  # noqa: BLE001 - 密钥不可用时降级为未签名
        logger.warning("获取签名密钥失败: %s", e)
        return None
    digest = sign_bytes(p.read_bytes(), key_bytes)
    sig_path = signature_path_for(p)
    sig_path.write_text(digest + "\n", encoding="utf-8")
    return sig_path


def verify_file_signature(file_path: str | os.PathLike, key: bytes | None = None) -> bool:
    """校验文件签名是否与当前内容匹配。

    Args:
        file_path: 待校验文件。
        key: 密钥；None 时自动获取。

    Returns:
        签名存在且匹配返回 True；无签名/无密钥/不匹配返回 False。
    """
    p = Path(file_path)
    sig_path = signature_path_for(p)
    if not p.exists() or not sig_path.exists():
        return False
    try:
        key_bytes = key if key is not None else get_secret_key()
        expected = sig_path.read_text(encoding="utf-8").strip()
    except (OSError, RuntimeError) as e:
        logger.debug("签名校验读取失败: %s", e)
        return False
    if not expected:
        return False
    with contextlib.suppress(OSError):
        return hmac.compare_digest(sign_bytes(p.read_bytes(), key_bytes), expected)
    return False
