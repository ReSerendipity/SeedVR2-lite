# 典型 AI 开发场景 SOP

> 本文由 2026-08-27 家族治理 E3 从 AGENTS.md 移出，内容逐字保留。

<!-- 📥 新SOP追加模板（AI 完成新类型任务后复制填好追加到这里）：
#### SOP-X: [场景名称]
**适用条件**：什么情况下走这个流程
**步骤**：
1. 第一步...
2. 第二步...
3. 第三步...
**验证**：怎么确认操作成功
**关联文件**：
- path/to/file1.py
- path/to/file2.py
-->

#### SOP-1: 新增一个路由模块（遵循 3.3 节的自动发现契约）
1. 在 `app/integrated_app/routes/` 下（或其子包 `restore/`、`system/`、`ui/`）新建文件；文件名随意（现有如 `upload.py`、`batch.py`、`health.py`），**唯一硬要求是 module 级有个叫 `router` 的属性**
2. 文件开头：
   ```python
   from fastapi import APIRouter, Depends

   router = APIRouter(prefix="/api/xxx", tags=["xxx"])  # 变量名必须是 router！

   @router.get("/list")
   async def list_xxx():
       return {"data": []}
   ```
3. 路由文件完成后，**不需要** 在任何地方手动 `include_router`：`auto_discover_routes(app)` 会递归扫描并注册
4. 启动 `python app/clean_launch.py` → 打开 `/docs` 验证新路由是否在 Swagger UI 中；**如果没出现，先翻启动日志找「导入路由模块失败」告警**（扫描器会吞掉 import 异常、不阻断启动）
5. CSRF 与登录保护是**中间件层统一处理**的：`app/integrated_app/middleware/csrf.py`（非安全方法校验 `X-CSRF-Token`）与 `app/integrated_app/middleware/basic_auth.py`；本仓**没有** `require_csrf_token` / `require_bearer_token` 这类 FastAPI 依赖，路由内不要写 `Depends(require_*)`

#### SOP-2: 新增一种模型引擎实现（本仓当前为单引擎，事实见 3.4 节）
1. 在 `app/integrated_app/engines/` 下新建模块（现有布局是「一个主类 + 若干 mixin」，不要求建子包）
2. 实现 `app/integrated_app/engine_interface.py` 的 `RestoreEngine` 协议（`Protocol`，不是 ABC，无需显式继承）：
   ```python
   # 必须实现：is_loaded / get_model_info / load_model / unload_model
   #           infer_image / infer_video / estimate_vram_required
   # 需要批量能力则再实现 BatchRestoreEngine.infer_batch
   class NewEngine:  # 不需要写括号继承
       async def infer_image(self, image_path: str, output_dir: str, **kwargs) -> RestoreResult: ...
   ```
3. **不存在任何自动发现/自动注册**（历史文档写的 engines 目录下 auto_register.py → 系 TTS 移植残留，本仓从未实现）：必须在 `app/integrated_app/model_manager.py` 里按 `config.yaml` 的模型选择显式实例化（现网只有一处 `SeedVR2Engine(self.config)`）；`_ModelRegistry.register(name, cls)` 是为此预留的接口，但当前**全仓无调用点**，只注册不取用等于没生效
4. 补 `config.yaml` 的 `model.models.<size>` 条目（含 `sha256_*` 字段，见 9.3 节）后重启，用 `/api/system` 下的健康与模型状态端点确认引擎已加载
5. **安全要求**：输出图像/视频必须过水印。现网水印是在**引擎管线内**调用的（`app/integrated_app/engines/_image_pipeline.py` 与 `_video_pipeline.py` 中的 `embed_watermark`），不是 service 层 → 新引擎要在同样位置接上，不能跳过

#### SOP-3: 修改 config.yaml 新增配置项
1. 先在 `app/integrated_app/config_models.py` 的 Pydantic 模型加字段（含默认值 + type annotation；`AppConfig` 及其子模型即 `config.yaml` 的结构契约）
2. 在仓库根级 `config.yaml` 的对应段落加同一项并写好注释说明（本仓没有 config.example.yaml 模板，`config.yaml` 自身已提交 Git，见 3.1 节）
3. 更新本文件第 7 节启动命令或第 3 节模块边界描述（如果新增配置影响启动流程或模块边界）
4. 执行 `python -c "from app.integrated_app.config import get_app_config; print(get_app_config().model_dump())"` 验证新字段被正确加载（`get_app_config()` 返回校验后的 `AppConfig`）

#### SOP-4: 修改核心模块后重新生成完整性清单（改完必做）
**适用条件**：改动任何被启动自检覆盖的核心模块后，必须重新生成清单，否则下次启动会报「完整性校验失败」误报。
**被覆盖的核心模块**（清单见 `app/integrated_app/security/integrity_manifest.json`，自检列表见 `integrity_selfcheck.py` 的 `_CORE_MODULES`）：
- `app_server.py` / `config.py` / `model_manager.py`
- `security/` 下：`path_guard.py` / `integrity_check.py` / `watermark.py` / `integrity_selfcheck.py`
- `middleware/` 下：`csrf.py` / `basic_auth.py`
- `engines/seedvr2_engine.py`

**步骤**：
1. 完成上述任一核心模块的代码修改（改完逻辑后、提交前）
2. 运行 `python scripts/generate_integrity_manifest.py` 重新生成清单
3. 确认输出显示所有模块 `[OK]` 且生成路径为 `app/integrated_app/security/integrity_manifest.json`

**验证**：启动前先跑一次自检确认通过：
```python
python -c "from app.integrated_app.security.integrity_selfcheck import run_startup_selfcheck; print(run_startup_selfcheck())"
# 期望输出 failed=0，failed_files=[]
```
**关联文件**：
- scripts/generate_integrity_manifest.py
- app/integrated_app/security/integrity_selfcheck.py
- app/integrated_app/security/integrity_manifest.json

#### SOP-5: 安装 Triton 并启用 torch.compile 加速（Blackwell/Windows）
**适用条件**：在 RTX 50 系（Blackwell sm_120）+ Windows 上，想给模型推理开 torch.compile(inductor) 提速。
**背景**：Windows 上 torch 不自带 triton，必须装社区版 `triton-windows`；装上后 inductor 才能编译自定义内核。
**步骤**：
1. 确认环境：`python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.get_device_capability(0))"`（Blackwell 需 triton>=3.3 / torch>=2.7 / CUDA>=12.8）
2. 安装匹配的 triton-windows：`pip install triton-windows`（挑选与 torch 版本兼容的版本，如 torch 2.13 用 3.7.x）
3. 冒烟验证：对一个小函数 `@torch.compile` 跑一次 forward，确认 inductor 在 sm_120 上能编译通过、无 `No available kernel`
4. 接入项目：`config.yaml` → `inference.torch_compile.enabled` 改为 `true`（`backend: inductor`）。引擎代码已有 try/except 回退，编译失败会自动回到未编译，不会崩
5. 启动 `app/clean_launch.py`，确认服务正常启动、模型加载无误；首次推理会触发 DiT/VAE 编译（较慢属正常）
**注意**：torch.compile 只消除了算子融合/调度开销，**不能解决显存不足导致的 CPU 换页**（12GB 下瓶颈更多来自 `blocks_to_swap` / `fp8_enabled`）。提速收益要在确认瓶颈不是换页后才明显。
**关联文件**：
- config.yaml（`inference.torch_compile` 段）
- app/integrated_app/engines/seedvr2_engine.py（DiT/VAE 编译应用点）

#### SOP-6: 修复工作台页面 v2 重构（结构布局 + 对比查看器升级）
**适用条件**：修改 `templates/restore.html` 结构布局、`static/js/app.js` 的对比查看器（CompareSlider）、或新增 `sv2-*` 样式时，必须遵守本 SOP，否则会静默破坏既有功能。
**背景**：2026-08-16 将修复页重构为「工作台」布局（页头单行化 + 一体化画布工具条 + 画布舞台 + 参数侧栏），对比查看器升级为真实放大倍率语义（1:1 = 原像素 100%）。
**硬性约束（ID 契约）**：
- `app.js` 与 restore.html 内联脚本**硬编码大量 id**：`progressCard/resultCard/compareCard/compareViewport/compareContainer/compareSlider/compareBefore/compareAfterImg/resultVideo/btnDownload/canvasStateLabel/btnCanvas*/restoreFileInfo/restoreFileInput/restoreUploadZone/imagePreview/batchProgressCard/folderPath/btnScanFolder/btnStartBatch/btnBrowseFolder/folderScanResults` 及全部参数 id（`ditModel/resolution/doubleResToggle/vaeModel/blocksToSwap/maxResolution/colorCorrection/batchSize/encodeTileSize/encodeTileOverlap/decodeTileSize/decodeTileOverlap/btnVramRecommend/btnStartRestore/btnResetRestore/advParams/btnToggleParams`）——**改结构只能换 class，不能改 id/name**。
- `collectParams()` 遍历 `#paramsSidebar` 内 input/select（跳过 `#advParams`），再遍历 `#advParams`；`_collectRestoreFormValues` 依赖 `#paramsSidebar [name]`。所有参数字段必须留在侧栏内，含 hidden 字段（`seed/attention_mode/dit_cache_model/vae_cache_model/temporal_overlap/prepend_frames/input_noise_scale/latent_noise_scale/dit_device/offload_device` 等）。
- 上传区依赖 `setupUploadZone(zone, fileInput, {onFileSelected, onFileCleared})` 与内联脚本的 `restoreFileInfo/imagePreview/imagePreviewContainer/workflowGuide/previewArea` 显示切换，保留 `sv-dropzone-*` 类名可白嫖既有样式。
**SV2 结构**：`.sv2-workbench`（列布局）> `.sv2-header`（标题+`.sv2-mode-seg` 分段控件）+ `.sv2-body`（grid: 主区 + 324px 侧栏）> `.sv2-main`（批量工具条 `#batchToolbar` + 一体化工具条 `#canvasToolbar` + 画布舞台 `#previewArea[data-mode-pane=single]` + 批量面板 `[data-mode-pane=batch]`）。模式切换仍走 `switchMode()`（toggle `.sv-mode-tab` active + `[data-mode-pane].active` + batchToolbar display）。
**对比查看器 v2（CompareSlider）**：构造签名不变（`initCompareSlider(containerId, sliderId, afterId)`）；状态改为 `mag`（1=适配）/`oneToOneMag`/`tx,ty` 平移；transform = `translate(tx,ty) scale(mag)` 且 **transform-origin:0 0**（`.sv2-workbench .sv-compare-container`）；分割线位置用 `offsetWidth/Height` 而非 getBoundingClientRect（有 transform 时会算错）；滚轮以光标为中心缩放、拖拽在放大态平移、双击 适配↔1:1、键盘固定 60px 步长平移；HUD 元素 `#compareHud` 与 `#compareZoomLabel` 显示真实倍率。**新增 id**（`tbFileName/plainViewer/plainImg/compareHud/btnCanvasClear/btnCanvasReplace/btnCanvasCompare/btnRestoreSidebar/sv2Body`）在 `showRestoreResult`/`resetRestore`/内联 `bindCanvasToolbar` 中同步维护。
**验证命令**：
```bash
node --check app/integrated_app/static/js/app.js
# 模板全量渲染冒烟：
python - <<'EOF'
import json; from jinja2 import Environment, FileSystemLoader
loc=json.load(open('app/integrated_app/locales/zh.json',encoding='utf-8'))
env=Environment(loader=FileSystemLoader('app/integrated_app/templates'))
env.globals['t']=lambda k,**kw: loc.get(k,k); env.globals['current_locale']='zh'
html=env.get_template('restore.html').render(); print(len(html))
EOF
```
**注意**：SV2 样式全部追加在 `style.css` 末尾并派生自 `--sv-*` 令牌（明暗主题自动适配，勿硬编码色值）；对比查看器改造后**不要**再用 `btnCanvasZoom`（已删除），缩放走 `btnCompareZoomIn/Out`。
**2026-08-16 增量（预览查看器 / 放大镜 / 右键平移）**：
- 上传后图片预览支持缩放/拖动/双击适配/放大镜：`PreviewViewer` 类（app.js，工厂 `initPreviewViewer('previewStage','previewImgWrap','imagePreview')`，`destroyPreviewViewer()` 清理），结构在 `#imagePreviewContainer` 内（`.sv2-preview-stage` > `.sv2-preview-img` > `#imagePreview`）。
- 放大镜：`#btnMagnifier` 工具条按钮（预览/结果共用，按当前阶段路由到 PreviewViewer 或 CompareSlider 的 `setMagnifier(on)`）；镜片 `.sv2-magnifier`（`#previewMagnifier`/`#compareMagnifier`）内 `.mg-before`/`.mg-after` 两层，经 `setLoupeLayer()` 按「主视图当前倍率 ×2」渲染背景，对比模式下镜片内也保持前后分割（clip-path 跟随 `position`）。
- 平移交互：CompareSlider「左键」承担——未放大时拖分割线、放大后拖平移；预览无分割线，左键平移；右键不拦截（`e.button !== 0` 直接 return），避免与浏览器/鼠标手势冲突（2026-08-16 由「右键平移」按用户反馈改为「左键平移」）。
- 新增导出：`initPreviewViewer/destroyPreviewViewer/getActiveCompareSlider/getActivePreviewViewer`；`initCompareSlider` 现会记录实例到模块变量 `activeCompareSlider`。
**2026-08-16 增量（体验增强 ×4）**：
- 完成/失败反馈：`showRestoreResult(taskId, taskType, meta)` 新增 meta.elapsedSec（SSE completed 时由本地 `Date.now()-startTime` 传入），工具条显示「耗时 XX」；新增 `showRestoreError(msg)`——失败时结果区显示错误卡 `#errorCard` + `#btnRetry`，重试走内联注入的 `window.__retryRestore = startRestore`（文件与参数不变直接重跑）。
- 参数方案预设：侧栏顶部 `#presetSelect` + `#btnPresetSave/#btnPresetDelete`，复用内联 `_collectRestoreFormValues/_applyRestoreFormValues`（注意应用后需手动 `syncResolutionUI()` 同步两倍模式联动），存 localStorage `sv_restore_presets`。
- 批量拖拽文件夹：`#restoreUploadZone` 的 **capture 阶段** drop 监听检测 `webkitGetAsEntry().isDirectory`，命中则 `stopImmediatePropagation` 阻断单文件流程 → `switchMode('batch')` + `openDirBrowser` 弹目录选择（浏览器安全限制拿不到拖入文件夹的绝对路径，只能引导选择）。
- 记住查看偏好：localStorage `sv_view_prefs` = {dir, magnifier, compare}；CompareSlider.setMode/setMagnifier、PreviewViewer.setMagnifier、内联对比模式切换均写入；`showRestoreResult`（方向/放大镜/对比模式）与预览加载（放大镜）时恢复。

---

