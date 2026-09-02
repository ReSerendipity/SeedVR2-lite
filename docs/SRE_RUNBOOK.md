# SRE 运行手册（SLA / SLO / 回滚 / 灾难恢复演练）

> **来源**：家族泛化自 TTS_MultiModel `docs/SRE_RUNBOOK.md` + 本仓既有云原生部署，本仓本地化。
> 配套：`docs/rollback_sop.md`、`docs/release-governance.md`。
> 适用版本：v1.5.0（与 `pyproject.toml` / `version.py` 一致）。

---

## 1. SLA / SLO

### 1.1 服务等级目标（SLO）

| 指标 | 目标 | 度量方式 | 数据源 |
|------|------|----------|--------|
| 可用性（月度） | ≥ 99.5% | liveness `/api/system/ping` | k8s `HEALTHCHECK` / `routes/system/health.py` |
| readiness（模型就绪） | ≥ 99.0% | `/api/system/ready`（模型预热中 503+Retry-After） | `routes/system/readiness.py` |
| P95 生成时延 | 设备相关 | 推理计时 | `metrics.py` |
| 生成成功率 | ≥ 99.0% | 成功/失败计数 | `/metrics` Prometheus |
| 完整性自检 | 100% | `integrity_check` 结果 | `security/integrity_check.py` |

### 1.2 错误预算（Error Budget）

- 月度可用性 99.5% ⇒ 允许不可用 ≈ **3.6 小时/月**。
- 超出时：冻结非紧急发布、优先稳定项、复盘。

### 1.3 探针（Probe）约定

- **liveness**：`/api/system/ping`（内存级，不碰 DB/GPU）。
- **readiness**：`/api/system/ready`（模型加载 + DB + GPU 深度检查）。

---

## 2. 回滚（Rollback）

> 详情见 `docs/rollback_sop.md`。

- **代码回滚**：`git revert <bad_commit>`，人工 push 后重启。
- **配置回滚**：`git revert` 对应 `config.yaml` 提交；容器只读挂载配置 + env 注入密钥。
- **权重回滚**：`model/` 禁区，SHA-256 复验（`integrity_manifest.json` / `integrity_check.py`）；永不在运行时改权重。

---

## 3. 灾难恢复演练（DR Drill）

| 场景 | RTO | RPO | 恢复手段 |
|------|-----|-----|----------|
| 单 Pod 崩溃 | < 1 min | 0 | k8s 自愈（liveness 重启） |
| 节点宕机 | < 5 min | 0 | 调度到其他节点（PDB） |
| 配置误改 | < 10 min | 0 | Git 回滚配置 |
| 权重损坏/篡改 | < 30 min | 按留存 | 从备份恢复 + 完整性复验 |
| history_db 损坏 | < 15 min | ≤ 留存窗口 | 从备份恢复 SQLite |
| 密钥泄露 | < 5 min | — | 轮转并重启 |

> 演练步骤（每季度一次）：预发副本部署 → 注入故障（kill Pod / 删 config / 损坏权重）→ 观测探针翻转 → §2 恢复 → 复盘记录 RTO/RPO。

**数据备份**：`history_db`（SQLite）定期备份 + 一致性快照 tips（无定制脚本时 `sqlite3 .backup`）；权重离线冷备 + `integrity_manifest` 复验；密钥走 `SEEDVR2_SECRET_KEY` / Secret 管理。

---

## 4. 事故复盘（Postmortem）

模板：时间线 → 影响面 → 根因（5 Whys）→ 改进项（owner+deadline）→ 是否触及错误预算。

---

## 5. 运维入口速查

| 用途 | 命令 / 端点 |
|------|-------------|
| 存活探针 | `GET /api/system/ping` |
| 就绪探针 | `GET /api/system/ready` |
| Prometheus 指标 | `GET /metrics` |
| 聚合诊断 | `python scripts/doctor.py`（八项诊断，`--json`） |
| 完整性缺签 | `python scripts/sign_integrity_manifest.py` |