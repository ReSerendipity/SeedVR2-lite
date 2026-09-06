# 安全整改执行对照表（终版）——对应 SECURITY_AUDIT_SeedVR2-lite_2026-09-06.md

> 执行模式：全自主 ｜ 执行日期：2026-09-06 ｜ 状态：**全部任务到达终态**
> 回滚点：`remediation-baseline-20260906` → `remediation-p0-20260906` → `remediation-p1-20260906` → `remediation-p2-20260906`（tag 链）
> 提交：`c8e73cd` `0e1e0be` `2d237cb`（P0）；`632a568` `ca2cc28`（P1）；`24fa248` `3067484` `b962346` `ad022a0`（P2）

## 0. 项目画像（阶段 0）

- 技术栈：Python 3.12（bundled WinPython `.venv`）+ FastAPI + Pydantic v2 + SQLite(aiosqlite)；桌面壳 Tauri v2（Rust）；前端原生 JS + Jinja2。
- 验证命令集（实测可用）：pytest / ruff / black / mypy / check_config_refs / check_spec_refs；桌面壳本轮未触碰源码（cargo 门禁不适用）。
- Git 约定（读历史总结）：约定式提交 + 中文描述 + 模块 scope。**决策 D1**：沿用。
- 远程：origin = github.com/ReSerendipity/SeedVR2-lite，开工时 main == origin/main。

### ⚠ 重大环境发现

会话期间工作区被「恢复未推送成果」系列提交（fc6474b…7f11b21）整体更新：**评估报告撰写时的部分发现已被先行治理轨道修复**（数据治理/成本治理/DX 评估等并行轨道）。因此每项任务开工前先对当前工作区重新核实（下表「开工核实」列），已覆盖项只验证不重做。

## 1. 任务终态

| # | 报告建议 | 开工核实 | 终态 | 验收证据 | 决策与理由 |
|---|---|---|---|---|---|
| T1 | R1a: browse-dir/open-explorer 白名单收口 | ✅ 开放（`ALLOWED_ROOT_DIRS=[]`） | **已完成** `c8e73cd` | test_settings_routes.py 20 passed（新增 5 用例：白名单外 403、根视图列白名单、parent 收敛、open-explorer 文件 400/白名单外 403）；全量回归零失败 | **D2**：白名单与 scan/download 同源（`runtime.security.allowed_base_dirs`）保持单一事实来源；空 path 改列白名单根不枚举盘符；前端 picker 对 `directory` 类型兼容（app.js:3467 仅图标分支）、e2e specs 全 mock 不受影响 |
| T2 | R1b: 容器/暴露自动鉴权 fail-closed | ✅ 开放 | **已完成** `0e1e0be` | test_basic_auth.py 51 passed（新增 10：fail-closed 矩阵/env 快捷通道/容器探测三分支） | **D3**：容器检测以镜像 ENV 为主（k8s+containerd 无 /.dockerenv）；**D4**：`SEEDVR2_ALLOW_UNAUTHENTICATED=1` 显式豁免保留回环映射可用性，fail-closed 为默认（compose 内已豁免并注释边界）；**D11**：`resolve_auth_settings` env-only 快捷通道（用户名+密码双 env 即启用），k8s 示例空密码占位 → 启动失败属有意设计 |
| T3 | R5: SECURITY.md 修正 | ◐ 版本表已由先行工作修为 1.5.x | **已完成** `2d237cb`+`632a568` | 白名单描述对齐 4 目录实际值、is_relative_to 语义、容器鉴权三种启用方式、溯源段如实标注视频水印限制 | 文档与代码同批同步防漂移 |
| T4 | R2: 水印 fail-closed + verify CLI + 鲁棒性验证 | ✅ 开放（`except:pass` 在位） | **已完成** `632a568` | test_watermark_policy 12 passed + test_verify_watermark_cli 3 passed；check_config_refs PASS（watermark_on_failure 声明即消费）；诊断脚本实测 CRF23/CRF14 均 **0/16 帧存活** | **D5 修订**：策略默认 `mark_metadata`（重试 1 次→error 日志+审计+侧车），`block`/`ignore` 可配；**转码实验结果 0/16 → 按 D5 预案不把鲁棒性写进断言**，固化为诊断脚本 `scripts/experiment_watermark_transcode.py`，算法增强列后续建议（超出报告范围）；verify CLI 已由先行工作创建（图像），本轮增量补视频采样验证 |
| T5 | R3: 删除联动+retention 扩展 | ☑ 已由先行工作覆盖 | **已完成（验证）** | test_history_delete_artifacts + test_pinned_retention + test_output_retention_watermark 共 29 passed | **D6**：uploads 走 TTL 治理（`uploads_max_age_days=7`）是先行工作的显式设计决策（避免批量共享输入被提前删除），采纳不重做；checkpoints 孤儿周期清扫不存在（记录删除即回收自洽），列后续建议 |
| T6 | R4: CI 门禁 | ◐ 镜像 Trivy 已存在（report-only 注释含升级路径）；semgrep 已更名 report-only 并记录 10 条存量 findings；容器装未锁 requirements.txt | **已完成（容器锁）+ 决策（门禁维持现状）** `ca2cc28` | requirements-container-lock.txt：111 包精确钉版（uv.lock 导出），Dockerfile 改装锁文件；仅 torch 行无哈希（cu132 索引不提供） | **D7**：semgrep 维持软门禁——10 条存量 ERROR findings 需 CI 环境分诊，本地无法复现扫描，盲加 `--error` 必红 main 违反 CI 铁律；仓库内已写明升级路径；**降级记录**：`--require-hashes` 全量强制因 cu132 索引无哈希元数据不可行，精确钉版已达成，哈希强制列后续（改 PyPI CUDA 轮或带哈希镜像源后开启） |
| T7 | R6: validate_path is_relative_to | ✅ 开放 | **已完成**（随 `c8e73cd`） | test_validate_path_sibling_prefix_forbidden + 路由层同型用例双层回归（旧实现放行/新实现 403） | 与 T1 同函数合并实施 |
| T8 | R7: Windows 路径向量测试 | ✅ 开放 | **已完成** `24fa248` | test_path_guard_windows.py 19 passed（真实 win32）；保留设备名/UNC/设备命名空间/盘符/尾部点空格/大小写折叠全覆盖；核心属性断言「放行 ⇒ 必在白名单子树内」 | **D8**：skipif 非 win32，CI windows-latest 矩阵真实执行 |
| T9 | R9: 限流扩面 + integrity_enforce 决策 | ✅ 开放 | **已完成** `3067484` | test_rate_limit.py 22 passed（新增 5：GET 独立池/互不挤占/路径匹配/向后兼容） | **D9**：GET 重资源端点独立限额（4×上传限额封顶 240/min，默认 0 向后兼容），不新增配置键；integrity_enforce **决策保持 false**（portable/用户改造安装不应被哈希失配锁死），理由注释于 config.yaml |
| T10 | R8: CSP 收紧路线 + 字体披露 | ✅ 开放 | **已完成** `b962346` | docs/CSP_TIGHTENING_ROADMAP.md（四步收紧+report-only 发布策略+meta/头原子同改约束）；PRIVACY_POLICY 补 Google Fonts 披露 | **D10**：报告原文即「路线」，前端事件委托重构超范围不实施；字体自托管需下载外部资产，纳入路线 S3 而非本轮 |
| T11 | R10: desktop 壳聚焦评估 | ✅ 无 desktop 安全文档 | **已完成** `b962346` | docs/SECURITY_REVIEW_DESKTOP_SHELL.md：更新链/IPC 白名单/拖拽白名单/回环绑定确认良好（无 Critical/High）；4 项建议（Tauri CSP null、withGlobalTauri、latest 端点漂移、拖拽大小上限）；确认容器 fail-closed 不影响桌面链路 | 聚焦评审定位（静态+配置面），完整渗透评估列后续 |

## 2. 验证结果汇总（阶段 2）

| 门禁 | 基线 | 改动后 | 判定 |
|---|---|---|---|
| pytest 全量 | 1379 passed, 1 skipped | **1436 passed, 1 skipped**（+57 用例，新增用例 100% 通过，零回归） | ✅ |
| ruff check . | — | All checks passed（修复过程中出现并消除 2 项：F841 未用变量、SIM115 文件打开） | ✅ |
| black --check . | — | 334 files unchanged（6 个本人改动文件已格式化） | ✅ |
| mypy app/integrated_app | — | Success（112 文件，0 错误） | ✅ |
| check_config_refs | PASS | PASS（7 键声明即消费，新增 watermark_on_failure 已接线） | ✅ |
| check_spec_refs | — | exit 0（phantom=0 dead_links=0） | ✅ |

## 3. 受阻与需人工决策

- **无受阻任务**。两项降级决策（非受阻）：semgrep 硬门禁（D7，本地无法复现扫描）、容器锁哈希强制（cu132 索引无哈希元数据）——均已给出后续路径。

## 4. 报告外发现（只记录，未处理）

1. 工作区在会话期间被「恢复未推送成果」系列提交整体更新，评估报告部分发现在开工前已被先行治理轨道修复（详见 §1 各行「开工核实」）。
2. **uv.lock 项目版本元数据滞后**：lock 内 seedvr2-lite 版本停在 1.5.0（pyproject 已 1.5.1），uv export 重解析时暴露；已按 `ad022a0` 元数据对齐（无依赖变化）。
3. **提交事故一次（已纠正）**：P1 首个提交曾把 index 中先行工作预暂存的 5 个报告重命名（迁移 docs/reports/）一并卷入；发现后立即 soft reset 并以 pathspec 限定重提（`632a568`），重命名保持原暂存状态未受内容影响。
4. **水印转码存活率 0/16**（CRF23/14）虽属报告 R2 范围，但「隐式水印对视频有损编码不可靠」的算法层增强超出本报告整改范围，仅诊断脚本留痕 + SECURITY.md 如实标注。
5. `.gitignore` 存在会话开始前的未提交改动（非本任务产生），未触碰。

## 5. 后续建议（下一轮）

1. 水印算法增强：抗有损编码嵌入（亮度分量/强度自适应/编码后验证重嵌），以 `experiment_watermark_transcode.py` 为量化基准。
2. semgrep 硬门禁：在 CI 环境分诊 10 条存量 ERROR findings 后按仓库内既定路径开启 `--error`。
3. 容器依赖哈希强制：torch 改用 PyPI CUDA 轮或带哈希镜像源后开启 `--require-hashes`。
4. checkpoints 孤儿周期清扫（当前仅记录删除时回收）。
5. 桌面壳 D-1~D-4（Tauri CSP、withGlobalTauri、更新端点钉 channel、拖拽大小上限）。
6. CSP 路线 S1-S4 按 report-only 策略推进；`verify_watermark.py` 视频路径纳入自动化测试（需 CI ffmpeg 矩阵）。
7. 配置文档：CONFIG.md 不含 runtime.security 键级明细，新增键的说明现由 config_models.py 字段描述 + config.yaml 注释承载，如需集中文档可评估生成式方案（generate_config_reference.py）。

## 6. 决策汇总（全部留痕）

| ID | 决策 | 理由 |
|---|---|---|
| D1 | 提交信息沿用约定式提交+中文描述 | git log 实证风格统一 |
| D2 | browse-dir/open-explorer 白名单与 scan/download 同源；根视图列白名单 | 单一事实来源；picker UX 在安全模型内可用 |
| D3 | 容器检测以 ENV 为主、运行时标记兜底 | k8s+containerd 不创建 /.dockerenv |
| D4 | 保留 SEEDVR2_ALLOW_UNAUTHENTICATED 显式豁免 | loopback 映射的本地容器可用性；fail-closed 为默认 |
| D5（修订） | 水印失败策略 mark_metadata 默认；鲁棒性不写断言，0/16 实测固化为诊断脚本 | 兼顾合规兜底与长任务不丢产物；诚实铁律——不伪造鲁棒性达标 |
| D6 | 采纳先行工作 uploads TTL 治理设计 | 架构一致性 > 改动面最小 |
| D7 | semgrep 维持软门禁；容器锁降级为精确钉版（哈希强制列后续） | 本地无法复现扫描；cu132 索引无哈希元数据 |
| D8 | Windows 向量测试 skipif 非 win32 | CI 双平台矩阵，windows job 真实执行 |
| D9 | GET 限流独立计数池 4×限额；integrity_enforce 保持 false | 不挤占上传配额；portable 可用性 |
| D10 | CSP 只出路线文档+字体披露 | 报告原文即「路线」；前端重构超范围 |
| D11 | resolve_auth_settings env-only 快捷通道 | 容器部署免维护 config.yaml；fail-closed 默认不变 |
| D12 | 报告重命名卷入事故以 pathspec 限定重提纠正 | 铁律 1 范围控制 + 恢复原暂存状态 |
