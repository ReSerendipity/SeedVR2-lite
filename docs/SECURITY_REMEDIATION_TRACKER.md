# SeedVR2-lite 安全整改追踪表

> 配套 Image_MultiModel 家族安全深度评估（`SECURITY_ASSESSMENT_v2.0.0.md`，17 项发现）。
> 本文记录 SeedVR2-lite 已落地的安全整改项与剩余项，便于跨仓统一对账。

## 1. 根因门禁（最高优先级，已落地）

**配置幻觉（Phantom Control）**：config.yaml 声明了安全控制，但代码未消费、
不报错、不告警、测试不失败 → 假安全感。SeedVR2-lite 的根因门禁为
`scripts/check_config_refs.py`（SeedVR2 适配版），作为 CI 质量门禁接入
`.github/workflows/ci.yml` 的 `quality-gate` job。

与 Image_MultiModel / TTS_MultiModel 的差异（适配点）：
- SeedVR2 的配置以 **dict** 形式经 `load_config()` 注入 `app.state.config`，
  代码中通过 `config.get("runtime", {}).get("security", {}).get("key", default)` 消费，
  故门禁同时捕获属性链、`.get()` 字符串键与字典下标 `obj["key"]`。
- 安全配置嵌在 **`runtime.security:`**（非顶层 `security:`），门禁遍历根为
  `runtime.security`。

门禁覆盖：
1. `config.yaml` 的 `runtime:` 段每个键须对应 `RuntimeConfig` 字段；
2. `config.yaml` 的 `runtime.security:` 每个键须对应 `RuntimeSecurityConfig` 字段
   （防 `extra="ignore"` 静默吞掉幽灵键）；
3. `runtime.security:` 每个键都须被代码真实消费（声明即消费，否则判失败）。

已通过负向测试证明门禁非 no-op：在临时 config 中加入 `phantom_secret_key` 或
移除某键的消费后，门禁均报 `[FAIL]`；真实仓库下 6 个安全键全部消费 → `[PASS]`。

## 2. 整改状态表

| 编号 | 类别 | 措施 | 状态 | 落地文件 |
|------|------|------|------|----------|
| G-01 | 配置幻觉根因门禁 | `check_config_refs.py`（runtime.security 适配）+ CI 接入 | ✅ 已落地 | `scripts/check_config_refs.py`、`.github/workflows/ci.yml` |
| M-02 | 安全响应头缺失 | `SecurityHeadersMiddleware`：CSP / nosniff / X-Frame-Options / Referrer-Policy / COOP，默认开启，最外层注册 | ✅ 已落地 | `app/integrated_app/middleware/security_headers.py`、`app/integrated_app/app_server.py` |
| C-01 | 0.0.0.0 监听 | `config.yaml` 仅 `127.0.0.1`；`ServerConfig.host` 强制回环校验器（`host_must_be_loopback`）；CI `security-assertions` 禁 0.0.0.0 | ✅ 既有 | `config_models.py`、`ci.yml` |
| H 系 | 鉴权/速率/CSRF | `basic_auth.py` / `rate_limit.py` / `csrf.py` 中间件齐备 | ✅ 既有 | `app/integrated_app/middleware/` |
| M 系 | 路径白名单/完整性 | `path_guard.py`、`integrity_selfcheck.py`、`integrity_manifest.json(.sig)`、`weight_encryption.py`、`watermark.py` 齐备 | ✅ 既有 | `app/integrated_app/security/` |

> 注：SeedVR2-lite 安全基线较成熟（auth/CSRF/rate-limit/path-guard/完整性校验/
> 权重加密/水印均已具备），本仓重点补强的是「配置-实现一致性根因门禁」与「安全
> 响应头中间件」两项此前缺口。

## 3. 日常纪律（与家族一致）

1. **新增 config.yaml 安全键**：必须同步在 `RuntimeSecurityConfig` 声明，并在代码
   中真实读取该字段；否则 `check_config_refs.py` 在 CI 直接判 `[FAIL]`。
2. **删除安全键**：同步删除配置模型字段与代码读取点，避免悬空引用。
3. **禁止绕过门禁**：CI 中该步骤为真实门禁，不得加 `|| true`。
4. **改核心安全模块后重算完整性清单**：`python scripts/generate_integrity_manifest.py`。

## 4. 跨仓对账

| 仓库 | 根因门禁 | 安全头中间件 | 提交状态 |
|------|----------|--------------|----------|
| Image_MultiModel | ✅ | ✅ | 已提交 |
| TTS_MultiModel | ✅ | ✅ | 已提交（本地） |
| SeedVR2-lite | ✅ | ✅ | 本次落地 |
| MiniMax-H3-lite | 待适配（@dataclass 无 config.yaml） | 审计中 | 只读审计 |
| SpiritPal | 不适用（Rust） | 审计中 | 只读审计 |
| DraftPeek | 不适用（Kotlin） | 审计中 | 只读审计 |
