# 完成定义（Definition of Done）

> **来源**：家族通用 DOD 模板 `.spec_audit/family_DOD.md`（源自 SpiritPal definition-of-done.md 泛化），本仓本地化。
> **适用范围**：SeedVR2-lite 全项目所有功能开发任务。

---

## 0. DoD 等级

| 等级 | 适用场景 | 要求 |
|------|---------|------|
| **Full DoD** | 正式功能开发 / 大型 PR | 全部检查类 |
| **Lite DoD** | Bug 修复 / 小优化 | 代码完成 + 测试覆盖 + 构建验证 |
| **Hotfix DoD** | 紧急线上修复 | 代码完成 + 构建验证（事后补齐其余） |

> 判定「完成」必须先跑对应等级清单；不满足不得标注完成、不得提交 main。

## 1. 代码完成

- [ ] 功能已实现，覆盖 PRD / Spec 定义的所有验收标准（AC）
- [ ] lint 通过：`ruff` / `ruff-format`（Python）+ `eslint` / `tsc`（前端 TS）0 error
- [ ] 无调试残留（`print()` / `console.log` / `breakpoint()`，诊断日志除外需标记）
- [ ] 未引入跨层违规引用（遵守 AGENTS.md §3 模块边界 + 禁区表）
- [ ] 新增路由遵守路由自动发现 / engines / middleware 分层约定
- [ ] 涉及引擎改动时遵守 AGENTS.md SOP（新增引擎 / 调试 / 发布 / 便携包 / 量化精度）

## 2. 测试覆盖

- [ ] 新增/修改函数有对应 pytest 单测（`tests/`）+ 前端 TS spec（Playwright）
- [ ] 覆盖正常路径 + 边界条件 + 异常场景
- [ ] 全量 `pytest` 通过（不新增失败用例）；前端 `npx playwright test` 通过
- [ ] GPU 依赖模块（engine/ optimization/ vram/）在无 GPU 环境有 mock 覆盖，避免 CI 全 skip
- [ ] 涉及金标准质量时确认 `tests/golden/` 基线未退化

## 3. 文档同步

- [ ] `AGENTS.md` 已同步（目录结构 / MODULE_MAP / 配置 / 引擎契约）
- [ ] 新增模块在 AGENTS.md / `docs/project/MODULE_MAP.md` 有对应条目
- [ ] 踩坑已追加到 `docs/project/KNOWN_ISSUES.md`（触发/现象/做法/日期）
- [ ] `CHANGELOG.md` 已记录变更（type 对应 Added/Fixed/…）
- [ ] `python scripts/check_spec_refs.py` 退出码 0（无幻影/死链/假门禁）

## 4. 国际化

- [ ] 新增 UI 文案已在 5 语言 JSON 同步（`app/integrated_app/locales/` 下 zh / zh-TW / en / ja / fr）
- [ ] 前端 spec 覆盖 i18n 切换（如涉及）

## 5. 构建 & 验证

- [ ] 完整构建/启动通过（`app/clean_launch.py`；`checkpoint.py` / `spec.py` 契约不破坏）
- [ ] 手动验证功能按预期工作（不仅是测试通过）
- [ ] 涉及便携包/发布时遵守 AGENTS.md SOP-3 / SOP-7（PORTABLE_BUNDLES.md）

## 6. 安全 & 隐私

- [ ] 无硬编码密钥 / API Key / 敏感常量（走 `.env.example`）
- [ ] 新增路由遵守安全中间件（basic_auth / csrf / rate_limit / path_guard / magic_check / magic / watermark）
- [ ] 未新增静默吞错（`except: pass` 等）
- [ ] 涉及禁区目录（model/ 权重、integrity 签名、安全模块）走人工确认流程

## 7. 可追溯性

- [ ] 变更有影响评估（破坏性 / 非破坏性）
- [ ] 涉及配置变更时：`config.yaml` 结构同步 config_models + `check_config_refs.py`
- [ ] 涉及版本：`version.py` / `pyproject.toml` 一致
- [ ] 涉及 CI 变更时与 `.github/workflows/*.yml` 实际文件一致（证据绑定）