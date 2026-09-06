# Models Source Baseline

This directory contains third-party model implementations derived from ByteDance's open-source projects. All files retain original copyright notices and are licensed under Apache-2.0 per upstream terms.

## Upstream Sources

### NaDiT (Native Resolution Diffusion Transformer)

- **Upstream**: Bytedance SeedVR2 / NaDiT
- **Repository**: https://github.com/Bytedance/SeedVR2 (official, may be private or archived)
- **License**: Apache-2.0 (Copyright 2025 Bytedance Ltd. and/or its affiliates)
- **Vendored Files**: `model_lib/dit/*`

**File Mapping**:
```
model_lib/dit/
├── __init__.py          # Re-exports get_block, NaDiT, NaDiTConfig
├── attention.py         # Attention mechanisms (torch normal + FA2 varlen)
├── embedding.py         # Timestep embeddings
├── mlp.py               # Feedforward networks
├── mm.py                # Multimodal wrapper (video/text dual-branch)
├── modulation.py        # AdaLN-Zero adaptive modulation
├── normalization.py     # Norm factory (LayerNorm, RMSNorm, FusedNorm)
├── patch.py             # 3D Patch embedder/decoder
├── rope.py              # 3D RoPE positional encoding
├── window.py            # Spatiotemporal window partitioning
├── na.py                # Variable-length sequence tools
├── nadit.py             # NaDiT main model definition
├── blocks/              # Standard DiT transformer blocks
└── nablocks/            # NaDiT transformer blocks
```

**Modifications**: Implementation adapted for SeedVR2-specific configurations (window size, latent dimension). Original architecture preserved.

---

### Stable Diffusion 3 Video VAE (Causal Video AutoencoderKL)

- **Upstream**: Stability AI / ByteDance SD3 VAE
- **License**: Apache-2.0 (Copyright 2023 HuggingFace Team; Copyright 2025 ByteDance Ltd.)
- **Vendored Files**: `model_lib/video_vae_v3/*`

**Purpose**: Causal 3D Video Autoencoder for temporal video compression/decompression in the SeedVR2 pipeline. Supports tiled decoding for memory efficiency.

---

### DiT v2 (Active Main Inference Path)

- **Upstream**: Bytedance SeedVR2 (same official project as `model_lib/dit`)
- **License**: Apache-2.0 (Copyright 2025 Bytedance Ltd. and/or its affiliates)
- **Vendored Files**: `model_lib/dit_v2/*` (~1.8k 行)
- **Consumed by**: `app/integrated_app/engines/seedvr2_engine.py:775` (`from model_lib.dit_v2.nadit import NaDiT`)、
  `_dit_pipeline.py:155`；配置入口 `configs_*/config.json` 的 `block_type: mmdit_sr`

> **勘误（2026-09-06，MLOps 评估落地）**：本节曾标注「Future extension placeholder / Not yet implemented」，
> 与运行时事实不符——`mmdit_sr` 块与 `dit_v2.nadit.NaDiT` 是 3B/7B 的**现役主推理路径**。

---

## Weight Download Strategy

Model weights (not source code) are downloaded separately via `scripts/download_model.py`:

```bash
# Download 3B model + VAE + text embeddings
python scripts/download_model.py --size 3b

# Download 7B or 7B-Sharp
python scripts/download_model.py --size 7b
python scripts/download_model.py --size 7b_sharp
```

Weights come from HuggingFace repository `Reserendipity/SeedVR2` or mirrors. See `NOTICE` file for full attribution.

## Compliance Checklist

When distributing this project:

- [ ] Include `NOTICE` file with all third-party attributions
- [ ] Preserve copyright headers in `model_lib/dit/*` and `model_lib/video_vae_v3/*`
- [ ] Provide link to upstream repositories where possible
- [ ] Ensure downstream users know weight licenses are separate from code license

## Version Tracking

To track changes in upstream model implementations:

1. Periodically check if new commits exist at upstream repos (if accessible)
2. Review `docs/repo-analysis/` for technical reports on similar models
3. Document any modifications made to adapt model APIs to local pipeline

### 本地锚定（可验证，MLOps P2-7）

上游以**官方发布包**形式提取（非 git 克隆），无上游 commit 哈希可锚；以下为本仓库
**导入 commit** 锚点（`git log --diff-filter=A -1 -- <path>` 可复现验证），作为回滚与
追溯的可逆基线：

| 目录 | 导入 commit | 日期 | 消费方 |
|---|---|---|---|
| `model_lib/dit`、`model_lib/dit_v2`、`model_lib/video_vae_v3` | `3cf7190` | 2026-08-20 | `engines/_dit_pipeline.py`、`seedvr2_engine.py` |
| `common/` | `c839113` | 2026-07-19 | 推理内核 + `training/distributed_trainer.py` |

**升级 SOP（vendored 内核更新）**：
1. 获取上游源码（官方发布包或仓库可达时），与本表锚点目录做 `diff -r`；
2. 逐文件合并，保留本节 "Modifications" 所列的本地适配（window size / latent dim / mmdit_sr 接口）；
3. 静态门禁：`ruff check model_lib && mypy app/integrated_app`（model_lib 属禁区目录，
   仅人工授权后修改——见 AGENTS.md §3）；
4. 回归：本地 CPU 测试 → 真机 `gpu-smoke.yml`（含 PSNR≥15/SSIM≥0.5 质量门）必须绿；
5. 更新本文件「本地锚定」表（新导入/升级 commit + 日期）、`Last updated`，并在
   `CHANGELOG.md` 记一条；上游可达后应回填真实上游 commit 哈希替换本表。

---

*Last updated*: 2026-09-06
*Baseline commit*: 见「本地锚定」表（`3cf7190`/`c839113`，导入 commit 锚点；上游 commit 未随发布包携带）
