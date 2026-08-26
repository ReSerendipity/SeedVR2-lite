# SeedVR2 仅支持 NVIDIA CUDA GPU 清理计划

## 目标

SeedVR2 模型仅支持 NVIDIA CUDA GPU 推理，不支持 CPU 推理（官网明确说明）。需要全面清理代码库中残留的 CPU 推理支持，同时确保无 NVIDIA GPU 时应用仍可启动供用户预览界面（但推理功能禁用）。

## 当前状态分析

### 已完成的清理（前一轮）
- `gpu_backend.py`：移除 `_CPUStrategy` 类，但无 GPU 时 `raise RuntimeError` —— **需改为降级模式**
- `clean_launch.py`：无 CUDA 时 `sys.exit(1)` —— **需改为继续启动**
- `model_manager.py`：移除 CPU 回退逻辑 —— 已完成
- `seedvr2_engine.py`：`_resolve_device` 移除 CPU/MPS 回退 —— 已完成
- `settings.html`：移除 CPU 后端选项 —— 已完成
- `AGENTS.md`、`CONSTRAINTS.md`：更新硬约束 —— 已完成

### 待解决的问题

#### 问题 1：无 GPU 时应用无法启动（违反用户需求）
- `gpu_backend.py` 第 261 行：`gpu_manager = GPUBackendManager()` 模块级实例化
- 第 180-183 行：无 GPU 时 `raise RuntimeError` → 模块导入失败 → `app_server.py` 第 29 行导入报错 → 应用无法启动
- `clean_launch.py` 第 116, 120 行：`sys.exit(1)` 直接退出
- **用户要求**：无 GPU 时不要退出，让用户可以预览界面

#### 问题 2：`backend.value` 调用会 AttributeError
- `app_server.py` 第 83 行：`gpu_manager.backend.value`
- `gpu.py` 第 32 行：`info.backend.value`
- `health.py` 第 68 行：`gpu_backend.backend.value`
- 如果 `_backend = None`，这些调用全部崩溃

#### 问题 3：前端 'CPU' 默认值残留
- `system_status.html` 第 229 行：`health.gpu.device_name || 'CPU'`
- `index.html` 第 174 行：`health.gpu?.device_name || 'CPU'`

#### 问题 4：i18n 文件残留 CPU 推理文案
- 4 个语言文件（zh/en/ja/fr）的 `cpu_mode`、`backend_cpu` 键
- `en.yaml`、`fr.yaml` 的 `cpu_mode_tip`、`unsupported_gpu_tip` 仍暗示 CPU 可用

#### 问题 5：`base.html` 注入孤儿键
- 第 71 行：`"system.cpu_mode": "{{ t('system.cpu_mode') }}"`

#### 问题 6：测试文件残留 CPU 模式测试
- `tests/specs/system-status.spec.ts` 第 401-519 行：整个 CPU mode 测试块
- `tests/specs/image-restore.spec.ts` 第 210-214 行：测试 'cpu' 设备
- `tests/specs/video-restore.spec.ts` 第 301-305 行：同上

#### 问题 7：文档残留过时 CPU 描述
- `.trae/documents/seedvr2_improvement_plan.md`：多处提及 CUDA/CPU 双后端
- `.trae/documents/seedvr2_unified_restore_plan.md`：第 8, 9, 22, 50 行
- `.trae/documents/seedvr2_ruff_cleanup_plan.md`：第 212, 237 行提及 `_CPUStrategy`
- `docs/prototype_preview.html`：CPU 模式 UI 示例
- `docs/UI_DESIGN_ANALYSIS.md`：CPU 模式分析

### 合法保留的 CPU 引用（不修改）
- BlockSwap `offload_device="cpu"`：CUDA 推理的显存管理机制
- `torch.empty(0, device='cpu')`：参数释放
- `torch.load(..., map_location="cpu")`：权重加载到 CPU 后再迁移到 GPU
- `psutil.cpu_count()`：系统监控
- `cpu_cores` i18n 键：CPU 核心数显示
- 历史审计报告（`seedvr2-ui-audit-report.md`、`分析页面设计风格_*.md`）：保留作为档案

## 实施方案

### 决策：降级模式架构
无 NVIDIA GPU 时，应用进入"降级模式"：
- 应用正常启动，UI 完全可访问
- 推理功能禁用，显示明确警告
- 新增 `GPUBackend.UNAVAILABLE` 枚举值表示无可用后端
- `is_gpu_available` 返回 False，前端据此显示警告

---

### 步骤 1：修改 `gpu_backend.py` 实现降级模式

**文件**：`c:\Users\HONOR\Seedvr2\bin\integrated_app\gpu_backend.py`

**改动**：
1. `GPUBackend` 枚举新增 `UNAVAILABLE = "unavailable"` 值
2. `_detect_backend` 方法：无 GPU 时不抛异常，设置 `_backend = GPUBackend.UNAVAILABLE`，`_strategy = None`，记录警告日志
3. `is_gpu_available` 属性：返回 `self._backend == GPUBackend.CUDA`（UNAVAILABLE 时返回 False）
4. `device_str` 属性：无策略时返回 "cuda"（仅用于日志，不会实际执行推理）
5. `get_gpu_info`：无策略时返回 `GPUInfo(backend=UNAVAILABLE, name="未检测到 NVIDIA GPU", ...)`
6. `can_load_model`：UNAVAILABLE 时返回 False
7. `get_recommended_model_size`：UNAVAILABLE 时返回 "3b"（默认值，不会实际使用）

**验证**：`_backend` 永远不为 None，`backend.value` 调用安全

---

### 步骤 2：修改 `clean_launch.py` 不退出

**文件**：`c:\Users\HONOR\Seedvr2\bin\clean_launch.py`

**改动**：
- 第 112-120 行：无 CUDA 时不 `sys.exit(1)`，改为打印警告并继续启动
- 警告内容："CUDA 不可用，应用将以降级模式启动。推理功能不可用，请安装 NVIDIA GPU 以启用推理。"
- 未安装 PyTorch 时同样不退出，打印警告继续

---

### 步骤 3：修改 `app_server.py` 容错降级模式

**文件**：`c:\Users\HONOR\Seedvr2\bin\integrated_app\app_server.py`

**改动**：
- 第 83 行：日志容错 `gpu_manager.backend.value if gpu_manager.backend else 'unavailable'`
- 第 86-92 行：模型自动加载逻辑增加 `if gpu_manager.is_gpu_available:` 检查，无 GPU 时跳过自动加载

---

### 步骤 4：修改 `model_manager.py` 无 GPU 时友好报错

**文件**：`c:\Users\HONOR\Seedvr2\bin\integrated_app\model_manager.py`

**改动**：
- `load_model` 方法开头增加 GPU 可用性检查：无 GPU 时直接抛 `RuntimeError("SeedVR2 仅支持 NVIDIA GPU 推理，当前未检测到 NVIDIA GPU")`
- 避免进入后续显存检查和设备解析流程

---

### 步骤 5：修改 `gpu.py` 容错降级模式

**文件**：`c:\Users\HONOR\Seedvr2\bin\integrated_app\routes\system\gpu.py`

**改动**：
- 第 32 行：`info.backend.value` 已经安全（UNAVAILABLE 有 value）
- 无需修改，确认 backend.value 不会崩溃即可

---

### 步骤 6：修改前端模板支持降级模式

#### 6.1 `system_status.html`
**文件**：`c:\Users\HONOR\Seedvr2\bin\integrated_app\templates\system_status.html`

**改动**：
- 第 229 行：`|| 'CPU'` 改为 `|| '--'`
- 第 232-238 行：硬件支持提示逻辑改为：
  - `is_gpu_available=true`：绿色成功提示"NVIDIA GPU detected"
  - `is_gpu_available=false`：红色危险提示"未检测到 NVIDIA GPU，推理功能不可用"
- 第 240-274 行：`if (health.gpu.is_gpu_available)` 分支保持，但 else 分支（已删除的 CPU 模式）不恢复
  - 无 GPU 时 GPU 详细信息字段显示 '--'，隐藏 VRAM 环形进度条

#### 6.2 `index.html`
**文件**：`c:\Users\HONOR\Seedvr2\bin\integrated_app\templates\index.html`

**改动**：
- 第 174 行：`|| 'CPU'` 改为 `|| '--'`
- 第 167-171 行：Hero 状态点逻辑，无 GPU 时显示 'offline'（红色）
- 第 178-195 行：显存显示逻辑，无 GPU 时显示 'N/A'，不再显示 'CPU'

#### 6.3 `restore.html`
**文件**：`c:\Users\HONOR\Seedvr2\bin\integrated_app\templates\restore.html`

**改动**：
- 页面加载时检查 `is_gpu_available`，无 GPU 时：
  - 显示顶部警告横幅"未检测到 NVIDIA GPU，推理功能不可用"
  - 禁用"开始修复"按钮（`#btnStartRestore`、`#btnStartBatch`）
  - 文件上传仍允许（让用户预览界面）

#### 6.4 `base.html`
**文件**：`c:\Users\HONOR\Seedvr2\bin\integrated_app\templates\base.html`

**改动**：
- 第 71 行：移除 `"system.cpu_mode"` 键注入（前端不再使用）
- 第 73 行：保留 `"system.cpu_mode_tip"`（改为"不支持"提示文字）
- 第 74 行：保留 `"system.unsupported_gpu_tip"`

---

### 步骤 7：修改 i18n 文件

#### 7.1 `en.yaml`
**文件**：`c:\Users\HONOR\Seedvr2\bin\integrated_app\locales\en.yaml`

**改动**：
- 第 240 行：`cpu_mode_tip` → `"CPU inference is not supported. SeedVR2 requires an NVIDIA GPU with CUDA."`
- 第 241 行：`unsupported_gpu_tip` → `"Non-NVIDIA GPU detected. SeedVR2 only supports NVIDIA CUDA GPU."`
- 第 276 行：`cpu_mode` → `"CUDA Unavailable"`（或移除该键）
- 第 313 行：`backend_cpu` → 移除该行
- 第 318 行：`gpu_not_found` 已正确，保留

#### 7.2 `zh.yaml`
**文件**：`c:\Users\HONOR\Seedvr2\bin\integrated_app\locales\zh.yaml`

**改动**：
- 第 276 行：`cpu_mode` → `"CUDA 不可用"`
- 第 313 行：`backend_cpu` → 移除该行

#### 7.3 `ja.yaml`
**文件**：`c:\Users\HONOR\Seedvr2\bin\integrated_app\locales\ja.yaml`

**改动**：
- 第 276 行：`cpu_mode` → `"CUDA 利用不可"`
- 第 313 行：`backend_cpu` → 移除该行

#### 7.4 `fr.yaml`（如存在）
**文件**：`c:\Users\HONOR\Seedvr2\bin\integrated_app\locales\fr.yaml`

**改动**：
- 第 240 行：`cpu_mode_tip` → `"L'inférence CPU n'est pas supportée. SeedVR2 nécessite un GPU NVIDIA avec CUDA."`
- 第 241 行：`unsupported_gpu_tip` → `"GPU non-NVIDIA détecté. SeedVR2 ne supporte que les GPU NVIDIA CUDA."`
- 第 276 行：`cpu_mode` → `"CUDA Indisponible"`
- 第 313 行：`backend_cpu` → 移除该行
- 第 318 行：`gpu_not_found` → `"Aucun GPU NVIDIA détecté. SeedVR2 nécessite un GPU NVIDIA CUDA."`

---

### 步骤 8：修改测试文件

#### 8.1 `system-status.spec.ts`
**文件**：`c:\Users\HONOR\Seedvr2\tests\specs\system-status.spec.ts`

**改动**：
- 第 401-519 行：移除整个 `test.describe('CPU mode', () => {...})` 块
- 替换为 `test.describe('CUDA unavailable mode', () => {...})` 测试降级模式：
  - mock `is_gpu_available=false`，验证显示"未检测到 NVIDIA GPU"警告
  - 验证推理按钮禁用
  - 验证系统信息仍正常显示

#### 8.2 `image-restore.spec.ts`
**文件**：`c:\Users\HONOR\Seedvr2\tests\specs\image-restore.spec.ts`

**改动**：
- 第 210-214 行：将测试值从 `'cpu'` 改为 `'cuda:1'`（测试设备切换功能）

#### 8.3 `video-restore.spec.ts`
**文件**：`c:\Users\HONOR\Seedvr2\tests\specs\video-restore.spec.ts`

**改动**：
- 第 301-305 行：同上，改为 `'cuda:1'`

---

### 步骤 9：修改文档文件

#### 9.1 `.trae/documents/seedvr2_improvement_plan.md`
**改动**：
- 第 10 行：移除"CPU"提及
- 第 19 行：更新为"仅含 CUDA"
- 第 34, 38-40 行：移除 CPU 策略提及
- 第 48, 55, 226, 243, 254 行：更新为"仅 NVIDIA CUDA"

#### 9.2 `.trae/documents/seedvr2_unified_restore_plan.md`
**改动**：
- 第 8, 9, 22, 50 行：移除 CPU 提及，更新为"仅 NVIDIA CUDA"

#### 9.3 `.trae/documents/seedvr2_ruff_cleanup_plan.md`
**改动**：
- 第 212, 237 行：移除 `_CPUStrategy` 提及

#### 9.4 `docs/prototype_preview.html`
**改动**：
- 移除 CPU 模式 UI 示例（第 472, 592, 625, 702, 887, 891, 894-895, 899, 985, 1012, 1067 行）
- 或在文件顶部添加注释说明"原型已过时，最终实现仅支持 NVIDIA CUDA"

#### 9.5 `docs/UI_DESIGN_ANALYSIS.md`
**改动**：
- 第 57, 61, 62, 207, 212 行：移除或更新 CPU 模式分析

---

### 步骤 10：保护推理 API

#### 10.1 `restore/unified.py`
**文件**：`c:\Users\HONOR\Seedvr2\bin\integrated_app\routes\restore\unified.py`

**改动**：
- 推理提交 API（`submit_restore` 等函数）开头增加 GPU 可用性检查：
  ```python
  from bin.integrated_app.gpu_backend import gpu_manager
  if not gpu_manager.is_gpu_available:
      raise HTTPException(
          status_code=503,
          detail="SeedVR2 仅支持 NVIDIA GPU 推理，当前未检测到 NVIDIA GPU。请安装 NVIDIA GPU 并配置 CUDA 驱动。"
      )
  ```

---

### 步骤 11：复查

完成所有修改后，执行以下复查：

1. **代码复查**：
   - 全局搜索 `device.*=.*["']cpu["']` 确认无推理设备设为 CPU（排除 BlockSwap offload_device）
   - 全局搜索 `sys.exit` 确认 clean_launch.py 无退出
   - 全局搜索 `raise RuntimeError` 确认 gpu_backend.py 无抛异常
   - 全局搜索 `'CPU'` 在前端模板中无残留默认值

2. **启动验证**：
   - 模拟无 GPU 环境，确认应用正常启动
   - 确认 UI 可访问，显示降级警告
   - 确认推理按钮禁用

3. **文档一致性**：
   - 确认所有文档不再提及"CUDA/CPU 双后端"
   - 确认 AGENTS.md、CONSTRAINTS.md 一致

4. **测试验证**：
   - 确认测试文件无 CPU 模式测试
   - 确认降级模式测试覆盖

## 假设与决策

1. **假设**：`GPUBackend.UNAVAILABLE` 枚举值不会破坏现有代码（所有 `backend.value` 调用返回字符串 "unavailable"）
2. **决策**：无 GPU 时 `device_str` 返回 "cuda" 而非 "cpu"，避免触发任何 CPU 路径；该值仅用于日志
3. **决策**：推理 API 返回 503 状态码（Service Unavailable），符合 HTTP 语义
4. **决策**：历史审计报告（`seedvr2-ui-audit-report.md`、`分析页面设计风格_*.md`）保留不动，作为项目历史档案
5. **决策**：`cpu_mode` i18n 键保留但改为"CUDA 不可用"文案，避免破坏可能的引用；`backend_cpu` 键直接移除（UI 已无此选项）

## 验证步骤

1. 启动应用（有 GPU 环境）：确认正常启动，推理功能正常
2. 模拟无 GPU 环境（mock `torch.cuda.is_available()` 返回 False）：
   - 确认应用启动，不退出
   - 确认 UI 可访问
   - 确认显示"未检测到 NVIDIA GPU"警告
   - 确认推理按钮禁用
   - 确认推理 API 返回 503
3. 运行 `pytest` 确认 Python 测试通过
4. 运行 `npx playwright test` 确认前端测试通过
5. 全局搜索确认无残留 CPU 推理引用
