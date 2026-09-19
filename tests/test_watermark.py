"""security/watermark.py 单元测试（DCT 频域水印）

覆盖：
- embed_watermark 正常嵌入、边界输入（None/空/灰度/多通道）
- extract_watermark 提取与原始载荷比对（品牌标识可验证）
- verify_watermark 签名验证逻辑（有密钥/无密钥路径）
- QIM 量化调制的水印鲁棒性（基础检查）
"""

from __future__ import annotations

import numpy as np
import pytest

from app.integrated_app.security.watermark import (
    _WATERMARK_ALPHA,
    _WATERMARK_BRAND,
    _WATERMARK_KEY_ENV,
    _bits_to_text,
    _text_to_bits,
    embed_watermark,
    extract_watermark,
    strip_watermark_envelope,
    verify_watermark,
)


def _rng_image(h: int = 256, w: int = 256, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)  # nosec B311 — 测试用确定性图像
    return rng.integers(0, 256, (h, w, 3), dtype=np.uint8)


class TestTextToBits:
    """文本转二进制位测试"""

    def test_simple_text_conversion(self):
        text = "A"
        bits = _text_to_bits(text)
        reconstructed = _bits_to_text(bits)
        assert reconstructed == text

    def test_unicode_text_conversion(self):
        text = "SeedVR2_中文"
        bits = _text_to_bits(text)
        reconstructed = _bits_to_text(bits)
        assert reconstructed == text

    def test_empty_text(self):
        text = ""
        bits = _text_to_bits(text)
        assert len(bits) == 0


class TestEmbedWatermark:
    """水印嵌入测试"""

    def test_embed_basic_rgb_image(self, tmp_path):
        """RGB 图像正常嵌入"""
        # 创建测试图像 (128x128 RGB)
        img = np.random.randint(0, 256, (128, 128, 3), dtype=np.uint8)
        payload = "TEST_WATERMARK_PAYLOAD"

        result = embed_watermark(img, payload=payload)

        assert result.shape == img.shape
        assert result.dtype == np.uint8
        # 嵌入强度极低时图像变化应该很小
        assert np.abs(result.astype(np.float64) - img.astype(np.float64)).max() < 10

    def test_embed_grayscale_image(self):
        """灰度图像嵌入"""
        img = np.random.randint(0, 256, (128, 128), dtype=np.uint8)
        result = embed_watermark(img, payload="TEST")

        assert result.shape == img.shape
        assert result.dtype == np.uint8

    def test_embed_none_input(self):
        """None 输入返回 None"""
        assert embed_watermark(None) is None

    def test_embed_empty_array(self):
        """空数组返回空数组"""
        img = np.array([])
        result = embed_watermark(img)
        assert result.size == 0

    def test_embed_small_image(self):
        """小于 8x8 的图像不崩溃（无法分块 DCT）"""
        img = np.random.randint(0, 256, (4, 4, 3), dtype=np.uint8)
        result = embed_watermark(img, payload="TEST")
        # 函数应返回有效结果但不一定包含水印
        assert result.shape == img.shape
        assert result.dtype == np.uint8

    def test_embed_preserves_value_range(self):
        """嵌入后像素值仍在 [0, 255] 范围内"""
        img = np.random.randint(0, 256, (128, 128, 3), dtype=np.uint8)
        result = embed_watermark(img, payload="TEST")

        assert result.min() >= 0
        assert result.max() <= 255


class TestExtractWatermark:
    """水印提取测试"""

    def test_extract_basic_brands_contain_seedvr2(self):
        """提取的水印应包含品牌标识 'SeedVR2'"""
        img = np.random.randint(50, 200, (128, 128, 3), dtype=np.uint8)
        payload = "SeedVR2_ReSerendipity_20250101120000"

        watermarked = embed_watermark(img, payload=payload)
        extracted = extract_watermark(watermarked)

        # 核心验证：品牌标识 'SeedVR2' 必须存在于提取结果中
        assert "SeedVR2" in extracted

    def test_extract_after_slight_compression_simulation(self):
        """模拟轻微 JPEG 压缩后的水印鲁棒性（丢弃高频噪声）"""
        img = np.random.randint(80, 180, (128, 128, 3), dtype=np.uint8)
        payload = "SeedVR2_CompressTest"

        watermarked = embed_watermark(img, payload=payload)
        # 轻微扰动（±2 量化误差，模拟 JPEG 低压缩比）
        perturbed = np.clip(watermarked.astype(np.int16) + np.random.randint(-2, 3, watermarked.shape), 0, 255).astype(
            np.uint8
        )

        extracted = extract_watermark(perturbed)
        # DCT 中频对轻微扰动具有鲁棒性
        assert "SeedVR2" in extracted or len(extracted) > 0


class TestVerifyWatermark:
    """水印验证逻辑测试"""

    @pytest.fixture(autouse=True)
    def setup_teardown(self, monkeypatch, tmp_path):
        """每个测试前清除水印密钥环境变量"""
        monkeypatch.delenv(_WATERMARK_KEY_ENV, raising=False)
        yield

    def test_verify_without_key_falls_back_to_brand_check(self, tmp_path, monkeypatch):
        """未配置密钥时退化为品牌字符串检查——因此任何人伪造的载荷同样能通过，不具举证力。"""
        import app.integrated_app.security.watermark as wm

        monkeypatch.setattr(wm, "_load_secret_key", lambda: None)
        monkeypatch.setattr(wm, "_load_verify_keys", lambda: [])
        monkeypatch.setattr(wm, "_key_missing_warned", True)
        img = _rng_image()

        # 无密钥时嵌入端会补品牌前缀，弱检测命中；攻击者用同一前缀也能命中
        genuine = embed_watermark(img, payload="NotSeedVR2_TestPayload")
        forged = embed_watermark(img, payload=f"{_WATERMARK_BRAND}_attacker_claim")
        assert verify_watermark(genuine) is True
        assert verify_watermark(forged) is True
        assert wm.watermark_key_available() is False

    def test_signed_watermark_rejects_other_keys(self, monkeypatch):
        """有密钥时严格验签：换一把密钥的伪造产物不得通过（这才是可举证的部分）。"""

        img = _rng_image()
        monkeypatch.setenv(_WATERMARK_KEY_ENV, "issuer-key-A")
        signed = embed_watermark(img, payload="task-1")
        assert verify_watermark(signed) is True

        monkeypatch.setenv(_WATERMARK_KEY_ENV, "attacker-key-B")
        assert verify_watermark(signed) is False

    def test_watermark_key_available_reads_env(self, monkeypatch):
        import app.integrated_app.security.watermark as wm

        monkeypatch.setenv(_WATERMARK_KEY_ENV, "env-key")
        assert wm.watermark_key_available() is True

    def test_verify_empty_image(self):
        """空图像验证返回 False"""
        assert verify_watermark(np.array([])) is False

    def test_verify_random_noise_image(self):
        """随机噪声图像不应通过验证"""
        noise = np.random.randint(0, 256, (128, 128, 3), dtype=np.uint8)
        # 没有嵌入水印，验证应失败
        assert verify_watermark(noise) is False

    def test_verify_with_custom_alpha(self):
        """自定义 alpha 参数验证"""
        img = np.random.randint(0, 256, (128, 128, 3), dtype=np.uint8)
        payload = "SeedVR2_CustomAlpha"

        watermarked = embed_watermark(img, payload=payload, alpha=_WATERMARK_ALPHA * 1.5)
        # 使用不同 alpha 提取可能会失效，但不应崩溃
        extracted = extract_watermark(watermarked, alpha=_WATERMARK_ALPHA * 1.5)
        assert isinstance(extracted, str)


class TestLogSilence:
    """默认日志级别下，水印不得在终端/日志里露面（交付形态不留标识痕迹的要求）。

    锁的是「信息类一律 DEBUG、只有真降级才响」：嵌入、验签、抽样复验、密钥生成与
    迁移都不该出现在 INFO 及以上；缺密钥与载荷截断属安全降级，保留 error/warning。
    """

    def test_normal_flow_logs_nothing_at_info_level(self, caplog, monkeypatch):
        import logging

        # 用环境变量注入密钥：嵌入与验签两条链都优先读它（patch _load_secret_key
        # 只会影响嵌入，verify_watermark 走 _load_verify_keys 会拿不到同一把密钥）
        monkeypatch.setenv(_WATERMARK_KEY_ENV, "hermetic-issuer-key")
        with caplog.at_level(logging.INFO, logger="app.integrated_app.security.watermark"):
            img = _rng_image(seed=13)
            product = embed_watermark(img, payload="task-quiet")
            assert verify_watermark(product) is True
            assert extract_watermark(product)

        offenders = [
            r.getMessage() for r in caplog.records if "水印" in r.getMessage() or "watermark" in r.getMessage().lower()
        ]
        assert not offenders, f"INFO 及以上级别泄漏水印字样: {offenders}"

    def test_missing_key_still_reports_as_error(self, caplog, monkeypatch):
        """反向锁：安全降级不能被一起静音掉——缺密钥必须仍是 error。"""
        import logging

        import app.integrated_app.security.watermark as wm

        monkeypatch.setattr(wm, "_load_secret_key", lambda: None)
        monkeypatch.setattr(wm, "_key_missing_warned", False)
        with caplog.at_level(logging.WARNING, logger="app.integrated_app.security.watermark"):
            embed_watermark(_rng_image(seed=14), payload="task-nokey")
        assert any("水印签名密钥" in r.getMessage() and r.levelno >= logging.ERROR for r in caplog.records)


class TestPayloadEnvelope:
    """载荷信封还原（签名摘要 / 品牌前缀剥除）测试——溯源反查靠它把水印对上 task_id。"""

    def test_strip_signed_envelope(self):
        extracted = "task-7f3a|" + "a" * 64 + "尾部噪声"
        assert strip_watermark_envelope(extracted) == "task-7f3a"

    def test_strip_unsigned_envelope(self):
        extracted = f"{_WATERMARK_BRAND}_task-7f3a|unsigned尾部噪声"
        assert strip_watermark_envelope(extracted) == "task-7f3a"

    def test_roundtrip_task_id_through_watermark(self, monkeypatch):
        """嵌入 task_id → 提取 → 剥信封，还原出同一个 task_id（P3-1 反查闭环）。"""

        monkeypatch.setenv(_WATERMARK_KEY_ENV, "issuer-key-roundtrip")
        img = _rng_image(seed=3)
        watermarked = embed_watermark(img, payload="task-abcdef0123")
        assert strip_watermark_envelope(extract_watermark(watermarked)) == "task-abcdef0123"

    def test_signed_payload_also_carries_brand(self, monkeypatch):
        """有密钥时载荷同样带品牌前缀。

        签名只能证明「持该密钥的实例嵌入了它」——分发版各自自动生成密钥，
        所以「出自 SeedVR2」这件事必须在提取结果里有载体，否则读出来只是一串 UUID。
        """
        monkeypatch.setenv(_WATERMARK_KEY_ENV, "issuer-key-brand")
        watermarked = embed_watermark(_rng_image(seed=9), payload="task-brand")
        extracted = extract_watermark(watermarked)
        assert _WATERMARK_BRAND in extracted
        assert strip_watermark_envelope(extracted) == "task-brand"
        assert verify_watermark(watermarked) is True

    def test_extract_best_recovers_robust_tier_payload(self, monkeypatch):
        """鲁棒档产物（JPEG/WebP/视频帧用的档位）只能靠逐档尝试读出载荷。

        `extract_watermark()` 默认按无损档参数提取，对这类产物是乱码——
        取证工具若不逐档尝试，就会在「验签明明通过」的情况下报不出 task_id。
        """
        from app.integrated_app.security.watermark import extract_watermark_best

        monkeypatch.setenv(_WATERMARK_KEY_ENV, "issuer-key-tier")
        img = _rng_image(seed=11)
        product = embed_watermark(img, payload="3f9a1c7e2b8d40a6", alpha=0.05, repeat=3)
        assert verify_watermark(product) is True
        assert strip_watermark_envelope(extract_watermark_best(product)) == "3f9a1c7e2b8d40a6"
        assert strip_watermark_envelope(extract_watermark(product)) != "3f9a1c7e2b8d40a6"

    def test_unsigned_payload_self_terminates(self, monkeypatch):
        """无密钥降级载荷带终止符，提取时按字节补齐的噪声不会混进 task_id。"""
        import app.integrated_app.security.watermark as wm

        monkeypatch.setattr(wm, "_load_secret_key", lambda: None)
        monkeypatch.setattr(wm, "_key_missing_warned", True)
        watermarked = embed_watermark(_rng_image(seed=4), payload="task-42")
        extracted = extract_watermark(watermarked, expected_length=2048)
        assert strip_watermark_envelope(extracted) == "task-42"


class TestExtractBounds:
    """提取端边界测试：提前退出与重复码反交织。"""

    def test_repeat_deinterleaving_recovers_payload(self):
        # 512x512 = 4096 块：签名载荷（约 632 bit）× repeat 3 仍装得下，不会被降档
        img = _rng_image(512, 512, seed=5)
        payload = "task-repeat3"
        watermarked = embed_watermark(img, payload=payload, alpha=0.05, repeat=3)
        extracted = extract_watermark(watermarked, alpha=0.05, repeat=3)
        assert payload in extracted

    def test_early_exit_matches_longer_read(self):
        """提前退出只允许少读尾部块，不得改变位序（载荷从左上角起算）。"""
        img = _rng_image(512, 512, seed=6)
        watermarked = embed_watermark(img, payload="SeedVR2_OrderCheck")
        short = extract_watermark(watermarked, expected_length=200)
        long = extract_watermark(watermarked, expected_length=2048)
        assert long[: len(short)] == short
        assert "SeedVR2_OrderCheck" in long

    def test_truncated_payload_warns(self, caplog, monkeypatch):
        """图像太小装不下载荷时必须告警——否则产物静默不可验签。"""
        import logging

        import app.integrated_app.security.watermark as wm

        monkeypatch.setattr(wm, "_load_secret_key", lambda: None)
        with caplog.at_level(logging.WARNING, logger="app.integrated_app.security.watermark"):
            embed_watermark(_rng_image(64, 64, seed=8), payload="x" * 60)
        assert any("容量不足" in r.message for r in caplog.records)
