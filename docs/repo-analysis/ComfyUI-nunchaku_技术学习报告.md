# ComfyUI-nunchaku 技术学习报告（SeedVR2-lite 竞品对标 · 代码级）

> **性质**：竞品对标学习报告（非建议文档）。事实来自 `C:\Users\Doro\reference_repos\SeedVR2-lite\ComfyUI-nunchaku` 浅克隆 + `gh api` 实时核验。
> **核验**：`nunchaku-ai/ComfyUI-nunchaku` — **2,920★ / Apache-2.0 / 推送 2026-02-19**。

## 一、概览
- **定位**：[Nunchaku](https://github.com/nunchaku-ai/nunchaku)（SVDQuant 4-bit 推理引擎）的 **ComfyUI 插件**，把 4-bit 量化模型接入本仓已有的 ComfyUI 工作流。
- **许可**：**Apache-2.0**（gh-api 确认）——可自由借鉴，无 GPL 风险。
- **形态**：ComfyUI 自定义节点 + `example_workflows/`（含 `install_wheel.json`、`nunchaku-z-image-turbo.json` 等）。

## 二、技术栈（README + 仓库结构）
- 节点：`NunchakuWheelInstaller`（装正确 wheel）、各模型量化节点；v1.0.0 起异步 offload（VRAM 低至 3 GiB）。
- 工作流：提供 Z-Image-Turbo / Qwen-Image / FLUX 系列 `example_workflows/*.json`，含 `install_wheel.json` 一键装引擎。
- 兼容性：ComfyUI 0.7+；20 系 GPU INT4；LoRA 原生节点（v1.2.0）。

## 三、核心能力
- **4-bit 模型即拖即用**：量化 Z-Image-Turbo / Qwen-Image / FLUX 在工作流中直接出图，显存大降。
- **Wheel 自管理**：`NunchakuWheelInstaller` 节点保证 ComfyUI 内装对 Nunchaku wheel，降低部署摩擦。
- **LoRA / ControlNet**：v1.2.0 起原生支持，复用本仓 LoRA 资产。

## 四、与 SeedVR2-lite 对标点（关键）
- **直接可复用节点**：本仓 `comfy_kernel/custom_nodes/` 已 vendor 多节点；ComfyUI-nunchaku 可作为**低显存扩散推理节点**加入，给 SeedVR2 超分/生成提速（主报告 §4.3 已标，且「已验证 svdq-int4 z-image-turbo」）。
- **Z-Image 闭环**：配合 `nunchaku` 引擎的 Z-Image-Turbo 4-bit，形成本仓 Z-Image 轻量推理链路。
- **许可清洁**：Apache-2.0，区别于 comfy_kernel 内 9 个 GPL-3.0 节点（见 MiniMax-H3-lite `LICENSE_COMPLIANCE.md` §3）。

## 五、许可与合规
- **Apache-2.0**：节点代码可自由 vendor/借鉴；按 `THIRD_PARTY_NOTICES.md` 登记。
- 无 GPL 依赖。

## 六、可借鉴点（P0/P1）
- **P0**：vendor `ComfyUI-nunchaku` 作为本仓低显存扩散节点（4-bit Z-Image/SeedVR2 推理）；复用 `install_wheel.json` 的 wheel 自管理范式。
- **P1**：LoRA/ControlNet 原生节点补本仓控制能力。

## 七、风险 / 不适用
- 依赖 `nunchaku` 引擎本体（须同步 vendor/锁定版本，记录 `FILEMAP`/`ARCH_MAP`）。
- 量化质量须与本仓扩散基线回归。

## 八、参考文件（克隆内可复核）
- `reference_repos/SeedVR2-lite/ComfyUI-nunchaku/README.md`（News/能力）
- `reference_repos/SeedVR2-lite/ComfyUI-nunchaku/example_workflows/`（Z-Image-Turbo 等工作流）
- `reference_repos/SeedVR2-lite/nunchaku/`（引擎依赖）
