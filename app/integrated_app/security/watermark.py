# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""不可感知数字水印模块 (DCT 频域水印)

在推理输出图像/视频帧中嵌入不可感知的 DCT 频域水印，
即使所有 UI/代码标识被移除，仍可从输出内容中提取归属水印，
是唯一可举证的侵权溯源手段。

水印信息: "SeedVR2_ReSerendipity" + 生成时间戳
嵌入策略: 在图像的 DCT 中频系数中嵌入二进制水印序列，
          利用人类视觉对中频不敏感的特性实现不可感知性。

安全特性:
    - 不可感知: 图像路径 (alpha=0.5) PSNR > 50dB；视频鲁棒档 (alpha=0.05)
      PSNR ≈ 37.5dB，属视觉透明档
    - 鲁棒性: 三通道等幅嵌入（纯亮度扰动）+ 连续重复码（视频路径 repeat=3），
      实测 H.264 CRF14/18/23 转码后签名验证存活（此前单通道嵌入转码后
      全灭，见 scripts/experiment_watermark_transcode.py 实验记录）
    - 可溯源: 提取水印可验证 "SeedVR2" 归属标识
    - 不可移除: 攻击者不知道水印嵌入位置和强度，难以完全去除

使用方式:
    from app.integrated_app.security.watermark import embed_watermark

    watermarked_np = embed_watermark(image_np)  # 在保存前调用
"""

import hashlib
import hmac
import logging
import os
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# 水印品牌标识
_WATERMARK_BRAND = "SeedVR2_ReSerendipity"

# 水印嵌入强度 (越小越不可感知，越大越鲁棒)
# 对于 QIM 量化: quant_step = 1.0 / alpha
# alpha=0.5 -> quant_step=2, 对 8bit 图像最大修改量 ~1, PSNR > 50dB
_WATERMARK_ALPHA = 0.5

# 视频帧路径的鲁棒档强度：quant_step=20，配合三通道等幅嵌入（纯亮度扰动）
# 与 repeat=3 重复码，实测 H.264 CRF14/18/23 转码后位误码率 ≈ 0
# （2026-09-06 转码实验，见 scripts/experiment_watermark_transcode.py）。
# 代价：PSNR ≈ 37.5dB（仍属视觉透明档），仅用于经有损编码的产物。
_VIDEO_ALPHA = 0.05
_VIDEO_REPEAT = 3

# ===== 密钥签名配置（v2）=====
# 载荷格式: "<brand>_<timestamp>|<hmac-sha256 hex>"
# 无密钥时嵌入旧格式未签名载荷（兼容旧版验证）；有密钥时嵌入签名载荷，
# 未持有密钥者无法伪造可通过验证的水印，溯源举证以签名验证为准。
_WATERMARK_KEY_ENV = "SEEDVR2_WATERMARK_KEY"
_WATERMARK_KEY_FILE = Path(__file__).resolve().parent.parent.parent.parent / ".watermark_key"
_HMAC_SEPARATOR = "|"

# DCT 块大小
_BLOCK_SIZE = 8

# 水印嵌入的中频系数位置 (在 8x8 DCT 块中)
# 选择中频区域 (4-6 行/列) 作为嵌入位置，平衡不可感知性和鲁棒性
_EMBED_POSITIONS = [
    (4, 5),
    (5, 4),
    (5, 6),
    (6, 5),
    (4, 6),
    (6, 4),
    (5, 5),
    (6, 6),
]


def _text_to_bits(text: str) -> np.ndarray:
    """将文本转换为二进制位序列。

    Args:
        text: 待嵌入的文本字符串。

    Returns:
        np.ndarray: 二进制位数组 (0 和 1)。
    """
    data = text.encode("utf-8")
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    return bits


def _bits_to_text(bits: np.ndarray) -> str:
    """将二进制位序列转换回文本。

    Args:
        bits: 二进制位数组。

    Returns:
        str: 解码后的文本字符串。
    """
    # 确保长度是 8 的倍数
    length = (len(bits) // 8) * 8
    if length == 0:
        return ""
    packed = np.packbits(bits[:length])
    try:
        return packed.tobytes().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _generate_watermark_payload() -> str:
    """生成包含品牌标识和时间戳的水印载荷。

    Returns:
        str: 格式为 "SeedVR2_ReSerendipity_YYYYMMDDHHMMSS" 的水印字符串。
    """
    timestamp = time.strftime("%Y%m%d%H%M%S")
    return f"{_WATERMARK_BRAND}_{timestamp}"


def _load_secret_key() -> bytes | None:
    """加载水印签名密钥（环境变量优先，其次项目根 .watermark_key 文件）。

    两者均未配置时首次运行自动生成密钥文件（等价 scripts/init_watermark_key.py），
    保证新部署开箱即有可证伪归属；生成失败（只读文件系统等）才降级为未签名水印。
    """
    env_key = os.environ.get(_WATERMARK_KEY_ENV, "").strip()
    if env_key:
        return env_key.encode("utf-8")
    try:
        if _WATERMARK_KEY_FILE.exists():
            key = _WATERMARK_KEY_FILE.read_text(encoding="utf-8").strip()
            if key:
                return key.encode("utf-8")
        # 首次运行自动生成（密钥文件已被 .gitignore 忽略，不会入库）
        import secrets as _secrets

        _WATERMARK_KEY_FILE.write_text(_secrets.token_hex(32) + "\n", encoding="utf-8")
        logger.info(f"已自动生成水印签名密钥: {_WATERMARK_KEY_FILE}（请离线备份）")
        return _WATERMARK_KEY_FILE.read_text(encoding="utf-8").strip().encode("utf-8")
    except Exception as e:
        logger.debug(f"水印密钥文件读写失败: {e}")
    return None


def _sign_payload(payload: str, key: bytes) -> str:
    """对水印载荷附加 HMAC-SHA256 签名。"""
    digest = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}{_HMAC_SEPARATOR}{digest}"


def _verify_signature(signed_payload: str, key: bytes) -> str | None:
    """验证签名载荷，返回原始载荷；签名缺失或无效返回 None。

    解析规则：首个分隔符前的部分为载荷，其后固定 64 位 hex 为摘要；
    提取噪声不会影响解析（分隔符后的多余内容被忽略）。
    比较以字节进行——提取噪声可能产生非 ASCII 字符，
    hmac.compare_digest 对含非 ASCII 的 str 会抛 TypeError，此处必须免疫。
    """
    sep_pos = signed_payload.find(_HMAC_SEPARATOR)
    if sep_pos < 0:
        return None
    payload = signed_payload[:sep_pos]
    digest = signed_payload[sep_pos + 1 : sep_pos + 1 + 64]
    if len(digest) != 64:
        return None
    expected = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected.encode("ascii"), digest.encode("utf-8", errors="replace")):
        return payload
    return None


def _dct_1d(arr: np.ndarray) -> np.ndarray:
    """一维 DCT 变换 (Type-II)。

    使用矩阵乘法实现，避免依赖 scipy。
    """
    size = arr.shape[-1]
    n = np.arange(size)
    k = n.reshape(-1, 1)
    dct_matrix = np.cos(np.pi * (2 * n + 1) * k / (2 * size)) * np.sqrt(2.0 / size)
    dct_matrix[0] *= 1.0 / np.sqrt(2)
    return arr @ dct_matrix.T


def _idct_1d(arr: np.ndarray) -> np.ndarray:
    """一维逆 DCT 变换 (Type-II 的逆)。

    使用矩阵乘法实现。
    """
    size = arr.shape[-1]
    n = np.arange(size)
    k = n.reshape(-1, 1)
    idct_matrix = np.cos(np.pi * (2 * k + 1) * n / (2 * size)) * np.sqrt(2.0 / size)
    idct_matrix[:, 0] *= 1.0 / np.sqrt(2)
    return arr @ idct_matrix.T


def _dct_2d_block(block: np.ndarray) -> np.ndarray:
    """二维 DCT 变换 (行 DCT 后列 DCT)。

    Args:
        block: 2D 数组 (通常 8x8)。

    Returns:
        np.ndarray: DCT 变换后的系数矩阵。
    """
    return _dct_1d(_dct_1d(block.T).T)


def _idct_2d_block(block: np.ndarray) -> np.ndarray:
    """二维逆 DCT 变换。

    Args:
        block: DCT 系数矩阵。

    Returns:
        np.ndarray: 空间域图像块。
    """
    return _idct_1d(_idct_1d(block.T).T)


def embed_watermark(
    image_np: np.ndarray,
    *,
    payload: str | None = None,
    alpha: float = _WATERMARK_ALPHA,
    repeat: int = 1,
) -> np.ndarray:
    """在图像中嵌入不可感知 DCT 频域水印。

    嵌入流程:
        1. 生成水印载荷 (品牌标识 + 时间戳)，配置密钥时附加 HMAC 签名
        2. 将载荷转换为二进制位序列
        3. 对图像分 8x8 块做 DCT 变换，**三通道等幅嵌入**：每个通道在相同
           系数位置做相同的 QIM 系数修改——三通道等幅扰动 = 纯亮度扰动
           （ΔY=Δ、ΔCr=0），构造上免疫 H.264 4:2:0 色度下采样对单通道
           嵌入的破坏（2026-09-06 转码实验确认的结构性根因，见
           scripts/experiment_watermark_transcode.py）
        4. 连续重复码：载荷位序列按 ``repeat`` 倍连续铺入块序列
           （块 j 承载位 ``bits[j // repeat]``），提取端按多数投票恢复；
           容量不足时自动降档（repeat 收敛到 B // len(bits)）
        5. 做 IDCT 变换回空间域，裁剪到有效像素范围 [0, 255]

    Args:
        image_np: 输入图像 NumPy 数组 (H x W x C, uint8)。
        payload: 自定义水印载荷，None 时自动生成品牌标识+时间戳。
        alpha: 水印嵌入强度（quant_step = 1/alpha）。图像路径用默认 0.5
            （PNG 无损保存，高保真）；经有损编码的产物（视频帧）用 0.05
            —— 实测 CRF14/18/23 转码后位误码率 ≈ 0（quant_step=20 承受
            H.264 中频量化噪声）。
        repeat: 重复码次数。视频路径建议 3；图像路径 1（PNG 无损无需冗余）。

    Returns:
        np.ndarray: 嵌入水印后的图像 (与输入相同 shape 和 dtype)。

    Note:
        - 图像路径 (alpha=0.5, repeat=1): PSNR > 50dB，视觉不可感知
        - 视频路径 (alpha=0.05, repeat=3): PSNR ≈ 37.5dB，属视觉透明档
          （DCT 中频 ±10 系数扰动分散到 8x8 块内 ±2-3 像素级变化）；
          换取 H.264 转码后签名验证可存活
    """
    if image_np is None or image_np.size == 0:
        return image_np

    if payload is None:
        payload = _generate_watermark_payload()
    key = _load_secret_key()
    if key is not None and _HMAC_SEPARATOR not in payload:
        payload = _sign_payload(payload, key)
    else:
        logger.debug(
            "未配置水印签名密钥，将嵌入未签名水印（不可证伪归属）。" "请运行 scripts/init_watermark_key.py 生成密钥"
        )

    bits = _text_to_bits(payload)
    if len(bits) == 0:
        return image_np

    # 转为 float 处理
    result = image_np.astype(np.float64).copy()
    h, w = result.shape[:2]
    if result.ndim == 2:
        result = result[:, :, np.newaxis]

    n_channels = result.shape[2]
    bit_idx = 0
    blocks_h = h // _BLOCK_SIZE
    blocks_w = w // _BLOCK_SIZE
    total_blocks = blocks_h * blocks_w
    # 容量不足时降档重复次数（至少 1 次完整嵌入）
    repeat = max(1, min(int(repeat), total_blocks // len(bits)))
    n_marked = min(total_blocks, len(bits) * repeat)
    quant_step = 1.0 / alpha

    for bi in range(blocks_h):
        for bj in range(blocks_w):
            if bit_idx >= n_marked:
                break

            # 提取 8x8 块
            y0 = bi * _BLOCK_SIZE
            x0 = bj * _BLOCK_SIZE
            bit = bits[bit_idx // repeat]

            modified_blocks = []
            for c in range(n_channels):
                block = result[y0 : y0 + _BLOCK_SIZE, x0 : x0 + _BLOCK_SIZE, c].copy()
                dct_block = _dct_2d_block(block)

                # 在中频位置嵌入水印位 (QIM: Quantization Index Modulation)
                # 三通道做相同的系数修改 → 扰动集中于亮度分量
                for py, px in _EMBED_POSITIONS:
                    coeff = dct_block[py, px]
                    quantized = round(coeff / quant_step)
                    if quantized % 2 != bit:
                        quantized += 1
                    dct_block[py, px] = quantized * quant_step

                modified_blocks.append(_idct_2d_block(dct_block))

            for c in range(n_channels):
                result[y0 : y0 + _BLOCK_SIZE, x0 : x0 + _BLOCK_SIZE, c] = modified_blocks[c]

            bit_idx += 1

    # 裁剪到有效范围并恢复 dtype
    result = np.clip(result, 0, 255).astype(image_np.dtype)
    if image_np.ndim == 2:
        result = result[:, :, 0]

    logger.debug(f"水印已嵌入: payload='{payload[:30]}...', {bit_idx}/{n_marked} 块已标记 (repeat={repeat})")
    return result


def extract_watermark(
    image_np: np.ndarray,
    *,
    expected_length: int = 256,
    alpha: float = _WATERMARK_ALPHA,
    repeat: int = 1,
) -> str:
    """从图像中提取 DCT 频域水印。

    用于验证输出图像是否包含 SeedVR2 归属水印。

    Args:
        image_np: 待检测的图像 NumPy 数组 (H x W x C, uint8)。
        expected_length: 期望提取的最大位数。
        alpha: 水印强度 (需与嵌入时一致)。
        repeat: 嵌入时的重复码次数 (需与嵌入时一致)；
            提取对每个位位置在 repeat 个连续块上做多数投票。

    Returns:
        str: 提取到的水印文本。如果包含 "SeedVR2" 则确认归属。
    """
    if image_np is None or image_np.size == 0:
        return ""

    data = image_np.astype(np.float64)
    if data.ndim == 2:
        data = data[:, :, np.newaxis]

    channel_data = data[:, :, 0]
    h, w = channel_data.shape
    blocks_h = h // _BLOCK_SIZE
    blocks_w = w // _BLOCK_SIZE
    total_blocks = blocks_h * blocks_w
    repeat = max(1, min(int(repeat), total_blocks))
    groups = total_blocks // repeat  # 每组 repeat 个连续块投票出一个位
    quant_step = 1.0 / alpha

    n_bits = min(expected_length, groups)
    bits: list[int] = []
    parity_flat: list[int] = []

    # 先按块顺序读出每块的中频奇偶投票结果
    for bi in range(blocks_h):
        for bj in range(blocks_w):
            block = channel_data[bi * _BLOCK_SIZE : (bi + 1) * _BLOCK_SIZE, bj * _BLOCK_SIZE : (bj + 1) * _BLOCK_SIZE]
            dct_block = _dct_2d_block(block)

            votes = []
            for py, px in _EMBED_POSITIONS:
                coeff = dct_block[py, px]
                votes.append(round(coeff / quant_step) % 2)
            parity_flat.append(1 if sum(votes) > len(votes) // 2 else 0)

    # 连续重复码反交织：位 j = 组 j（块 j*repeat .. j*repeat+repeat-1）多数投票
    for j in range(n_bits):
        group = parity_flat[j * repeat : (j + 1) * repeat]
        bits.append(1 if sum(group) * 2 > len(group) else 0)

    return _bits_to_text(np.array(bits, dtype=np.uint8))


# 验证候选方案 (alpha, repeat)：按嵌入路径枚举。
# - (0.5, 1)：图像路径默认（PNG 无损）与历史产物
# - (0.05, 1..3)：视频帧路径（有损编码鲁棒档，repeat 按容量可能降档）
# 实测依据见 scripts/experiment_watermark_transcode.py（2026-09-06）。
_VERIFY_SCHEMES: tuple[tuple[float, int], ...] = (
    (_WATERMARK_ALPHA, 1),
    (_VIDEO_ALPHA, 1),
    (_VIDEO_ALPHA, 2),
    (_VIDEO_ALPHA, 3),
)


def verify_watermark(image_np: np.ndarray, *, expected_length: int = 2048) -> bool:
    """验证图像是否包含可信的 SeedVR2 归属水印（v2 签名验证）。

    - 配置了密钥时（推荐）：严格验证 HMAC 签名，仅持有密钥嵌入的水印通过；
      旧版未签名水印将验证失败（无法证明真伪）。
    - 未配置密钥时：退化为弱检测（品牌字符串包含检查），仅作参考。
    - 依次尝试 :data:`_VERIFY_SCHEMES` 中的 (alpha, repeat) 组合，
      图像路径与视频路径（含容量降档）的产物均可验证，历史产物保持兼容。

    Args:
        image_np: 待验证的图像。
        expected_length: 提取位数上限（签名载荷较长，默认 2048 bit）。

    Returns:
        bool: True 表示检测到可信水印。
    """
    key = _load_secret_key()
    for alpha, repeat in _VERIFY_SCHEMES:
        try:
            extracted = extract_watermark(image_np, expected_length=expected_length, alpha=alpha, repeat=repeat)
            if not extracted:
                continue
            if key is not None:
                if _verify_signature(extracted, key) is not None:
                    return True
            elif "SeedVR2" in extracted:
                return True
        except Exception as e:
            logger.debug(f"水印验证失败 (alpha={alpha}, repeat={repeat}): {e}")
    return False
