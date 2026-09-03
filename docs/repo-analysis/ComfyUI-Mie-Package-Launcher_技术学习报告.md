# ComfyUI-Mie-Package-Launcher 技术学习报告（SeedVR2-lite 竞品对标 · 代码级）

> **性质**：竞品对标学习报告（非建议文档）。事实来自 `C:\Users\Doro\reference_repos\SeedVR2-lite\Mie-Package-Launcher` 浅克隆 + `gh api` 实时核验。
> **核验**：`MieMieeeee/ComfyUI-Mie-Package-Launcher` — **273★ / Apache-2.0 / 推送 2026-08-27**。

## 一、概览
- **定位**：**ComfyUI 启动器**——PyQt5 GUI + 无窗口 CLI，统一管理 ComfyUI 启停 / 多环境切换 / 镜像代理 / 内核+前端+模板库+依赖更新。
- **许可**：**Apache-2.0**（gh-api 确认）——可自由借鉴，无 GPL 风险。
- **形态**：Python（`__main__.py` CLI + PyQt5 GUI）；配套 `AGENTS.md` / `cli.md` 契约文档。

## 二、技术栈（README）
- CLI（agent 唯一入口）：`status --json`（退出码 0=在跑/3=未跑/1=异常）、`start`（阻塞到 `/system_stats` 就绪）、`stop`（幂等）、`update comfyui`（内核+前端+模板+依赖）、`info --json`、`logs`（务必 `--no-follow`）。
- 设计原则：**以 CLI 为准**，不自行加 `--env` 或改 `config.json` 绕过 GUI；稳定退出码（0/1/2/3/4）适合 systemd / NSSM / cron / GitHub Actions。
- 内核/前端分离：`update comfyui` 同步内核 + 前端 + 模板库 + 依赖，支持多环境切换。

## 三、核心能力
- **Agent 友好 CLI 契约**：JSON 输出 + 稳定退出码，便于自动化/CI/自启。
- **内核/前端分离更新**：便携包场景下内核与前端独立演进，降低打包复杂度。
- **多环境管理**：同机多套 ComfyUI 环境一键切换。
- **镜像代理**：内置 HF/PyPI/GitHub 镜像源配置（契合国内网络环境）。

## 四、与 SeedVR2-lite 对标点（关键）
- **便携包内核/前端分离**：本仓 SeedVR2-lite 以 **PyInstaller 便携包 + VitePress 文档站**交付（主报告 §4.3 标「便携包内核/前端分离更新」）——Mie-Launcher 的分离更新范式可直接借鉴进本仓便携包维护。
- **Agent CLI 契约**：本仓若提供 agent/自动化接口，其 `status/start/stop/--json/退出码` 契约是现成模板（对齐本仓 `AGENTS.md` 规范）。
- **合规清洁**：Apache-2.0，无 GPL 风险。

## 五、许可与合规
- **Apache-2.0**：代码可自由借鉴与商用；按 `THIRD_PARTY_NOTICES.md` 登记。
- 无 GPL 依赖。

## 六、可借鉴点（P0/P1）
- **P0**：便携包「内核/前端分离更新 + Agent CLI 契约（退出码/--json）」范式，作为本仓 SeedVR2 便携包与自动化维护参考。
- **P1**：多环境切换 + 镜像代理配置借鉴进本仓安装器。

## 七、风险 / 不适用
- 星数低（273★）、社区小；作为范式借鉴而非整库引入。
- PyQt5 GUI 体积较大，便携包须评估是否仅需 CLI 子集。

## 八、参考文件（克隆内可复核）
- `reference_repos/SeedVR2-lite/Mie-Package-Launcher/README.md`（CLI 契约/FAQ）
- `reference_repos/SeedVR2-lite/Mie-Package-Launcher/AGENTS.md`（agent 操作指南）
- `reference_repos/SeedVR2-lite/Mie-Package-Launcher/cli.md`（CLI flag/退出码/schema）
