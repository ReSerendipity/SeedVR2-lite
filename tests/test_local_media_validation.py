"""folder/batch 入口本地媒体文件校验测试（数据治理 P1-2）。

验收标准（对应评估报告 §9.2 P1-2）：
1. 合法魔数文件通过校验（image / video / ftyp 容器）；
2. 伪装扩展名（魔数不匹配）→ 400；
3. 大小超限（按类型分上限）→ 400；
4. 文件不可读 → 400；
5. 校验读取的上限值来自 runtime.security.max_upload_*_mb 配置。
"""

import os

import pytest
from fastapi import HTTPException

from app.integrated_app.routes.restore.common import validate_local_media_files

PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
FAKE_PNG = b"This is definitely not a PNG file" + b"\x00" * 8


@pytest.fixture
def config_with_tiny_limit() -> dict:
    return {
        "runtime": {
            "security": {
                "max_upload_image_mb": 1,
                "max_upload_video_mb": 2,
            }
        }
    }


class TestValidateLocalMediaFiles:
    def test_valid_png_passes(self, tmp_path, config_with_tiny_limit):
        p = tmp_path / "ok.png"
        p.write_bytes(PNG_HEADER)
        validate_local_media_files([(str(p), "image")], config_with_tiny_limit)

    def test_fake_png_rejected(self, tmp_path, config_with_tiny_limit):
        """验收点 2：伪装扩展名文件 400。"""
        p = tmp_path / "evil.png"
        p.write_bytes(FAKE_PNG)
        with pytest.raises(HTTPException) as exc_info:
            validate_local_media_files([(str(p), "image")], config_with_tiny_limit)
        assert exc_info.value.status_code == 400
        assert "魔数" in exc_info.value.detail or "伪装" in exc_info.value.detail

    def test_oversize_image_rejected(self, tmp_path, config_with_tiny_limit):
        """验收点 3：超过 1MB 的图片 400。"""
        p = tmp_path / "big.png"
        p.write_bytes(PNG_HEADER + b"\x00" * (1024 * 1024 + 1))
        with pytest.raises(HTTPException) as exc_info:
            validate_local_media_files([(str(p), "image")], config_with_tiny_limit)
        assert exc_info.value.status_code == 400
        assert "超过限制" in exc_info.value.detail

    def test_oversize_video_uses_video_limit(self, tmp_path, config_with_tiny_limit):
        """验收点 5：视频走独立上限（2MB），1.5MB 的伪装 MP4 报魔数而非大小。"""
        # 1.5MB 纯文本伪装 .mp4：图片上限(1MB)会拒大小，视频上限(2MB)不拒
        # → 应走到魔数校验失败而非大小拒绝
        p = tmp_path / "v.mp4"
        p.write_bytes(b"This is a plain text file, not a video" + b"\x00" * (1536 * 1024))
        with pytest.raises(HTTPException) as exc_info:
            validate_local_media_files([(str(p), "video")], config_with_tiny_limit)
        assert exc_info.value.status_code == 400
        # 未触发大小限制（2MB），而是内容校验失败
        assert "超过限制" not in exc_info.value.detail

    def test_missing_file_rejected(self, tmp_path, config_with_tiny_limit):
        """验收点 4：文件不可读 400。"""
        missing = str(tmp_path / "ghost.png")
        with pytest.raises(HTTPException) as exc_info:
            validate_local_media_files([(missing, "image")], config_with_tiny_limit)
        assert exc_info.value.status_code == 400

    def test_multiple_files_first_bad_file_wins(self, tmp_path, config_with_tiny_limit):
        good = tmp_path / "good.png"
        good.write_bytes(PNG_HEADER)
        bad = tmp_path / "bad.png"
        bad.write_bytes(FAKE_PNG)
        with pytest.raises(HTTPException):
            validate_local_media_files([(str(good), "image"), (str(bad), "image")], config_with_tiny_limit)

    def test_default_limits_without_config_keys(self, tmp_path):
        """配置缺失时回退默认上限（图 50MB / 视频 500MB）。"""
        p = tmp_path / "mid.png"
        payload = PNG_HEADER + b"\x00" * (30 * 1024 * 1024)  # 30MB < 默认 50MB
        p.write_bytes(payload)
        validate_local_media_files([(str(p), "image")], {"runtime": {"security": {}}})
        assert os.path.getsize(str(p)) > 0
