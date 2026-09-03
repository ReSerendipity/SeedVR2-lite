# SageAttention 技术学习报告（SeedVR2-lite 竞品对标 · 代码级）

> **性质**：竞品对标学习报告（非建议文档）。事实来自 `gh api` 实时核验 + README（thu-ml/SageAttention）。
> **核验**：`thu-ml/SageAttention` — **3,702★ / Apache-2.0 / 推送 2026-01-17**（SageAttention2++ / SageAttention3）。

## 一、概览
- **定位**：**即插即用的注意力加速库**——对 QKᵀ 做 INT8 量化+平滑、对 PV 做 FP8 量化，在几乎所有模型上**无损提速**；覆盖 SageAttention / 2 / 2++ / 3。
- **许可**：**Apache-2.0**（gh-api 确认）——可自由借鉴，无 GPL 风险。
- **形态**：CUDA/Python 内核库；支持 `torch.compile`（非 cuda-graph 模式）与分布式推理。

## 二、技术栈（README）
- 量化：QKᵀ INT8 + 平滑（多变粒度）；PV FP8 + FP16 累加；两级累加提升 FP8 精度。
- 硬件：优化内核覆盖 **Ampere / Ada / Hopper**；SageAttention3 为 **FP4（Blackwell）** 微缩放注意力。
- 论文：SageAttention（arXiv:2410.02367）、SageAttention2（2411.10958）、SageAttention3（2505.11594，NeurIPS2025 Spotlight）。

## 三、核心能力
- **无损注意力加速**：即插即用，不改模型结构，跨模型通用。
- **多代演进**：2++ 更高速度同精度；3 为 Blackwell FP4（精度敏感仍推荐 2）。
- **低侵入**：作为注意力后端替换，易集成进扩散/Transformer 推理。

## 四、与 SeedVR2-lite 对标点（关键）
- **扩散超分加速底座**：SeedVR2 系 DiT 视频超分，注意力是计算热点；SageAttention 即插即用加速可降时延（主报告 §4.3 标「已有调研备忘，升级到 2.2」）。
- **与 Nunchaku 互补**：Nunchaku 做权重 4-bit 量化，SageAttention 做注意力 8/4-bit——双层量化共同压低显存/提速，构成完整低显存方案。
- **硬件前瞻**：SageAttention3 FP4 为 Blackwell（RTX 50 系）铺路，契合本仓 5070 Ti 类新卡。

## 五、许可与合规
- **Apache-2.0**：内核+库可自由借鉴与商用；按 `THIRD_PARTY_NOTICES.md` 登记。
- 无 GPL 依赖。

## 六、可借鉴点（P0/P1）
- **P0**：将 SageAttention2++ 作为本仓扩散/Transformer 推理的注意力后端（升级至 2.2，主报告已指示）；与 Nunchaku 4-bit 组合成低显存推理栈。
- **P1**：SageAttention3 FP4 作为 Blackwell 新卡的前瞻加速项。

## 七、风险 / 不适用
- 精度敏感场景须谨慎（官方建议精度敏感用 2 而非 3）；须与本仓质量基线回归。
- CUDA-only（Ampere+），老卡/非 CUDA 设备不适用。

## 八、参考文件
- `gh api repos/thu-ml/SageAttention`（stars/license/pushed 核验）
- `thu-ml/SageAttention` GitHub README（特性/更新/论文）
