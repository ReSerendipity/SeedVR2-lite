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

## 2. 发现与建议（按优先级）——后续建议 R3 轮处置记录（2026-09-06）

| # | 级别 | 发现 | 处置 | 状态 |
|---|---|---|---|---|
| D-1 | Low | `tauri.conf.json` 的 `app.security.csp: null`——Tauri 层 CSP 关闭 | **核实后关闭**：主窗口创建后即 `navigate()` 到后端 `http://127.0.0.1:{port}`（window.rs:42），实际内容由后端 HTTP 头 CSP（per-request nonce）保护；tauri csp 仅作用于 tauri:// 内部资产（启动占位页，瞬时存在）。改动收益为零且有破坏占位页风险 | 已关闭（不适用） |
| D-2 | Low | `withGlobalTauri: true` 暴露 `window.__TAURI__` 全局 API | **核实后保留**：占位页 `index.html:58,62-63` 实际消费 `window.__TAURI__`（重试按钮 invoke + startup-status 事件监听）；远程后端页面本就经 initialization_script 获得 `__TAURI_INTERNALS__`，全局不新增暴露面 | 已关闭（必需项） |
| D-3 | Info | 更新端点 `releases/latest/download/shell-update.json` 的 latest 漂移 | **核实后关闭（评审自我修正）**：latest 语义与文件名无关，改名 `-stable` 不解决问题——Tauri updater 的标准形态即 latest 指针，回滚控制面在服务端（重发布/删除 Release 资产即可收回）；`pubkey` 已钉版，未签名产物不可激活。发布检查清单已注明回滚路径 | 已关闭（设计即如此） |
| D-4 | Info | 拖拽读取无大小上限 | **部分误报修正 + 实际加固**：200MB 上限早已存在（原 drag_drop.rs:61），但检查位于 `fs::read` 之后（保护响应体而非进程内存）。本轮抽出 `validate_dragged_file`（metadata 先查、超限拒绝发生在读取之前）+ 新增 Rust 单测（目录/正常/不存在三态）。>200MB 正向分支无法在单测构造（不造 200MB 临时文件），以常量与逻辑审查背书 | 已完成（`desktop/src-tauri/src/drag_drop.rs`） |

## 3. 结论

壳层安全姿态良好：更新链（minisign + SHA256 + 测试覆盖）、IPC 白名单、
拖拽白名单、回环绑定均已到位；无 Critical/High 发现。D-1/D-2 为低成本
加固项，建议随下个桌面壳迭代处理；完整渗透评估（含 NSIS 安装器、
自动更新中间人场景实测）不在本轮范围，列为后续工作。
