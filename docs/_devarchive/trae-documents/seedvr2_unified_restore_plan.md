# SeedVR2 统一修复页面与清理计划

## 1. Summary

基于用户反馈，项目已完成以下改造：
- 导航快捷键改为 Alt+1~Alt+5 并始终可见。
- 视频/图像修复合并为统一页面 `/restore`，旧页面与后端路由已删除。
- GPU 后端限制为仅 NVIDIA CUDA，其他后端已移除（无 GPU 时以降级模式启动，可预览界面但推理禁用）。
- 系统状态页已添加 NVIDIA CUDA 硬件支持提示，并注入对应国际化键。
- 多语言文件中废弃的 `nav.video_restore` / `nav.image_restore` / `home.video/image_feature` / `image:` 区段已清理，`restore.*` 键已补齐。
- 根目录遗留脚本已删除。
- 统一修复接口测试已补充。

当前剩余工作：运行 `ruff check` 与 `pytest` 进行最终验证并修复可能暴露的问题。

## 2. Current State Analysis

| 项目 | 状态 | 说明 |
|------|------|------|
| `/restore` 页面与路由 | 已完成 | `routes/restore/unified.py`、`templates/restore.html`、`routes/__init__.py` 已就位。 |
| 旧页面/路由删除 | 已完成 | `video_restore.html`、`image_restore.html`、`routes/restore/video.py`、`routes/restore/image.py` 已不存在。 |
| GPU 后端限制 | 已完成 | `gpu_backend.py` 的 `_DETECTION_ORDER` 仅含 `CUDA`。 |
| 导航快捷键 | 已完成 | `base.html` 使用 Alt+1~Alt+5；`style.css` 的 `.nav-shortcut` 已设 `opacity: 1`。 |
| `app.js` 清理 | 已完成 | 旧函数已删除，仅保留 `uploadRestore`、`startRestoreProgressSSE`、`resetRestore` 等统一接口。 |
| CSRF 跳过路径 | 已完成 | `csrf.py` 已改为 `/api/restore/`。 |
| 系统状态 GPU 提示 | 已完成 | `system_status.html` 已引用 `system.nvidia_ready` 等键，`base.html` 已将其注入 `window.__I18N__`。 |
| 多语言清理 | 已完成 | 废弃键与 `image:` 区段已从 `zh.yaml`、`en.yaml`、`ja.yaml`、`fr.yaml` 中移除；`restore.*` 所需键已补齐。 |
| `restore.html` JS | 已完成 | 模板内部 JS 不再引用 `image.*` 键，统一使用 `restore.*` 命名空间。 |
| 根目录遗留脚本 | 已完成 | `visual_regression_test.py`、`verify_image_endpoint_AI.py` 已不存在。 |
| 统一修复接口测试 | 已完成 | `tests/test_api.py` 已新增 `TestUnifiedRestoreAPI` 测试类。 |
| 代码与模板静态检查 | 待执行 | 需运行 `ruff check` 与 `pytest` 做最终验证。 |

## 3. Proposed Changes

### 3.1 运行静态检查与测试
**操作：**
1. 运行 `ruff check bin/integrated_app tests`。
2. 运行 `pytest tests/ -q`。

**处理结果：**
- 若 `ruff` 报告新增错误，定位并修复。
- 若 `pytest` 有失败用例，定位并修复。
- 若均为历史遗留问题（与本次改动无关），记录但不额外修改。

**原因：** 确保合并后的代码与测试通过，不引入新的 Python 错误。

## 4. Assumptions & Decisions

- **废弃文件直接删除：** 按用户要求，不保留 `video_restore.html`、`image_restore.html`、旧路由文件及根目录遗留脚本，也不保留重定向或兼容层。
- **GPU 仅支持 NVIDIA CUDA：** 已在 `gpu_backend.py` 中限制检测顺序（`_CPUStrategy` 已移除）；系统状态页提示语已注入国际化键；无 GPU 时以降级模式启动。
- **`video:` 区段保留：** `restore.html` 的高级参数面板仍使用 `video.*` 键描述 DiT/VAE 等专业参数，因此 `video:` 翻译区段保留。
- **`image:` 区段删除：** 统一页面 JS 更新为 `restore.*` 后，`image:` 区段不再被引用，已安全删除。
- **快捷键维持 Alt+1~Alt+5：** 已在键盘数字区连续排列，手部移动距离最短，不再更改。

## 5. Verification Steps

1. 运行 `ruff check bin/integrated_app tests`，确认无新增 Python 错误。
2. 运行 `pytest tests/ -q`，确认 `TestUnifiedRestoreAPI` 等用例通过。
3. 启动应用后访问 `/system-status`，确认 GPU 提示横幅显示为当前语言。
4. 访问 `/restore`，切换四种语言，确认页面所有标签、按钮、提示均无缺失或英文回退。
5. 在 `/restore` 页面点击“扫描”文件夹，确认提示文本为 `restore.scanning` 等统一键翻译。
