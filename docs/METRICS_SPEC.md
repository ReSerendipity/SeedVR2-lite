# SeedVR2 指标口径规范（Metrics Specification）

> 数据治理 P3-2 · 单一事实来源
> 适用范围：推理速度、显存、质量三类指标的**定义、采集点与比较规则**
> 代码对应：`engines/_image_pipeline.py`、`engines/_video_pipeline.py`、
> `optimization/gpu/vram_monitor.py`、`utils/image_metrics.py`

---

## 1. 为什么需要这份文档

改造前项目内同时存在**三套速度口径**（后端终态 metadata、前端实时帧间差、
benchmark 轮询计时），彼此不可比；显存有四个 PyTorch 原生计量口径但未定义
"该用哪个做决策"。本文件把这些口径固定下来，任何新增指标必须先登记再实现。

---

## 2. 时间口径

### 2.1 `processing_time`（秒）— 端到端墙钟

| 项目 | 定义 |
|---|---|
| 起点 | 推理管线函数被调用（`infer_image` / `infer_video`） |
| 终点 | 输出文件写入磁盘完成（视频含 ffmpeg 合成） |
| **包含** | 帧抽取、VAE 编码、DiT 采样、VAE 解码、色彩校正、水印、编码写盘 |
| **不包含** | 队列等待时间、模型加载（首次）、HTTP 传输 |
| 适用场景 | 用户体验口径、"这个任务跑了多久"、成本换算 |

### 2.2 `dit_seconds`（秒）— DiT 采样阶段耗时

来自 `VRAMPeakMonitor` 的 `dit_sample` 阶段 `duration_ms / 1000`。
仅覆盖扩散采样，不含 VAE 与 IO。用于评估模型/采样器本身的效率。

### 2.3 `stage_durations_ms`（毫秒）— 分阶段耗时

键为阶段名（`vae_encode` / `dit_sample` / `vae_decode` 等），值为该阶段耗时。
用于定位瓶颈阶段与显存泄漏排查。

---

## 3. 速度口径（**不可混用**）

| 指标 | 公式 | 口径层级 | 适用 |
|---|---|---|---|
| `processing_fps` | `output_frames / processing_time` | **端到端** | 视频任务用户可感知吞吐 |
| `steps_per_second`（it/s） | `sample_steps / dit_seconds` | **模型层** | 采样器/精度/分辨率的横向对比 |
| `avg_frame_time_ms` | `processing_time / output_frames * 1000` | 端到端 | 单帧成本估算 |
| 前端实时 fps | 帧间隔差 `Δframes / Δt` | **估算值** | 仅用于进度条观感，**禁止写入报告/基准** |

**铁律**：
1. 跨版本性能对比**只能**用同口径（优先 `steps_per_second`）；
2. `processing_fps` 受分辨率、帧数、磁盘 IO 影响极大，不得用于比较不同输入；
3. 图像任务无 fps 概念，`processing_time` 是唯一速度指标；
4. 前端 fps 为滑动窗口估算，与后端终态值存在偏差属预期，不必"修正对齐"。

---

## 4. 显存口径

`vram_monitor` 同时采集四项，用途区分如下：

| 指标 | 含义 | 用途 |
|---|---|---|
| `memory_allocated` | 当前张量实际占用 | 瞬时占用 |
| `memory_reserved` | 缓存分配器保留量 | 判断碎片/缓存未释放 |
| `max_memory_allocated` | 本次推理分配峰值 | **落库 `history.vram_peak_mb` 的唯一口径** |
| `max_memory_reserved` | 保留峰值 | 判断是否触发过扩容 |

- 每次推理开始调用 `reset_peak_memory_stats()`，峰值口径为**单次推理内**；
- 长时间运行的泄漏判定由 `optimization/gpu/vram_leak_detector.py` 负责
  （末段连续递增 + 涨幅超阈值 + 冷却期），该趋势数据经
  `GET /api/system/metrics` 的 `vram_leak` 字段暴露。

---

## 5. 质量口径

| 指标 | 定义 | 实现 |
|---|---|---|
| PSNR | `10·log10(1/MSE)`，完全一致返回 99.0 | `utils/image_metrics.psnr` |
| SSIM | Wang 2004，11×11 高斯窗，RGB 通道平均 | `utils/image_metrics.ssim` |
| Golden 基准对 | 合成源图 → 生产退化处理器 → 退化图 | `utils/golden_scenes.py` |

门禁契约见 `tests/test_golden_quality.py`：
- **色偏型退化**：`lab` / `adain` 必须带来 ≥ +3 dB 增益；
- **结构型退化**（噪声/模糊/降采样，无色偏）：允许有界回退
  （PSNR ≤ 3 dB、SSIM ≤ 0.15）——全局统计对齐在纯结构损伤下可能小幅劣化，
  属已知设计边界；
- `hsv` 因 HSV 空间非线性（Hue 环绕、S/V 有界）无法还原 RGB 加性色偏，
  仅要求不灾难性劣化。

---

## 6. 新增指标的流程

1. 在本文件登记名称、公式、采集点、口径层级；
2. 实现时写入 `RestoreResult.metadata`（推理侧）或 `metrics_collector`（运行侧）；
3. 补一条 `tests/` 断言，锁定口径定义；
4. 更新 CHANGELOG。
