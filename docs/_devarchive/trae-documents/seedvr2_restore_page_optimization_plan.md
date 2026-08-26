# SeedVR2 修复页面优化实施计划（更新版）

## 1. 摘要

本计划继续完成并验证统一修复页面（`/restore`）优化。前期已完成的改造包括：移除任务类型 Tab、按工作流节点重组参数、对齐 `SeedVR2.json` 默认值、精简 `VideoRestoreParams` 与 `unified.py` 参数解析。本次剩余工作聚焦于：

1. 修复 `app_server.py` 启动恢复任务时未传递 `config` 的问题。
2. 修复批量任务前端进度轮询 URL 与后端路由不匹配的问题。
3. 运行 `ruff` 与 `pytest` 完成代码质量与回归验证。
4. 浏览器验证页面功能与交互。

## 2. 当前状态分析

### 2.1 已确认完成项

| 文件 | 状态 | 说明 |
|------|------|------|
| `bin/integrated_app/templates/restore.html` | 已完成 | 已移除任务类型 Tab；右侧“模型设置”仅保留 DiT 模型、Seed、Resolution、Max Resolution；高级设置按 DiT / VAE / Upscaler 分组并默认折叠；`max_resolution` 默认值为 `0` 与工作流一致。 |
| `bin/integrated_app/static/css/style.css` | 已完成 | 已存在 `.sv-advanced-node-section` 分组样式。 |
| `bin/integrated_app/static/js/app.js` | 已完成 | 返回对象已导出 `api`，`SeedVR2.api.uploadRestore` 可正常调用。 |
| `config.yaml` | 已完成 | `inference` 段已按工作流节点默认值填充。 |
| `bin/integrated_app/routes/restore/unified.py` | 已完成 | 已移除冗余视频参数；新增 `_model_size_from_dit_model`；视频分辨率从 `config` 读取。 |
| `bin/integrated_app/config_models.py` | 已完成 | `VideoRestoreParams` 已精简为仅 `seed`。 |
| `bin/integrated_app/locales/*.yaml` | 已完成 | `restore.model_settings`、`restore.seed`、`restore.resolution`、`restore.max_resolution` 等键已补充。 |
| `_parse_workflow.py` | 已删除 | 临时脚本不存在于工作区。 |

### 2.2 待修复项

**问题 A：`app_server.py` 调用签名不匹配**

`bin/integrated_app/app_server.py:72` 当前调用：

```python
recovered_count = await unified_routes.recover_tasks(history_db, task_queue)
```

而 `bin/integrated_app/routes/restore/unified.py:901` 的函数签名为：

```python
async def recover_tasks(history_db: HistoryDB, task_queue: TaskQueue, config: dict | None = None) -> int:
```

视频任务恢复时需要 `config` 以读取 `restore.default_resolution_h / default_resolution_w`，否则恢复的视频任务会回退到硬编码默认值 `1080/1920`。必须传入 `config`。

**问题 B：批量任务进度轮询 URL 不匹配**

`bin/integrated_app/templates/restore.html:520` 当前调用：

```javascript
const data = await SeedVR2.api.get(`/api/restore/${batchId}/result`);
```

该 URL 命中的是单任务结果接口 `/{task_id}/result`，返回字段为 `status`、`progress`、`output_path` 等，不包含 `cached` 对象。前端后续访问 `data.cached` 会得到 `undefined`，导致 `total`、`completed`、`failed`、`current_index` 全为 `0/NaN`，批量进度条无法正常显示。

后端批量进度接口为 `/batch/{batch_id}/progress`，返回体包含：

```json
{
  "batch_id": "...",
  "status": "...",
  "progress": 0,
  "total": 0,
  "completed": 0,
  "failed": 0,
  "current_index": -1,
  "results": [],
  "media_type": "image"
}
```

前端应改为调用 `/api/restore/batch/${batchId}/progress`。

## 3. 具体修改方案

### 3.1 `bin/integrated_app/app_server.py` — 传递 config 给 recover_tasks

修改 `lifespan` 启动阶段调用：

```python
recovered_count = await unified_routes.recover_tasks(history_db, task_queue, config)
```

无需其他改动；`recover_tasks` 内部已将 `config` 通过闭包传给 `_process_video_task`。

### 3.2 `bin/integrated_app/templates/restore.html` — 修正批量进度轮询 URL

在 `pollBatchProgress` 函数中，将：

```javascript
const data = await SeedVR2.api.get(`/api/restore/${batchId}/result`);
const cached = data.cached || {};
```

改为：

```javascript
const data = await SeedVR2.api.get(`/api/restore/batch/${batchId}/progress`);
const cached = {
    total: data.total,
    completed: data.completed,
    failed: data.failed,
    current_index: data.current_index,
};
```

保持后续 `total`、`completed`、`failed`、`current_index` 读取逻辑不变。

## 4. 假设与决策

- **工作流为权威默认值**：已按 `SeedVR2.json` 节点参数完成对齐，本次不再调整数值。
- **视频输出尺寸来源不变**：视频任务的 `res_h` / `res_w` 继续从 `config.yaml` 的 `restore.default_resolution_h/w` 读取，不由前端表单控制。
- **批量进度接口唯一来源**：批量任务使用 `/api/restore/batch/{batch_id}/progress` 作为权威进度源，弃用原 `/{batch_id}/result` 轮询方式。
- **历史记录兼容**：`VideoRestoreParams` 保持 `extra="ignore"`，旧记录反序列化不会失败。

## 5. 验证步骤

1. **静态检查**：
   ```powershell
   ruff check bin/integrated_app tests
   ```
2. **单元测试**：
   ```powershell
   pytest tests/ -q
   ```
3. **启动应用**（可选，用于浏览器验证）：
   ```powershell
   python -m bin.integrated_app.app_server
   ```
4. **浏览器验证**：
   - 打开 `http://127.0.0.1:7870/restore`。
   - 确认无图像/视频 Tab，仅保留上传区与文件夹路径。
   - 确认右侧“模型设置”仅含：DiT 模型、Seed、Resolution、Max Resolution。
   - 确认“高级设置”默认折叠，展开后按 DiT / VAE / Upscaler 分组显示。
   - 上传单张测试图片，确认任务正常创建、进度更新、结果可下载。
   - 指定包含多个图片/视频的文件夹，点击“从文件夹批量处理”，确认批量进度条、成功/失败计数正常更新。
