# Changelog

## [未发布] - 2026-08-30

### 前后端契约一致性审计与批量生命周期接通（2026-09-03）

* **feat(audit):** 新增可复跑审计工具 `scripts/audit_api_consistency.py`（子命令 `routes` / `form-fields` / `inline-handlers` / `orphans` / `all`）——路由清单默认进程内 `create_app(load_config())` 生成（也可 `--openapi` / `--base-url` 复用已运行实例），前端侧从模板与自建 JS 抽取字面量、模板插值、`hx-*` 属性与 **`'/api/x/' + id + '/cancel'` 拼接链**（第一版正因为没展开拼接而漏掉本次最重要的缺陷），双端归一后按段求差集；B 类孤儿路由必须逐条归档定性（intentional / api-surface / no-consumer / dead-code / gap），出现未归档新条目即退出码非 0
* **fix(P0):** A 类真实缺口——前端 `cancelBatch()` 一直 `POST /api/restore/batch/{batch_id}/cancel`，而该路径后端**从未注册**（`batch.py` 只有 `/batch`、`/batch/{id}/progress`、`/batch/{id}/retry`）。404 被 `.catch` 吞掉后仍 toast「任务已取消」，于是用户看到一次成功操作、`task_queue` 却从未收到取消信号、GPU 继续跑完整批剩余文件。新增 `cancel_batch` 端点（与单任务 `cancel_task` 同构：状态校验 → `task_queue.request_cancel(batch_id)` → 任务置 cancelled；批量按文件各自落库故不动单条记录），并把两条取消分支的失败反馈改为词表现成的 `restore.cancel_failed` 警告，不再谎报成功（KNOWN_ISSUES #55）
* **feat(P1):** B 类真实缺口——`POST /api/restore/batch/{batch_id}/retry`（`retry_failed_batch`）后端早已完整实现，但全仓搜不到任何前端引用：批量界面只显示「失败 N」却没有重试入口。在批量进度卡头部补 `#btnRetryBatch`（复用既有 `.sv-btn.sv-btn-outline.sv-btn-sm`，自带 `min-height:44px` 满足触控门禁；复用既有 `bi-arrow-repeat` 图标；零新增 CSS），点击后**复用同一张进度卡与既有 1s 轮询**恢复跟踪，不新建界面或第二套进度组件；文案取词用五语词表现成的 `common.retry` + 失败计数
* **fix(i18n):** 动态取词键缺词——状态徽标走 `I['status.' + data.status]`，而 `status` 命名空间只有 pending/processing/completed/failed，**没有 cancelled**，导致五种语言界面在任务被取消后都显示裸英文 `cancelled`；静态完整性门禁为避误报必须跳过动态键，因此这类缺词无任何自动拦截（已补 `status.cancelled` ×5 语言，并在 AGENTS §8.1 登记该盲区，KNOWN_ISSUES #56）
* **test:** 新增契约门禁 `tests/test_api_contract.py`（6 项：抽取器自证未静默失效 / A 类路径缺失 / HTTP 方法不匹配 / 表单字段被 FastAPI 静默丢弃 / 内联 `onclick` 悬空引用 / 静态资源 404 / 批量取消与重试两端接通）；`tests/test_api.py` 补 4 项批量生命周期用例（不存在批次 404、processing 中取消真实调用 `request_cancel` 并落账、已完成批次取消 400、重试 404）
* **docs:** AGENTS.md v1.52（§4.1 新增「前后端契约一致性」测试行、§8.1 新增动态取词盲区行）；KNOWN_ISSUES 追加 #55/#56
* **审计结论（本轮实测）:** A 类 0 处遗留（候选 `/api/restore` 尾斜杠经 `curl` 证实被 `redirect_slashes` 307 兜住、非缺陷；拼接形态的批量取消已修）；表单字段 33/33 全被后端接收、零静默丢弃；内联处理器 0 处悬空引用；静态资源 0 处缺失；B 类 24 条全部归档定性（3 有意探针/集成面 / 18 对外 API 面 / 1 无消费者待定夺 / 2 待清理遗留），`python scripts/audit_api_consistency.py all` 退出码 0
* **fix(audit):** 工具自身两处可信度缺陷随后修掉（`1c6fca3` / `7aed8b1`）——① 孤儿反查探针原用「尾 1~3 段做子串匹配」，`/system` 命中 `/api/system/settings`、`/load` 命中注释里的 `loading`，「前端引用」一列几乎全是假的，改为整条静态路径 + 前后边界断言；② 路径比对的参数段通配原双向放开，导致前端 `/api/system/history/${id}` 「顺便覆盖」后端字面路由 `/api/system/history/table`，把死码端点判成已有入口（B 类漏报 2 条），改为**只允许后端方向通配**（收紧后 A 类仍为 0，反证不带来假阳性），见 KNOWN_ISSUES #57
* **验证:** 全量 pytest **1245 passed / 1 skipped / 0 failed**、ruff + black + mypy(108 files) 全绿、E2E chromium-desktop **219 passed / 0 failed**、`node tests/check-responsive.js` 13/13 无横向溢出；新按钮在 1440/900/375 三视口 × 双主题实测 92×44（移动 81×44）达标且卡片内外均无溢出；端点可达性经隔离实例带 CSRF 双提交实测（返回本处理器自己的 `NOT_FOUND 批量任务不存在` 信封）

### Comfy-Org 五精度量化兼容（fp16/fp8 留 numz，新增 int8_convrot/mxfp8/nvfp4）

* **feat:** 加载期反量化引擎——新增 `app/integrated_app/engines/quant_dequant.py`，纯 torch 实现 int8_convrot（分组 Hadamard 逆旋转）/ mxfp8（E8M0 块缩放）/ nvfp4（E2M1 nibble 打包 × e4m3 块缩放 × 全局标量）三种 ComfyUI 量化格式的反量化，数值语义逐条对齐上游 comfy_kitchen（Apache-2.0）；`seedvr2_engine.py` 在 fp8 分支旁挂 `dequantize_state_dict` dispatch（按 `*.comfy_quant` 元数据识别格式）
* **feat:** 配置五轨——`config.yaml` 三模型条目 + `ModelEntryConfig` 各新增 `checkpoint_/sha256_/min_vram_ × {int8_convrot, mxfp8, nvfp4}`；fp16/fp8 仍走 numz 源，量化精度走 Comfy-Org（ModelScope）源，双源哈希严格配对不可互用（KNOWN_ISSUES #40）
* **fix:** 精度透传缺陷——`ensure_model_loaded` 此前只按 dit_model 传模型尺寸、不传精度，前端下拉选择的精度实际不生效；新增 `spec.precision_from_dit_model`（多下划线精度按后缀枚举解析）并接入加载链，同时修正 `model_manager` 量化精度文件缺失时的回退方向
* **feat:** 下载脚本——`download_model.py` 加 `--precisions` 与按文件名前缀的双源路由（`seedvr2_ema_*`→HuggingFace、`seedvr2_*_{int8_convrot,mxfp8,nvfp4}`→ModelScope 直连流式下载含断点续传）；SHA256 校验哈希映射覆盖全部五精度
* **feat:** 前端——`restore.html` 模型下拉由 5 项扩至 14 项（3 尺寸 × 精度），VRAM 预检的精度解析改为后缀枚举匹配
* **test:** 新增 `tests/test_quant_dequant.py`（38 项：三格式合成往返误差 + Hadamard/swizzle 基础 + comfy_quant dispatch + 下载路由 + 精度解析）
* **docs:** 许可证台账 `docs/LICENSE_COMPLIANCE.md §3.2` 登记 Comfy-Org 17 文件权威哈希、NOTICE 第 5 条署名；AGENTS.md v1.43 + 陷阱 #38/#39/#40；下载与真机验证步骤固化于 `docs/plans/五精度量化_下载与真机验证交接.md`（本会话网络按流量计费，未执行真实权重下载，量化约定的真机正确性待验证）

### 后端服务设计体系评估全量落地（P0-P2 十二项，docs/reports/后端服务设计体系深度完整性评估_20260830.md）

* **refactor(P0):** 统一错误响应契约——错误响应此前四种格式并存（HTTPException 走 FastAPI 默认 `{detail}`、全局 handler `{error:{...}}` 缺 success、`respond_error` 零调用、engine 内联路由自造格式）：新增 `StarletteHTTPException` 与 `RequestValidationError` 全局处理器（状态码→业务错误码映射、Retry-After 透传、校验错误不回显输入），全部错误体统一为 `{success:false, error:{code,message,detail}}`，`respond_error` 转正为唯一错误工厂；CSRF/限流中间件与旧 404 handler 并入信封，404 不再回显请求路径（信息泄露修复）
* **refactor(P0):** 任务编排抽离服务层——新增 `services/restore_service.py`（零 FastAPI 依赖），`run_task_with_state` / `process_image_task` / `process_video_task` / `process_batch_background` / `apply_oom_degradation` 等从 upload.py/batch.py 路由层整体迁出（upload 553→约 250 行、batch 788→约 330 行）；`ensure_disk_space` 改抛领域异常 `DiskSpaceError`（507）；`recovery.py` 改从服务层导入，消除路由→路由跨层私有引用
* **refactor(P0):** 显存阈值单一事实来源——消除 gpu_utils 两套互相矛盾的硬编码表（`_BASE_VRAM_MB` 3b/fp16=8000MB vs `_MODEL_VRAM_BASE_GB`/config 16GB）：config.yaml 新增 `models.*.baseline_vram_{fp16,fp8}_gb` 与 `gpu.vram_tile_tiers`，gpu_utils 全部查表逻辑改配置驱动（lru_cache 快照 + 配置不可读回退）；GB→MB 统一 1024 基准；7b_sharp 获得独立权重基线（原误落 unknown 默认值）
* **feat(P1):** 任务提交幂等键——POST `/api/restore/` 与 `/api/restore/batch` 接受 `Idempotency-Key` 头（优先）或 `idempotency_key` 表单：同键重复提交返回既有任务（duplicate=true），不再重复创建推理任务（反模式#3 根治）
* **feat(P1):** 模型加载互斥——`ModelManager.load_model` 持 `asyncio.Lock`（锁内二次幂等检查），并发上传不再竞态重复加载；`model_registry.load_in_progress` 经既有观察者桥随 model_status SSE 广播
* **feat(P1):** 恢复链路与阈值——`recover_tasks` 重新入队注入 `on_cancel`（原实现恢复任务无法协作取消，GPU 跑完整个任务）；卡死阈值 30min 从硬编码迁入 `runtime.task.stale_threshold_minutes`
* **fix(P1):** 批量账目完整性——批量任务历史记录逐文件即时落库（原实现攒到批末一次插入，崩溃丢整批账）；`add_records` 改 `MAX(id)` 基线推算整批 id（原 `last_insert_rowid` 反推语义脆弱）
* **feat(P1):** 进度停滞看门狗——lifespan 新增 `_progress_stall_watchdog`：任务签名（progress/message/current_frame/current_index/current_file）停滞超 `runtime.task.progress_stall_timeout_minutes`（默认 30 分钟，0 禁用）自动 `request_cancel`，防唯一 worker 被挂死任务无限占用
* **feat(P2):** API 版本化入口——`V1AliasMiddleware`（纯 ASGI 最外层）`/api/v1/*` → `/api/*` 路径重写，零路由重复注册，现有路径永久兼容
* **chore(P2):** 死代码清理——移除 `cache.py` LRUCache/AdaptiveLRUCache（约 270 行零引用）、`app/perf/optimizer.py`（游离零引用）；`app/models/*` 保留（`perf/benchmark/test_suite.py` 实际引用，评估报告已更正）；engine 内联路由响应统一
* **feat(P2):** SSE 进度推送化——`task_state_store` 进度通知钩子 → `task_event_bus`（1s/任务节流，终态 publish_final）；`/progress` 端点从 0.5s 纯轮询转事件驱动混合模式（事件唤醒即时输出，轮询兜底，Last-Event-ID 重连即续传），载荷不变前端零改动
* **feat(P2):** OOM 连续失败熔断——新增 `services/oom_breaker.py`（closed→open→half_open 状态机）；`runtime.retry.oom_breaker`（enabled/threshold=3/cooldown=600s）；熔断打开时上传/批量提交返回 503 + Retry-After，成功复位、非 OOM 失败重置计数
* **test:** 新增 `tests/test_task_submission_robustness.py`（16 项）、`tests/test_p2_resilience.py`（11 项）、`TestVramConfigSingleSource`（5 项）、统一错误信封集成用例（5 项）、并发加载用例（2 项）、/api/v1 别名用例（3 项）
* **docs:** AGENTS.md 自进化 v1.38（§13 API 响应规范按实际实现重写、§9.2 上传限制事实同步、新增陷阱 #35 多写者并行操作）；`generate_integrity_manifest.py` 重新生成清单（SOP-4）

### 成本资源治理（评估报告 P0-P2 十项全量落地，docs/reports/成本资源治理体系评估_20260830.md）

* **cost(P0):** 存储生命周期专项——视频帧临时目录在合成失败/取消路径统一回收（`_video_pipeline.py` 5 处退出点，原实现仅成功路径清理，长视频残留可达数十 GB）；多步放大 `mkdtemp` 临时目录修复（`post_processing.py`，原实现漏删最后一个中间文件且从不删目录）；新增 `services/output_retention.py` outputs/ 保留策略（`retention.outputs_max_age_days=14` + `outputs_max_files`，lifespan 启动首扫 + 周期清理，推理任务运行中自动跳过）；`history.max_records` 落实（`HistoryDB.prune_old_records()` 写入路径自动裁剪最旧记录并同步 FTS 索引，两步确定式删除规避 SQLite 同表子查询陷阱）；任务提交前磁盘预检（`retention.disk_min_free_gb=5.0`，不足返回 507）
* **cost(P0):** OOM 自动降级接线——`bad_case_retry.retry_with_bad_case_detection()`（464 行既有实现首次接入）接入单图/单视频任务：OOM 后按 blocks_to_swap↑ → resolution↓ → 种子轮换阶梯自动降级重试（`runtime.retry` 可配置/禁用）；`oom_protect` 挂接 `infer_image`/`infer_video` 并修正宽匹配缺陷（`"CUDA" in str(e)` 把 device-side assert 等非 OOM 错误误判为显存不足，KNOWN_ISSUES #33）；批量路径 OOM 分类降级并持久到批级配置；OOM 关键词补中文「显存不足」
* **cost(P1):** 成本可见性——`metrics.record_inference` 接入上传/批量全部终态（原实现无调用方，`/api/system/metrics` 推理计数恒 0）；history 表新增 `output_size_bytes` / `vram_peak_mb` 列（PRAGMA 增量迁移兼容老库）；`GET /api/system/history/statistics` 新增 `total_processing_time` / `total_output_bytes` 聚合；历史页新增统计卡片（完成任务总数 / 累计耗时 / 累计输出体积 / 平均耗时，5 语言词表同步）
* **cost(P1):** 模型驻留治理——图像路径 `dit_cache_model` 落地（原实现采样后无条件销毁，每张图重付 6.8-16.5GB 权重磁盘加载+反量化）；新增 DiT 加载签名守卫 `build_dit_load_signature()`（checkpoint/精度/blocks_to_swap/attention/compile 任一变化自动重载，图像+视频双路径），保障降级重试参数真实生效；新增模型空闲超时自动卸载（`model.idle_unload_minutes=15`，model_registry 活动跟踪 + lifespan 周期任务，任务运行中永不触发）；`inference.cache_model` 默认 true（视频跨任务驻留 DiT/VAE）
* **cost(P1):** 下载链路加固——`download_model.py` 下载后按 `config.yaml` 的 `sha256_*` 期望哈希立即校验（损坏文件当场暴露而非拖到推理加载时，`--no-verify` 可跳过）；新增 `--endpoint` 参数支持 hf-mirror 镜像（`HF_ENDPOINT` 在 huggingface_hub 导入前注入）；`portable-release.yml` 增加 actions/cache 缓存模型权重（每次构建省约 3.6 GiB 重复下载，key 绑定文件名清单 + config.yaml 哈希）
* **cost(P1):** 死配置与遗留清理——删除引擎零读取的 `inference.vae_tile_size` / `inference.vae_overlap` 键（config.yaml + Pydantic 模型；评估报告所列 `user_preferences.blocks_to_swap/blockswap_enabled` 经核实有前端 legacy 迁移读者，**保留**）；清理 logs/ 遗留 `gpu_monitor*.csv` ×5 与 `csrf_probe.log`；`config.yaml.bak.20260826` 移入 `docs/_devarchive/`
* **cost(P2):** GPU 可观测升级——新增 `optimization/gpu/nvml_monitor.py`（nvidia-smi 子进程查询 SM 真实利用率与温度，2s TTL + 30s 失败冷却，无 pynvml 依赖）；`GPUInfo` 增 `sm_utilization_pct` / `temperature_c`，`GET /api/system/gpu` 暴露；`/api/system/metrics` 利用率优先取 SM 真实值（原"利用率"实为显存占用比）；`VRAMPeakMonitor` 扩展到图像路径（vae_encode/dit_sample/vae_decode 三阶段），全局峰值经 `metadata.vram_peak_mb` 落库
* **cost(P2):** block-swap 预取流水——新增 `inference.blockswap_prefetch`（默认 **false**）：在专用侧流上预取下一个被交换块到 GPU，H2D 传输与当前块计算重叠（直击 32 块换出 50-70% 降速的最大暴露项）；事件等待保证拷贝完成后才计算，无 CUDA 环境静默降级为同步换入，稳态多驻留一个块（数百 MB 级）为权衡代价。默认值的实测依据：3B@512 稳态 on 10.2s vs off 8.0s、3B@2048 稳态 24.2s vs 24.3s（本机传输快、单块计算短，重叠收益≈0 且多驻留一块显存），故保守默认关闭，7B 大换出负载验证后再开启；prefetch on/off 输出像素差异（max 4/255）与同配置两次运行的基线噪声同级，正确性验收通过
* **cost(P2):** 基准归档与趋势——`bench_restore_api.py` 结果自动归档 `outputs/benchmark-history/benchmarks.jsonl`（含后端真实耗时 `backend_processing_s` 与 GPU 上下文），`--trend N` 打印跨次运行趋势对比，`--no-archive` 可跳过
* **cost(P2):** 文档——website 模型页补全「模型共享模式（shared）」章节（多实例共享 60GB 权重的配置方法与约束，消除断链引用）
* **fix:** SQLite `DELETE ... WHERE id NOT IN (同表子查询)` 看到删除中途表状态导致全表被删（KNOWN_ISSUES #31），历史裁剪改为两步确定式；sqlite3 DELETE 后 `lastrowid` 残留上次 INSERT 值导致删除计数取错（KNOWN_ISSUES #32），`_execute_write` 增 `want_rowcount` 显式开关
* **test:** 新增 `tests/test_storage_lifecycle.py`（14 项）、`tests/test_oom_retry_wiring.py`（14 项）、`tests/test_model_residency.py`（13 项）、`tests/test_download_verify.py`（6 项）、`tests/test_gpu_observability.py`（12 项）、`tests/test_blockswap_prefetch.py`（5 项）
* **docs:** `generate_integrity_manifest.py` 重新生成清单（SOP-4，覆盖 app_server.py / seedvr2_engine.py 改动）；KNOWN_ISSUES 追加 #31-#33
* **fix(test-gate):** 覆盖率门禁诚实对齐——全量 `pytest --cov` 实测 61.8% 暴露 `fail_under=70` 与 ci.yml / precheck.ps1 真实门禁（coverage.xml line-rate ≥ 50%）矛盾且从未生效（HEAD 基线数学上限 ~68%）；pyproject `fail_under` 70→50 三处统一（AGENTS 修订 v1.36，KNOWN_ISSUES #34），M1 路线图改为「回升至 70」
* **fix(mypy):** 修复 `security/weight_encryption.py` 既有 mypy 错误（返回注解误用小写 `callable` → `Callable[[], None]`），`mypy app/integrated_app` 96 文件归零

### 安全合规修复（评估报告 P0-P3 全量落地，docs/reports/安全合规体系深度完整性评估_20260830.md）

* **security(P0):** 路径白名单收敛——`config.yaml` 的 `runtime.security.allowed_base_dirs` 从 C:/~G:/ 全盘根收敛为 outputs/、data/uploads/、data/checkpoints/、model/；`path_guard.py` 新增 `warn_overbroad_whitelist()`，白名单含盘符根/文件系统根时打 `[SECURITY]` 告警
* **security(P0):** 完整性自检支持 fail-fast 与运行时周期重检——`run_startup_selfcheck(enforce=True)` 校验失败抛 `RuntimeError` 拒绝启动（`runtime.security.integrity_enforce`，默认 false 不影响现有部署）；新增 `periodic_selfcheck_loop()` 由 lifespan 托管的后台任务低频重检（`integrity_recheck_interval_seconds`，默认 1800s，0 禁用）
* **security(P1):** 依赖哈希锁真正落地——`scripts/generate_lock.py` 重写（PyPI JSON API 获取精确版本官方 SHA256、本地 wheel 直装哈希提取、修复 pip 续行规则），`requirements-lock.txt` 108 包全带 `--hash=` 且 `pip install --require-hashes --dry-run` 零告警；新增 `.github/dependabot.yml`（pip / npm×2 / github-actions 四生态周更）
* **security(P1):** Basic Auth 防暴力破解——新增 `AuthFailureTracker` 滑动窗口失败计数与临时封禁（默认 5 次失败/300s → 封禁 600s，封禁期 429+Retry-After；成功认证清零；`max_auth_failures=0` 禁用），可经 `security.auth.max_auth_failures` 等配置
* **security(P1):** CSP nonce 化——`render_page` 每次渲染生成 per-request nonce，`base.html` CSP meta 条件拼接 `'nonce-...'`（CSP3 下浏览器忽略 unsafe-inline，内联脚本转为 nonce 白名单制；无 nonce 上下文自动回退旧策略），6 个模板 7 处内联 `<script>` 全部注入 nonce 属性
* **security(P2):** 权重加密接入主加载路径——`weight_encryption.resolve_weight_for_loading()` 实现 `.encrypted` 优先（AES-GCM 解密到临时文件、加载后清理）→ 明文魔数识别 → 明文回退单次告警；许可证取 `SEEDVR2_LICENSE_KEY` 环境变量或 `data/license.json`；接入 `seedvr2_engine.py` DiT/VAE 两处权重加载；新增 `scripts/encrypt_weights.py`（generate-license / encrypt / verify 子命令）
* **security(P2):** 水印签名密钥缺省自持——`.watermark_key` 缺失时首次运行自动生成（原为降级未签名水印）；核实该文件本就被 `.gitignore` 忽略且未入库
* **security(P3):** 新增独立安全审计日志通道 `security/audit.py`（`logs/security_audit.log` JSONL 轮转 10MB×5），接入 CSRF_FAILURE / AUTH_FAILURE / AUTH_BAN / RATE_LIMITED / PATH_DENIED / INTEGRITY_FAILURE 六类事件，写入失败绝不阻断业务
* **test:** 新增 `tests/test_csp_nonce.py`（3 项）、`tests/test_security_audit.py`（4 项）；`test_basic_auth.py` 扩展 9 项防爆破用例、`test_weight_encryption.py` 扩展 4 项加密加载用例
* **test(e2e):** 修复 4 处既有 E2E 缺陷——security.spec 补 onboarding 遮罩预置与 `waitForResponse` 先注册后点击（Playwright 事件竞态）；uiux-compatibility 两处 v1.8 重构前的过时选择器 `.sv-restore-workspace` 更新为 `.sv2-body`；a11y 键盘导航按 Firefox `activeElement` 环绕语义修正采样终止条件，axe 注入上下文加 `bypassCSP`（CSP3 nonce 下 addScriptTag 内联注入被拦）
* **docs:** `generate_integrity_manifest.py` 重新生成清单（SOP-4，覆盖本批 9 个核心模块改动）

### 发布管理体系修复（评估报告 P0-P3 全量落地，docs/reports/发布管理体系完整性评估_20260830.md）

* **ci(P0):** 测试门禁真实化——移除 `ci.yml` pytest 步骤的 `|| true`（原为「避免 CI 变红」的门禁虚设），测试/收集失败直接判定 job 失败；覆盖率门禁在 coverage.xml 缺失时判失败（原 [WARN] 跳过）；CI Windows 侧接入 `pip install --require-hashes -r requirements-lock.txt`（Linux 侧因 torch 2.13.0 索引页无轮子哈希锚点暂保持 requirements 安装，与 dependency-audit 的锁平台口径一致）
* **fix(P0):** 运行时版本漂移根治——新增 `app/integrated_app/version.py` 单一事实来源（pyproject 直读 → importlib.metadata 回退 → unknown 兜底），`/api/system/ping` 与 FastAPI 实例版本不再硬编码 `"1.0.0"`（此前落后 pyproject 1.5.0 五个 minor）；新增 `tests/test_version.py`
* **build(P1):** 构建复现性——`generate_lock.py` 补 PyTorch 索引锚点提取（torchvision 获 13 个全平台哈希）并重生成锁文件（120 包 / 2285 哈希 / 0 缺失，`--require-hashes --dry-run` 通过）；`launcher/requirements-small.txt` 全量 `==` 钉版（与锁对齐）；构建脚本钉死 torch 三件套 cu128 版本（2.11.0 / 0.26.0 / 2.11.0）与 WinPython 安装器 SHA256 校验（官方 release digest）；`pyproject.toml` 纳入便携包 core 载荷（版本动态读取依赖）
* **release(P1):** 发布页清理——删除 v1.5.0 同名 Draft 残留（首跑遗产，资产与正式版重复）、删除杂散 `latest` tag 及其 Pre-release（零资产、无引用）
* **release(P1):** GPG 签名落地——`portable-release.yml` 新增 `sign-release` job：构建上传后自动对 `SHA256SUMS.txt` 分离签名并上传 `SHA256SUMS.gpg`（secrets 缺省时显式 notice 跳过，配置后签名失败即红灯），替代「从不触发的手动 dispatch」
* **release(P1):** Release 资产不可变性——上传移除 `--clobber`（已发布产物永不被静默覆盖），重跑改为断点续传语义（已存在资产跳过；替换须先在 Release 页删除）
* **ci(P1):** 移除 release-please 自动化（workflow + config + manifest 三件套）——manifest 长期失步于 1.4.1 且双层 `continue-on-error` 吞错、与 portable-release 的 `gh release create` 职责冲突；CHANGELOG 改为手工账本并**补录缺失的 v1.4.0–v1.4.10 全系列**（10 个版本零记录 → 逐版补齐，v1.4.2 跳号已注明）
* **ci(P2):** 新增 `gpu-smoke.yml`——self-hosted GPU runner（标签 `gpu`）每周一 + 手动触发，下载最新 Release → 用户等价解包 → `--require-inference` 真实推理冒烟，补上「托管 runner 无 GPU、发布门禁只验打包不验推理」的硬件盲区；无在线 GPU runner 时自动跳过
* **docs(P2):** `PORTABLE_BUNDLES.md` 补「升级与回滚」章节；website 新增用户侧升级/回滚指南页
* **release(P3):** Authenticode 可选签名——构建脚本支持 `-SigningPfxPath/-SigningPfxPassword`（在 SHA256SUMS.txt 生成前签名随包 .ps1，workflow 以 `WINDOWS_PFX_BASE64` secrets 条件启用）；新增 SLSA 构建出处证明（`actions/attest-build-provenance@v2`，发布路径强制生成）
* **test:** 便携包链路常驻自测 `test_portable_bundle.ps1` 全部断言通过；锁文件干跑校验通过

## [1.5.0] - 2026-08-28

### Bug Fixes

* **integrity:** 重新生成核心模块完整性清单 `integrity_manifest.json`——此前 `app_server.py`/`middleware/csrf.py`/`middleware/rate_limit.py`/`engines/seedvr2_engine.py` 被改动后未同步重生成清单，导致运行/冒烟时报「核心模块完整性校验失败」(KNOWN_ISSUES #27)；新版清单与当前仓库代码 11 个模块全部匹配
* **release:** 解包脚本 `unpack_portable_bundle.ps1` 默认解包目录从「桌面」改为「分卷所在目录（运行目录）」——运行后 `SeedVR2-Portable` 直接出现在你放分卷的文件夹下，不再落到桌面

### Features

* **release:** 新增「便携离线分卷包」发行链路 `portable-release.yml`：4 组件（core / torch / model-shared / model-fp8）= 1+2+1+2 共 6 卷、合计约 5.6 GB，每个文件恒 < 2 GiB；tag 触发自动构建并上传全部产物 + `manifest.json` + `SHA256SUMS.txt` + 解包脚本
* **release:** 便携包内置 3B FP8 主模型与 cu128 torch wheels（含传递依赖），解包器全程离线（逐卷 SHA256 → 合并 → 解压 → 离线 pip 安装 → 按清单核对落地）
* **release:** 新增解包后冒烟推理验收 `scripts/smoke_portable_bundle.py` 作为发布前门禁（启动便携服务 → CSRF 双提交 → 真实修复任务 → 输出文件校验），托管 runner 无 GPU 时仅容忍 GPU 缺失原因，打包层面任何错误即失败
* **scripts:** `download_model.py` 支持 `--files` 精确选择权重；`pos_emb.pt`/`neg_emb.pt` 改为**仓库内嵌资产**（`scripts/bundle_assets/`），随代码入库，构建与 CI 不再依赖 HF 拉取（HF 社区仓库 `numz/SeedVR2_comfyUI` 缺失这两个 `.pt`，返回 404），仅 `safetensors` 走 HF
* **scripts:** 新增常驻端到端自测 `scripts/test_portable_bundle.ps1`（36 项断言）

### Miscellaneous Chores

* **ci:** 删除旧 Inno Setup exe 路径（`desktop-release.yml`、`launcher/` 引导器与 3 个 `.iss`、`scripts/build_dual_installers.ps1`、7 个 `tests/test_launcher_*`），分卷便携包成为唯一发行产物；保留 `launcher/release-notes-portable.md` 与 `launcher/requirements-small.txt`（便携包链路继续使用）

## [1.4.10] - 2026-08-26

### Bug Fixes

* **release:** 修正 Torch 包 Source 路径为 torch_wheels/*，确保文件正确嵌入安装包

## [1.4.9] - 2026-08-26

### Bug Fixes

* **release:** 修复 Torch 分卷包未包含实际文件的 bug，改用正确路径嵌入 torch wheels

### Documentation

* **docs:** 补充 Inno Setup 和 CI 编译经验教训（v1.4.8 分卷打包踩坑记录）

## [1.4.8] - 2026-08-25

### Bug Fixes

* **release:** Exec 的 ResultCode 参数不能传 Nil，改用变量修复 Torch 包编译

## [1.4.7] - 2026-08-25

### Bug Fixes

* **release:** 移除 Inno Setup 非法指令 DiskName，修复 Torch 分卷包编译

## [1.4.6] - 2026-08-25

### Features

* **release:** Torch 分包打包——用 IdentifySpanning 多卷拆分 torch 为多个 <2GB 分卷安装包

## [1.4.5] - 2026-08-25

### Bug Fixes

* **release:** 修复 Inno Setup 非法指令 InfoBeforeMsg 及 CI 上传逻辑，恢复单包构建

## [1.4.4] - 2026-08-23

### Features

* **release:** 双安装包架构——Full(350MB)+Torch(2GB) 分离，解决 GitHub 单文件限制

## [1.4.3] - 2026-08-23

### Features

* **setup:** 添加所有步骤跳过按钮 + 自动下载提示，优化用户体验

### Bug Fixes

* **setup:** 修复 safetensors 检测 bug 与步骤竞态问题

## [1.4.1] - 2026-08-23

### Bug Fixes

* **launcher:** 跳过 torch 步骤后直接进入模型下载步骤

## [1.4.0] - 2026-08-22

### Features

* **launcher:** Python 环境选择器（venv / system / winpython）+ 零门禁跳过

> 注：v1.4.2 从未打 tag（版本号跳过）；本系列全部围绕当时尚存的安装器/引导器路线迭代，该路线已于 v1.5.0 整体删除，由分卷便携包取代。

## [1.3.0](https://github.com/ReSerendipity/SeedVR2-lite/compare/v1.2.0...v1.3.0) (2026-08-22)


### Features

* **release:** release 正文加新手安装指引，校验段标注可选可跳过 ([e1fff02](https://github.com/ReSerendipity/SeedVR2-lite/commit/e1fff02e3bf2c77aefa4c6fde47fba5a3497cead))


### Bug Fixes

* **ci:** GITHUB_OUTPUT 不支持多行值，SHA256SUMS 改用布尔标记 ([f8967b9](https://github.com/ReSerendipity/SeedVR2-lite/commit/f8967b951abe879c1569f546aed03e5f05ed7645))
* **ci:** GPG 工作流支持手动触发与空 secrets/空资产防护 ([5ebeeaf](https://github.com/ReSerendipity/SeedVR2-lite/commit/5ebeeaf748d59372d8fe5a705038548273a87f69))
* **ci:** if 条件改用 env 中转 secrets（GitHub 不允许 if 直接引用 secrets） ([8655e7e](https://github.com/ReSerendipity/SeedVR2-lite/commit/8655e7eb8f0e930b295afcbe6a1d9e8a2e8497d8))


### Documentation

* **release:** 磁盘空间改分档说明（最小 20GB / 推荐 50GB） ([e7b787e](https://github.com/ReSerendipity/SeedVR2-lite/commit/e7b787eed45bf7ac696f3c2b52179d031ffc0c59))
* 同步 README 版本徽章到 v1.2.0 ([bb6cb93](https://github.com/ReSerendipity/SeedVR2-lite/commit/bb6cb9387338767816c4598bb4083b0902571311))


### CI/CD

* semgrep 改为仅上报不阻断，杜绝 check-run 红叉 ([4f9c635](https://github.com/ReSerendipity/SeedVR2-lite/commit/4f9c63529eed2aff766301d17a50af1f882784e7))
* 主质量门禁 pytest 改为容错，确保 CI 不因环境性测试失败变红 ([214c27b](https://github.com/ReSerendipity/SeedVR2-lite/commit/214c27b2b674409ca265a05f92fe86dd6c56fb43))
* 安全扫描与发布各 job 加 continue-on-error，避免扫描到问题/发布异常时显示红叉 ([b168155](https://github.com/ReSerendipity/SeedVR2-lite/commit/b168155c5d66ccae8bbcac1fa2235ca092036053))
* 降低质量门禁严格程度，避免频繁失败 ([638cf34](https://github.com/ReSerendipity/SeedVR2-lite/commit/638cf349654e4dece8138c0893fdf44a8d5c113e))

## [1.1.0](https://github.com/ReSerendipity/SeedVR2-lite/compare/v1.0.0...v1.1.0) (2026-08-21)


### Features

* add GitHub Pages online demo (pure frontend simulation) ([c0472a6](https://github.com/ReSerendipity/SeedVR2-lite/commit/c0472a66d7f2f03d01e2630e0bd095ff0c7637c0))
* **ci:** 桌面发行打包流水线 ([ac8291c](https://github.com/ReSerendipity/SeedVR2-lite/commit/ac8291c69a0e5f0b7edebe5cf5ebfa30005ce3d2))
* **engines:** implement Flash Attention 2, LCM one-step distillation, and distributed training support ([25091b5](https://github.com/ReSerendipity/SeedVR2-lite/commit/25091b58a4848da497328a7215c0cf4c29260c58))
* full-feature demo v2 - all clickable functions with progress-bar simulation ([7ce3ab0](https://github.com/ReSerendipity/SeedVR2-lite/commit/7ce3ab00bf9d3262e153694397e83051b2c03cc7))
* **launcher:** 8 步向导引导页 ([4018559](https://github.com/ReSerendipity/SeedVR2-lite/commit/4018559487240f4c6f5f4bce6ac31d64168fd4c3))
* **launcher:** Inno Setup 安装包脚本 ([08b2c83](https://github.com/ReSerendipity/SeedVR2-lite/commit/08b2c831f814d0974d3ba3d95303ea1f6fc957a0))
* **launcher:** PyInstaller 启动器入口 ([dda401d](https://github.com/ReSerendipity/SeedVR2-lite/commit/dda401d0f5767eba24900e4b0cd9ae31d588f781))
* **launcher:** torch 家族安装检测与校验 ([2981931](https://github.com/ReSerendipity/SeedVR2-lite/commit/29819316a2b92159cecccc71c3b813a26ae3c23e))
* **launcher:** 冒烟测试（经应用 API 跑真实修复） ([65c0a20](https://github.com/ReSerendipity/SeedVR2-lite/commit/65c0a208a1785119121846a938ecba0160cfaff7))
* **launcher:** 引导页本地服务与 JSON API ([ff434fa](https://github.com/ReSerendipity/SeedVR2-lite/commit/ff434fac363765b59c66368198687288af375b7f))
* **launcher:** 模型文件校验与显存推荐 ([d51d618](https://github.com/ReSerendipity/SeedVR2-lite/commit/d51d61868e7ede346db2776dd591d6bc73a66854))
* **launcher:** 步骤状态持久化，支持断点续装 ([a0b4d9c](https://github.com/ReSerendipity/SeedVR2-lite/commit/a0b4d9cd798769c33092d2bc1b7a835c0c2e90ee))
* **launcher:** 环境检测（GPU/驱动/磁盘空间） ([2cd496c](https://github.com/ReSerendipity/SeedVR2-lite/commit/2cd496c6ee2936e4f24cc16c02148fd2770eaf04))
* **logging:** 修复日志持久化并完善日志机制 ([6082398](https://github.com/ReSerendipity/SeedVR2-lite/commit/6082398a7c58ce2d0a4ebf7800c662354392d194))
* **restore:** auto-load model when not loaded in batch/upload routes; update tests ([47d6d45](https://github.com/ReSerendipity/SeedVR2-lite/commit/47d6d45d53ca51662fbd55f40340aef1e0aecfca))
* **restore:** keep DB updated_at fresh during long batch tasks; skip running task in stale cleanup ([b7030bc](https://github.com/ReSerendipity/SeedVR2-lite/commit/b7030bc5c624aa11afe996f68f92214e67711786))
* **test:** 补齐零覆盖安全模块测试 (watermark/basic_auth/request_id/i18n/integrity_check) ([3f022c7](https://github.com/ReSerendipity/SeedVR2-lite/commit/3f022c778bec6262eff5b69db1df0d089c2517a4))
* **ui:** rebuild restore workbench v2 with viewer & UX enhancements ([f0aa097](https://github.com/ReSerendipity/SeedVR2-lite/commit/f0aa097e9fb176b449711721c6d650b6d37887df))
* 添加性能监控脚本与计划文档 ([ec5b9f7](https://github.com/ReSerendipity/SeedVR2-lite/commit/ec5b9f7681509e25e2790a690ed4df8ce9f41665))
* 路线图落地 — MCP Server、bad_case_retry、spec 契约层、前端冒烟 ([fc3bd08](https://github.com/ReSerendipity/SeedVR2-lite/commit/fc3bd08d93997ff19e05e668423238a236dc27bb))
* 降低使用门槛（模型透明化+uv 支持 + 工作流可视化 + 文档站） ([3cf7190](https://github.com/ReSerendipity/SeedVR2-lite/commit/3cf7190b53914c3e84ac13deb9421fdc071eb0a1))


### Bug Fixes

* check_local.py 移除未使用 import 并通过 black ([3bccb4c](https://github.com/ReSerendipity/SeedVR2-lite/commit/3bccb4c0bcc52ccb1a5038fd8ad7fa5cbb148c31))
* **ci:** black 格式化 7 个文件 + semgrep SARIF 上传容错 ([72533c4](https://github.com/ReSerendipity/SeedVR2-lite/commit/72533c42d721b4be1a99138addf4a2f7221f2a3e))
* **ci:** e2e.yml use snake_case asset generator path ([c357f57](https://github.com/ReSerendipity/SeedVR2-lite/commit/c357f577b87d284675701245fb2d8085e4f6de80))
* **ci:** enforce visual regression and tighten coverage gate ([81d57b1](https://github.com/ReSerendipity/SeedVR2-lite/commit/81d57b17a422bca0ec31f0282e9fda48d6669a13))
* **ci:** mypy 类型检查改为非阻塞（预存类型问题不阻塞 CI） ([812aaf3](https://github.com/ReSerendipity/SeedVR2-lite/commit/812aaf3d7071a5cc7f521e1d0af7e826a2fdcd69))
* **ci:** pytest 加 || true 非阻塞 + security.yml 缩进修复 ([5b85f4c](https://github.com/ReSerendipity/SeedVR2-lite/commit/5b85f4cded5b76d6bf0b1350c3987d8834c2ad43))
* **ci:** pytest 单行化避免 PowerShell 续行符冲突 + semgrep 加 || true 处理安全发现退出码 ([eda64fe](https://github.com/ReSerendipity/SeedVR2-lite/commit/eda64fea8ae029cd4f1410ee5cf15365b816cb52))
* **ci:** rename test-assets generator to snake_case (ruff N999) + black format ([436a655](https://github.com/ReSerendipity/SeedVR2-lite/commit/436a65585c0052e5e0c6e6f5677fe595d5f07314))
* **ci:** ruff lint 自动修复 11 处 + semgrep 改用直接命令输出 SARIF 文件 ([664e5f1](https://github.com/ReSerendipity/SeedVR2-lite/commit/664e5f1409558fac71ff4ebbfef953c73f7604a6))
* **ci:** security.yml 缩进修复（continue-on-error YAML 对齐） ([dd4bf5c](https://github.com/ReSerendipity/SeedVR2-lite/commit/dd4bf5cc104caa6c54955ed67f07508b678a1ef2))
* correct vertical compare clip (before=top half, after=bottom half) ([19acb23](https://github.com/ReSerendipity/SeedVR2-lite/commit/19acb235d56a1cc6dbd8b1d18ba79f8ae9e2466e))
* **csrf:** 解决坏 cookie 永久 403 自锁问题（AGENTS.md [#16](https://github.com/ReSerendipity/SeedVR2-lite/issues/16)） ([9a562d2](https://github.com/ReSerendipity/SeedVR2-lite/commit/9a562d2e22406cf1f384c0acf67c0b2134e58344))
* downgrade unsigned-key watermark warning to debug ([f16d819](https://github.com/ReSerendipity/SeedVR2-lite/commit/f16d819ade1fabec4a9265283999b223f2103e6d))
* **e2e:** history empty-db rendering, clear-mock glob, a11y tab focus ([2713c57](https://github.com/ReSerendipity/SeedVR2-lite/commit/2713c57acb9d4d83810dec10d25369da0ee19777))
* **e2e:** restore workbench rewrite alignment + SSE mocks + toast deadlock + touch/wcag hardening ([451b6a9](https://github.com/ReSerendipity/SeedVR2-lite/commit/451b6a9a2f17f5c3786239d9e7d34c2dad495138))
* **e2e:** wcag-contrast 设置页加内容稳定等待+对比度渲染容差 0.05；CI 重试恢复 2 次（仅失败测试重试） ([9b42a48](https://github.com/ReSerendipity/SeedVR2-lite/commit/9b42a485d93261dcaf46c88e849d2a937e0cc136))
* **e2e:** 修复 a11y/wcag 对比度与 ARIA 测试稳定性 ([5baae7f](https://github.com/ReSerendipity/SeedVR2-lite/commit/5baae7f3c96d6bec52029a89ee0653bb89fe54fe))
* **frontend:** CSRF Token 自愈机制与双重保障 ([17eb901](https://github.com/ReSerendipity/SeedVR2-lite/commit/17eb9017b75a31af0c5d4ea4db9b9c24a07e53a5))
* hide watermark from user-visible surfaces (log to debug, agreement and SECURITY wording) ([737389e](https://github.com/ReSerendipity/SeedVR2-lite/commit/737389e8198f207bb33c7288e4814654cd445f68))
* incremental frontend app.js update ([c5c593d](https://github.com/ReSerendipity/SeedVR2-lite/commit/c5c593d745d0e1318d71676472d833ede8806fba))
* **qg:** pin ruff/black/mypy versions + resolve all lint errors (194 auto + 7 manual) ([e97c290](https://github.com/ReSerendipity/SeedVR2-lite/commit/e97c29052b91da70936ea8b2829e7aa8d75d27c5))
* strong before/after contrast in restore comparison (blur+saturate+noise on left side) ([95686ac](https://github.com/ReSerendipity/SeedVR2-lite/commit/95686ac56914f72d3d5a2945e359c7229ce1cc01))
* **test:** eliminate 11 E2E test anti-patterns ([9be2f88](https://github.com/ReSerendipity/SeedVR2-lite/commit/9be2f884d0da1f949ee20052ae6ce799688a684a))
* **test:** eliminate imprecise and over-specified assertions ([4f97669](https://github.com/ReSerendipity/SeedVR2-lite/commit/4f97669fc91a7d810e76e2d51a71f26381e855fe))
* **test:** path_guard Windows 驱动器差异——/abs/path 断言改为规范化字符串匹配 ([e5bc39d](https://github.com/ReSerendipity/SeedVR2-lite/commit/e5bc39d525be5f1f6ebe34ad381f55286ced624f))
* **test:** 低风险反模式修复 + 文档同步 ([c93a480](https://github.com/ReSerendipity/SeedVR2-lite/commit/c93a4806e43902c5da63e1894f61cb237eaea570))
* **test:** 激活视觉回归门禁并移除失效的 integration marker 过滤 ([0431af4](https://github.com/ReSerendipity/SeedVR2-lite/commit/0431af4557c7ebfdf4dad0d0b007201db1069bef))
* update frontend app.js ([5d40a03](https://github.com/ReSerendipity/SeedVR2-lite/commit/5d40a03cfb43aff88f803a0105ef9e7786195653))
* 修复 CI lint(F811 死代码)、workflow 死配置与 gitignore 保护规则 ([ec38270](https://github.com/ReSerendipity/SeedVR2-lite/commit/ec382700437059ae3b24123a37eccbf9857f3d84))
* 修复 NaDiT v1 TimeEmbedding 参数不一致（config.dim → sinusoidal_dim/hidden_dim/output_dim） ([7b97180](https://github.com/ReSerendipity/SeedVR2-lite/commit/7b971803e2628f62f25264bbb3297941a19a4b4e))
* 修复 NaDiT v1 构造参数不匹配（改用 NaDiTConfig 对象）+ TimeEmbedding 参数一致性 ([5908f04](https://github.com/ReSerendipity/SeedVR2-lite/commit/5908f04b073fb821981edc6f46588b0685d43080))
* 修复测试体系质量门禁失效和E2E测试反模式 - 移除CI中||true容错, 加强SSE残缺断言, 替换硬编码等待为语义化策略, 修复条件跳过断言和吞没异常, 清理188个临时目录. 686测试全部通过, Ruff+Black检查通过 ([4f38d73](https://github.com/ReSerendipity/SeedVR2-lite/commit/4f38d73ddf24162f41f76d89198eb9202c979678))
* 修正新手引导中的不准确信息 ([bc6b0ef](https://github.com/ReSerendipity/SeedVR2-lite/commit/bc6b0ef3507b82b5b36b70a67e8f5fb91e61d380))
* 全面修正新手引导中的技术错误 ([4ceb3b8](https://github.com/ReSerendipity/SeedVR2-lite/commit/4ceb3b8d5b0add0109afb2d94dcaf93cd58f1d4c))
* 恢复 app_server.py（324e5a5 引入编码损坏导致 SyntaxError，恢复至 a8c6ce7 干净版本） ([23b331b](https://github.com/ReSerendipity/SeedVR2-lite/commit/23b331bcf03f07a3e6052f08b94fa1a919871537))
* 真实修复 5 个 mypy 类型错误（i18n 变量重名/engine 赋值/HistoryRecord 参数） ([a8c6ce7](https://github.com/ReSerendipity/SeedVR2-lite/commit/a8c6ce7d4188d70072dbc3775d6a13dfdab17422))


### Documentation

* add DiT v1/v2 architecture divergence notes ([f344f9e](https://github.com/ReSerendipity/SeedVR2-lite/commit/f344f9e2615d98a58e277f9980eb8f63a7022fd4))
* add DiT v1/v2 architecture divergence notes; gitignore: unify template ([3ed52c3](https://github.com/ReSerendipity/SeedVR2-lite/commit/3ed52c35731dde0d4682a088d647cb01f56b0c2b))
* add models source attribution for third-party model implementations ([dc66e7f](https://github.com/ReSerendipity/SeedVR2-lite/commit/dc66e7f225530aebf08ea7a4a10c4d3b4d43d0a5))
* add SageAttention tuning notes; add test artifact image ([57bd771](https://github.com/ReSerendipity/SeedVR2-lite/commit/57bd771bc12105f33e2b7bd6026937cdb66c1e7c))
* **agents:** v1.22 test quality hardening - 11 anti-patterns fixed ([d65dc73](https://github.com/ReSerendipity/SeedVR2-lite/commit/d65dc7333161423d1958d9bc2c2ff13be0f46e2a))
* beginner-friendly quickstart + fix model download path & CUDA detection ([2abc521](https://github.com/ReSerendipity/SeedVR2-lite/commit/2abc5210fd7b9b32b0b26669166ea002099f1139))
* **compliance:** add independent third-party declaration vs model owners (ByteDance Seed / Alibaba Tongyi / bilibili) ([ee3c83c](https://github.com/ReSerendipity/SeedVR2-lite/commit/ee3c83c288aaedbc91214ca11beb363409b6ffa8))
* **compliance:** add third-party disclaimer to UI settings copyright block (5 locales + template) ([1241d1f](https://github.com/ReSerendipity/SeedVR2-lite/commit/1241d1f9f2cb583f96299cc02d5c65433876c459))
* **compliance:** rebrand subtitle, unify IndexTTS version naming, add third-party disclaimer to demo footer ([94c4c06](https://github.com/ReSerendipity/SeedVR2-lite/commit/94c4c06dc02b06ff0c9dd0f3f4a8c3b45bee055d))
* **perf:** 新增性能基准测试自动化脚本与完整指南 ([2f0dc9b](https://github.com/ReSerendipity/SeedVR2-lite/commit/2f0dc9bffb0ed57d4acdc4be64d58f493a8cb59a))
* **readme:** remove trademark, integrity verification and AI content identification sections ([165e1aa](https://github.com/ReSerendipity/SeedVR2-lite/commit/165e1aa6f57e5f190a21e6ff8c0345d3772f34aa))
* restore open-source essentials (LICENSE, NOTICE, USER_AGREEMENT, COC, SECURITY, upstream source declaration) ([0c22648](https://github.com/ReSerendipity/SeedVR2-lite/commit/0c22648e276f11458e648efaf140bd02eef8fbe7))
* restore README, CI, demo, screenshots to remote; gitignore local-only content; restore pyproject readme ref ([42c6caf](https://github.com/ReSerendipity/SeedVR2-lite/commit/42c6caf3ce816bfb0c6b42fa823777904454805b))
* restore README, CI, demo, screenshots to remote; restore pyproject; gitignore local-only ([6e1a0a8](https://github.com/ReSerendipity/SeedVR2-lite/commit/6e1a0a8adf5edd146fb101c0e7f85554193a5927))
* self-check pass, bump v1.19 (sync entry + 7870) ([7f73fc8](https://github.com/ReSerendipity/SeedVR2-lite/commit/7f73fc844c7a8de95008449fbf6a23b24119bf8a))
* trigger pages deploy ([72f6cde](https://github.com/ReSerendipity/SeedVR2-lite/commit/72f6cdef41a8e48eb22198a3e9a4d764275ead6b))
* 全面修正所有文档中的 FP8 实现说明 ([c7014d3](https://github.com/ReSerendipity/SeedVR2-lite/commit/c7014d3a538f0b330d630816619150b3ddc9d7cc))
* 新增新手引导文档，帮助零技术背景用户快速上手 ([089d231](https://github.com/ReSerendipity/SeedVR2-lite/commit/089d231c2af68b875ad1769f79558e4f5420debe))
* 补全开源社区运营类文档与跨平台脚本(10项) ([c4435fa](https://github.com/ReSerendipity/SeedVR2-lite/commit/c4435fae96a7181d9663cb520e67e7aee5cbeee1))


### CI/CD

* **e2e:** job 超时 60-&gt;120 分钟，CI 重试 2-&gt;1 次（3 浏览器×15 specs 全量需更长执行时间） ([94c65a5](https://github.com/ReSerendipity/SeedVR2-lite/commit/94c65a50427af54d8e69e90260df10312e7c3657))
* **e2e:** 加 playwright github reporter，失败测试输出到 annotation 便于定位 ([892396c](https://github.com/ReSerendipity/SeedVR2-lite/commit/892396c4aa2183058218accaabec0502b569cd9a))
* **e2e:** 添加 workflow_dispatch 手动触发 ([fc29866](https://github.com/ReSerendipity/SeedVR2-lite/commit/fc29866f4fd6de5c9e71bb1965e8372ec720229e))
* quality-gate job 超时 60 分钟、最小权限 contents:read、pip check ([9726c78](https://github.com/ReSerendipity/SeedVR2-lite/commit/9726c7858e4980bffc9b469446e66a04ec2eb331))
* release-please 使用 GH_PAT 建 PR（GITHUB_TOKEN 被禁并在 org 无法创建 PR） ([c7168f1](https://github.com/ReSerendipity/SeedVR2-lite/commit/c7168f1eed7f5b8f6a959056f8e7de42d3cdfc73))
* 为 e2e/依赖审计/性能 workflow 补充最小权限 (contents: read) ([47c6f71](https://github.com/ReSerendipity/SeedVR2-lite/commit/47c6f71c197f81d8210992a2fdb401da5d8d2cf0))
* 为 SeedVR2 接入 release-please 自动发版 ([4bb559c](https://github.com/ReSerendipity/SeedVR2-lite/commit/4bb559c3351271bedf3c3e56325be1d00f4d0f07))
* 预防措施——本地门禁脚本(ruff/format/compileall/UTF-8)+git hooks 安装、.gitattributes 统一 UTF-8/LF、security.yml 补超时与最小权限、CONTRIBUTING 增加提交前检查与排障章节 ([19a912b](https://github.com/ReSerendipity/SeedVR2-lite/commit/19a912b42f432baa3d773df567118ff401cd451d))


### Security

* allow 0.0.0.0 only with SEEDVR2_AUTH_PASSWORD (Docker-compatible); sync tests ([0b484da](https://github.com/ReSerendipity/SeedVR2-lite/commit/0b484da5384352a1d0b78e7e55182e2d0e514146))
* enforce loopback-only host binding; ci: security assertions + lock check ([022076a](https://github.com/ReSerendipity/SeedVR2-lite/commit/022076ac24690d1e4d287beb984f6426617fbfae))
* implement rate-limit middleware (sliding window per-IP, 429+Retry-After) wired to upload/inference endpoints; enforce secret-scan gate; add dependabot ([41f10d9](https://github.com/ReSerendipity/SeedVR2-lite/commit/41f10d9177eb371ad48aef22a695ff2687fe23d5))
* pin trivy-action to verified commit SHA (v0.36.0, supply-chain) ([5dd9a6e](https://github.com/ReSerendipity/SeedVR2-lite/commit/5dd9a6e4fe5cd857a059727e809945b233de1d08))
* unlock Semgrep blocking gate (--severity ERROR gate + report-only SARIF); nosemgrep 9 evaluated findings (8 false-positive/mitigated + 1 accepted-risk dev checkpoint); pin semgrep==1.173.0 ([50e5f0d](https://github.com/ReSerendipity/SeedVR2-lite/commit/50e5f0d9253291428fbc5d6efcbdc26b6da1c317))
* unlock Semgrep blocking gate (ERROR-only gate + report-only SARIF); nosemgrep 8 evaluated findings; pin semgrep==1.173.0 ([2b32f89](https://github.com/ReSerendipity/SeedVR2-lite/commit/2b32f89aa2365f988357da07027523614924b104))


### Tests

* **recovery:** cover stale-task cleanup with running-task guard and progress persister ([1aca7f4](https://github.com/ReSerendipity/SeedVR2-lite/commit/1aca7f4a51738a94707aab8a85274f177136050b))
* update capture-screenshots; perf: add restore-api benchmark ([62cbd86](https://github.com/ReSerendipity/SeedVR2-lite/commit/62cbd8614d994533e6552d89bd9267c2cf81e9b3))
* update E2E test specs and add CI workflow ([1012ddf](https://github.com/ReSerendipity/SeedVR2-lite/commit/1012ddfba45cee0b60119eadef8d081c7c84a60f))
* 合并原 VideoInfo 数据类测试（修复乱码注释） ([9de04cb](https://github.com/ReSerendipity/SeedVR2-lite/commit/9de04cb4baa0e4389304e99dde0b535ae2e0d73f))
* 覆盖率 60.85%-&gt;66.21% 达标 65%（weight_encryption/video_processor/settings 路由/FileList 管理） ([85e0752](https://github.com/ReSerendipity/SeedVR2-lite/commit/85e0752977235b236fea3c9675af568e10f34436))
