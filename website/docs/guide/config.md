---
outline: [2, 3]
---
# 配置参考（自动生成）

> 本页由 `scripts/generate_config_reference.py` 从 `app/integrated_app/config_models.py`（Pydantic 模型，含校验逻辑）自动生成，**请勿手改**；
> 重新生成：`python scripts/generate_config_reference.py`。实际生效值以仓库根目录 `config.yaml` 为准（其中 `security` 段与密钥类配置见 [安全与合规](/guide/security)）。

## `server` 段
HTTP 服务器配置模型

| 键 | 类型 | 默认值 |
|---|---|---|
| `server.host` | `str` | `'127.0.0.1'` |
| `server.port` | `int` | `7870` |
| `server.debug` | `bool` | `False` |
| `server.auto_open_browser` | `bool` | `True` |
| `server.allowed_origins` | `list[str]` | `['http://127.0.0.1:7870', 'http://localhost:7870']` |

## `model` 段
模型加载与管理配置模型

| 键 | 类型 | 默认值 |
|---|---|---|
| `model.default_size` | `str` | `'3b'` |
| `model.default_precision` | `str` | `'fp16'` |
| `model.pretrained_dir` | `str` | `'model'` |
| `model.model_source_mode` | `Literal['shared', 'portable']` | `'portable'` |
| `model.shared_models_root` | `str` | `''` |
| `model.auto_load` | `bool` | `True` |
| `model.device` | `str` | `'auto'` |
| `model.idle_unload_minutes` | `int` | `15` |
| `model.models.<size>.name` | `str` | `''` |
| `model.models.<size>.config_dir` | `str` | `''` |
| `model.models.<size>.checkpoint_fp16` | `str` | `''` |
| `model.models.<size>.checkpoint_fp8` | `str` | `''` |
| `model.models.<size>.vae_checkpoint` | `str` | `''` |
| `model.models.<size>.pos_emb` | `str` | `''` |
| `model.models.<size>.neg_emb` | `str` | `''` |
| `model.models.<size>.min_vram_fp16_gb` | `int` | `16` |
| `model.models.<size>.min_vram_fp8_gb` | `int` | `8` |
| `model.models.<size>.baseline_vram_fp16_gb` | `float` | `0` |
| `model.models.<size>.baseline_vram_fp8_gb` | `float` | `0` |
| `model.models.<size>.num_blocks` | `int` | `36` |
| `model.models.<size>.sha256_fp16` | `str` | `''` |
| `model.models.<size>.sha256_fp8` | `str` | `''` |
| `model.models.<size>.sha256_vae` | `str` | `''` |
| `model.models.<size>.sha256_pos_emb` | `str` | `''` |
| `model.models.<size>.sha256_neg_emb` | `str` | `''` |

## `restore` 段
视频/图像修复算法默认参数配置模型

| 键 | 类型 | 默认值 |
|---|---|---|
| `restore.default_resolution_h` | `int` | `1080` |
| `restore.default_resolution_w` | `int` | `1920` |
| `restore.default_scale_factor` | `float` | `2.0` |
| `restore.temporal_consistency` | `float` | `0.8` |
| `restore.detail_enhancement` | `str` | `'cinematic'` |
| `restore.seed` | `int` | `42` |
| `restore.sp_size` | `int` | `1` |

## `gpu` 段
GPU 后端配置模型

| 键 | 类型 | 默认值 |
|---|---|---|
| `gpu.backend` | `str` | `'auto'` |
| `gpu.vram_tile_tiers` | `list[GpuVramTileTier]` | `[{'min_available_gb': 20.0, 'tile_size': 1024, 'tile_overlap': 512}...` |

## `history` 段
历史记录数据库配置模型

| 键 | 类型 | 默认值 |
|---|---|---|
| `history.db_path` | `str` | `'data/history.db'` |
| `history.max_records` | `int` | `10000` |

## `i18n` 段
国际化（i18n）配置模型

| 键 | 类型 | 默认值 |
|---|---|---|
| `i18n.default_locale` | `str` | `'zh'` |
| `i18n.available_locales` | `list[str]` | `['zh', 'zh-TW', 'en', 'ja', 'fr']` |

## `logging` 段
日志系统配置模型

| 键 | 类型 | 默认值 |
|---|---|---|
| `logging.level` | `str` | `'INFO'` |
| `logging.file` | `str` | `'logs/app.log'` |
| `logging.max_size_mb` | `int` | `50` |
| `logging.backup_count` | `int` | `3` |

## `cache` 段
文件缓存配置模型

| 键 | 类型 | 默认值 |
|---|---|---|
| `cache.ttl` | `int` | `86400` |
| `cache.max_size_mb` | `int` | `500` |

## `inference` 段
推理优化配置模型

| 键 | 类型 | 默认值 |
|---|---|---|
| `inference.blocks_to_swap` | `int` | `0` |
| `inference.blockswap_prefetch` | `bool` | `False` |
| `inference.swap_io_components` | `bool` | `False` |
| `inference.offload_device` | `str` | `'cpu'` |
| `inference.attention_mode` | `str` | `'sdpa'` |
| `inference.inference_mode` | `str` | `'distilled'` |
| `inference.resolution` | `int` | `2048` |
| `inference.max_resolution` | `int` | `0` |
| `inference.batch_size` | `int` | `1` |
| `inference.uniform_batch_size` | `bool` | `True` |
| `inference.temporal_overlap` | `int` | `0` |
| `inference.prepend_frames` | `int` | `0` |
| `inference.temporal_segment_size` | `int` | `0` |
| `inference.temporal_segment_overlap` | `int` | `8` |
| `inference.input_noise_scale` | `float` | `0.0` |
| `inference.latent_noise_scale` | `float` | `0.0` |
| `inference.restoration_guidance_scale` | `float` | `1.0` |
| `inference.color_correction` | `str` | `'lab'` |
| `inference.seed` | `int` | `-1` |
| `inference.enable_debug` | `bool` | `False` |
| `inference.fp8_enabled` | `bool` | `False` |
| `inference.distilled_mode` | `bool` | `False` |
| `inference.cache_model` | `bool` | `False` |
| `inference.force_reload_dit` | `bool` | `False` |
| `inference.torch_compile` | `dict[str, Any]` | `{}` |
| `inference.memory_threshold` | `float` | `0.95` |
| `inference.memory_min_available_gb` | `float` | `2.0` |

## `retention` 段
输出产物保留策略配置模型

| 键 | 类型 | 默认值 |
|---|---|---|
| `retention.outputs_max_age_days` | `int` | `14` |
| `retention.outputs_max_files` | `int` | `0` |
| `retention.outputs_cleanup_interval_seconds` | `int` | `3600` |
| `retention.disk_min_free_gb` | `float` | `5.0` |

## `runtime` 段
运行时配置根模型

| 键 | 类型 | 默认值 |
|---|---|---|
| `runtime.sse.max_duration_seconds` | `int` | `300` |
| `runtime.sse.heartbeat_interval_seconds` | `int` | `30` |
| `runtime.sse.poll_interval_seconds` | `float` | `0.5` |
| `runtime.batch.max_retries` | `int` | `2` |
| `runtime.batch.retry_base_delay_seconds` | `float` | `1.0` |
| `runtime.batch.retry_max_delay_seconds` | `float` | `30.0` |
| `runtime.retry.enabled` | `bool` | `True` |
| `runtime.retry.max_retries` | `int` | `2` |
| `runtime.retry.base_delay_seconds` | `float` | `1.0` |
| `runtime.retry.max_delay_seconds` | `float` | `30.0` |
| `runtime.retry.oom_breaker.enabled` | `bool` | `True` |
| `runtime.retry.oom_breaker.threshold` | `int` | `3` |
| `runtime.retry.oom_breaker.cooldown_seconds` | `float` | `600.0` |
| `runtime.task.id_length` | `int` | `16` |
| `runtime.task.max_timeout_seconds` | `int` | `3600` |
| `runtime.task.queue_maxsize` | `int` | `100` |
| `runtime.task.auto_recover` | `bool` | `False` |
| `runtime.task.checkpoint_dir` | `str` | `'data/checkpoints'` |
| `runtime.task.checkpoint_every` | `int` | `1` |
| `runtime.task.stale_threshold_minutes` | `int` | `30` |
| `runtime.task.progress_stall_timeout_minutes` | `int` | `30` |
| `runtime.upload.large_file_threshold_mb` | `int` | `10` |
| `runtime.upload.chunk_size_bytes` | `int` | `8192` |
| `runtime.security.allowed_base_dirs` | `list[str]` | `PydanticUndefined` |
| `runtime.security.rate_limit_per_minute` | `int` | `30` |
| `runtime.security.integrity_enforce` | `bool` | `False` |
| `runtime.security.integrity_recheck_interval_seconds` | `int` | `1800` |
| `runtime.security.max_upload_image_mb` | `int` | `50` |
| `runtime.security.max_upload_video_mb` | `int` | `500` |

- `user_preferences` = `{}`
