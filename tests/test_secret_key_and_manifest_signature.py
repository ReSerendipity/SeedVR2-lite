"""密钥权限与清单签名测试（数据治理 P3-3）。

验收标准（对应评估报告 §9.2 P3-3）：
1. 密钥文件权限收紧：POSIX 下为 0600（Windows 下不报错、尽力而为）；
2. 文件签名 → 校验通过；内容篡改 → 校验失败；
3. 无签名文件 → 校验返回 False（不是异常）；
4. 密钥轮换后旧签名失效（密钥即信任根）；
5. 启动自检查觉未签名清单：非 enforce 时告警继续，enforce 时拒绝启动。
"""

import os
import stat
import sys

import pytest

from app.integrated_app.security.secret_key import (
    SIGNATURE_SUFFIX,
    get_secret_key,
    harden_secret_file_permissions,
    sign_bytes,
    sign_file,
    signature_path_for,
    verify_file_signature,
)

TEST_KEY = b"unit-test-secret-key"


class TestSecretPermissions:
    def test_harden_permissions_posix(self, tmp_path, monkeypatch):
        """验收点 1：POSIX 下收紧为 0600。"""
        # Windows 分支会调用 icacls 修改 ACL，在临时目录内执行会污染
        # pytest 临时目录回收（WinError 5）；此处将其替换为 no-op 隔离副作用
        if os.name == "nt":
            import subprocess

            class _Result:
                returncode = 0
                stderr = ""

            monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Result())

        key_file = tmp_path / "secret.key"
        key_file.write_text("a" * 64, encoding="utf-8")
        os.chmod(key_file, 0o644)
        assert harden_secret_file_permissions(key_file) is True
        if os.name != "nt":
            mode = stat.S_IMODE(os.stat(key_file).st_mode)
            assert mode == 0o600

    def test_harden_missing_file_returns_false(self, tmp_path):
        assert harden_secret_file_permissions(tmp_path / "nope.key") is False

    def test_get_secret_key_is_stable_and_reads_env(self, tmp_path, monkeypatch):
        """环境变量优先于密钥文件。"""
        monkeypatch.setenv("SEEDVR2_SECRET_KEY", "from-env")
        assert get_secret_key(key_file=tmp_path / "unused.key") == b"from-env"


class TestFileSignature:
    def test_sign_and_verify(self, tmp_path):
        """验收点 2：签名 → 校验通过。"""
        target = tmp_path / "manifest.json"
        target.write_text('{"files": {}}', encoding="utf-8")
        sig = sign_file(target, TEST_KEY)
        assert sig is not None and sig.exists()
        assert sig.name.endswith(SIGNATURE_SUFFIX)
        assert verify_file_signature(target, TEST_KEY) is True

    def test_tampered_content_fails(self, tmp_path):
        """验收点 2：内容篡改 → 校验失败。"""
        target = tmp_path / "manifest.json"
        target.write_text('{"files": {"a": "1"}}', encoding="utf-8")
        sign_file(target, TEST_KEY)
        target.write_text('{"files": {"a": "TAMPERED"}}', encoding="utf-8")
        assert verify_file_signature(target, TEST_KEY) is False

    def test_missing_signature_returns_false(self, tmp_path):
        """验收点 3：无签名文件返回 False。"""
        target = tmp_path / "unsigned.json"
        target.write_text("{}", encoding="utf-8")
        assert verify_file_signature(target, TEST_KEY) is False
        assert not signature_path_for(target).exists()

    def test_key_rotation_invalidates_old_signature(self, tmp_path):
        """验收点 4：换密钥后旧签名失效。"""
        target = tmp_path / "manifest.json"
        target.write_text('{"files": {}}', encoding="utf-8")
        sign_file(target, TEST_KEY)
        assert verify_file_signature(target, b"rotated-key") is False

    def test_signature_path_derivation(self, tmp_path):
        target = tmp_path / "x.json"
        assert str(signature_path_for(target)).endswith("x.json.sig")

    def test_sign_bytes_deterministic(self):
        assert sign_bytes(b"abc", TEST_KEY) == sign_bytes(b"abc", TEST_KEY)
        assert sign_bytes(b"abc", TEST_KEY) != sign_bytes(b"abd", TEST_KEY)


class TestSelfCheckIntegration:
    def test_verify_manifest_signature_helper(self, tmp_path, monkeypatch):
        """自检辅助函数：无签名返回 False，签名后返回 True。"""
        from app.integrated_app.security import integrity_selfcheck as isc

        fake_manifest = tmp_path / "integrity_manifest.json"
        fake_manifest.write_text('{"files": {}}', encoding="utf-8")
        # 用固定密钥替代真实密钥源，保证可重复
        monkeypatch.setattr("app.integrated_app.security.secret_key.get_secret_key", lambda *a, **k: TEST_KEY)
        assert isc.verify_manifest_signature(fake_manifest) is False
        sign_file(fake_manifest, TEST_KEY)
        assert isc.verify_manifest_signature(fake_manifest) is True

    def test_enforce_rejects_unsigned_manifest(self, tmp_path, monkeypatch):
        """验收点 5：enforce=True 且清单未签名 → 拒绝启动。"""
        from app.integrated_app.security import integrity_selfcheck as isc

        monkeypatch.setattr(isc, "_get_manifest_path", lambda: tmp_path / "integrity_manifest.json")
        (tmp_path / "integrity_manifest.json").write_text('{"files": {}}', encoding="utf-8")
        monkeypatch.setattr("app.integrated_app.security.secret_key.get_secret_key", lambda *a, **k: TEST_KEY)
        with pytest.raises(RuntimeError):
            isc.run_startup_selfcheck(enforce=True)

    def test_non_enforce_warns_and_continues(self, tmp_path, monkeypatch):
        """验收点 5：非 enforce 时告警继续（向后兼容）。"""
        from app.integrated_app.security import integrity_selfcheck as isc

        monkeypatch.setattr(isc, "_get_manifest_path", lambda: tmp_path / "integrity_manifest.json")
        (tmp_path / "integrity_manifest.json").write_text('{"files": {}}', encoding="utf-8")
        monkeypatch.setattr("app.integrated_app.security.secret_key.get_secret_key", lambda *a, **k: TEST_KEY)
        result = isc.run_startup_selfcheck(enforce=False)
        assert result["total"] == 0

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX 权限断言")
    def test_generated_key_file_mode(self, tmp_path):
        key_file = tmp_path / "gen.key"
        key = get_secret_key(key_file=key_file)
        assert key
        mode = stat.S_IMODE(os.stat(key_file).st_mode)
        assert mode == 0o600
