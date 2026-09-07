# SeedVR2-lite · 后端服务设计评估报告（v1.5.1 · 2026-09-06）

> 评估对象：`C:/Users/Doro/SeedVR2-lite`（FastAPI + Jinja2/HTMX 自托管 Web 服务）
> 方法：评估提示词 §4 全部取证命令 + 真机实测（RTX 5070 Ti Laptop 12GB / 31.7GB RAM / torch 2.13.0+cu132）
> 所有结论均有 file:line 或实测命令佐证，无虚构端点与文件。

---

## 0. 总评（TL;DR）

**评估提示词预设的 P0（同步长任务阻塞事件循环）不成立。** 该项目已经是「单 worker 有界队列 + `asyncio.to_thread` 协作式取消」的正确架构，且完成度远超提示词预期：就绪探针、OOM 熔断、系统内存守卫、批量断点续跑、幂等键、schema 版本化迁移均已落地。八维度均分 **4.25 / 5**。

真机实测新发现一个 **P1**：`engine.load_model` 在事件循环上同步执行多 GB 权重文件的 SHA256 校验，实测阻塞 ping **4057ms**（3B mxfp8）；7B fp16 权重更大，会打爆 Docker `HEALTHCHECK --timeout=5s`，导致容器被判 unhealthy。

实测中任务本身因**本机内存不足**（RAM 96.8% > 守卫阈值 95%，可用 1.0GB/31.7GB）被引擎内存守卫终止——这是容错机制的正确实证，不是代码缺陷；但暴露了 P2：该确定性错误被坏案例重试又跑了 2 次（进程 RSS 峰值 15.53GB）。

---

## 1. 事实核对表（含路径幻觉纠正与反提示词事实）

| 提示词锚点 | 实际情况 | 证据 |
|---|---|---|
| FastAPI ≥0.115 / uvicorn / gunicorn / pydantic v2 / aiosqlite | ✅ 成立 | `pyproject.toml:40-75` |
| gunicorn `-w 1`，`EXPOSE 7870` | ✅ 成立（含 `--graceful-timeout 90 --timeout 120`） | `Dockerfile:80-85` |
| HEALTHCHECK 用 `/api/system/ping`，start-period 180s | ✅ 成立 | `Dockerfile:71-72` |
| `restore_routes.py` / `batch_restore.py` 存在 | ❌ 不存在（提示词纠正正确） | `ls` 实测 |
| `app/perf/optimizer.py` 未确认存在 | ❌ 不存在；`app/perf/` 仅剩 `__pycache__`（残留空目录） | `ls -la app/perf/` |
| `app/models/{lcm_distill,raft_flow,rife_interpolator}` | ✅ 存在 | `ls app/models/` |
| `app/vram/flash_attention_wrapper.py` | ✅ 存在 | `ls app/vram/` |
| 74 个 `test_*.py` | ✅ 成立（74） | `ls tests/test_*.py \| wc -l` |
| 13 个 Playwright spec | ⚠️ 修正：实际 **15** 个 `.spec.ts`（`tests/specs/`） | `ls tests/specs/*.spec.ts \| wc -l` |
| i18n 五语言 | ✅ 成立（zh / zh-TW / en / fr / ja） | `ls app/integrated_app/locales/` |
| 限流 30/min | ✅ 成立，且仅作用于 4 个提交类 POST 端点 | `rate_limit.py:65-70`、`config.yaml runtime.security` |
| 中间件 csrf/rate_limit/security_headers/request_id/error_handler | ✅ 成立，另有 `basic_auth` / `tracing` | `middleware/` 目录 |
| 「只有 `/api/system/ping` 一档」 | ❌ **不成立**：已有 `/api/system/health`（详情档）与 `/api/system/ready`（就绪档，503 语义） | `routes/system/health.py`、`routes/system/readiness.py` |
| 反模式 #6「无就绪端点」 | ❌ **不成立**：`GET /api/system/ready` 已实现（模型加载中返回 503 + Retry-After） | `readiness.py:32-90` |
| `mcp_server.py` 引入额外暴露面 | ✅ 存在但为 **stdio 模式独立进程**（Claude Desktop 场景），不挂载到主应用，无 HTTP 暴露面 | `mcp_server.py:10-29` |
| 提交队列「来一个就跑、无显式队列」 | ❌ **不成立**：`TaskQueue`（asyncio.Queue maxsize=100、任务超时、worker 重启、取消回调注册表） | `task_queue.py` |

其他关键配置事实（`config.yaml`）：`model.auto_load=false`（本机配置，实测启动后 `model_loaded=false`）；`runtime.task.max_timeout_seconds=86400`（覆盖代码默认 3600）；`queue_maxsize=100`；`progress_stall_timeout_minutes=30`；`oom_breaker threshold=3 / cooldown 600s`；`retention.disk_min_free_gb=5`。

---

## 2. 八维度评分（0–5 + 证据）

### 2.1 分层与职责边界 — **4.5**
- `routes → services → engines` 三层成立：`restore_service.py:1-12` 明确「路由层只保留 HTTP 协议适配」「本模块禁止依赖 Request/Response/HTTPException」「推理在 TaskQueue 单 worker 串行」。
- routes 不直接 import engines：全 routes 唯一命中是 `health.py:128` 引擎侧工具函数 `_get_system_memory`（可接受，可下沉 utils）。
- 路由文件规模健康（top3：settings 612 / common 583 / batch 473 行），无千行 fat controller。
- 扣分点：`app_server.py` **1011 行**，lifespan 内联了 4 个后台循环（stale 清理 / 停滞看门狗 / outputs 清理 / 空闲卸载），`/api/engine/*` 路由内联定义在装配函数中（`app_server.py:732-831`）——建议拆出 `lifecycle/` 模块。

### 2.2 单 worker 长任务架构 — **4.5**（本项目核心挑战，答得最好）
- 推理在事件循环外：`engines/_image_pipeline.py:142`、`_video_pipeline.py:73` 均为 `await asyncio.to_thread(...)`；**实测推理期 ping 12.6ms**（见 §3）。
- 显式队列：`task_queue.py:58` 有界 `asyncio.Queue(maxsize=100)`；任务级超时（配置 86400s）；worker 异常自动重启 ≤3 次（`task_queue.py:159-179`）。
- 取消：`threading.Event` + 引擎阶段检查点 `_check_cancelled()` 抛 `InferenceCancelledError`（`seedvr2_engine.py:210-245`），并诚实文档化「to_thread 中的同步代码无法被 Task.cancel 中断」（`task_queue.py:114`、`exceptions.py:278-289`）。
- 进度：帧级同步回调 → 内存缓存 → 事件总线（按任务 1s 节流，`app_server.py:295-333`）→ SSE 推送（心跳 30s / 连接 300s / 断线重连取快照，`task.py:36-152`）。
- 双看门狗：进度停滞看门狗（30min 签名无变化自动 request_cancel）+ 卡死任务定期清理。
- 扣分点见缺陷 P1-1 / P2-1 / P2-2。

### 2.3 健康检查与就绪语义 — **4**
- 三档分离已落地：`/ping`（liveness，轻量）+ `/health`（详情：系统资源/模型/GPU）+ `/ready`（就绪，`load_in_progress` 时 503 + `Retry-After: 5`）。Dockerfile 用 ping 做 liveness 是正确选择。
- 扣分点：**GPU 故障假阳性**——`gpu_available` 只查「有能力」不查「健康」，CUDA 上下文损坏后 ping/ready 仍 200，所有任务失败；`verify_model_files` 阻塞期间 ping 可超过 Docker HEALTHCHECK 的 5s 超时（P1-1 / P2-4）。

### 2.4 数据访问 — **4.5**
- 全部 aiosqlite（全仓唯一 `import sqlite3` 在 `history_db.py:24`，仅用于文档注释，**无同步混用**——§4④ grep 零命中 `sqlite3.connect`）。
- WAL + `busy_timeout` 30s + FTS5 全文索引（触发器同步）+ `PRAGMA user_version` 版本化幂等迁移（`history_db.py:38-78`）+ UPDATE 列白名单。
- 长任务期间进度每 30s 节流落库（`run_coroutine_threadsafe`，`restore_service.py:164-208`），短事务不阻塞。
- 小瑕疵：`history` 表无 `created_at`/`status` 索引（`max_records=10000` 内影响可忽略）。

### 2.5 缓存与中间态 — **4
- 模型驻留策略明确且务实：启动只载配置+嵌入（~1MB），DiT/VAE **按阶段加载、用完即毁**，任何时刻 RAM 最多一个大模型（`seedvr2_engine.py:271-280`）；空闲 15min 自动卸载（任务运行中不卸，`app_server.py:475-498`）。
- 断点续跑：批量任务文件级 checkpoint（size+mtime 指纹校验，`restore_service.py:743-829`）；单视频任务**无帧级续跑**（重跑成本高，P2-6）。
- outputs 保留策略（14 天/磁盘水位 5GB 预检→507）+ 上传缓存 TTL。

### 2.6 容错与降级 — **4.5**
- 实测验证了两道防线：① OOM 熔断器（连续 3 次 OOM 拒新任务 600s，`upload.py:171-181` 503 + Retry-After）；② **系统内存守卫**——本机 RAM 96.8% 时正确终止推理并给出明确错误（实测日志「内存使用率 96.8% 超过阈值 95%…必须立即终止模型」）。
- 坏案例重试降级阶梯（blocks_to_swap↑ → resolution↓ → 种子轮换）+ 降级血缘写入历史 parameters（可解释性）。
- 磁盘预检在任务前拒绝（507）而非写一半失败。
- 扣分点：内存守卫的确定性错误被重试 2 次（P2-2）。

### 2.7 安全中间件实效 — **4.5**
- CSRF：签名 token 双提交（cookie `csrf_token` + `X-CSRF-Token`），安全方法自动种 token，防 403 自锁（`csrf.py:52-64`）；限流只覆盖 4 个提交端点、不误伤轮询；XFF 默认不信任（需 `SEEDVR2_TRUST_PROXY=1` 显式开启）。
- 路径防护（下载走 PathGuard 白名单，`task.py:334-338`）、上传魔数校验、权重 SHA256（CWE-353）、核心模块完整性自检 + 周期重检、水印绑定 task_id、BasicAuth 公网可选。
- 扣分点：完整性清单漂移（P2-3）、`/docs` 默认暴露（P2-5）。

### 2.8 API 设计 — **4**
- 统一响应信封 `{success, data|error:{code,message,detail}}`（P0-1），状态码→错误码映射齐全，HTMX 请求分流 HX-Trigger Toast，兜底 500 不泄露堆栈/指纹。
- 语义正确：429（限流）、503（熔断/GPU/加载失败，带 Retry-After）、507（磁盘）、404/400。
- 版本化：`/api/v1/*` → `/api/*` 别名中间件已预留（`app_server.py:167-185`）；幂等键（`Idempotency-Key`）已落地。
- 扣分点：`request_id` 只在响应头 `X-Request-ID` 与日志，不在错误信封体内；`/docs` `/openapi.json` 未显式管理（P2-5）。

| 维度 | 得分 |
|---|---|
| 分层与边界 | 4.5 |
| 长任务架构 | 4.5 |
| 健康检查语义 | 4.0 |
| 数据访问 | 4.5 |
| 缓存与中间态 | 4.0 |
| 容错降级 | 4.5 |
| 安全中间件实效 | 4.5 |
| API 设计 | 4.0 |
| **均分** | **4.25** |

---

## 3. 实测记录（§4③，2026-09-06 真机）

环境：RTX 5070 Ti Laptop 12GB / 31.7GB RAM / torch 2.13.0+cu132 / 权重 `model/seedvr2_3b_mxfp8.safetensors`。

| 步骤 | 结果 |
|---|---|
| 启动服务（7870） | ✅ 启动期 ping 3.5–36ms；`/api/system/ready` 返回 `{"status":"ready","model_loaded":false,"gpu_available":true}`（`auto_load=false` 语义正确） |
| 提交 `grace_hopper.jpg`（CSRF 双提交握手 → POST `/api/restore/`） | ✅ 4.6s 返回 `task_id=c450dc4f22874665`；**期间一次 ping 高达 4057ms**（归因：`engine.load_model` 在事件循环上同步执行多 GB 权重 SHA256 校验，`seedvr2_engine.py:314`） |
| 推理执行 | 任务被引擎**系统内存守卫**终止：RAM 96.8% > 95%（可用 1.0GB/31.7GB，另发现本机其他进程占用了大量内存）；推理线程内 ping 12.6ms（事件循环未被推理阻塞的实证） |
| 失败后行为 | BadCaseRetry 对该确定性错误又重试 2 次（3/3 耗尽），服务进程 RSS 峰值 15.53GB；任务状态正确落库为 failed；ping 全程可用 |
| 收尾 | 服务已停止，端口已释放，无残留进程 |

---

## 4. 缺陷清单

### P1（1 项）
**P1-1 权重 SHA256 完整性校验同步阻塞事件循环**
- 位置：`app/integrated_app/engines/seedvr2_engine.py:312-314`（`async def load_model` 内直接调用 `verify_model_files`，`security/integrity_check.py:138` 为同步逐文件哈希）。
- 实测：3B mxfp8（约 5GB）阻塞 ping **4057ms**；7B fp16 权重更大，预计阻塞数十秒。
- 影响：① 提交首个任务期间 UI/健康检查全部卡死；② Docker `HEALTHCHECK --timeout=5s` 连续超时 → 容器被判 unhealthy → 被编排重启（重启后再次校验，死循环风险）；③ gunicorn `--timeout 120` 同理存在边缘风险。
- 最小修复：`integrity_results = await asyncio.to_thread(verify_model_files, pretrained_root_path, model_cfg, precision)`（一行；`load_in_progress` 状态与 SSE 桥接已就位，无需其他改动）。

### P2（6 项）
- **P2-1 队列满时提交请求无限阻塞**：`task_queue.py:119` 为 `await self._queue.put(...)`（有界队列满时挂起，docstring 自认「满时 submit 将阻塞」），调用方 `routes/restore/upload.py:316/323` 无超时。长任务积压时客户端请求静默挂死（可超分钟/小时级）。建议 `put_nowait` → 503 `QUEUE_FULL` + Retry-After，或 `asyncio.wait_for(put, timeout=...)`。
- **P2-2 内存守卫确定性错误被坏案例重试放大**：实测同一「内存使用率超阈值」错误被 BadCaseRetry 重试 2 次（每次重新走阶段加载），进程 RSS 峰值 15.53GB。`bad_case_retry.py` 的 `FailureType` 只有 OOM/NETWORK/CANCELLED/UNKNOWN，未把系统 RAM 守卫错误识别为不可重试/应计熔断。建议：错误分类加入 RAM 守卫模式（不可重试或计入熔断）。
- **P2-3 完整性清单漂移**：启动自检报告 `model_manager.py`、`security/integrity_check.py`、`engines/seedvr2_engine.py` 哈希不匹配（manifest 未随代码更新重签）。`integrity_enforce=false` 时仅告警——告警常态化会被忽视；`enforce=true` 时会拒绝启动。建议发布流程末步重签 manifest。
- **P2-4 GPU 健康假阳性**：`/ping`、`/ready` 的 `gpu_available` 仅反映能力探测（`gpu_backend.is_gpu_available`），CUDA OOM/上下文损坏后仍 200，表现为「健康但所有任务失败」。建议 `/ready` 增加轻量 `torch.cuda` 探测（如 `torch.cuda.mem_get_info`），或在 `/health` 详情档标注降级原因。
- **P2-5 `/docs` `/redoc` `/openapi.json` 默认暴露**：`app_server.py` 未设置 `docs_url/redoc_url`（grep 零命中），默认开启且不受 BasicAuth 之外的控制。本地工具可接受；公网部署建议显式关闭或确认 BasicAuth 覆盖。
- **P2-6 单视频任务无帧级/段级断点续跑**：批量任务有文件级 checkpoint，但单个长视频失败即整体重跑（分钟级成本翻倍）。远期可结合阶段边界（VAE/DiT 切换点）做段级续跑。

---

## 5. 必答三问

**Q1：推理是否在事件循环外执行？**
是。链路为 `TaskQueue` 单 worker 协程 → `await asyncio.to_thread(推理同步代码)`（`_image_pipeline.py:142`、`_video_pipeline.py:73`），实测推理期 ping 12.6ms、卡死风险由停滞看门狗兜底。取消不靠 `Task.cancel` 而是协作式：`on_cancel`（`engine.request_cancel` 设 `threading.Event`）→ 推理线程在阶段检查点抛 `InferenceCancelledError` → GPU 资源及时释放；进度由同步回调写内存缓存 + `run_coroutine_threadsafe` 节流落库 + 事件总线推 SSE。**唯一例外是 P1-1 的 SHA256 校验**（提交路径在事件循环上），最小改造一行 `asyncio.to_thread` 即可，无需引入独立推理进程（现架构下取消/进度/健康语义均自洽，改进程反而会破坏共享显存与回调模型）。

**Q2：是否需要补 `/readyz`？**
不需要再补——`GET /api/system/ready` 已实现且语义正确（`load_in_progress` → 503 + `Retry-After`，探针永不 5xx 崩溃）。Dockerfile 继续用 `/ping` 做 liveness 是对的；K8s 部署时把 readinessProbe 指向 `/api/system/ready`、startupProbe 给足宽限即可。可选增强：P2-4 的 GPU 健康探测并入 `/ready`。

**Q3：限流如何从「30 请求/分钟」改为「并发任务数」？**
现状评估：30/min 仅作用于 4 个提交类 POST 端点（`rate_limit.py:65-70`），真正的并发控制已由架构承载（单 worker 串行 + `queue_maxsize=100` 有界队列 + 任务超时），「单位错配」已被结构性缓解。因此不建议把限流中间件改成并发数语义，而是：
1. **把并发语义落在队列上（核心）**：修复 P2-1——队列满立即 503 `QUEUE_FULL` + `Retry-After`，等效「最多 100 个在途任务，超出快速拒绝」；可在响应头带 `X-Queue-Depth` 提升可观测性。
2. **可选每 IP 在途上限**：`runtime.task.max_inflight_per_ip`（如 5），提交时统计该 IP 的 pending/processing 任务数，超限 429/503——比改请求频率更贴合「分钟级任务」语义。
3. 保留 30/min 作为防滥用兜底（成本为 0，对正常用户无感）。

---

## 6. 改进路线图

| 优先级 | 事项 | 工作量 |
|---|---|---|
| P1 | `verify_model_files` 包 `asyncio.to_thread`（P1-1） | 一行 + 回归 |
| 快赢 | 队列满 503 快速拒绝（P2-1）；RAM 守卫错误不可重试（P2-2） | 各 ~10 行 |
| 短期 | 发布流程重签完整性 manifest（P2-3）；`/ready` 增加 GPU 探测（P2-4）；docs 端点显式配置（P2-5） | 小 |
| 中期 | 单视频段级续跑（P2-6）；`app_server.py` lifespan 拆分 `lifecycle/` 模块；`history` 表按需补索引 | 中 |
| 观察 | Dockerfile 可在注释中给出 K8s readinessProbe 指向 `/api/system/ready` 的示例 | 文档 |

---

## 7. 附录：关键取证命令与输出摘要

```bash
# ② executor 检索：69 个 async def 路由；to_thread/executor 命中 30+ 处
grep -rn "run_in_executor\|to_thread\|ThreadPoolExecutor\|asyncio.Queue" app/integrated_app/ --include=*.py
# → 推理走 to_thread（_image_pipeline.py:142 / _video_pipeline.py:73）；队列 asyncio.Queue(maxsize=100)

# ④ 数据访问：sqlite3.connect 全仓零命中（history_db.py:24 仅注释引用）；aiosqlite 全覆盖
grep -rn "sqlite3.connect" --include=*.py app/   # 零输出

# ⑥ 健康端点：/api/system/ping + /api/system/health + /api/system/ready 三档
ls app/integrated_app/routes/system/  # health.py readiness.py sse.py gpu.py metrics.py ...

# 实测（完整记录见 §3）
curl -s -o /dev/null -w "%{time_total}" http://127.0.0.1:7870/api/system/ping
# → 启动期 3.5–36ms；提交首个任务期间 4057ms（SHA256 阻塞）；推理期 12.6ms
```
