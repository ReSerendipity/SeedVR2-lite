# SeedVR2-lite · 数据治理评估报告（2026-09-06）

> 评估对象：`C:/Users/Doro/SeedVR2-lite`（main @ 94dbfe8，pyproject 1.5.1，Python 3.12.10）
> 评估依据：`提示词/SeedVR2-lite_数据治理评估提示词.md` v1.5.0；所有结论以本机实测为准。
> ⚠️ **对提示词本身的锚点纠错见 §0.1**——提示词的核心前提「本项目无训练能力」经实测不成立。

---

## 0. 项目事实锚点核对结果（先核对，再评估）

| 项 | 提示词声称 | 实测结论 | 证据 |
|---|---|---|---|
| 数据库 | aiosqlite，位置待核实 | ✅ `data/history.db`（WAL 模式，`user_version=2`），`history` + `tasks` + FTS5 | `app/integrated_app/history_db.py`、实测 schema |
| 留存配置 | retention 四项 | ✅ `outputs_max_age_days=14`、`outputs_cleanup_interval_seconds=3600`、`disk_min_free_gb=5.0`、`outputs_max_files=0`（第四项提示词漏记：数量上限规则存在但默认禁用） | `config.yaml` |
| PathGuard 白名单 | 4 目录 | ✅ `outputs/`、`data/uploads/`、`data/checkpoints/`、`model/` | `config.yaml runtime.security.allowed_base_dirs` |
| 权重完整性 | sha256_* 校验 | ✅ **24 项全覆盖**：3B / 7B / 7B-Sharp × 8 键（fp16/fp8/int8_convrot/mxfp8/nvfp4/vae/pos_emb/neg_emb），无缺失 | `config.yaml` 实测 |
| 溯源水印 | .watermark_key 66B | ✅ 存在且 gitignored；DCT 嵌入 payload=task_id | `security/watermark.py` |
| 断点 | 粒度待核实 | ✅ **任务级 JSON**（文件级指纹续跑），非帧级中间态；中间帧在 `outputs/video/_frames` | `app/integrated_app/checkpoint.py` |
| 心跳 | 用途待核实 | ⚠️ **是训练心跳**，写入者是 `training/distributed_trainer.py`（见 §0.1） | `data/heartbeats/heartbeat.json` |
| 测试数据 | 是否真实用户数据？ | ✅ 无真实用户数据：`data/test-inputs/grace_hopper.jpg` 为公共领域标准测试图且被 git 追踪；`data/{video,image}/transforms/` 仅是预处理脚本 | `git ls-files data/` |
| 历史可清 | 隐私政策承诺 | ✅ API 存在：`DELETE /api/system/history/{id}` 与 `DELETE /api/system/history`（支持 before_date/status 过滤） | `routes/system/history.py:234,311` |
| **训练能力** | **不存在，禁止评估** | ❌ **提示词错误**：`training/` 存在且被 git 追踪（见 §0.1） | `git ls-files training/` |

### 0.1 锚点纠错：training/ 真实存在，且今天刚运行过

提示词的「关键纠正」声称 `training/distributed_trainer.py` 不存在、本项目无训练能力、并以此禁止评估训练数据治理。**实测推翻该前提**：

- `git ls-files training/` 返回 4 个文件：`__init__.py`、`dataset_manifest.py`（数据集内容寻址清单，schema `seedvr2-dataset-manifest/1`）、`distributed_trainer.py`（36KB，DeepSpeed/FSDP/DDP）、`weight_sidecar.py`；
- 最近提交 `0ff19a5` / `0671d3b`（2026-08-30）：「数据集清单 / 权重 sidecar / epoch 快照保留 (P2-1/P2-2/P2-3)」「启用 P2-3 epoch 快照滚动清理（默认保留最近 2 个）」；
- `data/heartbeats/heartbeat.json` 内容为 `{"step": 4, "epoch": 0, "timestamp": "2026-09-06T10:09:19", "pid": 9488}`——**评估当天上午有一次训练运行**（进程已退出，`tasklist` 查无 pid 9488）；心跳为 tmp+rename 原子写单文件，无累积风险。

处理方式：按提示词意图不展开完整训练数据治理评估，仅在 §5 路线图中给出实测事实锚定；**提示词 v1.5.0 自身需要修订**（其旧版纠错变成了新版幻觉）。

---

## 1. 数据资产清单与账本（实测）

实测命令：`du -sh model outputs data ...` + `find -type f | wc -l`（2026-09-06）。

| 资产 | 体积 | 文件数 | 内容与备注 |
|---|---|---|---|
| `model/` | **3.8 GB** | 4 | 当前在场：`seedvr2_3b_mxfp8.safetensors` + `ema_vae_fp16.safetensors` + `pos_emb.pt` + `neg_emb.pt`（7B/7B-Sharp 未下载，按需策略生效） |
| `outputs/` | 9.6 MB | 11 | 4 个用户产物（9-02/9-03 的 3B 修复图）+ 7 个**开发调试产物混入**（`quant-verify/` 5 个脚本、`benchmark-history/benchmarks.jsonl`、`_bench_input.png`） |
| `data/` 合计 | 442 KB | — | 见下行拆分 |
| ├ `history.db`(+wal/shm) | ~420 KB | 3 | 10 条 history 记录；`max_records=10000` 启动裁剪兜底 |
| ├ `uploads/` | ~61 KB | 1 | `1788660473_grace_hopper_5b0346.jpg`（评估当日上传）；**无任何清理代码覆盖此目录** |
| ├ `checkpoints/` | 0 | 0 | 任务级 JSON 断点；批量完成即删，当前为空 |
| ├ `test-inputs/` | ~61 KB | 1 | git 追踪的公共领域测试图 |
| ├ `heartbeats/` | 72 B | 1 | 训练心跳单文件（原子写） |
| ├ `video/` `image/` | ~0 | 0 | 仅 transforms 预处理脚本目录（git 追踪） |
| `.watermark_key`（根） | 66 B | 1 | DCT 水印密钥，gitignored ✅ |
| `data/.seedvr2_secret` | 64 B | 1 | gitignored ✅ |
| `logs/security_audit.log` | 独立通道 | — | JSONL，RotatingFileHandler 10MB×5（`security/audit.py`） |

总账：**~3.9 GB**，其中 97% 为模型权重；隐私敏感面（uploads）当前极小但机制上无限增长。

---

## 2. 血缘完整性核对表

样本：`data/history.db` 10 行，`input_sha256` 非空率 **10/10**。`parameters` 为 20+ 字段 JSON。

| 血缘要素 | 记录位置 | 结论 |
|---|---|---|
| 输入文件路径 | `history.input_file` | ✅ |
| **输入哈希** | `history.input_sha256`（`compute_file_sha256` 实算，`restore_service.py:1091`） | ✅ 提示词担心的最大缺口已由迁移 v2 补齐 |
| 模型规格 3B/7B/7B-Sharp | `history.model_size` | ✅ |
| 精度 fp16/fp8/int8_convrot/mxfp8/nvfp4 | `parameters.dit_model`（如 `"3b_fp16"`、`"3b_nvfp4"`） | ✅ |
| tile 档位与 overlap | `parameters.encode/decode_tile_size`、`*_tile_overlap` | ✅ |
| VAE tiling 开关 | `parameters.encode_tiled / decode_tiled` | ✅ |
| 附加模型（RIFE/RAFT/LCM） | `parameters.temporal_overlap` 有；RIFE/RAFT/LCM 无独立字段 | △ 部分 |
| 输出编码参数 | `parameters.output_format` 字段存在但实测值为空串 | △ |
| **ffmpeg 版本** | 无任何记录（`grep ffmpeg history_db.py restore_service.py` 零命中） | ❌ **血缘断点** |
| 降级重试事件 | OOM 降级参数写入历史（`build_degradation_metadata`，`restore_service.py:560+`） | ✅（P0-3 已落地） |
| 处理耗时 / 显存峰值 / 输出体积 | `processing_time` / `vram_peak_mb` / `output_size_bytes` | ✅ |
| 输出文件嵌入元数据 | 无 EXIF/comment 写入（grep 零命中）；水印为像素域 DCT | ❌ 文件脱离系统后失去参数上下文 |
| 水印可验证性 | 嵌入侧 `watermark_payload=task_id` ✅；`scripts/` 无水印验证工具（`verify_engine.py` 是引擎自检不是水印校验） | ❌ |

---

## 3. 七维度评分（0–5）

### 3.1 隐私数据治理 — **2.5 / 5**

**加分项（代码级确认）**：
- `outputs/` 14 天清理**真删文件**：`output_retention.py:77,101` 为 `os.remove`，非仅删记录；`_remove_leftover_dirs:139` 对 `_frames` 残留目录 `rmtree`。执行者为 `app_server.py` lifespan：**启动先执行一次补清**（服务关闭期间的漏清在重启后补上）+ `periodic_output_cleanup` 周期任务（1h），带 `is_busy` 忙碌跳过避免与活动任务竞争。
- 视频失败路径多点清理：`engines/_video_pipeline.py` 7 处 `rmtree(frames_dir)`（612/640/647/724/735 等，含读帧失败、无帧产出、异常分支）；`video_processor.py:440` 用 `tempfile.TemporaryDirectory()` 上下文管理器托管编码临时目录。
- 历史删除 API 存在且删除会同步 FTS 索引；`max_records=10000` 自动裁剪。
- 上传面防护完备：魔数校验、限流 30/min、50MB/500MB 大小上限、PathGuard 白名单 + CSRF 全局中间件。

**失分项**：
- **`data/uploads/` 永不清理**（P0，见 §4）：用户上传的原始隐私视频/图像无限期留存——outputs 有 14 天策略而 uploads 没有，留存不对称。
- 历史删除**只删 DB 记录**：`history_db.delete_record/clear_records`（`history_db.py:605-627`）为纯 SQL DELETE，不连带输出文件、断点 JSON——隐私承诺「历史可清」的彻底性缺口（P1）。
- 14 天自动删除无删除前警告、无「标记保留」机制（P1）。

### 3.2 血缘可追溯 — **3.5 / 5**

加分：input_sha256 实算入库、parameters 覆盖精度/tile/seed/VAE/色彩校正等 20+ 字段、OOM 降级事件入血缘（超出提示词预期）。失分：ffmpeg 版本缺失、输出编码参数空值、附加模型字段不全、输出文件无元数据嵌入、无水印验证工具（对照 §2 核对表）。

### 3.3 Schema 演进 — **4.5 / 5**

提示词预期「隐式升级埋雷」，实测为**显式版本化迁移**：`history_db.py` 通过 `PRAGMA user_version`（当前 =2）驱动 `_MIGRATIONS` 链（v0→v1 补 `output_size_bytes`/`vram_peak_mb`，v1→v2 补 `input_sha256`，均 `ALTER TABLE ADD COLUMN`），建表用 `CREATE TABLE IF NOT EXISTS` + WAL，且有专项测试 `test_history_db_migrations.py`。唯一缺口：迁移前无自动备份（P2）。

### 3.4 权重治理 — **4 / 5**

- 覆盖度：**24 项 sha256 全覆盖，零缺失**（3B/7B/7B-Sharp × fp16/fp8/int8_convrot/mxfp8/nvfp4 + vae + pos_emb + neg_emb）。
- `download_model.py`：下载后**强制**按 config 期望哈希校验（默认 True）→ ModelScope 镜像换源场景哈希不一致会被拦截 ✅；按需下载（`--size 3b/7b/7b_sharp`、`--precisions`、`--files`、`--no-vae`）✅；HF/ModelScope 双源路由 + hf-mirror 镜像 + `.part` 断点续传 ✅；量化包默认不下载（3B 三件约 6.7GB 需显式选择）✅。
- 磁盘账本：按需策略生效（3.8GB 在场，未下全量）。
- 缺口：**加载面无校验**——`model_manager.py:127-146` `validate_model_file` 仅检查文件存在；用户手动放置任意权重可被直接加载（提示词反模式 #6 成立，属 P1）。`security/integrity_selfcheck.py` 的完整性自检覆盖的是**核心代码模块**（防篡改/供应链投毒），不覆盖权重文件，且 `integrity_enforce=false` 默认不强制。

### 3.5 断点与中间态 — **4 / 5**

- 粒度已核实：`TaskCheckpoint` 为**任务级 JSON**（`{task_id}.json`，含 completed_files 路径+大小+mtime 指纹），每完成 1 个文件落盘（`checkpoint_every=1`），崩溃重启跳过已完成文件；批量完成后 `remove_checkpoint`（`restore_service.py:1131-1133`）。
- 中间帧：`outputs/video/_frames` 在成功与异常路径均有显式清理，硬崩溃（进程级）由 retention 的 `_frames` mtime 回收兜底——但兜底要等满 14 天年龄阈值（P2）。
- 失败任务 checkpoint JSON 永久留存（无 TTL、无孤儿扫描）；且 JSON 内含本机绝对路径（P2）。
- 心跳：训练器原子写单文件，无小文件累积风险（提示词担心的「大量小文件」不成立）。

### 3.6 配置治理 — **3 / 5**

- Pydantic 校验器覆盖完整（`config_models.py:557-567` retention 字段带范围约束；禁 0.0.0.0 校验器在位）。
- 写接口 `POST /api/system/settings` 受全局 CSRF 中间件（Double Submit Cookie）+ 仅绑定 127.0.0.1 保护。
- 缺口：**配置热改无审计**——`security/audit.py` 通道完善（JSONL/轮转/字段规范）但事件仅覆盖 CSRF/AUTH/RATE/PATH/INTEGRITY，settings 变更（含可改 `allowed_base_dirs` 安全白名单！）不产生审计记录（P1）；`integrity_enforce=false` 默认（P2）。

### 3.7 测试数据隔离 — **4 / 5**

- `tests/conftest.py:61-80`：`tmp_path` + 重定向 `history.db` 与 `config.yaml` ✅；`test_storage_lifecycle.py` 12 项测试全部 `tmp_path`，不触真实 `outputs/` ✅；`test_history_db_migrations.py` 专项覆盖 ✅。
- 仓库测试素材零真实用户数据（grace_hopper.jpg 公共领域 + transforms 脚本）✅。
- 缺口：`data/` 生产/测试混用（uploads 与 test-inputs 同级）；`outputs/` 混入 `quant-verify/` 等开发产物（P2，且这些产物 14 天后会被清理误伤——功能上无碍但污染用户画廊语义）。

---

## 4. 缺陷清单（P0/P1/P2 + 证据 + 复验命令）

### P0-1 `data/uploads/` 永不清理，用户原始隐私数据无限留存
- **证据**：`grep -rn "uploads" app/integrated_app/services/*.py | grep -i "remove|unlink|delete|clean|retention"` 零命中——全仓无 uploads 清理路径；retention 只挂 `outputs/`（`app_server.py:450-470`）。实测 `data/uploads/image/1788660473_grace_hopper_5b0346.jpg` 已在场。用户上传 500MB 视频的目录上限场景下，uploads 只增不减。
- **复验**：`grep -rn "uploads" app/integrated_app/services/output_retention.py app/integrated_app/app_server.py | grep -v cache_dir`

### P1-1 历史删除不连带文件，「历史可清」承诺不彻底
- **证据**：`history_db.py:605-627` `DELETE FROM history`（含 FTS 同步）；输出文件仍留在 `outputs/` 直到 14 天龄化，断点 JSON 永留。
- **复验**：`sed -n 605,627p app/integrated_app/history_db.py`

### P1-2 ffmpeg 版本未入血缘
- **证据**：`grep -rn "ffmpeg" app/integrated_app/history_db.py app/integrated_app/services/restore_service.py` 零命中；`parameters` JSON 无版本字段。
- **复验**：§4 命令 ①。

### P1-3 模型加载面无 sha256 白名单
- **证据**：`model_manager.py:127-146` 仅 `os.path.exists(checkpoint_path)`；sha256 校验只在下载后（`download_model.py:289`）与手动 `verify_engine.py`。
- **复验**：`sed -n 127,146p app/integrated_app/model_manager.py`

### P1-4 配置热改无审计日志
- **证据**：`routes/system/settings.py:185-238` POST 直接 `save_config`，可改 `runtime.security.allowed_base_dirs`；`security/audit.py` 事件枚举不含 CONFIG_UPDATE。
- **复验**：`grep -n "audit" app/integrated_app/routes/system/settings.py`（零命中）。

### P1-5 14 天删除无警告、无「标记保留」
- **证据**：`output_retention.py` 清理逻辑无通知回调、无豁免标记（仅 `.gitkeep` 占位豁免）；`outputs_max_files=0` 数量规则默认禁用。

### P2-1 失败任务断点 JSON 无 TTL（含本机绝对路径）｜P2-2 `outputs/` 混入开发产物（`quant-verify/` 5 个脚本、`benchmark-history/`、`_bench_input.png`）｜P2-3 `data/` 生产与测试子目录混用｜P2-4 DB 迁移前无自动备份｜P2-5 输出文件无参数元数据嵌入且无水印验证工具（对照 MiniMax-H3-lite 的 `scripts/verify_watermark.py`）｜P2-6 `integrity_enforce=false` 默认不强制

**反模式判定**（对照提示词 §3 九条）：#1 清理只删记录不删文件 → outputs 不成立（真删）、历史删除成立（P1-1）；#2 14 天无警告删除 → 成立（P1-5）；#3 失败残留 → 基本不成立（多点清理 + 兜底，仅 14 天兜底延迟与 checkpoint 无 TTL 为弱化形式）；#4 血缘缺失 → 输入哈希已补齐，ffmpeg 版本仍缺（P1-2）；#5 隐式 schema 升级 → **不成立**（显式 user_version 迁移链）；#6 权重白名单缺失 → **成立**（P1-3）；#7 测试生产同目录 → 成立（P2-3）；#8 测试数据含真实用户数据 → **不成立**（公共领域素材）；#9 旧提示词评估训练治理 → **提示词自身纠错错误**：training/ 存在（§0.1）。

---

## 5. 改进路线图

**P0（本周）**：uploads 留存策略落地——复用 `output_retention.py` 模式为 `data/uploads/` 挂同款周期任务（建议独立 `uploads_max_age_days`，默认 7–14 天；`data/uploads/restored/` 与 outputs 同策略），Lifespan 启动补清 + 忙碌跳过逻辑可直接复用 `is_busy` 回调。

**P1（两周内）**：
1. 历史删除连带文件：`delete_record/clear_records` 接收 `output_file` 路径，经 PathGuard 校验后 `unlink`（缩略图/断点一并清理）；
2. ffmpeg 血缘：`FFmpegProcessor.__init__` 缓存 `-version` 首行一次，写入 `parameters.ffmpeg_version`（最最小改动，无需加列）；或迁移 v3 加列；
3. 加载面白名单：`load_model` 前按 config 期望哈希校验在场权重（复用 `compute_file_sha256`，GB 级文件约秒级，可缓存至 `data/model_hash_cache.json`）；
4. 配置审计：settings POST 成功后 `audit_event("CONFIG_UPDATE", keys=[...])`；
5. 保留标记：history 表加 `pinned INTEGER DEFAULT 0`（迁移 v3）或输出文件旁 `.keep` 侧车，retention 清理跳过 pinned；删除前经 SSE 广播即将清理清单。

**P2（一个月）**：失败 checkpoint 加 TTL（如 `stale_threshold_minutes` 的 N 倍）并在启动时孤儿扫描；`quant-verify/`、`benchmark-history/` 迁出 `outputs/`（至 `artifacts/`）；`data/` 拆分 `data/test/` 隔离测试素材；迁移前 `history.db → history.db.bak-v{n}` 自动备份；输出文件 EXIF/comment 嵌入生成参数 + 提供 `scripts/verify_watermark.py`。

**三个必答问题**：

1. **`outputs_max_age_days=14` 会不会造成用户资产丢失？——会。** 判据为 mtime（重新打开/下载不刷新），无警告、无豁免标记、数量规则默认禁用，用户把修复结果当长期资产时第 15 天静默丢失。替代方案：**按磁盘水位触发**（`disk_min_free_gb` 已有任务预检语义，扩展为清理触发器）+ `outputs_max_files` 数量上限保留最新 N 个 + 「标记保留」（pinned 列 / `.keep` 侧车）+ 清理结果 SSE 通知。
2. **「失败即清理」如何落地？——本项目已有 80% 答案**：`_video_pipeline.py` 多点位 `rmtree` + `TemporaryDirectory` 上下文 + retention 兜底。补强三点：① 用 `@contextmanager` 把 7 处散落的 `rmtree(frames_dir)` 收拢为单一 `frames_dir_scope()`，保证任何退出路径（含 `CancelledError`）必经 `finally`；② 启动时孤儿扫描：`_frames` 目录 mtime 早于 `progress_stall_timeout_minutes`（已有配置，30min）即回收，不必等 14 天；③ 定时扫孤儿保留为崩溃兜底（现有 `_remove_leftover_dirs` 改用短阈值即可）。
3. **历史库缺哪些血缘字段？最小改动是什么？**——缺 ffmpeg/ffprobe 版本（P1-2）、输出编码参数实际值（`output_format` 空串）、GPU/后端信息、checkpoint 文件级哈希（`dit_model` 只有逻辑名）、水印 payload 与输出的显式关联（当前只隐含在像素里）。**最小改动 = 零迁移**：`FFmpegProcessor` 初始化时缓存一次版本串 + 任务创建时把 `ffmpeg_version`、`gpu_backend` 塞进现有 `parameters` JSON（TEXT 列已容纳）；正式做法为迁移 v3 加 `ffmpeg_version`/`gpu_backend` 两列并沿用 `_MIGRATIONS` 链与专项测试模式。

**训练数据治理（实测事实锚定，按提示词意图不展开）**：`training/dataset_manifest.py` 已实现数据集内容寻址清单（dataset_sha256 → 实验追踪），`distributed_trainer.py` 已实现 step 快照 keep=3 / epoch 快照 keep=2 滚动清理、心跳原子写——训练侧数据治理反而领先于推理侧（P2-1/P2-2/P2-3 已落地）。提示词 v1.5.0 需删除「无训练能力」的错误锚点。
