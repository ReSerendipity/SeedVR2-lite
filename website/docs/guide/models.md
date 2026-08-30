# 模型下载与选型

## 模型格式与精度

> **模型格式：`.safetensors`**（非 GGUF、非 PTH）。SeedVR2 官方与社区仓库均以
> HuggingFace `safetensors` 格式分发，本项目仅兼容该格式。
> 精度支持 **FP16（全精度，画质最佳）** 与 **FP8（E4M3FN 量化，省显存）** 两种；
> **不兼容 GGUF / INT4 / INT8 等其他量化**（这些格式在修复类扩散模型中会明显损伤画质）。

## 资源占用与效果对比

| 模型 | 精度 | 文件直链（`huggingface.co/numz/SeedVR2_comfyUI/resolve/main/…`） | 最低显存 | 约内存 | 效果 |
|---|---|---|---|---|---|
| SeedVR2-3B | FP16 | `seedvr2_ema_3b_fp16.safetensors` | 16 GB | ~12 GB | ★★★ 最佳 |
| SeedVR2-3B | FP8 | `seedvr2_ema_3b_fp8_e4m3fn.safetensors` | 8 GB | ~8 GB | ★★☆ 略降 |
| SeedVR2-7B | FP16 | `seedvr2_ema_7b_fp16.safetensors` | 24 GB | ~20 GB | ★★★ 最佳 |
| SeedVR2-7B | FP8 | `seedvr2_ema_7b_fp8_e4m3fn.safetensors` | 12 GB | ~12 GB | ★★☆ 略降 |
| SeedVR2-7B-Sharp | FP16 | `seedvr2_ema_7b_sharp_fp16.safetensors` | 24 GB | ~20 GB | ★★★ 最佳（细节增强） |
| SeedVR2-7B-Sharp | FP8 | `seedvr2_ema_7b_sharp_fp8_e4m3fn.safetensors` | 12 GB | ~12 GB | ★★☆ 略降 |

> 配套必需文件（所有模型共用）：
> `ema_vae_fp16.safetensors`（视频 VAE）、`pos_emb.pt` / `neg_emb.pt`（文本嵌入）。

### 选型建议

- 显存 ≤ 12 GB → 选 **3B FP8**（最低 8 GB）或 7B FP8 + BlockSwap
- 显存 16–24 GB → 选 **3B FP16** 或 **7B FP8**（画质/显存均衡）
- 显存 ≥ 24 GB → 选 **7B-Sharp FP16**（三档中画质与细节最好）

> 📌 "最低显存"为模型推理所需的显卡显存下限（来自 `config.yaml` 的 `model.models.*.min_vram_*_gb`）；
> "约内存"为推理时系统 RAM 占用经验值，实际以「系统状态」页监控为准。
> ⚠️ **重要说明**：当前项目的 FP8 实现**仅用于权重存储格式**。推理时权重仍按 FP16/FP32 加载，
> 因此 **FP8 模型和 FP16 模型的推理速度基本相同**。真正影响速度的是 BlockSwap、分辨率和帧数。
> - **BlockSwap 开启**：降低 20-70% 速度（取决于交换块数）
> - **分辨率提高**：2048×2048 比 1024×1024 慢 3-4 倍  
> - **FP8 vs FP16**：几乎无差异（当前未实现真正的 FP8 计算内核）
>
> 显存不足时可通过 **FP8 + BlockSwap**（GPU/CPU 动态换入换出 Transformer 块）进一步压降显存需求。

## 下载方式

### 方式 A：自动下载（推荐）

```bash
python scripts/download_model.py --size 3b        # 3B + VAE + 嵌入（默认）
python scripts/download_model.py --size 7b        # 7B + VAE + 嵌入
python scripts/download_model.py --size 7b_sharp  # 7B-Sharp + VAE + 嵌入
```

- 已存在的文件会自动跳过，可随时重跑补全，支持断点续传
- 大陆网络慢：先执行 `set HF_ENDPOINT=https://hf-mirror.com` 再重跑脚本

### 方式 B：手动下载（网络更稳时）

每个文件的**完整直链**（把 `<FILE>` 替换成下表文件名，`hf-mirror.com` 为国内加速镜像）：

```text
https://huggingface.co/numz/SeedVR2_comfyUI/resolve/main/<FILE>
https://hf-mirror.com/numz/SeedVR2_comfyUI/resolve/main/<FILE>   # 国内加速
```

| 文件 | 说明 |
|---|---|
| `seedvr2_ema_3b_fp16.safetensors` | 3B DiT（FP16） |
| `seedvr2_ema_3b_fp8_e4m3fn.safetensors` | 3B DiT（FP8） |
| `seedvr2_ema_7b_fp16.safetensors` | 7B DiT（FP16） |
| `seedvr2_ema_7b_fp8_e4m3fn.safetensors` | 7B DiT（FP8） |
| `seedvr2_ema_7b_sharp_fp16.safetensors` | 7B-Sharp DiT（FP16） |
| `seedvr2_ema_7b_sharp_fp8_e4m3fn.safetensors` | 7B-Sharp DiT（FP8） |
| `ema_vae_fp16.safetensors` | 视频 VAE（所有模型共用，必须） |
| `pos_emb.pt` / `neg_emb.pt` | 文本嵌入（所有模型共用，必须） |

把下载好的文件放到 `model/` 根目录，**文件名不要改**。

> 备选来源：官方仓库 `huggingface.co/ByteDance-Seed/SeedVR2-3B` / `SeedVR2-7B`（文件名可能略异，需对照 `config.yaml` 中的 `checkpoint_*` / `vae_checkpoint` / `pos_emb` / `neg_emb` 字段）。

## 验证放对位置

最终 `model/` 根目录下应直接看到这些文件（以 3B 为例）：

```text
model/
├── seedvr2_ema_3b_fp16.safetensors
├── seedvr2_ema_3b_fp8_e4m3fn.safetensors
├── ema_vae_fp16.safetensors
├── pos_emb.pt
└── neg_emb.pt
```

> 💡 多项目共用一套模型？把 `config.yaml` 的 `model.model_source_mode` 改为 `shared` 并指定 `model.shared_models_root` 指向共享目录即可（见下方「模型共享模式」）。

## 模型共享模式（shared）

一台机器跑多个 SeedVR2 实例（或家族内多个项目）时，默认的 `portable` 模式会让每个实例各自持有一份约 60 GB 的权重。`shared` 模式让所有实例指向同一份物理文件，磁盘占用只算一份：

```yaml
model:
  model_source_mode: shared
  shared_models_root: "D:/shared_models"   # 绝对路径，目录结构要求与 portable 的 model/ 完全一致
  pretrained_dir: model                    # portable 模式的回退值，shared 下不再使用
```

要求与行为：

- 共享目录必须是**平铺结构**（权重直接放在根下，不要建子目录），文件名与各模型条目的 `checkpoint_fp16` / `checkpoint_fp8` / `vae_checkpoint` / `pos_emb` / `neg_emb` 字段一致；
- `shared_models_root` 为空字符串时自动回退到 `portable` 模式（使用 `pretrained_dir`）；
- SHA256 完整性校验（`config.yaml` 各条目的 `sha256_*` 字段）在 shared 模式下同样生效，多个实例共享的是同一份已校验权重；
- 首次配置：把现有 `model/` 目录的内容复制或移动到共享目录即可，之后其他实例无需再下载；
- 下载脚本可直接写入共享目录：`python scripts/download_model.py --size 3b --save-dir "D:/shared_models"`；
- 7B 与 7B-Sharp 是**不同的权重**（字节数相同但内容不同，`sha256_*` 可验证），共享目录里两组文件都需要保留；如果只用得上 FP8，可按需只保留 `*_fp8_e4m3fn.safetensors` 以减半存储。

