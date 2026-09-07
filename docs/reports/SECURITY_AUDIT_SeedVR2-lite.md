# 安全审计 — SeedVR2-lite

> 只读审计 · 快照版 · 审计日期：2026-09-02
> 审计对象：FastAPI + 图像/视频修复引擎（SeedVR2 系，支持 LCM distill / RIFE / RAFT 流水线）
> 方法：静态扫描 + git 追踪面核查 + 配置校验器审查。未做动态渗透。

## 执行摘要（总体评级：中 / Medium）

无凭据入库、依赖锁定齐全、host 强制回环校验器为已有机制。安全机制完备（integrity_manifest + 签名、weight_encryption、magic_check、path_guard、basic_auth/CSP nonce）。主要关注点集中在**模型权重下载链路**与**分布式/多节点场景的可选暴露面**。已确认项见下。

## 已验证项（✓ = 本次核查通过）

### 1. 凭据 / 密钥
- **✓ 无密钥入库**：`git ls-files` 扫描 `.env`、`*.jks`、`*.keystore`、`*.pem`、`*.key`、`secrets/`、`credential` 均无命中；仅存在 `scripts/init_watermark_key.py`（初始化脚本，密钥本体不入库）。
- **✓ 密钥生成强度**：`scripts/encrypt_weights.py` + `security/secret_key.py` 配合 `.watermark_key`/签名密钥，无硬编码默认值。
- **✓ 清单签名**：`security/integrity_manifest.json` 附带 `.sig` 签名文件（`sign_integrity_manifest.py`），自检脚本校验。

### 2. 网络暴露 / 绑定
- **✓ loopback 强制**：`app/integrated_app/config_models.py:70` 内置 host 校验器，仅允许回环地址，禁止 `0.0.0.0` 公网暴露。

### 3. 依赖供应链
- **✓ 锁文件齐全**：`requirements-lock.txt`（pip-compile），另有 `uv.lock` 双锁。

### 4. 组件/合规
- **✓ 声明完整**：根级 `NOTICE` + 模型权重许可（SeedVR2 系模型开源条件见上游）；`USER_AGREEMENT.md` 已置根。

## 待关注点位（非阻断）

| # | 级别 | 点位 | 建议 |
|---|---|---|---|
| 1 | Medium | 模型权重下载（`scripts/download_model.py`）经第三方 CDN/HF | 安装前核验 SHA256（`test_download_verify.py` 已覆盖），配合完整性签名兜底 |
| 2 | Low | Flash Attention 需手动编译安装（CUDA 环境），包来源非 pip 单一渠道 | 用 `perf/benchmark/install_flash_attn.bat` 固定版本，勿随意换源 |
| 3 | Info | Kubernetes 部署示例（deploy/kubernetes/）暴露 Service | 生产部署启用 Ingress TLS + 认证，勿直接映射端口 |

## 门禁适用性说明

本仓库 `scripts/check_config_refs.py` / `check_spec_refs.py` 已覆盖 config 键消费与规范文件引用一致性；`docs/METRICS_SPEC.md` 与 `SECURITY_REMEDIATION_TRACKER.md` 为治理配套。

---

*快照审计，非正式安全承诺；建议在每次大版本发布前复核。*