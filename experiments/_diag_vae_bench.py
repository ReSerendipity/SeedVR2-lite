"""诊断: SeedVR2 VAE tiled decode 性能对比 (Conv3d workaround on/off, 不同 tile size)。

运行: ./.venv/Scripts/python.exe experiments/_diag_vae_bench.py
"""

import gc
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import torch  # noqa: E402
from safetensors.torch import load_file  # noqa: E402

import model_lib.video_vae_v3.modules.causal_inflation_lib as cil  # noqa: E402
from model_lib.video_vae_v3.modules.attn_video_vae import (  # noqa: E402
    VideoAutoencoderKLWrapper,
)

VAE_CKPT = ROOT / "model" / "ema_vae_fp16.safetensors"
YAML = ROOT / "model_lib" / "video_vae_v3" / "s8_c16_t4_inflation_sd3.yaml"

print("torch", torch.__version__, "cudnn", torch.backends.cudnn.version())
print("workaround default =", cil.NVIDIA_CONV3D_MEMORY_BUG_WORKAROUND)


def build_vae():
    import yaml

    params = yaml.safe_load(YAML.read_text(encoding="utf-8"))
    with torch.device("meta"):
        model = VideoAutoencoderKLWrapper(
            spatial_downsample_factor=params.get("spatial_downsample_factor", 8),
            temporal_downsample_factor=params.get("temporal_downsample_factor", 4),
            in_channels=params.get("in_channels", 3),
            out_channels=params.get("out_channels", 3),
            down_block_types=tuple(params.get("down_block_types")),
            up_block_types=tuple(params.get("up_block_types")),
            block_out_channels=tuple(params.get("block_out_channels")),
            layers_per_block=params.get("layers_per_block", 2),
            latent_channels=params.get("latent_channels", 16),
            use_quant_conv=params.get("use_quant_conv", False),
            use_post_quant_conv=params.get("use_post_quant_conv", False),
            temporal_scale_num=params.get("temporal_scale_num", 2),
            inflation_mode=params.get("inflation_mode", "pad"),
            slicing_sample_min_size=params.get("slicing_sample_min_size", 4),
            freeze_encoder=False,
        )
    sd = load_file(str(VAE_CKPT), device="cpu")
    for k in list(sd.keys()):
        if sd[k].dtype != torch.bfloat16:
            sd[k] = sd[k].to(torch.bfloat16)
    info = model.load_state_dict(sd, strict=False, assign=True)
    del sd
    gc.collect()
    print("missing", len(info.missing_keys), "unexpected", len(info.unexpected_keys))
    for _n, m in model.named_modules():
        for bn, b in list(m.named_buffers(recurse=False)):
            if b.is_meta:
                setattr(m, bn, torch.zeros_like(b, device="cpu"))
    model.requires_grad_(False).eval()
    return model.cuda()


def bench(model, z, tile_px, overlap_px, tag, workaround):
    cil.NVIDIA_CONV3D_MEMORY_BUG_WORKAROUND = workaround
    orig = model.slicing_decode
    times = []

    def timed(zz):
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        out = orig(zz)
        torch.cuda.synchronize()
        times.append(time.perf_counter() - t0)
        return out

    model.slicing_decode = timed
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    with torch.no_grad():
        out = model.decode(z.clone(), tiled=True, tile_size=(tile_px, tile_px), tile_overlap=(overlap_px, overlap_px))
    torch.cuda.synchronize()
    total = time.perf_counter() - t0
    model.slicing_decode = orig
    peak = torch.cuda.max_memory_allocated() / 2**30
    print(
        f"[{tag}] workaround={workaround} tile={tile_px}/{overlap_px} "
        f"tiles={len(times)} total={total:.2f}s per_tile={[f'{t:.2f}' for t in times]} "
        f"peak={peak:.2f}GB out={tuple(out.sample.shape)}"
    )
    del out
    gc.collect()
    torch.cuda.empty_cache()
    return total


def main():
    model = build_vae()
    torch.cuda.empty_cache()
    print("VAE on gpu, VRAM alloc =", torch.cuda.memory_allocated() / 2**30)

    # 与日志一致: latent 234x418 (输出 1872x3344)
    z = torch.randn(1, 16, 1, 234, 418, device="cuda", dtype=torch.bfloat16)

    for workaround in (True, False):
        for tile, ov in ((1024, 128), (512, 64)):
            try:
                bench(model, z, tile, ov, f"t{tile}", workaround)
            except Exception as e:  # noqa: BLE001
                print(f"  !! tile={tile} workaround={workaround} FAILED: {type(e).__name__}: {e}")
                gc.collect()
                torch.cuda.empty_cache()

    # 单帧小 latent 基线
    z_small = torch.randn(1, 16, 1, 64, 64, device="cuda", dtype=torch.bfloat16)
    for workaround in (True, False):
        cil.NVIDIA_CONV3D_MEMORY_BUG_WORKAROUND = workaround
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
        t0 = time.perf_counter()
        with torch.no_grad():
            o = model.decode(z_small, tiled=False)
        torch.cuda.synchronize()
        dt = time.perf_counter() - t0
        print(f"[single 64x64 latent, no tiling] workaround={workaround} {dt:.3f}s out={tuple(o.sample.shape)}")
        del o
        gc.collect()
        torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
