# 发布/回滚/SLA 总纲（Release Governance）

> **来源**：家族通用 `.spec_audit/family_release_governance.md`（泛化自 DraftPeek VERSIONING.md + TTS SRE_RUNBOOK/rollback_sop），本仓本地化。
> **适用范围**：SeedVR2-lite 全项目发布、回滚与运行稳定性。

---

## 1. 版本号规范

- 遵循 SemVer `MAJOR.MINOR.PATCH`。MAJOR=不兼容变更、MINOR=向后兼容新功能、PATCH=向后兼容修复。
- 版本权威位：`app/integrated_app/version.py`（pyproject 直读）→ `pyproject.toml`，`AGENTS.md` 顶部「对应项目版本」与 `CHANGELOG.md` 一致。当前 **v1.5.0**。
- 可选预发布：`-alpha.N` / `-beta.N` / `-rc.N`。

## 2. CHANGELOG 管理

- 遵循 Keep a Changelog：`Added / Changed / Deprecated / Removed / Fixed / Security`。
- 每次 PR 合并 `main` 在 `[Unreleased]` 下追加，type 对应。

## 3. 发布流程（便携包为主产物）

1. 确认 `[Unreleased]` 条目完整
2. 同步版本位：`pyproject.toml` + `AGENTS.md` + `CHANGELOG.md`
3. 打 tag `git tag v<X.Y.Z>` → `git push origin v<X.Y.Z>` 触发 `portable-release.yml`（分卷便携包）
4. 便携包产物自动 GPG 签名 + 校验和 + provenance（见 `docs/project/PORTABLE_BUNDLES.md`）
5. CI 盯到终态：push 后 `gh run list` / `gh run watch`，红了当场修或 revert 止损

## 4. 回滚判定（满足任一即触发）

1. 关键成功率指标跌破阈值
2. readiness 持续 degraded（模型加载 / 显存熔断）
3. P0/P1 安全告警（权重完整性校验失败且阻断启动）
4. 关键 API 契约断裂流入生产

## 5. 回滚执行

- **代码回滚**：`git revert <bad_release_commit>`（保历史、可再 forward），人工 push 后重启。
- **配置回滚**：`git revert` 对应 `config.yaml` 提交；容器只读挂载配置 + 环境变量注入密钥。
- **权重回滚**：`model/` 禁区，SHA-256 复验（`integrity_manifest.json` / `weight_encryption`）后再动；永不在运行时改权重。
- **便携包回滚**：旧版 Release 资产不可变（`--clobber` 已去除），用户重新下载上一版本 + 校验和复验。

## 6. 回滚后验证（闭环）

1. liveness `GET /api/system/ping` 返回 ok
2. readiness `GET /api/system/ready` 返回 ready
3. 最小 smoke：`pytest` smoke 用例 / Playwright 冒烟
4. 观察关键指标回升、无新告警（`GET /metrics`）

## 7. SLA / 错误预算

- 目标可用性 ≥99.5%（月度）⇒ 错误预算 ≈ 3.6h/月。
- 超出错误预算：冻结非紧急发布、优先稳定项、复盘。
- liveness = 内存级探针；readiness = 深度探针（模型预热 503+Retry-After）。

## 8. 发布前检查清单

- [ ] 版本位全部同步（`pyproject.toml` / `version.py` + `AGENTS.md` + `CHANGELOG.md`）
- [ ] CHANGELOG `[Unreleased]` 已改版本 + 日期
- [ ] 全量 pytest 通过（门禁实测：质量 gate 双 OS + E2E 无 `--update-snapshots`）
- [ ] `ruff` / `black` / `mypy` 全绿
- [ ] `python scripts/check_spec_refs.py` 退出码 0
- [ ] 便携包自测 `test_portable_bundle.ps1` 通过
- [ ] GPG 签名 + SHA256 校验和已生成并验证
- [ ] tag 已推送触发 `portable-release.yml`