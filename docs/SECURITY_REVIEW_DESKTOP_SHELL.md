# 桌面壳（desktop/）聚焦安全评估（评估报告 R10 · 2026-09-06）

> 范围：Tauri v2 功能壳（`desktop/src-tauri/src/`：main/tray/window/notification/
> drag_drop/updater/config + port_manager/python_process/health_check）与前端桥接
> `desktop/src/desktop-bridge.js`。聚焦评审（静态 + 配置面），非全量渗透；Rust 侧
> 已有 35 项单测（更新换载/回滚/重型目录保留）作为回归背书。

## 1. 已确认的正面机制（✓）

| 面 | 机制 | 证据 |
|---|---|---|
| 更新链完整性 | minisign 公钥钉版 + HTTPS GitHub Releases 端点；换载流程含 SHA256 校验阶段 | `tauri.conf.json:36-39`；`updater.rs:768-778` |
| IPC 面 | capabilities 精确列举自定义命令（无 shell/fs/http 等高危插件）；remote 源白名单仅 `127.0.0.1/localhost` | `capabilities/main.json` |
| 拖拽读取 | 拖入路径先登记白名单，`read_dragged_file` 仅放行已登记路径（有单测覆盖跨白名单拒绝） | `drag_drop.rs:24-53` |
| 后端拉起 | Python 子进程显式绑定 `127.0.0.1`（宿主回环档，等价无鉴权威胁模型的既定基线） | `python_process.rs:77`；`port_manager.rs:7` |
| 桥接脚本 | initialization_script 注入、无 CDN 依赖、浏览器模式静默降级、幂等防双执行 | `desktop-bridge.js:5-34` |
| 安装面 | NSIS `installMode: currentUser`（非提权安装） | `tauri.conf.json:29-31` |

**与本轮 P0 变更的兼容性**：R1b 的容器 fail-closed 鉴权仅对容器环境
（`SEEDVR2_DEPLOYMENT=container` / 容器运行时标记）生效；桌面壳原生拉起后端
不触发该门禁，桌面用户流程不受影响。

## 2. 发现与建议（按优先级）

| # | 级别 | 发现 | 建议 |
|---|---|---|---|
| D-1 | Low | `tauri.conf.json` 的 `app.security.csp: null`——Tauri 层 CSP 关闭。当前加载内容为本地后端页面（其 HTTP 响应头 CSP 仍然生效），但壳内注入的 `initialization_script` 与任何未来 webview 内内容不受 Tauri CSP 约束 | 设置最小 Tauri CSP（`default-src 'self' http://127.0.0.1:*; connect-src ipc: http://ipc.localhost` 形态），与后端 CSP 收紧路线（docs/CSP_TIGHTENING_ROADMAP.md）一并推进 |
| D-2 | Low | `withGlobalTauri: true` 向页面暴露完整 `window.__TAURI__` 全局 API；桥接实际只用 `__TAURI_INTERNALS__` | 确认无其他消费方后关闭，缩小页面可达的 API 面 |
| D-3 | Info | 更新端点固定 `releases/latest/download/shell-update.json`——latest 漂移意味着回滚版本不可指向（换载/回滚逻辑已测试，但 latest 语义绕过了版本钉版纪律） | 改用固定 channel 文件（如 `shell-update-stable.json`）由发布流程原子更新 |
| D-4 | Info | 拖拽白名单按文件粒度登记，未见单文件大小上限（`read_dragged_file` 全量读取） | 对齐上传限制（image 50MB/video 500MB）设置读取上限 |

## 3. 结论

壳层安全姿态良好：更新链（minisign + SHA256 + 测试覆盖）、IPC 白名单、
拖拽白名单、回环绑定均已到位；无 Critical/High 发现。D-1/D-2 为低成本
加固项，建议随下个桌面壳迭代处理；完整渗透评估（含 NSIS 安装器、
自动更新中间人场景实测）不在本轮范围，列为后续工作。
