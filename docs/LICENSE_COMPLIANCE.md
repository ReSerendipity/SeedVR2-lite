# SeedVR2-lite 许可证合规台账（License Compliance）

> 最后更新：2026-08-27（家族治理 Phase D6，实测枚举）。
> ⚠️「商用合规 / 合规要求」两列仅记录**事实与风险提示**，不构成法律意见；标「需人工确认」者必须人工补查后再分发。

## 1. 主程序许可

| 组件 | 许可证 | 商用合规 | 合规要求 |
|---|---|---|---|
| SeedVR2-lite 项目代码 | Apache-2.0（以根级 LICENSE 为准） | 宽松许可 | 保留版权声明与 NOTICE |

## 2. 随仓内嵌模型实现（`model_lib/`，ByteDance 上游派生）

> 本仓 **无内嵌 ComfyUI 内核**（实测无 `comfy_kernel/`）；`model_lib/` 为逐目录 vendor 的模型实现，溯源见 `model_lib/SOURCE.md`。

| 组件 | 许可证 | 商用合规 | 合规要求 |
|---|---|---|---|
| `model_lib/dit/`（NaDiT） | Apache-2.0（Copyright 2025 ByteDance Ltd.） | 宽松许可（与主许可同源） | 保留原版权头；随包附 NOTICE |
| `model_lib/dit_v2/` | 预留（未实现） | — | 实现后按上游条款补录 |
| `model_lib/video_vae_v3/`（SD3 因果 Video VAE） | Apache-2.0（Copyright 2023 HuggingFace Team / 2025 ByteDance Ltd.） | 宽松许可 | 保留原版权头；随包附 NOTICE |
| `model_lib/common/`（diffusion/distributed 等） | Apache-2.0（同上游） | 宽松许可 | 保留版权头 |

## 3. 模型权重

| 组件 | 许可证 | 商用合规 | 合规要求 |
|---|---|---|---|
| SeedVR2 权重（`model/`，`scripts/download_model.py` 下载） | 以 HuggingFace 仓库（Reserendipity/SeedVR2）与 NOTICE 为准 | 需人工确认 | 权重许可独立于代码许可 |

## 4. 维护约定

- `model_lib/SOURCE.md` 为溯源基线：新增/升级 vendored 模型代码，**必须**同步更新它与本台账。
- 复核命令（只读）：`Get-ChildItem model_lib -Directory`；`Get-Content model_lib/SOURCE.md -TotalCount 5`。