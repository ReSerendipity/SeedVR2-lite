"""smoke 常驻执行模式（--serve-and-run）的本地可测逻辑（MLOps 后续建议落地）。

真实端到端（起服务→执行外部命令→关停）在 GPU runner 的 quant-baseline.yml
里跑；此处锁定其 CPU 可测的纯逻辑：参数解析（REMAINDER 吞并后续全部 token，
含 -- 开头）与 {python} 占位展开。

验收标准：
1. 不带 --serve-and-run 时默认空列表（常规冒烟路径零变化）；
2. 带 --serve-and-run 时，其后的全部参数（包括 --base-url 这类选项样式 token）
   完整进入列表、且不与 smoke 自身参数冲突；
3. resolve_serve_command 只展开 {python} 占位，其余 token 原样。
"""

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "smoke_portable_bundle", _REPO_ROOT / "scripts" / "smoke_portable_bundle.py"
)


def _load_smoke():
    module = importlib.util.module_from_spec(_SPEC)
    assert _SPEC.loader is not None
    _SPEC.loader.exec_module(module)  # noqa: S301 - 受控本地脚本
    return module


smoke = _load_smoke()


class TestServeAndRunArg:
    def test_default_empty(self):
        args = smoke.parse_args(["--app-dir", "X"])
        assert args.serve_and_run == []

    def test_remainder_swallows_option_like_tokens(self):
        args = smoke.parse_args(
            [
                "--app-dir",
                "X",
                "--require-inference",
                "--serve-and-run",
                "{python}",
                "perf/benchmark/quant_quality_baseline.py",
                "--base-url",
                "http://127.0.0.1:7870",
                "--resolution",
                "512",
            ]
        )
        assert args.serve_and_run == [
            "{python}",
            "perf/benchmark/quant_quality_baseline.py",
            "--base-url",
            "http://127.0.0.1:7870",
            "--resolution",
            "512",
        ]
        assert args.app_dir == "X"
        assert args.require_inference is True  # 前置自身参数照常解析


class TestResolveServeCommand:
    def test_placeholder_expanded(self):
        cmd = smoke.resolve_serve_command(["{python}", "run.py", "--x", "1"], r"C:\Py\python.exe")
        assert cmd == [r"C:\Py\python.exe", "run.py", "--x", "1"]

    def test_no_placeholder_passthrough(self):
        cmd = smoke.resolve_serve_command(["python", "a.py"], "/unused")
        assert cmd == ["python", "a.py"]
