# SeedVR2-lite 数据管理与数据治理体系 —— 深度完整性评估报告

> 📅 评估日期：2026-08-30
> 🔍 评估方式：全仓静态审查（routes/ engines/ services/ security/ training/ tests/ perf/ CI workflows 逐点取证，所有结论均绑定 `文件:行号` 证据）
> 📊 总体评分：**B-（70/100）** —— 运行时数据生命周期治理意外地成熟（保留策略/断点/OOM 降级均有真实实现），但**数据血缘完整性、质量量化指标、Golden 数据集、训练数据版本化**四大支柱存在结构性缺失

---

## 〇、评估方法与前置条件核实

### 0.1 关键资产分布核实（与推断对照）

| 推断路径 | 实际状态 | 备注 |
|---|---|---|
| `app/integrated_app/history_db.py` | ✅ 存在（619 行） | SQLite + WAL + FTS5，见 §1.1 |
| `app/integrated_app/config_models.py` | ✅ 存在 | 12 配置域 Pydantic 聚合（:735-771），见 §1.2 |
| `app/models/`（RAFT/RIFE/LCM） | ⚠️ 仅 4 个 py，**全是占位实现** | `raft_flow.py:47-56` `_load_model` 置 `self._model = True`；`rife_interpolator.py:29-30` torch.jit.load 被注释；`lcm_distill.py:249-251` prompt 编码为随机嵌入。**无任何权重文件与 metadata** |
| `training/distributed_trainer.py` | ✅ 存在 | checkpoint 策略完整，见 §1.4 |
| `tests/test_history_db.py` / `test_color_fix.py` | ✅ 存在 | 见 §4 |
| `perf/benchmark/` | ✅ 存在 | 仅测 API 耗时，见 §6.2 |
| 真实权重 | 根级 `model/` | 3b/7b/7b_sharp × fp16/fp8（3.16~15.35GB）+ VAE + pos/neg_emb，**无 sidecar metadata 文件** |

**关键修正**：`app/models/` 与根级 `model/` 是两个不同层次——前者是"占位代码层"（RAFT 流估计、RIFE 插帧、LCM 蒸馏均未真正可用），后者是"真实权重层"。评估数据治理时两者须分开对待：前者目前不产生数据治理负担，但一旦启用会绕过全部现有完整性校验链。

### 0.2 数据落盘全景图

```
data/
├── history.db (+wal 1.21MB, +shm)     # 历史库，WAL 已启用（history_db.py:143）
├── uploads/{image,video,restored}/    # 上传原件，文件式存储（非 base64 入库）
├── checkpoints/                       # 运行时任务断点（JSON per task）
├── heartbeats/heartbeat.json          # 训练心跳（原子写）
├── .seedvr2_secret (64B)              # ⚠️ 水印/签名密钥明文落盘
└── tmp_sse_debug.db (56KB)            # ⚠️ 调试残留，无清理机制
outputs/
├── video/_frames/                     # ⚠️ 峰值可达数十 GB 的中间帧目录
└── benchmark-history/benchmarks.jsonl # 基准归档
experiments/logs/{exp_id}.jsonl        # 训练实验追踪（超参，非数据版本）
model/                                 # 15.35GB 级权重，元数据仅存 config.yaml
```

---

## 一、子体系评估之 1：数据模型与 Schema 治理

### 1.1 Restore History DB Schema —— **已实现（85 分）**

**表结构**（`history_db.py:150-185`）：
- `history` 表：`id`（自增主键）、`task_type`、`input_file`、`output_file`、`model_size`、`status`（默认 pending）、`parameters`（JSON 字符串）、`processing_time`、`created_at`、`error_message`、`output_size_bytes`（存储成本可见性，P1-1）、`vram_peak_mb`（P2-1）
- `tasks` 表：`task_id`（UUID 主键）↔ `record_id` 双向关联（`get_task_by_record_id:611-618`）
- `history_fts`：FTS5 外部内容模式覆盖 4 个检索字段，3 触发器（INSERT/DELETE/UPDATE，:196-217）自动同步

**工程亮点**：
- WAL + `synchronous=NORMAL` + `busy_timeout`（:143-148），UPDATE 列名白名单防注入（:374-385、:591），批量插入失败降级逐条（:351-366），异步上下文管理器保证连接释放
- 明确"单连接串行写"契约（:345 注释），多进程并发依赖 WAL 兜底

**缺陷**：
- ❌ **无 schema version**：全仓 `PRAGMA user_version` / migrations 零命中。迁移是"CREATE IF NOT EXISTS + PRAGMA table_info 探测补列"（:167-173），属隐式迁移。新增列一旦涉及类型变更或 NOT NULL 约束即失控
- ❌ 迁移无失败处理与回滚记录——补列失败只是静默跳过

### 1.2 配置即 Schema（Pydantic 治理）—— **已实现（90 分）**

`config_models.py` 12 域聚合（:735-771），`extra="ignore"` 保证向前兼容。数据治理直接相关域全部有界校验：
- `HistoryConfig`（:260-290）：`db_path`、`max_records: 10000`（1-100000）
- `RetentionConfig`（:527-544）：`outputs_max_age_days: 14`、`disk_min_free_gb: 5.0`
- `RuntimeTaskConfig`（:444-490）：`checkpoint_dir/checkpoint_every/auto_recover/stale_threshold_minutes`
- `ModelEntryConfig`（:95-139）：5 个 sha256 字段

**⚠️ 发现一处配置漂移**：`routes/restore/upload.py:198-201` 注释声称从 `runtime.security.max_upload_*_mb` 读取大小限制，但 `RuntimeSecurityConfig` 模型与 config.yaml 均未定义这两个字段——实际永远走 `common.py:47-48` 的回退硬编码（图 50MB / 视频 500MB）。这是"文档/注释与机器事实不一致"的典型治理缺口（恰好违反本项目 AGENTS.md 铁律 #1 的精神）。

### 1.3 模型 Checkpoint Metadata —— **部分实现（55 分）**

- ✅ 主模型（SeedVR2 DiT/VAE）：完整性由 `integrity_check.py` 双层保障——`verify_model_files` 在加载前强制调用（`seedvr2_engine.py:311-317`，失败 raise），config.yaml 15 个 sha256 字段全部非空（:87-127）；另有代码自检 `integrity_selfcheck.py` + `integrity_manifest.json`（11 文件哈希，支持运行时周期重检 1800s）
- ❌ **无版本/来源/训练谱系元数据**：权重只有文件名（`seedvr2_ema_3b_fp16.safetensors`），无 sidecar JSON/YAML 记录训练日期、数据集、超参、parent checkpoint。模型"从哪来"完全不可考
- ❌ `app/models/` 的 RAFT/RIFE/LCM 完全无版本管理（且本身是占位实现）
- ⚠️ 完整性 manifest 与被校验代码**同目录且无签名**——能改代码的攻击者可同步改 manifest，防投毒能力打折

### 1.4 Training Dataset Versioning —— **未实现（10 分）**

- `training/` 仅 2 个文件。Trainer 通过 `setup_dataloader(dataset)`（:281-311）接收外部 Dataset，**全目录零命中** `manifest|dataset_version|data_version`
- 唯一追踪物是 `app/utils/experiment_tracker.py`（JSONL/wandb 超参记录），记录的是 config 字典而非数据指纹
- 结论：**训练数据 → 模型权重的 provenance 链完全断裂**。训练产出的权重无法回答"用了哪批数据、哪个数据集版本"。对本项目而言风险可控（训练目录更像实验性脚手架），但若认真开展分布式训练这是第一大缺口

### 1.5 子体系小结

| 项 | 状态 | 得分 |
|---|---|:--:|
| History schema + FTS + 索引 | 已实现 | 85 |
| Schema 版本化迁移 | 未实现 | 10 |
| Pydantic 配置治理 | 已实现（1 处漂移） | 90 |
| 权重完整性校验（CWE-353） | 已实现 | 90 |
| 权重版本/谱系 metadata | 未实现 | 10 |
| 训练数据 versioning/manifest | 未实现 | 10 |

---

## 二、子体系评估之 2：数据质量管理

### 2.1 输入校验 —— **部分实现（65 分）**

**已实现（纵深防御）**：
- 大小限制：图片 50MB / 视频 500MB（`upload.py:198-224`）
- 扩展名白名单（`common.py:37-43`）+ **魔数双校验**（`magic_check.py:84-169`，含 WEBP/AVI/ftyp 二次校验、空文件拒绝），接线于 `upload.py:211-228`
- 损坏文件失败路径完整：PIL 解码失败 → `RestoreResult(success=False)` → 落库 failed（`_image_pipeline.py:795-798` → `restore_service.py:332-335`）；视频打不开/0 帧均清理 frames_dir 并失败（`_video_pipeline.py:289-293, 594-597`）

**缺口**：
- ❌ 上传时**无分辨率/时长/帧数上限校验**——ffprobe 能力已有（`video_processor.py:130-198`）但只用于"两倍分辨率"参数覆写，不用于拒绝 4 小时 8K 视频（这会静默生成数十 GB 中间帧）
- ❌ 无 HTTP content-type 白名单（以魔数替代，尚可接受）
- ❌ **`folder_path` 模式与批量入口不校验魔数/大小**（`upload.py:232-246`、`batch.py:169-181` 仅按扩展名收集直接推理）——绕过上传接口的旁路校验缺口

### 2.2 修复质量指标 —— **关键缺口（35 分）**

- ✅ color_fix 五算法（LAB/HSV/小波/小波自适应/AdaIN，`color_fix.py:27-218`），图像与视频逐帧均已接线，`tests/test_color_fix.py` 覆盖形状/值域/极端输入/回退逻辑
- ✅ **执行失败**重试体系完整：`bad_case_retry.py` 失败分类（OOM/网络/取消）→ 三级降级阶梯（blocks_to_swap↑ → 分辨率×0.75 → fp16→fp8+seed 轮换，:246-310）→ 指数退避；批量场景 OOM 降级写回批级配置避免重复 OOM（`restore_service.py:544-577`）；OOM 熔断（503+Retry-After）；29 个单测
- ❌ **无 LPIPS/PSNR/SSIM 输出质量评估**：全仓 SSIM 仅用于场景切换检测（`video_processing_enhance.py:1103-1152`）、PSNR 仅用于水印不可感知性验证（`watermark.py:14,41`）。修复结果"是否比输入更好"完全靠人眼
- ❌ **无"质量 bad case"重试**：现有重试只针对"执行失败"，不针对"色偏/模糊/伪影"等质量不达标输出。`retry_with_bad_case_detection` 名字里有 bad case，实际是失败重试且重试耗尽后**接受低质量输出优雅降级**（:430-440）

### 2.3 子体系小结

输入端"能否进来"的防线较完整，输出端"修得好不好"零度量。对一个**视觉修复项目**而言，这是治理体系中最讽刺的缺口：有 4 个量化指标采集显存峰值，却没有 1 个指标评估修复质量。

---

## 三、子体系评估之 3：数据所有权与血缘

### 3.1 Source → Restored 血缘 —— **部分实现（60 分）**

**已实现**：
- `HistoryRecord` 保存 input_file 路径 + 完整参数 JSON（seed/tile/overlap/precision/color_correction，`upload.py:293`）+ model_size 档位 + output_file/output_size_bytes/vram_peak_mb
- task ↔ record 双向关联、FTS5 全文反查（含输出文件名）、按类型/状态过滤
- 输出文件名自带时间+模型档位+UUID8（`_image_pipeline.py:56-71`：`YYYYMMDD_HHMMSS_3B_<uuid8>.png`）
- **DCT-QIM 频域水印 + HMAC-SHA256 签名**（`watermark.py:138-160, 213-319`）：图像输出与视频逐帧均嵌入，可举证所有权——这是同类项目少有的强项

**缺口**：
- ❌ **HistoryRecord 无源文件内容 hash 列**。任务级 checkpoint 的 `_file_fingerprint`（`checkpoint.py:40-54`）只有 path+size+mtime 且不入历史库。上传文件重名/被覆盖后，历史记录与实际产出无法严格对应
- ❌ **model_size 非精确版本**（"3b" 不区分当天权重是否被替换过，尽管 sha256 校验存在，但校验哈希未写进历史记录）
- ❌ **无输出→任务反查 API**：水印虽可提取验证，但不绑定 task_id/record_id；无法用"一张输出图"反查"哪次任务、什么参数、哪个输入"

### 3.2 Training Data → Model Weights Provenance —— **未实现（0 分）**

见 §1.4。权重的"出身"（数据集、训练超参、parent checkpoint、git commit）无处记录。

### 3.3 子体系小结

推理侧血缘做到"路径+参数+水印"的**弱血缘**（无内容寻址）；训练侧血缘为**零**。

---

## 四、子体系评估之 4：测试数据管理

### 4.1 Golden 数据集 —— **未实现（15 分）**

- `tests/test-assets/` 仅 3 个"最小合法魔数"占位文件（585B JPEG / 70B PNG / 64B MP4），`generate_test_assets.py:29-72` 自述"1×1 红像素 + 仅 ftyp 骨架，**不可解码播放**"，仅供 Playwright 前端校验
- **无真实 golden 图片/视频、无"退化输入→期望输出"基准对**
- 讽刺点：应用层有现成的 `HierarchicalDegradationProcessor.apply_degradation`（`video_processing_enhance.py:1536-1601`：降采样+噪声+模糊+色偏，注释明说"用于训练数据增强或退化模拟"）——**现成的合成退化生成器存在却未接入测试体系**
- `test_color_fix.py` 用 numpy 合成图像自建输入，属"测试内联数据"而非受版本管理的 golden 集

### 4.2 性能基线产物 —— **部分实现（50 分）**

- `bench_restore_api.py` 输出 JSONL 归档（`outputs/benchmark-history/`）+ `--trend` 趋势打印
- 视觉回归 baseline 机制完整：`uiux-compatibility.spec.ts-snapshots/` + `maxDiffPixelRatio: 0.01` 硬门禁 + 禁止 `--update-snapshots` 绕过（`e2e.yml:85-93`）
- 缺失：无跨版本对比的自动 diff、无阈值告警、bench_restore_api 未进 CI

---

## 五、子体系评估之 5：数据生命周期

### 5.1 总览 —— **意外地成熟（80 分，本项目治理最强项）**

| 生命周期环节 | 机制 | 证据 |
|---|---|---|
| 上传原件 | TTL 清理（86400s）+ 每小时后台任务 | `cache.py:44-66, 218-283`，lifespan 挂载 `app_server.py:338` |
| 输出目录 | 按年龄（14d）/数量清理 + 磁盘水位预检（<5GB 拒绝新任务 507） | `output_retention.py:40-114`、`restore_service.py:80-114` |
| 视频中间帧 | `_frames` 目录所有失败路径 rmtree + 残留兜底回收 + 级联删空目录 | `_video_pipeline.py:291-293, 594-597, 621-633`；`output_retention.py:117-148` |
| History DB | 插入后自动 prune 至 10000 条（两步确定式裁剪）+ 手动清理端点 | `history_db.py:517-561`、`routes/system/history.py:290-318` |
| 运行时 checkpoint | 任务完成即删 + 启动恢复 + 卡死看门狗 30min | `restore_service.py:958-961`、`app_server.py:299-404` |
| 卡死任务 | 启动清账 + 每 5 分钟周期 + 防误杀（跳过运行中） | `recovery.py:112-182` |
| 训练 checkpoint | `keep_last_checkpoints: 3` 滚动删除（epoch 快照豁免）+ 断点续训 | `distributed_trainer.py:104-105, 570-595, 597-631` |
| 推理期清理 | 忙碌时跳过清理（`is_busy` 防竞态） | `app_server.py:421-423` |

**剩余缺口**：
- ⚠️ `cache.max_size_mb: 500` 配置存在但 `cache.py` **只实现了 TTL 清理，未实现按总大小清理**——又一处"配置承诺未兑现"
- ⚠️ 半成品输出文件（compose 中断的 mp4）无显式删除（仅 `_frames` 有兜底）
- ⚠️ `data/tmp_sse_debug.db`、`app/integrated_app/data/` 空目录等残留无回收机制
- ❌ 训练 epoch checkpoint 豁免滚动删除 → 长期训练仍可能累积（但至少按步快照有界）

### 5.2 用户数据桌面迁移

- History DB 路径走 config（`db_path: data/history.db`），`conftest.py:70` 测试隔离已验证可重定位
- 但**无版本化迁移框架**（§1.1）→ 桌面版升级时旧 db 结构变更风险高；无导出/导入 API

---

## 六、子体系评估之 6：指标口径治理

### 6.1 VRAM 口径 —— **已实现且统一（90 分）**

- `vram_monitor.py:180-188` 四口径并行采集（allocated/reserved/max_allocated/max_reserved）+ 驱动层 `mem_get_info`，全仓其他统计点（`gpu_utils.py:218-219`、`_memory_utils.py:245-246`、`memory_manager.py:130-133`、`cache_manager.py:98`）口径一致
- 分阶段 context manager（vae_encode/dit_sample/vae_decode）+ `reset_peak_memory_stats` 起点重置（:294）+ 阶段净增量
- **峰值落库**：`global_peak_allocated_mb` → history 表 `vram_peak_mb` 列，形成跨任务可查询记录
- OOM 处理纵深完整：VAE tile 减半 → 禁 tiled → 参数降级重试 → 批级熔断
- ❌ 缺**自动泄漏告警**：分阶段报告可人工定位泄漏，但无"连续 N 任务峰值单调上涨"的检测

### 6.2 推理速度口径 —— **部分实现（55 分）**

- 后端总口径统一（含帧抽取→DiT→VAE→色偏→ffmpeg 全流程，`_video_pipeline.py:658`），输出 fps 与 avg_frame_time_ms
- ❌ **三套口径并存**：后端终态 metadata fps / 前端实时帧间差 fps（`static/js/app.js:1270-1276`）/ benchmark 双计时（轮询 vs backend_processing_s）；图像管线无 fps
- ❌ 无 per-step it/s（timestep 进度只有静态 sample_steps 报告）
- ❌ 基准无质量维度：`bench_restore_api.py` 仅测耗时；`performance.yml` 的 locust 有 P95<500ms/错误率<1% 阈值但无 baseline 对比；`gpu-smoke.yml` 真实推理仅有超时门槛
- **定义未文档化**：无任何文档规定"processing_time 是否含 IO/合成、fps 分母是输入帧还是输出帧"

### 6.3 质量分口径 —— **未实现（0 分）**

无 LPIPS/PSNR/SSIM 评分；PSNR 只服务水印验证。项目连"修复后质量分"的概念都未引入。

---

## 七、反模式识别核查（逐条验证）

| # | 指控反模式 | 核查结论 | 证据 |
|---|---|---|---|
| 1 | 大量 restoration outputs 无清理机制 | **不成立**（已治理） | outputs 14 天保留 + 数量上限 + 周期清理（`output_retention.py`），历史上显然被治理过（代码注释引用 P0-1） |
| 2 | Training checkpoints 无限累积占 TB 级 | **部分成立** | 按步快照有界（keep 3），但 **epoch 快照永久豁免**（`distributed_trainer.py:570-595` 正则区分）；且 fp16 权重 15GB 级，epoch 级累积现实可达 TB |
| 3 | 显存泄漏检测缺失 | **部分成立** | 无自动告警/自动检测，但四口径分阶段监控 + 峰值落库提供了人工排查的全部数据；缺的只是"最后一公里"的异常检测规则 |
| 4 | Batch 失败留下 partial data | **基本不成立** | 逐文件即时落库防丢账（P1-7）、取消时剩余文件落库 cancelled、断点续跑、完成清 checkpoint、`_frames` 全失败路径清理；仅半成品 mp4 无兜底 |
| 5 | Model weight encryption 未实施 | **成立** | 无任何加密/签名存储机制；但 sha256 完整性校验（CWE-353）+ HMAC 水印已实现——完整性有，机密性无。对 Apache-2.0 开源权重而言优先级确实不高，但 `data/.seedvr2_secret` 明文落盘（64B）且 manifest 无签名，值得收紧 |
| 6 | 历史记录 db size 无控制增长 | **不成立** | max_records=10000 插入后自动 prune + FTS 同步裁剪 + 手动清理端点。唯一隐忧是 WAL 文件（实测 1.21MB > 主库 144KB）无 checkpoint 管理，但量级无害 |

**结论**：列表中 6 条反模式有 3 条已被系统性治理过（且代码注释显示是有编号、有意识的治理工程 P0-1/P1-1/P1-7/P2-1），真正残留的是 #2 的 epoch 豁免、#3 的告警缺失、#5 的机密性。

---

## 八、特别警示（视觉修复项目特殊性）逐项回应

### 8.1 Huge Storage Requirements —— ✅ 已正面应对
磁盘水位预检（5GB 拒单）、输出 TTL、中间帧兜底回收、`output_size_bytes` 入库形成存储成本可观测。**下一步应做**：按用户的存储预算自适应调整 `outputs_max_age_days`（当前是静态 14 天，40GB 输出与 400GB 输出同策略）。

### 8.2 GPU Memory Intensive —— ✅ 应对充分
见 §6.1，这是全项目指标治理的标杆。缺自动泄漏告警。

### 8.3 分布式训练同步复杂度 —— ⚠️ 半成熟
心跳文件原子写（`distributed_trainer.py:486-518`）、AMP scaler 状态保存（防续训 loss scale 从头摸索）、断点续训齐备；但 dataset versioning 为零、DDCP/故障节点检测未见、多卡数据一致性（seed/shuffle 状态）未入 checkpoint。

### 8.4 Desktop App 数据迁移 —— ❌ 未准备
无 db schema version（升级即风险）、无历史库导出/导入、`app/integrated_app/data/` 与根 `data/` 双目录并存（cwd 相关路径解析副产物，已实际出现）、config 无版本迁移机制（`extra="ignore"` 只保证向后兼容，不保证语义迁移）。

### 8.5 权衡分析

**Storage cost vs User convenience**：
- 当前默认（14 天输出 + 10000 条历史 + 500MB 缓存声明）偏向便利性，合理
- 建议引入分层策略：输出缩略图/元数据永久保留（体积 KB 级），原分辨率输出按预算滚动——用户可"看到历史"但需重跑才能拿回大图，成本降至 1/1000

**Quality vs Performance trade-offs**：
- 现有降级阶梯（分辨率×0.75 → fp8 → 接受低质量输出）是纯性能导向，**质量损失无度量**——降级后用户拿到的是"更差但成功"的结果，且历史记录不标注"此结果经历过 OOM 降级"
- 建议最小改造：把降级事件写入 `parameters` JSON（一行代码级别），让血缘可解释；中期接入 PSNR/LPIPS 抽样评估，为"是否值得重试"提供依据

---

## 九、综合评分与优先级修复路线

### 9.1 评分矩阵

| 子体系 | 得分 | 一句话结论 |
|---|:--:|---|
| 1. 数据模型与 Schema | **55** | 运行时库优秀；版本化迁移与训练侧 versioning 双缺失 |
| 2. 数据质量管理 | **50** | 输入防线完整；输出质量零度量（视觉修复项目的核心空洞） |
| 3. 所有权与血缘 | **40** | 弱血缘（路径+参数+水印）；无内容寻址；训练血缘为零 |
| 4. 测试数据管理 | **30** | 占位资产；退化生成器有现成代码却未接入测试 |
| 5. 数据生命周期 | **80** | 全项目最强项，反模式指控大半不成立 |
| 6. 指标口径治理 | **60** | VRAM 口径是标杆；速度三口径并存；质量分不存在 |

### 9.2 修复优先级（按 ROI 排序）

| 优先级 | 事项 | 成本 | 理由 |
|:--:|---|---|---|
| **P0** | 补齐 `upload.py:198-201` 声称的 `max_upload_image_mb/video_mb` 配置字段（模型+yaml） | 30 分钟 | 已知配置漂移，承诺未兑现 |
| **P0** | history_db 加 `PRAGMA user_version` + 最小迁移框架 | 半天 | 桌面版升级的前置保险，越晚成本越高 |
| **P0** | OOM 降级事件写入历史 `parameters` | 1 小时 | 一行级改动，血缘可解释性质变 |
| **P1** | HistoryRecord 增加源文件 sha256 列 | 半天 | 弱血缘 → 内容寻址血缘 |
| **P1** | folder/batch 入口复用魔数校验 | 2 小时 | 堵住旁路校验缺口 |
| **P1** | 接入现成 `HierarchicalDegradationProcessor` 构建 golden 退化基准对 + CI 质量门禁 | 1-2 天 | 生成器已存在，只差接线；从根本上回答"改坏了没有" |
| **P1** | 缓存 `max_size_mb` 兑现实现（或删除配置） | 2 小时 | 第二处配置漂移 |
| **P2** | 训练 dataset manifest（数据集哈希+清单入 experiment_tracker） | 1 天 | 训练谱系从 0 到 1 |
| **P2** | 权重 sidecar metadata（训练日期/数据集/parent） | 1 天 | 同上 |
| **P2** | epoch checkpoint 纳入保留策略 | 2 小时 | TB 级风险点的最后一道闸 |
| **P2** | VRAM 泄漏自动告警（连续 N 任务峰值单调涨） | 1 天 | 数据已落库，只差检测规则 |
| **P3** | 输出水印绑定 task_id、输出→任务反查 API | 2-3 天 | 血缘可追溯闭环 |
| **P3** | 统一速度口径文档 + per-step it/s | 1 天 | 指标口径治理 |
| **P3** | `data/.seedvr2_secret` 权限收紧 + manifest 外置/签名 | 半天 | 安全加固 |

### 9.3 最终评语

该项目的数据治理呈现明显的**"运行时强、分析性弱"**特征：任务执行链路上的数据保障（断点、清理、重试、显存观测）达到了桌面级重型应用的良好水准，且有清晰的治理编号痕迹（P0-1/P1-7/P2-1）表明团队进行过专项治理；但**作为"视觉修复项目"最核心的两条数据链——"修复质量是否达标"与"这个输出/权重从哪来"——恰恰是最薄弱的**。前者的解法成本极低（退化生成器代码已在仓内），建议作为下一轮治理的第一刀。
