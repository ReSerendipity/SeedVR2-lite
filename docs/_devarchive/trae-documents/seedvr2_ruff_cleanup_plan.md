# SeedVR2 历史遗留 ruff 错误修复计划

## 1. Summary

修复项目 `bin/integrated_app` 和 `tests` 中全部 245 个 ruff 静态检查错误，使 `ruff check bin/integrated_app tests` 输出 0 errors。

## 2. Current State Analysis

按错误码分类（245 个总计）：

| 错误码 | 数量 | 说明 | 修复策略 |
|--------|------|------|----------|
| UP045 | 117 | `Optional[X]` → `X \| None` | `ruff --fix` 自动修复 |
| UP006 | 27 | `List[X]` → `list[X]`，`Dict[X,Y]` → `dict[X,Y]` | `ruff --fix` 自动修复 |
| I001 | 25 | import 排序不规范 | `ruff --fix` 自动修复 |
| F401 | 19 | 未使用的 import | `ruff --fix` 自动修复 |
| UP035 | 18 | `typing.X` 已弃用，改用 `collections.abc` 或内置 | `ruff --fix` 自动修复 |
| B007 | 8 | 循环变量未使用，需改为 `_name` | 手动修复 |
| UP015 | 6 | `open(..., "r")` 中 `"r"` 多余 | `ruff --fix` 自动修复 |
| E402 | 5 | 模块级 import 不在文件顶部 | 手动修复（加 `# noqa: E402`） |
| SIM105 | 4 | `try-except-pass` → `contextlib.suppress` | 手动修复 |
| F841 | 3 | 赋值后未使用的局部变量 | 手动修复 |
| SIM102 | 3 | 嵌套 `if` 可合并为 `and` | 手动修复 |
| F821 | 2 | 未定义变量 `result_np` | 手动修复（业务逻辑 bug） |
| UP009 | 2 | 不必要的 UTF-8 编码声明 | `ruff --fix` 自动修复 |
| N806 | 1 | 变量名 `LARGE_FILE_THRESHOLD` 不符合小写规范 | 手动修复 |
| F541 | 1 | f-string 无占位符 | `ruff --fix` 自动修复 |
| B905 | 1 | `zip()` 缺少 `strict=` 参数 | 手动修复 |
| B027 | 1 | 抽象基类空方法缺 `@abstractmethod` | 手动修复 |
| UP007 | 1 | `Union[X, Y]` → `X \| Y` | `ruff --fix` 自动修复 |
| A002 | 1 | 函数参数 `format` 遮蔽内置名 | 手动修复 |

**自动修复覆盖**：UP045 + UP006 + I001 + F401 + UP035 + UP015 + UP009 + F541 + UP007 = **196 个**，占总数 80%。

**手动修复**：B007 + E402 + SIM105 + F841 + SIM102 + F821 + N806 + B905 + B027 + A002 = **49 个**。

## 3. Proposed Changes

### 3.1 自动修复（ruff --fix）

**操作：** 运行 `ruff check bin/integrated_app tests --fix --unsafe-fixes`

**覆盖错误码：** UP045, UP006, I001, F401, UP035, UP015, UP009, F541, UP007

**影响文件（25 个）：** 所有 `bin/integrated_app/` 和 `tests/` 下的 Python 文件

**风险：** 低。这些是纯语法/风格变换，不改变运行时行为。`--unsafe-fixes` 仅用于 I001 的 import 重排，确保第三方/本地 import 分组正确。

### 3.2 手动修复 B007 — 循环变量未使用（8 处）

**文件与位置：**

1. `bin/integrated_app/cache.py:165,210,227` — `for root, dirs, files` → `for root, _dirs, files`
2. `bin/integrated_app/engines/seedvr2_engine.py:416,1445,1617` — `for name, module in ...` → `for _name, module in ...`
3. `bin/integrated_app/optimization/blockswap.py:297` — `for name, buffer in ...` → `for _name, buffer in ...`
4. `bin/integrated_app/optimization/blockswap.py:698` — `for module, module_name in ...` → `for module, _module_name in ...`

### 3.3 手动修复 E402 — 模块级 import 不在顶部（5 处）

**文件：** `bin/integrated_app/engines/seedvr2_engine.py:43-47`

**原因：** 该文件在 import 之前执行了 `sys.path.insert(0, ...)`，导致后续 import 必须在路径修改之后。这是项目结构决定的，无法简单移动。

**操作：** 在第 43-47 行的每个 import 语句末尾添加 `# noqa: E402`。

### 3.4 手动修复 SIM105 — try-except-pass → contextlib.suppress（4 处）

1. `bin/integrated_app/config.py:92-95`:
   ```python
   # 修改前
   try:
       os.unlink(tmp_path)
   except OSError:
       pass
   # 修改后
   with contextlib.suppress(OSError):
       os.unlink(tmp_path)
   ```

2. `bin/integrated_app/engines/seedvr2_engine.py:418-421`:
   ```python
   # 修改前
   try:
       module.get_axial_freqs.cache_clear()
   except Exception:
       pass
   # 修改后
   with contextlib.suppress(Exception):
       module.get_axial_freqs.cache_clear()
   ```

3. `bin/integrated_app/progress.py:100-103`:
   ```python
   # 修改前
   try:
       cb()
   except Exception:
       pass
   # 修改后
   with contextlib.suppress(Exception):
       cb()
   ```

4. `bin/integrated_app/routes/system/sse.py:143-146`:
   ```python
   # 修改前
   try:
       yield _format_sse("error", {"message": "SSE 连接异常，请刷新页面重试"})
   except Exception:
       pass
   # 修改后
   with contextlib.suppress(Exception):
       yield _format_sse("error", {"message": "SSE 连接异常，请刷新页面重试"})
   ```
   **注意：** 此处 `contextlib.suppress` 内含 `yield`，在 async generator 中使用 `with` 语句是合法的。

### 3.5 手动修复 F841 — 赋值后未使用的局部变量（3 处）

1. `bin/integrated_app/engines/seedvr2_engine.py:595` — `temporal_overlap = inf["temporal_overlap"]`:
   **操作：** 删除该行。该变量在后续代码中未使用。

2. `bin/integrated_app/engines/seedvr2_engine.py:596` — `prepend_frames = inf["prepend_frames"]`:
   **操作：** 删除该行。该变量在后续代码中未使用。

3. `bin/integrated_app/model_manager.py:218` — `previous_engine = model_registry.get_engine()`:
   **操作：** 删除该行。`previous_size`、`previous_precision`、`previous_loaded` 在后续回滚逻辑中使用，但 `previous_engine` 未被引用。

### 3.6 手动修复 SIM102 — 嵌套 if 合并（3 处）

1. `bin/integrated_app/optimization/blockswap.py:601-603`:
   ```python
   # 修改前
   if getattr(self, "_blockswap_bypass_protection", False):
       if hasattr(self, '_original_to'):
           return self._original_to(device, *args, **kwargs)
   # 修改后
   if getattr(self, "_blockswap_bypass_protection", False) and hasattr(self, '_original_to'):
       return self._original_to(device, *args, **kwargs)
   ```

2. `bin/integrated_app/optimization/memory_manager.py:368-369`:
   ```python
   # 修改前
   if hasattr(module, 'memory') and module.memory is not None:
       if torch.is_tensor(module.memory) and (module.memory.is_cuda or module.memory.is_mps):
   # 修改后
   if hasattr(module, 'memory') and module.memory is not None and torch.is_tensor(module.memory) and (module.memory.is_cuda or module.memory.is_mps):
   ```

3. `bin/integrated_app/optimization/memory_manager.py:411-412`:
   ```python
   # 修改前
   if buffer.is_cuda or (hasattr(buffer, 'is_mps') and buffer.is_mps):
       if buffer.numel() > 0:
   # 修改后
   if (buffer.is_cuda or (hasattr(buffer, 'is_mps') and buffer.is_mps)) and buffer.numel() > 0:
   ```

### 3.7 手动修复 F821 — 未定义变量 `result_np`（2 处，同一行）

**文件：** `bin/integrated_app/engines/seedvr2_engine.py:1167`

**当前代码：**
```python
"output_resolution": f"{result_np.shape[1]}x{result_np.shape[0]}" if 'result_np' in dir() else f"{res_w}x{res_h}",
```

**问题：** `'result_np' in dir()` 检查的是当前局部作用域的变量名列表，但 `result_np` 在此处从未定义，`dir()` 中不会包含它，因此条件永远为 `False`，回退到 `f"{res_w}x{res_h}"`。

**操作：** 删除无效的条件分支，直接使用 `f"{res_w}x{res_h}"`：
```python
"output_resolution": f"{res_w}x{res_h}",
```

### 3.8 手动修复 N806 — 变量名不符合小写规范（1 处）

**文件：** `bin/integrated_app/cache.py:74`

```python
# 修改前
LARGE_FILE_THRESHOLD = 10 * 1024 * 1024
# 修改后
large_file_threshold = 10 * 1024 * 1024
```

### 3.9 手动修复 B905 — zip() 缺少 strict=（1 处）

**文件：** `bin/integrated_app/engines/seedvr2_engine.py:1927`

```python
# 修改前
for noise, aug_noise, latent_blur in zip(noises, aug_noises, cond_latents)
# 修改后
for noise, aug_noise, latent_blur in zip(noises, aug_noises, cond_latents, strict=True)
```

### 3.10 手动修复 B027 — 抽象基类空方法缺 @abstractmethod（1 处）

**文件：** `bin/integrated_app/gpu_backend.py:66`

```python
# 修改前
def synchronize(self) -> None:
    """同步设备（默认无操作）"""
    pass
# 修改后（添加 raise NotImplementedError，因为子类应覆盖此方法）
def synchronize(self) -> None:
    """同步设备"""
    raise NotImplementedError
```

**注意：** 不使用 `@abstractmethod` 是因为 `_CPUStrategy` 已移除（SeedVR2 不支持 CPU 推理），仅 `_CUDAStrategy` 需要覆盖 `synchronize`。改为 `raise NotImplementedError` 更明确：子类必须覆盖或显式处理。

### 3.11 手动修复 A002 — 函数参数遮蔽内置名（1 处）

**文件：** `bin/integrated_app/video_processor.py:139`

```python
# 修改前
def extract_frames(self, video_path, output_dir, format="png", ...):
# 修改后
def extract_frames(self, video_path, output_dir, fmt="png", ...):
```

**需同步修改：** 该方法内部所有引用 `format` 的地方改为 `fmt`，以及调用处。

### 3.12 最终验证

1. 运行 `ruff check bin/integrated_app tests`，确认输出 `0 errors`。
2. 运行 `pytest tests/ -q`，确认全部测试通过。
3. 启动应用验证基本功能正常。

## 4. Assumptions & Decisions

- **E402 不移动 import：** `seedvr2_engine.py` 的 `sys.path.insert` 在 import 之前是项目结构决定的，无法简单移动 import 到顶部，因此使用 `# noqa: E402` 抑制警告。
- **F821 删除无效分支：** `result_np` 从未定义，`'result_np' in dir()` 永远为 `False`，直接删除无效分支。
- **B027 使用 `raise NotImplementedError`：** 而非 `@abstractmethod`，因为 `_CPUStrategy` 已移除（不支持 CPU 推理），使用 `raise NotImplementedError` 让 `_CUDAStrategy` 子类显式处理 `synchronize` 更清晰。
- **A002 改名为 `fmt`：** 而非 `format_`，因为 `fmt` 是 Python 社区中替代 `format` 参数的常见命名。
- **自动修复先于手动修复：** 先运行 `ruff --fix` 处理 196 个自动修复项，再处理 49 个手动项，避免自动修复覆盖手动修改。

## 5. Verification Steps

1. `ruff check bin/integrated_app tests` 输出 `All checks passed!`。
2. `pytest tests/ -q` 全部通过。
3. 应用可正常启动，`/restore`、`/system-status`、`/history`、`/settings` 页面可访问。
