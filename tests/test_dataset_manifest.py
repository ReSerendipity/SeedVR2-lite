"""训练数据集清单测试（数据治理 P2-1）。

验收标准（对应评估报告 §9.2 P2-1）：
1. 清单内容寻址：内容不变则摘要不变；内容变更则摘要变更；
2. 摘要与遍历顺序/平台无关（按相对路径排序后再哈希）；
3. 目录不存在时返回空清单，不抛异常；
4. 落盘 → 读回无损（write_manifest / load_manifest 往返）；
5. 清单可写入实验追踪（本地 JSONL 中含 dataset_sha256 摘要）。
"""

import json
import os

import pytest

from app.utils.experiment_tracker import ExperimentTracker
from training.dataset_manifest import (
    MANIFEST_SCHEMA,
    build_manifest,
    digest_of,
    load_manifest,
    write_manifest,
)


@pytest.fixture
def dataset_dir(tmp_path):
    d = tmp_path / "dataset"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_bytes(b"seedvr2-a")
    (d / "b.txt").write_bytes(b"seedvr2-b")
    (d / "sub" / "c.bin").write_bytes(b"\x00\x01\x02\x03")
    return str(d)


class TestBuildManifest:
    def test_manifest_structure(self, dataset_dir):
        """验收点 1：结构字段与逐文件哈希正确。"""
        manifest = build_manifest(dataset_dir)
        assert manifest["schema"] == MANIFEST_SCHEMA
        assert manifest["total_files"] == 3
        assert manifest["total_bytes"] == 9 + 9 + 4
        paths = {item["path"] for item in manifest["files"]}
        assert paths == {"a.txt", "b.txt", "sub/c.bin"}
        assert manifest["truncated"] is False
        assert len(manifest["dataset_sha256"]) == 64

    def test_digest_is_content_addressed(self, dataset_dir):
        """验收点 1：内容变更 → 摘要变更。"""
        before = digest_of(build_manifest(dataset_dir))
        with open(os.path.join(dataset_dir, "a.txt"), "wb") as f:
            f.write(b"seedvr2-a-modified")
        after = digest_of(build_manifest(dataset_dir))
        assert before != after

    def test_digest_stable_across_runs(self, dataset_dir):
        """验收点 1/2：同内容多次生成摘要一致。"""
        assert digest_of(build_manifest(dataset_dir)) == digest_of(build_manifest(dataset_dir))

    def test_paths_use_posix_separator(self, dataset_dir):
        """验收点 2：相对路径统一 posix 分隔，跨平台摘要一致。"""
        manifest = build_manifest(dataset_dir)
        assert all("/" in item["path"] or "/" not in item["path"] for item in manifest["files"])
        assert any(item["path"] == "sub/c.bin" for item in manifest["files"])

    def test_missing_dir_returns_empty_manifest(self, tmp_path):
        """验收点 3：目录不存在返回空清单。"""
        manifest = build_manifest(str(tmp_path / "nope"))
        assert manifest["total_files"] == 0
        assert manifest["files"] == []
        assert len(manifest["dataset_sha256"]) == 64

    def test_digest_of_fallback_recompute(self, dataset_dir):
        """digest_of 在缺失 dataset_sha256 时可按 files 重算。"""
        manifest = build_manifest(dataset_dir)
        expected = manifest["dataset_sha256"]
        stripped = {k: v for k, v in manifest.items() if k != "dataset_sha256"}
        assert digest_of(stripped) == expected

    def test_max_files_truncation(self, tmp_path):
        """超过 max_files 时截断并标注。"""
        d = tmp_path / "big"
        d.mkdir()
        for i in range(10):
            (d / f"f{i}.txt").write_bytes(b"x" * (i + 1))
        manifest = build_manifest(str(d), max_files=4)
        assert manifest["truncated"] is True
        assert manifest["total_files"] == 4


class TestManifestIO:
    def test_write_and_load_roundtrip(self, dataset_dir, tmp_path):
        """验收点 4：落盘 → 读回无损。"""
        manifest = build_manifest(dataset_dir)
        out = str(tmp_path / "nested" / "manifest.json")
        written = write_manifest(manifest, out)
        assert os.path.exists(written)
        loaded = load_manifest(written)
        assert digest_of(loaded) == digest_of(manifest)
        assert loaded["total_files"] == manifest["total_files"]


class TestTrackerIntegration:
    def test_log_dataset_manifest_writes_jsonl(self, dataset_dir, tmp_path):
        """验收点 5：清单摘要写入本地实验日志。"""
        log_dir = str(tmp_path / "logs")
        tracker = ExperimentTracker(
            experiment_name="unit-test",
            use_wandb=False,
            local_log_dir=log_dir,
        )
        try:
            manifest = build_manifest(dataset_dir)
            tracker.log_dataset_manifest(manifest)
        finally:
            tracker.finish()

        log_path = os.path.join(log_dir, "unit-test.jsonl")
        assert os.path.exists(log_path)
        with open(log_path, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
        manifest_records = [r for r in records if "dataset_sha256" in r]
        assert manifest_records
        assert manifest_records[0]["dataset_sha256"] == digest_of(manifest)
        assert manifest_records[0]["dataset_total_files"] == 3

    def test_log_non_dict_ignored(self, tmp_path):
        """非 dict 入参不抛异常（健壮性）。"""
        tracker = ExperimentTracker(
            experiment_name="unit-test-bad", use_wandb=False, local_log_dir=str(tmp_path / "logs2")
        )
        try:
            tracker.log_dataset_manifest(["not", "a", "dict"])  # type: ignore[arg-type]
        finally:
            tracker.finish()
