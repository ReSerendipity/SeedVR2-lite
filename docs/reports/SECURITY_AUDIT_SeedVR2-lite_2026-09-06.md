# 安全合规评估 — SeedVR2-lite（v1.5.1）

> 评估日期：2026-09-06 ｜ 只读静态评估，未做动态渗透
> 评估对象：`C:/Users/Doro/SeedVR2-lite`（FastAPI 自托管 Web 服务，处理用户私有视频/图像）
> 基线：`SECURITY_AUDIT_SeedVR2-lite.md`（2026-09-02，评级 **Medium**）——本次为**增量评估**，不重复基线已验证项
> 威胁模型分两档：**A. 回环自托管**（默认 `127.0.0.1:7870`，单用户）／**B. 容器暴露**（Docker/k8s，服务绑定 `0.0.0.0`，暴露面取决于端口映射与集群策略）
> 硬约束遵守：未读取/输出 `.watermark_key` 内容（仅核验存在性 66B、gitignore:210、未入库）；先读基线审计再评增量。

## 总体评级：Medium（与基线持平）

结构性机制在改善（留存清理落地、加载前 SHA256、k8s 加固、CSRF/CSP nonce 有测试背书），但**暴露部署档**下存在一个组合高危面（无鉴权 + 目录枚举/打开端点无白名单），且水印合规兜底是 fail-open、SAST 门禁名实不符。

---

## 1. 事实核对表（含对评估提示词的纠正）

| # | 锚点项 | 提示词/基线说法 | 本次核实结果 | 证据 |
|---|---|---|---|---|
| 1 | SECURITY.md 位置 | 根目录 `SECURITY.md` | **已迁移至 `.github/SECURITY.md`**；「支持 1.0.x」过时表述**仍在**（实际版本 1.5.1，提示词写的 1.5.0 也已过时） | `.github/SECURITY.md:5-10`；`pyproject.toml:3` |
| 2 | 绑定 | Pydantic 禁 `0.0.0.0`；Docker 容器内 `0.0.0.0` | ✓ 属实。校验器白名单仅 `{127.0.0.1, localhost, ::1}` | `config_models.py:68-74`；`Dockerfile:81` |
| 3 | k8s 暴露 | 基线 Info：Service 暴露需 Ingress TLS+认证；提示词判据「NodePort/LB 即 P1」 | **已是 ClusterIP**（非 NodePort/LB），且 Deployment 有 `runAsNonRoot` + seccomp + `drop ALL` caps + 非 root 容器 + 只读权重挂载。P1 判据**不触发**。遗留：无 Ingress/NetworkPolicy/auth 示例 | `deploy/kubernetes/service.yaml:8-9`；`deployment.yaml:31-34,61-64` |
| 4 | PathGuard 白名单 | 「4 个目录：outputs/uploads/checkpoints/model」 | ✓ `config.yaml` 确实配 4 目录，且 scan/download/history 三类端点消费 `runtime.security.allowed_base_dirs`。**但 SECURITY.md 声称「仅 outputs/ 和 data/uploads/」——文档少写了两个**；且 `browse-dir`/`open-explorer`/`resolve` 路径端点**不经 PathGuard**（见 R1） | `config.yaml:187-191`；`restore/scan.py:95-97`；`restore/task.py:335-336`；`system/history.py:208-209`；`.github/SECURITY.md:63` |
| 5 | realpath 后是否复检 | 提示词担心「解析完不校验等于没解析」 | ✓ **已复检**：`resolve()` 后用 `resolved == base or base in resolved.parents` 做白名单匹配，另有 NUL/控制字符拒绝与过宽白名单告警 | `path_guard.py:146-150,114-117,58-78` |
| 6 | 密钥 | `.watermark_key` 66B 不入库 | ✓ 属实（66 字节，`gitignore:210`，`git ls-files` 无命中）。`encrypt_weights.py` 为**真实 AES-GCM 权重加密 + 机器绑定许可证**，运行时 `decrypt_to_memory` 不落明文临时文件 | 本表硬约束；`scripts/encrypt_weights.py:7-21` |
| 7 | 加载前 SHA256 | SECURITY.md 声明 | ✓ 属实：引擎加载前校验权重哈希（CWE-353 防御注释） | `seedvr2_engine.py:311`；`config.yaml` 全部 `sha256_*` 齐全（fp16/fp8/int8_convrot/mxfp8/nvfp4/vae/pos_emb/neg_emb × 3 尺寸） |
| 8 | `.pt` weights_only | 「优先 weights_only=True」 | ✓ 全仓 `torch.load` 仅 3 处调用点：engine 两处显式 `weights_only=True`；其余走 `_safe_torch_load` 包装器（默认 `weights_only=True`，pickle 回退需显式 `allow_pickle_fallback=True` 并打 CRITICAL 日志）。未发现裸 pickle 加载点 | `seedvr2_engine.py:347-352`；`framework_engineering.py:51-101` |
| 9 | 留存清理 | 「outputs_max_age_days=14 是否真删文件」 | ✓ **真删文件**：`os.remove` + 残留 `_frames` 目录 `shutil.rmtree`，启动即清一次（重启补清）+ 每 3600s 周期清 + 任务运行时 `is_busy` 跳过。**但仅覆盖 `outputs/`**，`data/uploads/`（24h TTL）与 `data/checkpoints/`（无任何清理策略）各自为政（见 R3） | `output_retention.py:40-148`；`app_server.py:446-468`；`cache.py:49-50,322-345` |
| 10 | disk_min_free_gb 行为 | 三种可能 | ✓ **拒绝新任务**：`ensure_disk_space` 预检在 upload/batch 提交入口执行，低于 5GB 拒绝（不是清最旧也不只是告警） | `restore_service.py:85-100`；`upload.py:272`；`batch.py:165` |
| 11 | 上传防护 | 魔数校验 + 30/min 限流 | ✓ 属实。魔数白名单含 RFF 容器特判；限流只对 **POST + 匹配路径**、按直连 IP（不信任 XFF） | `magic_check.py:46-47,110-151`；`rate_limit.py:23,118-131,190` |
| 12 | Semgrep「`\|\| true`」 | 提示词按旧写法判断 | **机制变了但结论不变**：`\|\| true` 已删，改为依赖 `semgrep scan`（非 `ci`）默认退出码语义——**findings 仍不阻断，仅工具自身故障才失败**；job 名 "(block on ERROR)" 名不副实 | `security.yml:23-31` |
| 13 | 镜像无扫描 | buildx 无 Trivy | ✓ 仍无镜像 CVE 扫描；仅 `provenance: true` + `sbom: true`。另有缺口：**容器装的是未锁 `requirements.txt`（版本区间）而非 lock 文件** | `docker-publish.yml:81-82`；`Dockerfile:13-16,26` |
| 14 | Trivy 密钥扫描时机 | 提示词问「是否 PR 阶段运行」 | ✓ PR + push main + 每周一均运行，`exit-code: 1`、CRITICAL/HIGH 阻断、SHA pin | `security.yml:48-65` |
| 15 | CSRF | 提示词问「签名 token 还是双提交」 | ✓ **两者皆是**：HMAC 签名 token（`nonce.hmac`）+ 双提交 cookie + SameSite=Strict，覆盖 POST/PUT/DELETE/PATCH（locale 豁免）；`tests/test_csrf_signed.py` 背书 | `csrf.py:9-11,102-118` |
| 16 | CSP nonce | 提示词问「nonce 是否每次随机」 | ✓ **已实现**：渲染期 per-request nonce 注入 meta+全部内联脚本，`test_csp_nonce.py` 断言跨请求唯一。**缺口**：`'unsafe-inline'` 作为无 nonce 上下文的永久兜底保留（现代浏览器 nonce 存在时忽略 unsafe-inline，旧内核则退化为放行） | `security_headers.py:28-49`；`base.html:6-10`；`tests/test_csp_nonce.py` |
| 17 | 水印 | 「覆盖全部输出路径？重编码存活？」 | 默认开启（`security.watermark.enable` 缺省 True）、图像单帧 + 视频逐帧、payload HMAC 签名绑定 task_id。**但嵌入失败 `except: pass` 仅 debug 日志（fail-open）**；重编码鲁棒性无任何测试/证据；`scripts/` 无 `verify_watermark.py` CLI（仅模块内 `verify_watermark` 函数） | `_image_pipeline.py:358-368`；`_video_pipeline.py:544-566`；`watermark.py:379` |
| 18 | 旧路径幻觉 | `app/integrity/self_check/`、`model_encryption/` 不存在 | ✓ 确认不存在；真实位置 `app/integrated_app/security/`（path_guard/magic_check/watermark/weight_encryption/integrity_* 等 8 模块 + 签名 manifest）。`integrity_enforce: false`（自检非强制） | `app/integrated_app/security/`；`config.yaml:192` |
| 19 | NOTICE ffmpeg | 「不随仓库分发」 | ✓ 属实：`app/ffmpeg.exe`/`ffprobe.exe`（各 ~220MB）仅本机存在，`gitignore:254-255` 未跟踪。GPL 边界处理正确 | `git check-ignore` 实测 |
| 20 | 下载链路 | HTTPS/镜像回退疑虑 | ✓ HF 走 huggingface_hub（HTTPS）、Comfy-Org 量化走 ModelScope HTTPS 直连、`--endpoint hf-mirror.com` 可选镜像；**两源下载后都按 config.yaml sha256 强校验**。遗留：镜像源不校验证书指纹级一致性，靠哈希兜底（可接受） | `download_model.py:29-38,80,107-120` |
| 21 | 错误信息泄漏 | 「堆栈/路径泄漏？」 | `error_handler.py` 未发现 `traceback/format_exc/detail=str(e)` 直泄模式（未逐行穷尽审计，标记为低置信通过） | `middleware/error_handler.py` |

---

## 2. 七维度评分

| 维度 | 得分 | 依据（正面 → 缺口） |
|---|---|---|
| 隐私数据治理 | **3.5 / 5** | + outputs 真删文件（年龄+数量+残留帧目录回收）、启动补清、busy 跳过；uploads 24h TTL + 500MB 上限淘汰；disk_min_free 拒新任务。− 历史删除**只删 DB 记录不删文件**（`history_db.py:605-608`）；uploads 不随任务终态删除（最长滞留 24h）；`data/checkpoints/` 不在任何清理策略内；失败残留兜底只覆盖 outputs/_frames |
| 路径与文件安全 | **3.5 / 5** | + PathGuard 设计扎实：resolve 后 parents 复检、NUL/控制字符拒绝、过宽白名单告警、403 通用消息不回显、审计事件；scan/download/history 全部接线。− **`browse-dir`/`open-explorer` 绕过白名单**（`ALLOWED_ROOT_DIRS = []`，`settings.py:45`）；`validate_path` 前缀 `startswith` 存在兄弟目录前缀绕过隐患（当前未触发）；TOCTOU 已知已记录；UNC/8.3/保留设备名/尾点空格等 Windows 向量 21 项测试均未覆盖 |
| 模型供应链 | **4.0 / 5** | + 全部权重/VAE/嵌入有 sha256 锚点；下载后即校验 + 加载前再校验（双保险）；`_safe_torch_load` 默认安全模式、回退显式门控；AES-GCM 权重加密（可选，解密在内存）。− 手动放置 `model/` 的权重：加载前校验**依赖 config 哈希存在**，未入 config 的第三方权重「存在即加载」；容器镜像装未锁依赖 |
| Web 层防护 | **4.0 / 5** | + 回环强制校验器；CSRF 签名双提交（测试背书）；CSP per-request nonce（测试背书）；限流默认不信 XFF；BasicAuth 可选且自带 5 次失败封禁（常量时间比较）；错误通用化。− 默认无鉴权（依赖文档警示）；限流仅 POST 窄集；`'unsafe-inline'` 兜底；暴露档下 browse-dir/open-explorer 成为信息泄漏/任意打开原语 |
| 水印与 AI 标识合规 | **2.5 / 5** | + 默认开启、全输出管线（图/视频逐帧）、payload 签名 + task_id 可溯源性、密钥独立文件；有单测。− **fail-open**（嵌入失败静默输出无水印，违反「标识义务不应可选」）；无转码后存活证据（PNG→H.264/H.265 DCT 鲁棒性未知）；无用户侧验证 CLI；无密钥轮换 SOP；显式 AI 标识（UI 提示）未见实现痕迹 |
| 供应链 CI | **3.0 / 5** | + Trivy 密钥扫描 exit-1 且 PR 即跑；pip-audit + safety 双扫（周更兜底）；SARIF 上报；双锁文件；gpg-signed-release。− Semgrep findings 不阻断且 job 名误导；镜像无 CVE 扫描；容器用未锁 `requirements.txt`（镜像内依赖版本漂浮，与 lock 文件脱节——Dockerfile 注释已知但未解决）；pip-audit 仅 lock 文件变更时触发 |
| 文档一致性 | **2.0 / 5** | + NOTICE GPL 边界正确；PRIVACY_POLICY 与数据流基本一致；DEPLOYMENT.md 引用有效。− `.github/SECURITY.md` 版本表停在 1.0.x；PathGuard 白名单描述与实际（4 目录）不符、未提 browse-dir/open-explorer 豁免；基线审计时点后新增的 desktop/ Tauri 壳**完全未纳入任何安全文档**（signed updater 有 pubkey 在 `tauri.conf.json:36`，是好信号，但无评估记录） |

---

## 3. 风险清单（按威胁模型分档）

> 「回环档」= 默认自托管；「暴露档」= Docker `-p` 端口映射 / k8s Ingress 暴露。

| ID | 级别（回环档 / 暴露档） | 风险 | 证据 | 最小修复 |
|---|---|---|---|---|
| R1 | Info / **High** | **无鉴权 + 目录枚举/打开端点无白名单**：`GET /api/system/browse-dir?path=X&show_files=true` 可枚举全盘任意目录（含文件名+大小），`POST /api/system/open-explorer` 可 `os.startfile` 打开任意路径（Windows 上对文件即用默认程序打开=潜在执行原语）。暴露档下与「默认无鉴权」叠加，等于把服务器的文件系统索引交给同网段任何人 | `settings.py:45,451-554,557-612`；`app_server.py:636-645`（auth 默认关） | ① `ALLOWED_ROOT_DIRS` 填充为 `runtime.security.allowed_base_dirs` 同源白名单；② 容器/非回环 host 自动启用 BasicAuth（或启动时强制要求 `SEEDVR2_AUTH_PASSWORD`）；③ open-explorer 收敛为仅目录、且限白名单 |
| R2 | Medium / Medium | **水印 fail-open + 鲁棒性无证据**：嵌入异常仅 debug 日志后照常输出（合规承诺的技术兜底可静默失效）；DCT 水印经 ffmpeg H.264/H.265 重编码后的存活率无任何测试；无用户侧验证工具 → 《AI 生成合成内容标识办法》的隐式标识义务靠「应该没问题」支撑 | `_image_pipeline.py:366-368`（`except: pass`）；`_video_pipeline.py:560-565`；`scripts/` 无 verify_watermark | 嵌入失败改 fail-closed（重试→降级元数据标识→记录审计→按配置决定是否阻断输出）；加「水印→转码→提取」端到端回归测试；补 `scripts/verify_watermark.py` CLI |
| R3 | Medium / Medium | **删除语义与清理盲区**：历史删除只 `DELETE FROM history`，输出文件留盘至 14 天龄期；uploads 不随任务终态删除；`data/checkpoints/` 断点无任何清理策略（失败任务残留期无限）；PRIVACY_POLICY「可随时清空」与用户预期（删记录=删数据）存在落差 | `history_db.py:605-608`；`cache.py:49-50`；retention 仅扫 `outputs/`（`app_server.py:446-453`） | 任务终态 finally 钩子清理 uploads/checkpoints（见 §5-Q2 方案）；history DELETE 联动删文件（删前 PathGuard 复验）；retention 扫描器扩展到三个数据目录 |
| R4 | Low / Medium | **CI 供应链门禁名实不符**：Semgrep job 名 "(block on ERROR)" 实际 findings 不阻断；镜像无 Trivy CVE 扫描；容器内依赖未锁版本（`pip install -r requirements.txt` 区间解析）→ 镜像供应链完整性弱于本机开发环境 | `security.yml:23-31`；`docker-publish.yml`（无 trivy 步骤）；`Dockerfile:26` | Semgrep 换 `--error` 语义（先 triage 存量）；docker-publish 加 Trivy image scan（CRITICAL/HIGH exit 1）；Linux 平台重跑 `generate_lock.py` 产平台锁并 `--require-hashes` 安装 |
| R5 | Low / Low | **安全文档失真**：支持版本表 1.0.x（实际 1.5.1）→ 漏洞响应承诺范围模糊；PathGuard 白名单描述缺 2 目录、未提豁免端点；desktop 壳无安全评估记录 | `.github/SECURITY.md:5-10,63` | 半小时级文档修正（见整改优先级 P0） |
| R6 | Low / Low | `validate_path` 前缀匹配 `startswith(realpath(r))` 未加路径分隔符，白名单根为空当前未触发，一旦填充即现兄弟目录绕过（`C:\proj\outputs` 放行 `C:\proj\outputs_evil`） | `settings.py:79-81` | 填充白名单时同步改为 `Path.is_relative_to()` 语义 |
| R7 | Low / Low | Windows 特有路径向量（UNC、8.3 短名、CON/NUL 保留名、尾点尾空格、大小写）零测试覆盖（`test_path_guard.py` 21 项全是 POSIX 式用例；NUL/控制字符仅代码层防御） | `tests/test_path_guard.py` 用例清单 | 补参数化 Windows 向量测试（`pathlib.resolve()` 在 Windows 的行为矩阵） |
| R8 | Low / Low | CSP 保留 `'unsafe-inline'` 兜底（现代浏览器被 nonce 压制，旧内核退化为放行）；`style-src/font-src` 依赖 Google Fonts CDN——页面加载即发起第三方请求，与 PRIVACY_POLICY「本地优先」表述有张力 | `security_headers.py:37-49`；`base.html:7` | 前端内联事件改事件委托后移除 unsafe-inline；字体本地化或文档声明 |
| R9 | Info / Low | 限流仅覆盖 POST 窄集（30/min 全局 per-IP）；`integrity_enforce: false` 自检不强制；scan-folder 白名单含 `model/`、`data/checkpoints/` 可被枚举清单（不含内容下载） | `rate_limit.py:190`；`config.yaml:187-192` | 限流扩面到重资源 GET（scan/browse）；integrity_enforce 决策化（开启或删除配置项） |
| R10 | Info / Info | desktop/ Tauri 壳（2026-09-04 新增）未纳入安全评估；已确认 updater 配置 minisign pubkey（签名更新是好基础），托盘/拖拽/增量换载链路未审 | `tauri.conf.json:36-37` | 单独一轮桌面壳评估（更新包签名校验路径、拖拽落点、桥接降级逻辑） |

---

## 4. 三个必答问题

### Q1：容器/k8s 默认配置是否会把无鉴权服务暴露到局域网？

**默认配置下不会，但「用户常规操作」会。**
- k8s：Service 是 **ClusterIP**（不是 NodePort/LoadBalancer），无 Ingress 清单 → 集群外不可达；提示词的「NodePort/LB 即 P1」判据不触发。
- Docker：镜像内 `--bind 0.0.0.0` 是容器必需，真正的暴露开关是用户 `docker run -p 7870:7870`——这恰是 README 引导 GPU 用户最可能的用法，而镜像**不带任何鉴权默认值**，`server.host` 校验器管不到 gunicorn 绑定。
- 一旦暴露：叠加 R1（全盘目录枚举 + os.startfile 打开原语）与上传/GPU 占用，就是本次评估最高的现实风险。

**最小修复**（按性价比排序）：
1. **启动时自动鉴权**：`should_enable_auth` 增加条件——检测到容器环境（`/.dockerenv` 存在）或 `server.host` 非回环 → 无 `SEEDVR2_AUTH_PASSWORD` 则拒绝启动并打印修复指引（fail-closed 优于文档警示）。
2. **白名单收口 browse-dir/open-explorer**：`ALLOWED_ROOT_DIRS` 与 `runtime.security.allowed_base_dirs` 同源。
3. 文档在 Docker 章节顶部加红字警示 + k8s 示例补 NetworkPolicy/Ingress+BasicAuth 模板（`basic_auth` 中间件已现成）。

### Q2：失败残留与清理不彻底如何兜底？「失败即清理」工程方案

现状盘点：outputs 已有年龄+数量+残留帧目录三重清理且启动补清；**uploads（24h TTL）、checkpoints（永不清理）、历史删除不删文件**是三个盲区。方案：

1. **任务生命周期终态钩子（核心）**：`TaskQueue` 状态机进入终态（completed/failed/cancelled）的统一出口处加 `finally` 语义清理：
   - 删除该任务 `data/uploads/` 源文件（已有路径在任务记录里；删除前 `PathGuard.is_safe_path` 复验）；
   - 删除 `data/checkpoints/<task_id>/` 断点目录；
   - 失败任务额外清 `outputs/` 下本次 `_frames` 临时目录（现行 retention 只回收超龄残留，失败目录 mtime 停在失败时刻，要等 14 天——改为失败即清）。
2. **retention 扫描器扩展**：`periodic_output_cleanup` 改为多目录（outputs 按年龄/数量；uploads、checkpoints 按 24h/7d 年龄），配置项同步 `config_models.py` Pydantic 模型与 `CONFIG.md`。
3. **删除联动**：`DELETE /history/{id}` 与 `clear_records` 返回被删记录的 output_path 清单，逐个 PathGuard 复验后 unlink；缩略图同生命周期。
4. **可观测**：清理失败（OSError）记 `audit_event` + 计数指标，避免静默堆积；启动补清已具备（cache/outputs 清理循环首跳即执行），保持。
5. **测试**：终态清理幂等性、失败残留回收、删除联动不越白名单三组回归。

### Q3：Semgrep 不阻断、镜像无扫描，补齐的 CI 成本各是多少？

| 缺口 | 补法 | 一次性成本 | 持续成本 |
|---|---|---|---|
| Semgrep 不阻断 | 先 triage 存量 ERROR 级告警（仓库已有 `nosemgrep` 标注习惯，预计存量不多）→ 把 job 改为 `semgrep scan --config auto --severity ERROR --error`，同时把 job 名改为与行为一致 | **0.5–1 人日**（大头是存量告警分类，不是 CI 改动） | 偶发误报白名单维护 |
| 镜像无扫描 | `docker-publish.yml` 推送后加 `aquasecurity/trivy-action`（image 模式，`severity: CRITICAL,HIGH`、`exit-code: 1`，配 `.trivyignore` 管理 baseline） | **0.5 人日** + baseline 决策 | 每次 CI 约 1–2 分钟 |
| （附加发现）容器依赖未锁 | Linux 环境跑 `scripts/generate_lock.py` 产平台锁，Dockerfile 换 `--require-hashes` | 0.5 人日 | 随 Dependabot 重锁节奏 |

---

## 5. 与基线审计（2026-09-02）的增量差异

| 基线条目 | 本次状态 | 说明 |
|---|---|---|
| 无凭据入库 | ✓ 保持 | `.watermark_key` 仍 gitignore + 未跟踪；`git ls-files` 密钥模式扫描无新增命中 |
| loopback 强制校验器 | ✓ 保持 | `config_models.py:68-74` 白名单 `{127.0.0.1, localhost, ::1}` |
| 双锁文件 | ✓ 保持 / ⚠ 新缺口 | 本机锁齐全；**容器镜像实际装未锁 `requirements.txt`**（基线未发现） |
| 权重下载链路 Medium 关注 | **改善** | 下载后 sha256 即校验（P1-3 落地）+ 引擎加载前二次校验 + ModelScope 双源按哈希兜底 |
| k8s 暴露 Info | **改善** | ClusterIP + runAsNonRoot + seccomp + caps drop + 非 root + 只读权重挂载；仍缺 Ingress/auth/NetworkPolicy 示例 |
| integrity_manifest + 签名 | ✓ 保持 | manifest + `.sig` 在位；`integrity_enforce: false` 未强制（本次标记 R9） |
| magic_check / path_guard / basic_auth / CSP | ✓ 保持 | 本次深化：CSRF 为签名双提交、CSP nonce 每请求随机均有测试背书；basic_auth 带封禁器 |
| ——基线未覆盖—— | **新增** | ① browse-dir/open-explorer 白名单缺失（R1）；② retention 服务落地（落实基线 P0-1 存储反模式整改，真删文件）；③ 水印 payload 绑 task_id（改善）；④ pip-audit/safety CI 与 Trivy 密扫 SHA pin（改善）；⑤ **desktop/ Tauri 壳全新攻击面未评估**（R10）；⑥ SECURITY.md 版本表过时未修（R5，基线时点即已过时） |

---

## 6. 整改优先级

**P0（本周，合计 ≈ 1 人日）**
1. `browse-dir`/`open-explorer` 白名单收口 + 容器/非回环自动鉴权（R1，暴露档 High）。
2. `.github/SECURITY.md`：版本表 1.5.x、PathGuard 描述对齐实际、补 browse-dir 豁免说明（R5）。

**P1（两周内，合计 ≈ 3–4 人日）**
3. 水印 fail-closed + `scripts/verify_watermark.py` + 转码鲁棒性回归测试（R2）。
4. 任务终态清理钩子 + retention 扩展到 uploads/checkpoints + 历史删除联动删文件（R3，按 §4-Q2 方案）。
5. CI：Semgrep `--error` 化、镜像 Trivy、容器锁文件（R4，成本见 Q3）。

**P2（下个迭代）**
6. Windows 路径向量参数化测试（R7）；`validate_path` 改 `is_relative_to` 语义（R6，与白名单填充同批）；限流扩面 + integrity_enforce 决策（R9）；CSP 收紧路线 + 字体本地化（R8）。
7. desktop/ Tauri 壳独立安全评估（R10）。

---

## 附录：关键复核命令

```bash
# R1：白名单空置证据
grep -n "ALLOWED_ROOT_DIRS" app/integrated_app/routes/system/settings.py
# PathGuard 接线端点
grep -rn "build_default_path_guard" app/integrated_app/routes/
# R3：删除只动 DB
grep -n "def delete_record" -A 4 app/integrated_app/history_db.py
# R2：fail-open 证据
sed -n '358,368p' app/integrated_app/engines/_image_pipeline.py
# R4：门禁语义
sed -n '23,31p' .github/workflows/security.yml
grep -n "requirements.txt" Dockerfile
# 加载前 SHA256
sed -n '305,315p' app/integrated_app/engines/seedvr2_engine.py
# 留存真删文件
sed -n '63,148p' app/integrated_app/services/output_retention.py
# 密钥未入库（禁读内容）
git check-ignore -v .watermark_key && git ls-files | grep -i watermark_key  # 应无输出
```

*本评估为静态快照，建议每次大版本发布前复核；桌面壳（desktop/）建议单独成篇。*
