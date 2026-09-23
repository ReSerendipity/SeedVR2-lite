"""torch.compile 分阶段（dit / vae）取参与应用的行为测试。

锁的是"只编一个阶段"这个能力本身：改结构之前 DiT 与 VAE 读的是同一个 dict，
所以任何一侧单独开编译都做不到。这里覆盖取参解析、旧扁平配置兼容、以及
编译失败/不可用时**绝不中断出图**的降级契约。
"""

from __future__ import annotations

import torch
import torch.nn as nn

from app.integrated_app.engines.seedvr2_engine import _apply_stage_compile, _stage_compile_args

NESTED = {
    "dit": {"enabled": True, "mode": "reduce-overhead", "fullgraph": True, "backend": "inductor", "dynamic": False},
    "vae": {"enabled": False},
}
FLAT = {"enabled": True, "mode": "default", "backend": "inductor", "fullgraph": False, "dynamic": False}


# --------------------------------------------------------------------------
# 取参解析
# --------------------------------------------------------------------------


def test_nested_config_selects_only_its_own_stage():
    assert _stage_compile_args(NESTED, "dit")["enabled"] is True
    assert _stage_compile_args(NESTED, "vae")["enabled"] is False


def test_missing_stage_falls_back_to_disabled():
    """只配了 dit 时，vae 必须是「不编」而不是继承 dit 的 enabled=True。"""
    assert _stage_compile_args({"dit": {"enabled": True}}, "vae") == {}


def test_legacy_flat_config_applies_to_both_stages():
    """config.yaml 是用户手改的文件：旧扁平写法若被静默忽略，等于让人失去已开启的加速。"""
    assert _stage_compile_args(FLAT, "dit") == FLAT
    assert _stage_compile_args(FLAT, "vae") == FLAT


def test_empty_and_none_inputs_are_safe():
    for args in (None, {}, {"dit": None}, {"dit": {}}):
        assert _stage_compile_args(args, "dit") == {}, f"{args!r} 应解析为空（不编译）"


def test_returned_dict_is_a_copy():
    """返回的字典被上层 .get/更新使用，不得是 config 内部对象的别名。"""
    out = _stage_compile_args(NESTED, "dit")
    out["enabled"] = "篡改"
    assert NESTED["dit"]["enabled"] is True


# --------------------------------------------------------------------------
# 应用与降级契约
# --------------------------------------------------------------------------


class _FakeOptimizer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.avail = True
        self.compiled = 0

    def is_available(self):
        return self.avail

    def compile(self, model):
        self.compiled += 1
        model._fake_compiled = True
        return model


def _patch_optimizer(monkeypatch, factory):
    import app.integrated_app.optimization.gpu.vram_toolchain as vt

    monkeypatch.setattr(vt, "CompileOptimizer", factory, raising=True)


def test_disabled_stage_returns_model_untouched(monkeypatch):
    calls = []
    _patch_optimizer(monkeypatch, lambda cfg: calls.append(cfg) or _FakeOptimizer(cfg))
    model = nn.Linear(2, 2)
    out = _apply_stage_compile(model, {"dit": {"enabled": False}}, "dit")
    assert out is model
    assert calls == [], "未启用阶段不得构造编译配置"


def test_enabled_stage_compiles_with_its_own_params(monkeypatch):
    seen = {}

    def factory(cfg):
        seen["cfg"] = cfg
        return _FakeOptimizer(cfg)

    _patch_optimizer(monkeypatch, factory)
    model = nn.Linear(2, 2)
    out = _apply_stage_compile(model, NESTED, "dit")
    assert getattr(out, "_fake_compiled", False) is True
    assert seen["cfg"].mode == "reduce-overhead"
    assert seen["cfg"].fullgraph is True


def test_vae_stage_is_independent_of_dit(monkeypatch):
    """两阶段读同一份配置但结论相反 —— 这是本次拆分的全部意义。"""
    seen = []
    _patch_optimizer(monkeypatch, lambda cfg: seen.append(cfg) or _FakeOptimizer(cfg))
    model = nn.Linear(2, 2)
    _apply_stage_compile(model, NESTED, "dit")
    out_vae = _apply_stage_compile(model, NESTED, "vae")
    assert len(seen) == 1, "vae 未启用，不应再构造一次编译配置"
    assert out_vae is model


def test_unavailable_compiler_degrades_to_plain_model(monkeypatch):
    def factory(cfg):
        opt = _FakeOptimizer(cfg)
        opt.avail = False
        return opt

    _patch_optimizer(monkeypatch, factory)
    model = nn.Linear(2, 2)
    assert _apply_stage_compile(model, FLAT, "dit") is model


def test_compile_exception_never_propagates(monkeypatch):
    """加速项失败必须只降级；抛出去会让整次推理白跑，代价远大于少一个优化。"""

    def boom(cfg):
        raise RuntimeError("inductor unavailable")

    _patch_optimizer(monkeypatch, boom)
    model = nn.Linear(2, 2)
    assert _apply_stage_compile(model, FLAT, "vae") is model


def test_legacy_flat_still_compiles_both_stages(monkeypatch):
    seen = []
    _patch_optimizer(monkeypatch, lambda cfg: seen.append(cfg) or _FakeOptimizer(cfg))
    model = nn.Linear(2, 2)
    _apply_stage_compile(model, FLAT, "dit")
    _apply_stage_compile(model, FLAT, "vae")
    assert [c.mode for c in seen] == ["default", "default"]


# --------------------------------------------------------------------------
# compile_support 探测：必须跑到前向，否则惰性编译下会误报"可用"
# --------------------------------------------------------------------------


class _WrapsButFailsForward:
    """compile() 返回了包装对象，首次前向才失败 —— 真实惰性编译的失败时机。"""

    def __init__(self, inner):
        self._inner = inner

    def to(self, device):  # 真实 OptimizedModule 会代理 .to()，假对象也得会
        self._inner = self._inner.to(device)
        return self

    def __call__(self, *args, **kwargs):
        raise RuntimeError("codegen failed at first forward")


class _WrapsAndWorks:
    def __init__(self, inner):
        self._inner = inner

    def to(self, device):
        self._inner = self._inner.to(device)
        return self

    def __call__(self, *args, **kwargs):
        return self._inner(*args, **kwargs)


def _patch_support_optimizer(monkeypatch, wrap):
    from app.integrated_app.optimization.gpu import vram_toolchain as vt

    class Opt:
        def compile(self, model):
            return wrap(model)

        def last_error(self):
            return ""

    monkeypatch.setattr(vt, "_COMPILE_SUPPORT", None)
    monkeypatch.setattr(vt, "_triton_present", lambda: True)  # 只验前向逻辑，triton 前提另测
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)  # 不在 CI 上碰 GPU
    monkeypatch.setattr(vt, "CompileOptimizer", lambda cfg: Opt())
    return vt


def test_probe_reports_unavailable_when_forward_fails(monkeypatch):
    """回归锁：只测 compile() 返回值会在惰性编译下误报可用，UI 于是把坏开关显示成能用。"""
    vt = _patch_support_optimizer(monkeypatch, _WrapsButFailsForward)
    out = vt.compile_support()
    assert out["available"] is False
    assert "first forward" in str(out["reason"])


def test_probe_reports_available_only_after_successful_forward(monkeypatch):
    vt = _patch_support_optimizer(monkeypatch, _WrapsAndWorks)
    assert vt.compile_support()["available"] is True
    assert vt.compile_support()["reason"] == ""


def test_probe_flags_unwrapped_model_as_unavailable(monkeypatch):
    """compile() 静默返回原模型（本机真实情形：inductor 编码失败被吞）必须判不可用。"""
    from app.integrated_app.optimization.gpu import vram_toolchain as vt

    class Opt:
        def compile(self, model):
            return model

        def last_error(self):
            return "UnicodeDecodeError: gbk"

    monkeypatch.setattr(vt, "_COMPILE_SUPPORT", None)
    monkeypatch.setattr(vt, "_triton_present", lambda: True)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(vt, "CompileOptimizer", lambda cfg: Opt())
    out = vt.compile_support()
    assert out["available"] is False and "gbk" in out["reason"]


def test_real_probe_keeps_available_and_reason_consistent():
    """不变式：available 与 reason 必须互斥，否则调用方无法据此置灰开关。"""
    from app.integrated_app.optimization.gpu.vram_toolchain import compile_support

    out = compile_support(refresh=True)
    assert isinstance(out["available"], bool)
    assert bool(out["reason"]) is not out["available"]


def test_probe_refuses_to_claim_available_without_triton_on_cuda(monkeypatch):
    """回归锁：nn.Linear 探测会在缺 triton 的机器上误报可用，真 3B DiT 首帧才抛。"""
    import torch

    from app.integrated_app.optimization.gpu import vram_toolchain as vt

    monkeypatch.setattr(vt, "_COMPILE_SUPPORT", None)
    monkeypatch.setattr(vt, "_triton_present", lambda: False)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    out = vt.compile_support()
    assert out["available"] is False and "triton" in out["reason"]


def test_triton_check_is_skipped_without_cuda(monkeypatch):
    """无 CUDA 时不该因缺 triton 就判不可用（CPU/MPS 上编译另有可用路径）。"""
    import torch

    from app.integrated_app.optimization.gpu import vram_toolchain as vt

    monkeypatch.setattr(vt, "_COMPILE_SUPPORT", None)
    monkeypatch.setattr(vt, "_triton_present", lambda: False)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    vt.compile_support(refresh=True)["available"]  # 不因 triton 早退；结果取决于本机真实编译能力
    assert "triton" not in str(vt.compile_support()["reason"])


def test_probe_uses_cuda_when_available(monkeypatch):
    """回归锁：探测曾因固定在 CPU 上跑而误判不可用（CPU 上 inductor 退到 C++ 后端、要 MSVC
    cl.exe），会把可用的 CUDA 环境错误置灰。只断言"选了哪个设备"，不碰真 GPU（CI 无卡）。"""
    import types

    import torch

    from app.integrated_app.optimization.gpu import vram_toolchain as vt

    seen = []

    class Spy:
        def __init__(self, inner):
            self._inner = inner

        def to(self, device):
            seen.append(("to", str(device)))
            return self

        def __call__(self, x):
            seen.append(("forward_device", str(x.device)))
            return torch.zeros(2, 8)

    class Opt:
        def compile(self, model):
            return Spy(model)

        def last_error(self):
            return ""

    monkeypatch.setattr(vt, "_COMPILE_SUPPORT", None)
    monkeypatch.setattr(vt, "_triton_present", lambda: True)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(vt, "CompileOptimizer", lambda cfg: Opt())
    # 假装 CUDA 张量：避免这个测试需要真实 GPU
    monkeypatch.setattr(
        torch,
        "randn",
        lambda *a, **k: types.SimpleNamespace(device="cuda:0"),
    )

    assert vt.compile_support()["available"] is True
    assert seen == [("to", "cuda"), ("forward_device", "cuda:0")], "探测必须与被测对象同设备"
