# SeedVR2 改进实施计划（终稿）

## 1. 项目技术栈简述

SeedVR2 采用 **Python + FastAPI** 作为后端 Web 服务框架，**Jinja2 + Bootstrap 5 + 原生 JavaScript** 构建前端页面；核心推理基于 **PyTorch**，通过分阶段加载（VAE → DiT → VAE）的 ComfyUI 工作流模式运行 3B/7B 扩散模型。显存优化手段包括 **BlockSwap 块交换**、**Tiled VAE 分块编解码**、FP8/FP16 多精度推理以及 `expandable_segments` 显存分配策略。任务调度使用单 Worker 异步队列，历史记录与任务状态持久化到 **SQLite（WAL + FTS5）**，并通过 **SSE** 向前端实时推送进度。国际化基于 YAML 翻译文件，GPU 后端抽象层当前仅保留 **NVIDIA CUDA** 与 **CPU** 两种支持。

## 2. 本次改进目标

1. **导航快捷键始终可见**（已完成于 `style.css`，需同步更新 `base.html` 与 `app.js` 的键位）。
2. **系统状态页明确提示仅支持 NVIDIA CUDA GPU**（后端 `gpu_backend.py` 与前端提示横幅已就绪，需确认多语言键完整；无 GPU 时以降级模式启动，可预览界面但推理禁用）。
3. **合并视频/图像修复为统一页面 `/restore`**，删除旧页面与旧后端，统一由 `routes/restore/unified.py` 接管。
4. **快捷键改用手部移动距离最短的 Alt+数字（1-5）**：首页、修复、历史、系统状态、设置。
5. **废弃文件直接删除**，不保留兼容层或重定向。

## 3. 当前状态分析

| 模块 | 当前状态 | 说明 |
|------|----------|------|
| `bin/integrated_app/gpu_backend.py` | 已仅保留 CUDA | `_DETECTION_ORDER` 与 `_STRATEGY_MAP` 仅含 CUDA，docstring 已更新。 |
| `bin/integrated_app/static/css/style.css` | 快捷键已始终显示 | `.sv-nav-link .nav-shortcut` 默认 `opacity: 1`。 |
| `bin/integrated_app/templates/system_status.html` | 已添加 GPU 支持横幅 | `gpuSupportNotice` 根据后端动态设置提示文本。 |
| `bin/integrated_app/routes/restore/common.py` | 已创建 | 公共任务状态、常量、批量任务结构。 |
| `bin/integrated_app/routes/restore/unified.py` | 已创建 | 统一 `/api/restore/*` API 与 `recover_tasks`。 |
| `bin/integrated_app/routes/__init__.py` | 仍注册旧路由 | 仍指向 `video`/`image`，页面路由仍有 `/video-restore`、`/image-restore`。 |
| `bin/integrated_app/app_server.py` | 启动恢复仍引用旧模块 | `lifespan` 中导入 `image_routes` / `video_routes` 并调用恢复。 |
| `bin/integrated_app/templates/base.html` | 仍为两个旧导航项 | 快捷键为 Alt+H/V/I/Y/S/T，未合并。 |
| `bin/integrated_app/static/js/app.js` | 快捷键与 API 仍为旧版 | `uploadVideo`/`uploadImage`、进度 SSE 端点仍为 `/api/restore/video` 与 `/api/restore/image`。 |
| `bin/integrated_app/templates/index.html` | 仍为视频/图像两个入口 | 需合并为单一“修复”入口。 |
| `bin/integrated_app/templates/history.html`、`history_table.html` | 空记录引导指向 `/video-restore` | 需改为 `/restore`。 |
| `bin/integrated_app/middleware/csrf.py` | SSE 跳过路径仍为旧前缀 | `SKIP_PATHS` 中 `/api/restore/video/` 需改为 `/api/restore/`。 |

## 4. 详细变更方案

### 4.1 GPU 后端：仅 NVIDIA CUDA（确认与收尾）

**文件**：`bin/integrated_app/gpu_backend.py`

- 确认 `GPUBackend` 枚举仅含 `CUDA`（`CPU` 已移除，不支持 CPU 推理）。
- 确认 `_STRATEGY_MAP` 仅注册 `_CUDAStrategy()`（`_CPUStrategy` 已移除）。
- 确认 `_DETECTION_ORDER = [GPUBackend.CUDA]`。
- 若仍存在 ROCm/XPU/MPS 相关代码或辅助函数，彻底删除。
- 无 GPU 时应用以降级模式启动：可预览界面但推理功能禁用。

### 4.2 系统状态页 GPU 支持提示（确认多语言）

**文件**：`bin/integrated_app/templates/system_status.html`

- 保留 `gpuSupportNotice` 横幅。
- 逻辑已根据 `health.gpu.backend` 与 `is_gpu_available` 区分：CUDA 绿色、无 GPU 降级模式黄色、其他红色。

**文件**：`bin/integrated_app/locales/{zh,en,ja,fr}.yaml`

- 确保存在以下键（当前 `zh.yaml` 已有，需同步到其他三种语言）：
  - `system.gpu_support_title`
  - `system.nvidia_ready`
  - `system.degraded_mode_tip`
  - `system.unsupported_gpu_tip`

### 4.3 路由注册：切换为统一后端

**文件**：`bin/integrated_app/routes/__init__.py`

- `ROUTE_MODULES` 修改：
  - 删除 `("bin.integrated_app.routes.restore.video", "/api/restore/video", "视频修复")`
  - 删除 `("bin.integrated_app.routes.restore.image", "/api/restore/image", "图像修复")`
  - 新增 `("bin.integrated_app.routes.restore.unified", "/api/restore", "修复")`
- `register_page_routes` 修改：
  - 删除 `@app.get("/video-restore")` 与 `@app.get("/image-restore")`。
  - 新增 `@app.get("/restore")`，渲染 `restore.html`，`active_page="restore"`。
  - 现有 404 catch-all 会自动将 `/video-restore`、`/image-restore` 重定向到 `/`。

### 4.4 启动任务恢复：改为统一模块

**文件**：`bin/integrated_app/app_server.py`

- 将 `lifespan` 中的：
  ```python
  from bin.integrated_app.routes.restore import image as image_routes
  from bin.integrated_app.routes.restore import video as video_routes
  recovered_count = await image_routes.recover_tasks(history_db, task_queue)
  recovered_count += await video_routes.recover_tasks(history_db, task_queue)
  ```
  替换为：
  ```python
  from bin.integrated_app.routes.restore import unified as unified_routes
  recovered_count = await unified_routes.recover_tasks(history_db, task_queue)
  ```

### 4.5 CSRF 跳过路径更新

**文件**：`bin/integrated_app/middleware/csrf.py`

- 将 `SKIP_PATHS` 中的 `"/api/restore/video/"` 改为 `"/api/restore/"`，确保统一后的 SSE 进度端点不被 CSRF 拦截。

### 4.6 导航栏合并与快捷键改 Alt+1..5

**文件**：`bin/integrated_app/templates/base.html`

- 将“视频修复”“图像修复”两个 `<li>` 合并为一个：
  ```html
  <li>
      <a href="/restore" class="sv-nav-link {% if active_page == 'restore' %}active{% endif %}">
          <i class="bi bi-lightning-charge nav-icon"></i>
          <span>{{ t('nav.restore') }}</span>
          <span class="nav-shortcut">Alt+2</span>
      </a>
  </li>
  ```
- 其余导航项快捷键统一改为数字：
  - 首页 `/` → `Alt+1`
  - 修复 `/restore` → `Alt+2`
  - 历史 `/history` → `Alt+3`
  - 系统状态 `/system-status` → `Alt+4`
  - 设置 `/settings` → `Alt+5`
- `window.__I18N__` 注入：
  - 删除 `nav.video_restore`、`nav.image_restore`。
  - 新增 `nav.restore`。
  - 新增统一页面可能用到的 `restore.*` 键：`restore.title`、`restore.subtitle`、`restore.image`、`restore.video`、`restore.auto_detect`。

### 4.7 首页快速入口合并

**文件**：`bin/integrated_app/templates/index.html`

- 删除“视频修复”“图像修复”两个 `sv-quick-card`。
- 新增统一的“修复”卡片：
  ```html
  <a href="/restore" class="sv-quick-card">
      <div class="card-icon icon-restore">
          <i class="bi bi-lightning-charge-fill"></i>
      </div>
      <h3>{{ t('home.restore_feature') }}</h3>
      <p>{{ t('home.restore_feature_desc') }}</p>
      <div class="card-arrow">{{ t('home.start') }} <i class="bi bi-arrow-right"></i></div>
  </a>
  ```

### 4.8 历史记录空状态引导更新

**文件**：`bin/integrated_app/templates/history.html`、`bin/integrated_app/templates/history_table.html`

- 将空记录时的链接 `/video-restore` 改为 `/restore`。
- 按钮文本由 `nav.video_restore` 改为 `nav.restore`（或 `common.start`）。

### 4.9 前端全局脚本更新

**文件**：`bin/integrated_app/static/js/app.js`

- 快捷键映射改为：
  ```javascript
  const NAV_SHORTCUTS = {
      '1': { path: '/', label: '首页' },
      '2': { path: '/restore', label: '修复' },
      '3': { path: '/history', label: '历史记录' },
      '4': { path: '/system-status', label: '系统状态' },
      '5': { path: '/settings', label: '设置' },
  };
  ```
- 移除不再使用的函数（或保留但不公开）：`uploadVideo`、`uploadImage`、`startVideoProgressSSE`、`startImageProgressSSE`、`resetVideoRestore`、`resetImageRestore`。
- 公开 API 中新增：`uploadRestore(formData)`、`startRestoreProgressSSE(taskId, taskType)`、`resetRestore()`。
- 统一修复页面的具体提交、轮询、结果展示逻辑直接写在 `restore.html` 的 `{% block scripts %}` 中，避免 `app.js` 过度膨胀。

### 4.10 新建统一修复页面

**文件**：`bin/integrated_app/templates/restore.html`（新建）

页面结构：

1. **任务类型选择**：
   - `task_type` 字段：`auto`（默认，按扩展名自动检测） / `image` / `video`。
   - 以 Tab 或单选按钮呈现。
2. **左侧列**：
   - 输入源卡片：文件上传区（`restoreUploadZone` / `restoreFileInput`） + 文件夹路径（`folder_path`） + 开始按钮。
   - 单文件进度卡片（`#progressCard`）：进度条、状态、取消按钮。
   - 批量进度卡片（`#batchProgressCard`）：总进度、成功/失败计数、文件列表、重试按钮。
   - 结果卡片（`#resultCard`）：
     - 图像任务显示对比滑块（复用 `compareContainer`）。
     - 视频任务显示 `<video>` 播放器。
3. **右侧列**：
   - **模型与输出**：`dit_model`、图像输出 `resolution`、视频输出 `resolution_h` / `resolution_w` / `scale_factor` / `video_seed`。
   - **高级参数（默认折叠）**：DiT/VAE 的 device、blocks_to_swap、attention_mode、encode/decode tiled、tile size/overlap、color_correction 等，字段名与 `unified.py::parse_unified_params` 完全一致。
   - **视频输出设置（仅视频模式显示）**：`output_format`、`output_crf`。
4. **脚本**：
   - 使用 `SeedVR2.setupUploadZone` 绑定上传区。
   - 收集表单并 `POST /api/restore`。
   - 单文件使用 `EventSource /api/restore/{task_id}/progress`。
   - 批量任务使用 `POST /api/restore/batch`，轮询 `/api/restore/batch/{batch_id}/progress`。
   - 根据后端返回的 `task_type` 渲染图像对比或视频播放器。

### 4.11 国际化键补充

**文件**：`bin/integrated_app/locales/{zh,en,ja,fr}.yaml`

新增键（所有语言）：

```yaml
nav:
  restore: "修复"

home:
  restore_feature: "开始修复"
  restore_feature_desc: "上传图片或视频，使用 SeedVR2 模型进行超分辨率增强"

restore:
  title: "修复"
  subtitle: "上传图片或视频，SeedVR2 将自动按类型处理"
  image: "图像"
  video: "视频"
  auto_detect: "自动检测"
  select_task_type: "选择任务类型"
  upload_hint: "拖拽文件到此处，或点击选择"
  supported_formats: "支持图片（PNG/JPG/BMP/WEBP）与视频（MP4/AVI/MOV/MKV）"
```

保留 `video.*` 与 `image.*` 中仍在统一页面复用的键（如 `video.before_after`、`image.batch_from_folder`、`video.single_file_upload` 等）。

### 4.12 清理旧文件

删除以下文件：

- `bin/integrated_app/templates/video_restore.html`
- `bin/integrated_app/templates/image_restore.html`
- `bin/integrated_app/routes/restore/video.py`
- `bin/integrated_app/routes/restore/image.py`

## 5. 实施步骤（执行顺序）

1. **确认 GPU 后端**：检查 `gpu_backend.py`，确保仅 NVIDIA CUDA 策略（`_CPUStrategy` 已移除）。
2. **确认系统状态提示**：检查 `system_status.html` 与四语言 `system.*` 键。
3. **更新路由注册**：修改 `routes/__init__.py`。
4. **更新启动恢复**：修改 `app_server.py`。
5. **更新 CSRF 跳过路径**：修改 `middleware/csrf.py`。
6. **更新导航栏**：修改 `base.html`。
7. **更新首页与历史记录空状态**：修改 `index.html`、`history.html`、`history_table.html`。
8. **更新全局脚本**：修改 `app.js` 快捷键映射与 API 封装。
9. **创建统一页面**：新建 `restore.html`。
10. **更新多语言**：修改 `locales/{zh,en,ja,fr}.yaml`。
11. **删除旧文件**：删除 `video_restore.html`、`image_restore.html`、`video.py`、`image.py`。
12. **验证**：运行测试与静态检查，手动访问 `/restore`、快捷键、系统状态页。

## 6. 假设与关键决策

- **统一后端已就绪**：`routes/restore/unified.py` 与 `common.py` 已实现，本次以“接入、验证、清理”为主。
- **废弃文件不保留**：按用户要求直接删除旧模板与旧后端，不保留兼容路由或重定向。
- **无独立帮助页面**：将 NVIDIA CUDA 支持提示放在已有 GPU 信息的系统状态页。
- **快捷键使用 Alt+1..5**：数字键在键盘上横向连续，手部移动距离最短；避免使用 Ctrl+数字（浏览器标签页切换冲突）。
- **图像/视频参数共存**：统一页面同时渲染两套参数表单；后端根据 `task_type` 提取对应模型字段，未使用字段被忽略。图像修复时视频相关字段使用默认值即可。

## 7. 验证步骤

1. 启动应用，打开任意页面：
   - 导航栏显示 `Alt+1` ~ `Alt+5`，且始终可见。
   - 按 `Alt+1/2/3/4/5` 可跳转到对应页面。
2. 访问 `/system-status`：
   - NVIDIA 环境显示绿色 NVIDIA 就绪提示。
   - 非 NVIDIA 环境显示黄色降级模式提示（无 GPU 时可预览界面但推理禁用）。
3. 访问 `/restore`：
   - 页面布局正常，任务类型切换可用。
   - 上传图像/视频后调用 `/api/restore`，SSE 进度正常。
   - 图像完成后显示对比滑块；视频完成后显示播放器。
4. 批量文件夹：
   - 图像文件夹调用 `/api/restore/batch`，轮询 `/api/restore/batch/{id}/progress` 正常。
   - 视频文件夹同理。
5. 服务重启：
   - 未完成任务从数据库恢复并重新入队。
6. 旧地址：
   - `/video-restore`、`/image-restore` 自动 302 到 `/`。
7. 运行 `pytest tests/` 与 `ruff check bin/integrated_app tests`（已知历史问题除外），确认无新增错误。
