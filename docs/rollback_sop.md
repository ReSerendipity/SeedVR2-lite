# 回滚 SOP（自动化回滚 / 版本化回滚）

> **来源**：家族泛化自 TTS_MultiModel `docs/rollback_sop.md`，本仓本地化。
> 配套：`docs/SRE_RUNBOOK.md`、`docs/release-governance.md`。
> 原则：**先止血（快速恢复服务），再定位（事后复盘）**。MTTR 目标 < 30 分钟。

## 0. 前置约定

- 发布由 `portable-release.yml`（tag 触发）产出分卷便携包；tag 即回滚点。
- 回滚 = 代码/配置/权重回退到上一个稳定 tag。

## 1. 决策：何时回滚

满足任一即触发：

1. 成功率跌破 SLO 阈值；
2. readiness `/api/system/ready` 持续 degraded（模型无法加载 / 显存熔断）；
3. P0/P1 安全告警（完整性校验失败且 `block_startup_on_failure=true`）；
4. 关键 API 契约断裂流入生产。

## 2. 回滚步骤

```bash
# 查看最近 tag
git tag --sort=-creatordate | head -5

# 在当前分支生成一个「反向提交」回退到目标 tag 的发布提交
git revert <bad_release_commit>
# 默认只生成提交，不自动 push；需人工 git push 后重启/重建便携包
```

## 3. 容器化 / 便携包回滚

- **k8s**：`kubectl set image deployment/seedvr2 *.*=<image>:v<tag>` 或 `kubectl rollout undo`。
- **便携包**：旧版 Release 资产不可变（`--clobber` 已去除），用户下载上一版本 + SHA256 校验 + GPG 验签。

## 4. 回滚后验证（闭环）

1. liveness `GET /api/system/ping` → ok；
2. readiness `GET /api/system/ready` → ready；
3. 最小 smoke（Playwright / 便携包自测 `test_portable_bundle.ps1`）；
4. 观察成功率回升、无新告警（`GET /metrics`）。

## 5. 数据库 / 历史库回滚说明

- 历史库（SQLite）**不随代码回滚**；旧版本继续读写（有迁移兼容逻辑时应保留）。
- 回滚前备份：`cp <history.db> <history.db>.bak-$(date +%s)`。

## 6. 权重回滚

- `model/` 为权重禁区，禁止运行时修改。
- 权重回滚需人工确认 + `integrity_manifest.json` / `integrity_check.py` 的 SHA-256 复验。

## 7. 事后（Postmortem）

回滚完成后 24 小时内产出简短复盘（见 `SRE_RUNBOOK.md` §4）：根因、MTTR、止血项、永久修复项、负责人。