# Nunchaku 技术学习报告（SeedVR2-lite 竞品对标 · 代码级）

> **性质**：竞品对标学习报告（非建议文档）。事实来自 `C:\Users\Doro\reference_repos\SeedVR2-lite\nunchaku` 浅克隆 + `gh api` 实时核验。
> **核验**：`nunchaku-ai/nunchaku` — **3,941★ / Apache-2.0 / 推送 2026-03-07**（SVDQuant，ICLR2025 Spotlight）。

## 一、概览
- **定位**：面向 **4-bit 神经网络的高性能推理引擎**（SVDQuant 量化）；覆盖 FLUX / Qwen-Image / Z-Image / SANA 等扩散模型，显著提升吞吐并降显存。
- **许可**：**Apache-2.0**（gh-api 确认）——可自由借鉴，无 GPL 风险。
- **形态**：Python 推理引擎（`nunchaku/` 包、`examples/` 脚本、ComfyUI 子目录）+ 量化库 DeepCompressor。

## 二、技术栈（README + 仓库结构）
- 量化：**SVDQuant** 4-bit（底层 DeepCompressor）；NVFP4（RTX 5090）~3× 提速且质量优于 INT4。
- 关键能力：异步 offload（FLUX VRAM 低至 **3 GiB** 无损）、per-layer CPU offload（最低 4 GiB）、4-bit T5 编码器（质量=FP8 T5）、multi-LoRA、ControlNet。
- 硬件：Ampere/Ada/Hopper；INT4 支持 20 系（Turing）；v1.2.0 起 **LoRA 原生 ComfyUI 节点**。

## 三、核心能力
- **Z-Image 大幅加速**：v1.2.0 带来 **20–30% Z-Image 性能提升**；已发布 4-bit `Tongyi-MAI/Z-Image-Turbo` 并附示例脚本——与 Image_MultiModel / 本仓 Z-Image 主线直接呼应。
- **极低显存**：异步 offload 使大模型 3 GiB 可跑，契合本仓低显存/便携包目标（RTX 5070 Ti Laptop 类）。
- **原生 LoRA + ComfyUI**：LoRA 支持与 ComfyUI 节点开箱即用，复用本仓 `comfy_kernel` 路线。

## 四、与 SeedVR2-lite 对标点（关键）
- **视频超分加速同源**：SeedVR2 系扩散超分，Nunchaku 的 SVDQuant 4-bit + offload 可直接降 SeedVR2 推理显存/提速（本仓以扩散为主，GAN 兜底）。
- **Z-Image 协同**：本仓若引入 Z-Image（见 Image_MultiModel 报告），Nunchaku 是官方推荐 4-bit 推理路径。
- **许可清洁**：Apache-2.0，区别于 comfy_kernel 的 GPL-3.0；作为加速引擎无传染风险。

## 五、许可与合规
- **Apache-2.0**：引擎+量化库可自由借鉴与商用；按 `THIRD_PARTY_NOTICES.md` 登记。
- 无 GPL 依赖（与 ComfyUI custom_nodes 的 9 个 GPL-3.0 节点形成对比，见 MiniMax-H3-lite `LICENSE_COMPLIANCE.md`）。

## 六、可借鉴点（P0/P1）
- **P0**：SVDQuant 4-bit + 异步 offload 作为本仓扩散超分/生成的低显存推理底座；Z-Image-Turbo 4-bit 示例直接复用。
- **P1**：multi-LoRA + ControlNet 支持补本仓 LoRA/控制能力。

## 七、风险 / 不适用
- 量化带来轻微质量折损（SVDQuant 设计已最小化）；须与本仓质量基线回归比对。
- 主打开源生态围绕 FLUX/Qwen-Image/Z-Image，对 SeedVR2 权重的 4-bit 转换需验证可用性。

## 八、参考文件（克隆内可复核）
- `reference_repos/SeedVR2-lite/nunchaku/README.md`（News/能力）
- `reference_repos/SeedVR2-lite/nunchaku/nunchaku/models/linear.py`（4-bit 线性层）
- `reference_repos/SeedVR2-lite/nunchaku/examples/`（各模型示例脚本）
