# Seedvr2 AGENTS.md — AI 辅助开发指南

> 🧬 **自进化协议版本**：v1.26  
> 📅 **最后更新日期**：2026-08-27  
> 🎯 **对应项目版本**：v1.0.0（Apache-2.0 开源协议）

---

## 0. 文档优先级（单一事实来源）

当以下文档相互矛盾时，**以此顺序为准**，并立即按铁律 #1 修正靠后者：

1. 代码与配置本身（`pyproject.toml` / `package.json` / `.pre-commit-config.yaml` / 源码）
2. `docs/official_spec.md`（若本仓存在；当前本仓无此文件）
3. `AGENTS.md`
4. `README.md` / `docs/**`
5. `CHANGELOG.md`

> 判据：**能被机器验证的事实永远优先于自然语言描述。**

---

## ⚠️ 🤖 Agent 行为契约（自进化协议 · 必须严格遵守）

AI Agent 打开本文件后的**第一件事**是执行下面的「🧪 自进化自检清单」，并遵守以下 5 条铁律：

### 🔴 6 条自进化铁律
1. **🔄 同步规则（Synchronize First）**：如果发现项目实际情况（目录结构、依赖版本、技术栈、配置文件名等）与本文件描述 **不一致** → **立即更新本文件**，不要只改代码不改 AGENTS.md。这是最高优先级的规则。
2. **📝 坑点累积（Gotchas Accumulation）**：每次修复 Bug / 踩坑后（哪怕是很小的坑），**必须** 追加一条到第 11 节「常见陷阱 / 注意事项」，写清楚：触发场景、现象/报错、正确做法、首次发现日期。
3. **📚 SOP 累积（SOP Accumulation）**：每次完成一个「本文件现有 SOP 没覆盖」的典型开发任务后，**必须** 把步骤整理成新 SOP 追加到第 12 节「典型 AI 开发场景 SOP」。
4. **✅ 自检流程（Self-Check on Startup）**：每次打开本文件准备工作前，**必须** 先运行下面的「🧪 自进化自检清单」，逐项核对，有任何一项不符先修正 AGENTS.md 再干活。
5. **🏷️ 版本递增（Version Increment）**：每次更新本文件内容后，**必须** 做三件事：① 文件顶部「自进化协议版本号」+0.1（小改）或 +1.0（大改/框架调整）；② 更新「最后更新日期」；③ 在文件末尾「📋 自进化修订记录表」追加一行记录。
6. **🔬 证据绑定（Evidence Binding）**：本文件中每出现一个**可执行文件路径**（脚本、配置、workflow、源码），它必须是**当时可验证存在**的。引用前跑一次 `python scripts/check_spec_refs.py`；若确实想描述尚未实现的东西，必须显式加 `（计划，未实现）` 前缀。禁止把"CI 会阻断 X"写成一个 CI 里不存在的门禁。

### 🧪 自进化自检清单（每次启动工作前必跑）
- [ ] 目录结构（实际代码在 `app/integrated_app/` 下的 routes/ engines/ security/ middleware/ services/；另有根级 `common/`、`model_lib/`、`configs_3b/`、`configs_7b/`）是否和第 3 节模块边界描述一致？
- [ ] 禁区目录表（model_lib/、common/、configs_*）是否仍适用？如有新增禁区目录，是否已更新第 3.2 节？
- [ ] 上次工作是否踩了新坑？如果是，是否已追加到第 11 节常见陷阱？
- [ ] 新增的路由是否定义了 module-level 的 `router = APIRouter(...)`（自动发现契约见第 3.3 节，**不靠文件名**）？
- [ ] 是否修改了 `config.yaml` 结构或新增配置项？如果是，是否已同步 `app/integrated_app/config.py` / `config_models.py` 的 Pydantic 模型 + 本文件第 7 节启动命令说明？
- [ ] 上次更新是否正确递增了自进化协议版本号 + 追加了修订记录表？
- [ ] 本文引用的 scripts/ configs/ workflows/ 路径是否全部真实存在？（跑 `python scripts/check_spec_refs.py`，要求退出码 0）
- [ ] §pre-commit 表格是否与 `.pre-commit-config.yaml` **双向**一致？（既无虚构钩子，也无漏记实际钩子）

---

## 1. 项目概览

> **Seedvr2**：VR 场景多模态内容生成后端服务。  
> 定位：高性能、安全合规的 AI 推理网关，支持本地多种模型引擎的统一 API 接入。  
> 开源协议：**Apache-2.0**  
> 技术栈：**Python 3.11+ + FastAPI 0.115+ + Uvicorn + Pydantic v2 + SQLAlchemy 2.0 + AioSQLite + PyYAML** + 自研安全模块（PathGuard + CSRF + 完整性校验 + 水印嵌入）  
> 入口文件：`app/clean_launch.py`（推荐）或 `python -m uvicorn app.integrated_app.app_server:app`
> 默认监听：`http://127.0.0.1:7870`（禁止 `host="0.0.0.0"`，见第 11 节常见陷阱 #3）

> ⚠️ **实际结构修正（2026-08-14）**：以上目录结构（`api/`、`core/`、`engines/`、`configs/`）
> 为本仓库早期规划的目标结构，与当前实际实现不符。**当前实际入口与结构**：
> - 实际入口：`app/clean_launch.py`（推荐）或 `python -m uvicorn app.integrated_app.app_server:app`
> - 实际应用代码：`app/integrated_app/`（app_server.py + routes/ + engines/ + optimization/ + security/ 等）
> - 实际配置文件：**项目根目录 `config.yaml`**（无 `configs/` 目录；`configs_3b/`、`configs_7b/` 是模型架构配置）
> - 实际配置加载：`app/integrated_app/config.py`（`get_app_config()`）+ `config_models.py`（Pydantic）
> - 实际监听：`http://127.0.0.1:7870`
> 第 3 节「模块边界」、第 7 节「启动命令」、第 13 节「API 响应规范」仍以实际结构为准，
> 遇到不一致时以代码为准、并更新本文件。

---

## 2. 代码风格 & 格式约定

### 2.1 工具配置（pyproject.toml 已统一配置）
| 工具 | 用途 | 配置位置 |
|------|------|---------|
| **Ruff** | Lint + Import Sort | `[tool.ruff]`：`line-length = 100`，`target-version = "py311"`，select = ["E4", "E7", "E9", "F", "I", "W", "UP006~UP035"] |
| **Black** | 代码格式化 | `[tool.black]`：和 Ruff line-length 对齐，100 字符 |
| **Mypy** | 类型检查 | `[tool.mypy]`：`strict = false`（渐进式严格，核心文件逐步加 `# mypy: strict`） |

### 2.2 命名规则
- **文件/模块**：`snake_case.py`
- **类/异常**：`PascalCase`（例：`ModelLoadError(Exception)`）
- **函数/方法/变量**：`snake_case`（例：`async def generate_scene()`）
- **常量/枚举值**：`UPPER_SNAKE_CASE`（例：`MAX_IMAGE_SIZE = 4096`）
- **私有成员**：单下划线前缀 `_xxx`（模块级函数、内部方法）
- **豁免范围（以 `pyproject.toml` 为准，不存在额外豁免目录）**：`model_lib/` 同时被 ruff 的 per-file-ignores 与 mypy 的 exclude 覆盖；mypy 还对 `model_lib.*`、`common.distributed.*`、`common.diffusion.*` 设了 `ignore_errors`（这些模块需要真实 CUDA GPU 与权重才能验证）。历史文档写的 `engines/_legacy/`、`configs_local/` 两个豁免目录**不存在** → 系移植残留，已移除

### 2.3 Import 顺序（严格遵守，Ruff I 规则自动校验）
```python
# 1. 标准库（import os / import asyncio / from typing import Annotated）
# 2. 第三方库（import uvicorn / from fastapi import APIRouter）
# 3. 本地项目（from common.config import settings / from security.path_guard import safe_join）
```
> Ruff `isort` 配置为 `force-single-line = true`，禁止 `from fastapi import APIRouter, Depends, HTTPException` 这种一行多个 import。

### 2.4 类型注解（Mypy 要求）
- 所有 public 函数 / 方法必须加参数 + 返回值类型注解，例：
  ```python
  async def generate_scene(prompt: str, steps: int = 20) -> dict[str, object]:
      ...
  ```
- FastAPI 路由函数的 Pydantic 模型入参不需要重复写 `Annotated[XxxModel, Body()]`，直接 `def route(body: XxxModel)` 即可（Pydantic v2 默认行为）

---

## 3. 模块边界 & 关键规则（🚫 跨层引用严格禁止）

> 完整目录树、禁区目录表、路由自动发现规则、引擎契约现状已移至 [docs/project/MODULE_MAP.md](docs/project/MODULE_MAP.md)，移出时逐字保留。

**关键规则（保留）**：
- 跨层引用严格禁止（api/ 只做组装；core/ 不直接依赖 engines/）。
- 路由自动发现：模块级 `router` 变量即自动注册，文件名不要求 `_router.py` 后缀。
- 禁区：`model_lib/`、`security/`、`config.yaml` 禁止 AI 自动修改。

## 4. 测试约定

### 4.1 测试框架 & 运行命令
| 类型 | 框架 | 命令 | 覆盖率门槛 |
|------|------|------|:----------:|
| 单元测试 | pytest + pytest-asyncio + pytest-cov | `pytest tests/unit -q`（或脚本里 `python -m pytest tests/unit --cov=app/integrated_app --cov-report=term-missing`） | ≥ 70%（CI 强制：`--cov-fail-under=70`） |
| 集成测试 | pytest + TestClient（FastAPI）；用例**扁平放在 `tests/` 根目录**、靠 `@pytest.mark.integration` marker 选中（本仓无按目录分层的集成层） | `pytest tests/ -m integration -q` | 不计入 fail_under，但必须全部通过 |
| E2E / 浏览器测试 | Playwright + TypeScript；spec 在 `tests/specs/*.spec.ts`，配置 `tests/playwright.config.ts`（chromium / firefox / webkit × desktop / laptop / tablet / mobile 多 project） | **在 `tests/` 目录下**执行 `npx playwright test`（等价 `npm test`；需服务已监听 `http://127.0.0.1:7870`；只想验证用例收集加 `--list`） | 不计入 fail_under，但必须全部通过 |
| 安全测试 | pytest + 手动攻击用例（路径穿越 / CSRF / SQL 注入） | `pytest tests/security -q` | 必须 100% 通过，CI 中阻断 PR |
| 性能测试（手动） | pytest-benchmark（可选；当前未接入，参考 `perf/benchmark/`） | `pytest tests/perf -q` | 无强制，仅供参考对比 |

### 4.2 覆盖率分阶段路线图（诚实设定，逐步提升）

> 数值必须与 `pyproject.toml` 的 `fail_under` 一致（当前 = **70**）。历史报告中的 65 属旧快照，以其成文时间为准，不做改动。

| 阶段 | 目标 fail_under | 达成条件 | 状态 |
|---|---|---|---|
| 当前 | 70 | 已用 `--cov-fail-under=70` 锁定（`pyproject.toml`） | ✅ |
| M1 | +10 | 补齐 `model_lib/common`、`model_lib/video_vae_v3` 关键模块分支覆盖 | pending |

### 4.3 测试命名 & 结构
- 目录结构：`tests/`（扁平布局，非 `unit/integration/security` 分层；分层仅通过 marker 区分）
- 类名：`class Test<被测类>:`（PascalCase，首字母必须 Test）
- 方法名：`def test_<场景>_<期望结果>_<条件>():`（snake_case，前缀必须 test_）
  ```python
  # ✅ 正确示例
  class TestPathGuard:
      @pytest.mark.asyncio
      async def test_safe_join_blocks_path_traversal(self):
          with pytest.raises(SecurityError):
              await safe_join("/base", "../etc/passwd")
  ```
- **严禁 `assert True` 凑覆盖率**，每个断言必须对应真实行为验证
- Marker 说明（pyproject.toml 已注册）：`@pytest.mark.integration`（9 个 API/集成文件使用）、`@pytest.mark.benchmark`（当前未使用）；历史文档所述的 `security/slow/gpu` markers 与 `pytest-benchmark` 依赖不存在于本项目（AGENTS.md v1.13 同步）

---

## 5. 依赖管理

### 5.1 依赖文件分工
| 文件 | 用途 | 是否提交 Git |
|------|------|:------------:|
| `requirements.txt` | 生产依赖（FastAPI / Uvicorn / Pydantic / Pillow / AioSQLite 等） | ✅ |
| `requirements-dev.txt` | 开发依赖（pytest / ruff / mypy / coverage / pytest-asyncio / pre-commit） | ✅ |
| `requirements-lock.txt` | 完整锁定的依赖版本（含传递依赖），用于部署复现 | ✅ |
| `pyproject.toml` | 项目元数据 + 工具配置（Ruff / Black / Mypy / Pytest） | ✅ |

### 5.2 加新依赖的标准流程
1. 本地装好（`pip install xxx`），测通功能
2. 在 `requirements.txt` 加一行（不加版本号或加最低兼容版本号）
3. 开发依赖则加到 `requirements-dev.txt`
4. 执行 `python scripts/generate_lock.py`（或 `pip freeze --all > requirements-lock.txt` 后人工去 Python 本身的包）重新生成 lock 文件
5. `scripts/verify_engine.py` 跑一遍检查依赖完整性

---

## 6. 代码质量检查（提交前必跑）
```bash
# Lint + Import 排序
ruff check . --fix

# 格式化
black .

# 类型检查（核心文件）
mypy api/core.py api/main.py common/ core/ engines/

# 单测 + 覆盖率（fail_under=70，source=app/integrated_app）
pytest tests/ --cov=app/integrated_app --cov-fail-under=70 -q
```
> 提交前至少通过 `ruff check .`（没 fix 但没 error 也行）+ `pytest tests/`。

---

## 7. 构建 / 启动命令

### 7.1 一键启动脚本（推荐）
| 平台 | 安装依赖（首次） | 启动服务 |
|------|:---------------:|---------|
| **Windows** | 双击 / 终端执行 `install.bat` | 执行 `start.bat` → 自动打开 `http://127.0.0.1:7870/docs` |
| **Linux/macOS** | `chmod +x install.sh && ./install.sh` | `chmod +x start.sh && ./start.sh` |

### 7.2 手动启动命令（调试时使用）
```bash
# 方式 A（推荐，含环境自检 + 健康检查输出）
python app/clean_launch.py
# → 监听 http://127.0.0.1:7870

# 方式 B（纯 Uvicorn，适合前台调试）
python -m uvicorn app.integrated_app.app_server:app --host 127.0.0.1 --port 7870 --reload
# ⚠️ --reload 仅限开发！生产严禁使用 --reload（会重复加载引擎导致 GPU OOM）

# 生产启动（守护进程模式，建议用 systemd）
python -m uvicorn app.integrated_app.app_server:app --host 127.0.0.1 --port 7870 --workers 1
# ⚠️ workers 只能 1！模型引擎是单例全局的，多 worker 会重复加载模型到 GPU，直接 OOM
```

### 7.3 启动后验证
浏览器打开 `http://127.0.0.1:7870/docs` 能看到 Swagger UI → 点「GET /api/system/health」→ Try it out → Execute → 返回 200 OK，JSON 里有 `{"status": "ok", "system": {...}, "model": {...}, "gpu": {...}}` 之类的字段即启动成功（真实返回**无 `engines_loaded` 字段**，旧文档示例 `{"status":"ok","engines_loaded":3}` 不实；另有轻量探针 `GET /api/system/ping` 返回 `{"status":"ok","version":...}`）。

---

## 8. i18n 多语言

> ⚠️ 当前状态（2026-08-27 核实）：本仓库**未实现** gettext 多语言体系。
> `common/locale/`、`*.po`、`*.mo`、`scripts/update_pot.py` 均不存在，
> 历史文档中相关的 6 步新增翻译流程与「CI 阻断漏翻译」承诺**已失效**，不得作为工作依据。
> 若未来引入 i18n，需先补齐工具链再恢复本节。

### 8.1 当前真实机制（JSON 词表 + Jinja 全局函数）
| 环节 | 实际位置（已核实存在） | 说明 |
|------|----------------------|------|
| 翻译模块 | `app/integrated_app/i18n.py` | 提供 `t(key, locale=...)`、`get_available_locales()`、`current_locale` 状态；**不是** gettext 的 `_()` |
| 词表文件 | `app/integrated_app/locales/` | 5 个 JSON：`zh.json`、`zh-TW.json`、`en.json`、`ja.json`、`fr.json`（各 10 键）→ 语言是**中/繁/英/日/法**，历史文档写的「韩」不存在 |
| 可用语言清单 | `app/integrated_app/config_models.py` | `available_locales` 默认值 `["zh", "zh-TW", "en", "ja", "fr"]` |
| 模板取值 | `app/integrated_app/templates/` | 模板经 Jinja 全局函数 `t` 取词（用法见 SOP-6 的渲染冒烟脚本） |
| 一致性测试 | `tests/test_i18n.py`、`tests/specs/i18n.spec.ts` | pytest 单测 + Playwright E2E 双层覆盖 |

### 8.2 已知缺陷（勿依赖）
- `scripts/check_i18n_keys.py` **文件存在但当前跑不通**：它按 YAML 词表实现（`load_yaml_keys` + `*.yaml`），而实际词表是 `app/integrated_app/locales/*.json`，直接执行会报「未找到 YAML 语言文件」并以退出码 2 结束 → 该脚本**不构成任何 CI 门禁**。
- 新增翻译键的正确做法：在 5 个 JSON 文件里**同时**补齐同一 key（缺键不会有任何工具拦截，只能靠 `tests/test_i18n.py` 与人工 review），改完无需重新编译任何二进制文件。

---

## 9. 安全注意事项（🚫 不允许违反）

### 9.1 路径安全
- **所有文件 IO（读/写/删除/列目录）必须走 `security.path_guard.safe_join(base_dir, user_input_path)`**，禁止直接 `os.path.join` + `open()`，因为 `os.path.join("/base", "../etc/passwd")` 会拼接成 `/etc/passwd`（路径穿越漏洞）
  ```python
  # ❌ 错误写法
  with open(os.path.join(UPLOAD_DIR, filename), "wb") as f: ...

  # ✅ 正确写法
  safe_path = await safe_join(UPLOAD_DIR, filename)  # 路径穿越会抛出 SecurityError
  with open(safe_path, "wb") as f: ...
  ```

### 9.2 上传安全
- 文件大小限制（`config.yaml` → `security.max_upload_mb`，默认 100MB），超过直接返回 413
- MIME type 白名单 + 魔数双校验（不能只看扩展名）
- 生成输出必须经过 `security.watermark.embed_watermark(image_bytes)` 嵌入不可见版权水印

### 9.3 模型安全
- 权重加载前必须做 SHA256 完整性校验：入口是 `app/integrated_app/security/integrity_check.py` 的 `verify_model_files(pretrained_dir, model_cfg, precision)`，由 `app/integrated_app/engines/seedvr2_engine.py` 在读取 checkpoint 之前调用；任一项返回 False 即 `raise RuntimeError` 拒绝加载（CWE-353 防篡改/投毒）
- 期望哈希写在 **`config.yaml` 各模型条目** `model.models.<size>` 下的 `sha256_fp16` / `sha256_fp8` / `sha256_vae` / `sha256_pos_emb` / `sha256_neg_emb` 字段，新增模型必须同步补这些字段
- ⚠️ 不存在独立的 checksum 清单文件（历史文档写的 configs 目录下的 model_checksums.yaml → 本仓从未有过，系移植残留）
- ⚠️ 当前 `config.yaml` 里**一个 `sha256_*` 字段都没填** → `verify_checkpoint(..., skip_if_empty=True)` 直接跳过并打 WARNING，门禁实际空转（「校验通过」不等于「已验证」）。补齐哈希前，不得对外声称启动时已强制权重校验

### 9.4 网络安全
- **禁止在任何环境设置 `host="0.0.0.0"`**，默认只监听 `127.0.0.1`，外网访问必须套 Nginx 反向代理（带 HTTPS + Basic Auth + IP 白名单）
- SSE 接口必须过 CSRF token 校验（前端从 `/api/v1/csrf-token` 取 token，请求头带 `X-CSRF-Token`）

---

## 10. Git 提交规范 & 版本管理

### 10.1 Conventional Commits（和 TTS_MultiModel / Image_MultiModel 对齐）
```
<type>(<scope>): <subject>
```
Type 列表：`feat` / `fix` / `docs` / `style`（纯格式调整，非 UI）/ `refactor` / `perf` / `test` / `chore` / `ci` / `security`  
Scope 建议：`core` / `security` / `engines` / `routes` / `i18n` / `ci`

### 10.2 版本号同步修改清单（发版时必改 3 处）
| # | 文件路径 | 要改的字段 | 示例（v1.0.0 → v1.1.0） |
|---|---------|-----------|------------------------|
| 1 | `pyproject.toml` | `[project] version` | `version = "1.0.0"` → `version = "1.1.0"` |
| 2 | `common/config.py` | `APP_VERSION` 常量 | `APP_VERSION = "1.0.0"` → `APP_VERSION = "1.1.0"` |
| 3 | `CHANGELOG.md` 顶部标题 | `## [v1.x.x] - YYYY-MM-DD` | 对应新增一级 heading |

> Git Tag 格式：`git tag -a v1.1.0 -m "Release v1.1.0"`，推到 remote 后 GitHub Release GPG 签名自动构建。

---

## 11. 常见陷阱 / 注意事项（每条都有 ✅正确 / ❌错误对照）

> 完整内容已移至 [docs/project/KNOWN_ISSUES.md](docs/project/KNOWN_ISSUES.md)，移出时逐字保留；核心纪律见 §0/§3 硬约束。

## 12. 典型 AI 开发场景 SOP（照着做，少踩坑）

> 完整 SOP 已移至 [docs/project/AI_DEV_SOPS.md](docs/project/AI_DEV_SOPS.md)，移出时逐字保留；核心 SOP 一览：SOP-1 新增引擎 / SOP-2 调试 / SOP-3 发布。

## 13. API 响应规范（保持所有路由一致）

### 13.1 成功响应（统一包装）
所有成功响应必须走 `common.respond_success(data, message=None)`：
```python
# ✅ 正确
return respond_success({"scene_id": "xxxxx"}, message="Scene generated successfully")
# → HTTP 200: {"code": 0, "message": "Scene generated successfully", "data": {"scene_id": "xxxxx"}}

# ❌ 错误：裸返回 dict
return {"scene_id": "xxxxx"}  # 前端无法统一判断成功/失败
```

### 13.2 错误响应（统一用 FastAPI `HTTPException`，不要 raise 自定义异常然后全局 handler 转，除非是 Security 类的全局错误）
```python
# ✅ 正确
from fastapi import HTTPException

if scene_id not in db:
    raise HTTPException(status_code=404, detail="Scene not found")
```

---

## 14. 发布流程 & CI/CD 说明

### 14.1 CI 工作流（.github/workflows/ci.yml）
- 触发：push 到 `main` / `release/*`，以及所有 PR
- Jobs：
  1. `lint-and-typecheck`：`ruff check .` + `mypy api/ common/ core/ engines/ security/`
  2. `unit-tests`：依赖 lint 通过后，`pytest tests/unit --cov-fail-under=70`
  3. `security-tests`：`pytest tests/security -v`（100% 通过要求）
  4. E2E 不挂在 ci.yml 上 → 由独立工作流 `e2e.yml` 跑矩阵：`cd tests && npx playwright test --project=<project>`（见第 4.1 节；视觉回归 baseline 用 `update-baselines.yml` 手动触发重新生成）

### 14.2 发版标准步骤（Release Engineering）
1. 开分支 `release/v1.x.x`（从 main checkout）
2. 修改版本号（第 10.2 节 3 处：pyproject.toml / config.py / CHANGELOG.md）
3. 本地跑：`ruff + mypy + pytest 全量 + security 攻击测试`
4. 提交 PR 到 main，PR title 用 `chore(release): v1.x.x`（触发 release-please 流程）
5. PR 合 main 后，打 Git Tag `v1.x.x`（和版本号严格一致），推 remote
6. GitHub Release 页面会自动 GPG 签名构建产物（要求 PGP key 已在 GitHub Secrets 配置）

---

## 📋 自进化修订记录表（AGENTS.md 进化史）

| 自进化版本 | 日期 | 触发原因 | 更新内容摘要 | 对应项目版本 | 已校验 |
|:---------:|------|---------|------------|:------------:|:-----:|
| v1.0 | 2026-08-10 | 初始建立自进化协议 | 从 Seedvr2 项目健康度评估报告建议补齐：建立自进化协议（5 条铁律 + 自检清单）+ 启动命令章节 + i18n 翻译规范章节 + 版本号同步修改清单 + 发布流程 & CI/CD 说明 + API 响应规范 + 2 个典型 SOP | v1.0.0  | — |
| v1.1 | 2026-08-13 | 修改核心模块后完整性清单过期 | 新增第 11 节陷阱 #5（修改核心模块后忘记重新生成完整性清单）+ 新增 SOP-4（修改核心模块后重新生成完整性清单，含被覆盖模块清单、步骤、验证命令） | v1.0.0  | — |
| v1.2 | 2026-08-13 | 视频修复报 No module named 'models.video_vae_v3' | 新增第 11 节陷阱 #6（`bin/models` 常规包遮蔽项目根 `models` 命名空间包）；修复为给项目根 `models/` 补 `__init__.py` | v1.0.0  | — |
| v1.3 | 2026-08-13 | 视频两倍检测失败（CSP 拦截 blob 媒体） | 新增第 11 节陷阱 #7（CSP 缺 `media-src blob:` 导致 `<video>` 无法加载 blob 源）；修复为 `templates/base.html` CSP 加 `media-src 'self' blob:` | v1.0.0  | — |
| v1.4 | 2026-08-13 | 批量修复接口 422（api.post 硬编码 JSON 头） | 新增第 11 节陷阱 #8（`api.post` 硬编码 JSON 头导致 FormData 提交被 422）；修复为 `static/js/app.js` 的 `api.post` 自动检测 FormData 跳过 JSON 转换 | v1.0.0  | — |
| v1.5 | 2026-08-14 | 仓库「克隆即用」审计 + 新手保姆式引导 | ① 第 1 节补充「实际结构修正」块（实际入口 `bin/clean_launch.py`、根目录 `config.yaml`、监听 7870，与早期规划结构漂移的说明）；② 新增陷阱 #9（模型下载脚本落盘路径与 config 引用不一致，需放 `model/` 根目录）与 #10（新版 nvidia-smi `CUDA UMD Version` 格式导致 tokens 偏移）；③ README 升级为保姆式新手教程 + 模型权重下载保姆级说明；`scripts/download_model.py` 改为逐文件下载到根目录（幂等）；`install.bat` 自动探测 CUDA 版本选择 PyTorch index | v1.0.0  | — |
| v1.6 | 2026-08-16 | Blackwell 上安装 Triton 加速 + 排查三加速库可用性 | ① 新增陷阱 #11（Blackwell sm_120 上 PyTorch SDPA 的 FLASH/cuDNN 内核 `No available kernel`，实测只有 EFFICIENT/MATH 可用）；② 新增 SOP-5（安装 triton-windows 并开启 `torch_compile.enabled=true` 加速，含冒烟验证与「compiler 不治 CPU 换页」的警告）；③ 结论：flash-attn 无 torch2.13/cu132/py3.12 匹配轮子、sageattention 为训练向且量化损伤修复画质，均未接入 | v1.0.0  | — |
| v1.7 | 2026-08-16 | torch.compile 首次慢/重启重复编译 | ① 新增陷阱 #12（torch.compile 首次推理慢且重启后重复编译：inductor 默认缓存目录 `~/.cache/torch/inductor` 未生效，需在 `bin/clean_launch.py` 设 `TORCHINDUCTOR_CACHE_DIR` 到项目 `.torch_cache/inductor` 持久化，实测重启首次从 ~113s→~76s，`.torch_cache/` 已加 .gitignore）；② 实测结论：torch_compile 对视频稳态提速 ~22%（30.2 vs 38.8），对图片反而慢 ~27%；FP8 小图比 FP16 慢，视频 FP8 仅比 FP16 快 ~7%；最终配置定为 `default_precision: fp16` + `torch_compile.enabled: true`；③ 新增可复用基准脚本 `perf/benchmark/bench_restore_api.py` | v1.0.0  | — |
| v1.8 | 2026-08-16 | 修复工作台页面 v2 重构（结构布局 + 对比查看器升级） | ① restore.html 重构为工作台布局：页头单行化（`.sv2-header` + `.sv2-mode-seg`）、一体化画布工具条 `#canvasToolbar`（清除/替换/对比模式/方向/缩放/适配/重置/下载/再次修复）、画布舞台（上传/预览/进度/结果 四态）、参数侧栏收窄 + 折叠后右侧「参数」恢复入口；所有既有 id/name 保留（app.js 与 collectParams 硬编码依赖）；② app.js CompareSlider 升级为真实放大倍率语义：mag/oneToOneMag/tx,ty 平移、滚轮以光标为中心缩放、拖拽在放大态平移、双击 适配↔1:1、键盘固定 60px 步长、`#compareHud` 显示真实倍率；showRestoreResult/resetRestore 同步新工具条状态；③ style.css 追加 `sv2-*` 作用域样式（派生自 `--sv-*` 令牌，明暗主题自动适配）；④ 新增 SOP-6（修复页 v2 的 ID 契约 + 查看器语义 + 验证命令）；原文件备份于工作区 `outputs/migration-backup/` | v1.0.0  | — |
| v1.9 | 2026-08-16 | 修复页图片功能增量（预览查看器/放大镜/平移） | ① 上传图片后预览支持滚轮缩放、拖动平移、双击 适配/1:1、HUD 倍率：新增 `PreviewViewer` 类 + `initPreviewViewer/destroyPreviewViewer` 导出，结构 `#previewStage > #previewImgWrap > #imagePreview`；② 放大镜工具 `#btnMagnifier`（预览/结果共用，路由到 PreviewViewer/CompareSlider.setMagnifier），镜片 `.sv2-magnifier` 内 before/after 双层，`setLoupeLayer()` 按主视图倍率×2 渲染，对比模式下镜片内保持前后分割；③ 拖拽平移统一为左键（对比：左键未放大拖分割线/放大拖平移；预览：左键平移），右键不拦截避免与浏览器手势冲突；④ `initCompareSlider` 记录实例 + 新增 `getActiveCompareSlider/getActivePreviewViewer` 导出；SOP-6 补充增量说明 | v1.0.0  | — |
| v1.10 | 2026-08-16 | 用户反馈修正：平移改回左键 | 对比查看器与预览的拖动平移由「右键」改为「左键」（`e.button !== 0` 直接忽略），移除右键分支与 `contextmenu preventDefault`，右键完全归还浏览器/手势；同步更新查看器内提示文案与 SOP-6 增量说明 | v1.0.0  | — |
| v1.11 | 2026-08-16 | 修复页体验增强 ×4 | ① 完成/失败反馈：`showRestoreResult` 增加 meta.elapsedSec 显示「耗时」，失败显示错误卡 + 一键重试（`window.__retryRestore` 复用 startRestore）；② 参数方案预设：侧栏 `#presetSelect` + 保存/删除，复用 `_collectRestoreFormValues/_applyRestoreFormValues` 存 localStorage；③ 批量拖拽文件夹：dropzone capture 阶段检测目录 → 切批量模式 + 弹目录选择（浏览器限制无法读绝对路径）；④ 记住查看偏好：`sv_view_prefs`（方向/放大镜/对比模式）保存与恢复；SOP-6 补充增量说明 | v1.0.0  | — |
| v1.12 | 2026-08-16 | 高级设置无法滚动 + 参数无讲解 | ① 侧栏滚动修复：旧 `.sv-param-sidebar` 为 `overflow:visible` + `> *` `flex-shrink:0` + 固定 `max-height`，被 `.sv2-sidebar` 覆盖为 `overflow:hidden` 后展开的高级设置被裁剪且无法滚动；修复为侧栏内容包 `.sv2-sidebar-scroll`（`flex:1; overflow-y:auto`），`.sv-param-actions` 移出滚动区固定底部，并覆盖 `padding/max-height/position/flex-shrink`；② 高级设置全部参数补详细中文 `data-tooltip`（13 处，含 maxResolution/colorCorrection/batchSize/swap_io/编码解码 tile 与重叠/分块开关/tile_debug/uniform_batch/debug_mode），advParams 顶部加 `.sv-adv-intro` 总览说明（含隐藏参数由系统自动优化的提示）；tooltip 为纯 CSS `[data-tooltip]::after` 实现，直接加属性即可 | v1.0.0  | — |
| v1.13 | 2026-08-16 | 高级设置无法打开/展开区被压缩（二轮，Playwright 实测定位） | ① 根因一：`base.html` Google Fonts 样式表**同步阻塞 DOMContentLoaded**（弱网/被 CSP 拦截时 `readyState` 长期 `loading`）→ 页面内容可见但内联脚本（含高级设置点击）从未初始化；修复为 `media="print" onload="this.media='all'"` 异步加载 + noscript 兜底，新增陷阱 #13；② 根因二：旧规则 `.sv-param-sidebar .sv-advanced-params.open{flex-shrink:1;min-height:0}`（specificity 0,2,0）把展开后的 advParams 在 flex 容器里压缩到 12px，且 `overflow-y:visible` 与旧 `overflow-x:hidden` 混用被浏览器强制计算为 `auto`；修复为 `.sv2-sidebar .sv2-sidebar-scroll .sv-advanced-params.open{flex-shrink:0;min-height:auto;max-height:none;overflow:visible}`（0,3,0）并去掉 700px 展开限高，交由外层滚动容器统一滚动；③ 用 `tests/` Playwright 写临时诊断脚本（`_adv_check*.js`）实测：修复前 readyState=loading/advOpen=false/adv h=12px → 修复后 complete/advOpen=true/adv h=1276px/外层可滚，截图复查通过 | v1.0.0  | — |

| v1.25 | 2026-08-27 | **家族规范完整性审计（Phase B · B4）：自进化协议打补丁（第 6 条铁律 + 修订表已校验列）** | ① 新增第 6 条铁律「证据绑定（Evidence Binding）」：可执行路径必须当时可验证存在、未实现项须显式标注、禁止虚构 CI 门禁；② 自检清单追加两项：路径真实存在校验（跑 `python scripts/check_spec_refs.py`）与 pre-commit 双向一致校验；③ 修订记录表增加「已校验」列，历史行统一填 `—`（未校验），新条目须填 `✓ (check_spec_refs)` 或 `✗`；④ 本仓新增 `scripts/check_spec_refs.py` 家族审计 wrapper 与 `.github/workflows/docs-consistency.yml`（本地/含审计器环境强校验，纯 CI 环境找不到审计器时降级跳过保持绿）。本行即首个填写「已校验」的条目 | v1.0.0| ✓ (check_spec_refs) |
| v1.26 | 2026-08-27 | **家族规范治理 Phase C/D/E 落地（一致性·补齐·账本）** | C2 合规文档统一命名；C0 未入库 docs 链接标注；D1 §0 仲裁节；D2 docs/adr/ 架构决策记录；D3 FILEMAP+同步脚本；D4 禁区章节；D5 .github 治理层补齐；D6 许可证台账（model_lib 矩阵）；D9 覆盖率路线图（65→70 纠偏，与 pyproject.toml 一致）；E3 AGENTS 体量拆分 78.6KB→47.0KB；E4 迁移报告。以上各项均只更正与事实不符的表述，未新增任何未实现的承诺 | v1.0.0 | ✓ (check_spec_refs) |

<!-- 🔄 下次更新 AGENTS.md 时，在上面表格末尾追加新一行，不要删除历史记录 -->
| v1.14 | 2026-08-17 | 测试体系审计修复：门禁虚设/marker 失效/零覆盖模块/文档漂移同步 | ① e2e.yml：移除 `--update-snapshots` bootstrap（视觉回归改为 `--ignore-snapshots`，本地 win32 基线强制）+ ci.yml 删除空转的 `-m "not integration"`；② T2：9 个 TestClient 集成文件打标 `pytest.mark.integration`（test_api/schema/history_htmx/settings_routes/sse_integration/ui_routes + conftest）；③ T5：同步 AGENTS.md §4 实际测试布局（扁平 tests/ 非 unit/integration 分层）、marker 真实使用情况（integration/benchmark 注册 + 零使用 markers 说明）、缺失依赖说明；新增陷阱 #14（视觉门禁虚设）+ #15（marker 配置与执行策略脱节），CI/CD 章节注释修正为单 OS 触发与 pytest-cov 覆盖；版本递增 v1.14 | v1.0.0  | — |
| v1.15 | 2026-08-17 | 修复视频上传 `POST /api/restore` 被 403 永久拦截（CSRF 坏 cookie 自锁） | 根因：浏览器残留签名失效的 `csrf_token` cookie，原中间件仅在「cookie 缺失」时重种、有 cookie 就跳过 → 坏 cookie 永不淘汰，每次上传都 403「没有权限执行此操作」，与前端 `error.403` 文案对应。修复：① `middleware/csrf.py` 改为 `_has_valid_cookie()` 判定，对**安全方法响应与 403 失败响应一律补发有效 token**（`_set_csrf_cookie`），坏 cookie 自动替换自愈，并已 `scripts/generate_integrity_manifest.py` 重新生成清单（SOP-4）；② `static/js/app.js` 新增 `csrfSafeFetch`，非安全请求（`api.post/delete/uploadRestore`）统一携带 token，403 时自动重试一次。Playwright 实测：注入坏 cookie → 首次 403 → 服务端补发 → 重试 → 通过 CSRF 进入业务（503 模型未加载）。新增陷阱 #16 | v1.0.0  | — |
| v1.16 | 2026-08-17 | 点击「开始修复」自动加载模型再修复 | 需求：点开始修复时若模型未加载，自动加载模型后直接执行，无需手动预加载。实现：① `routes/restore/common.py` 新增 `ensure_model_loaded(model_manager, dit_model)`——调幂等的 `model_manager.load_model`（同模型已加载则短路，否则加载 dit_model 对应尺寸），失败抛 503「模型自动加载失败」；② `upload.py` 的 `POST /api/restore/` 与 `batch.py` 的 `POST /api/restore/batch` 移除原「`if not model_registry.model_loaded: 503 模型未加载`」守卫，改为 `await common.ensure_model_loaded(...)`（同时把模型尺寸与 dit_model 参数对齐，解决「已加载 3b 但选了 7b」的尺寸不符问题）；③ 两侧路由注入 `model_manager: ModelManager = Depends(get_model_manager)`；④ 更新 `tests/test_api.py` 的 `test_restore_without_model_returns_503` → `test_restore_auto_loads_model_when_not_loaded`（断言自动 await load_model 且不再以模型未加载 503 拒绝）。注意：`POST /api/restore/` 与 `/batch` 的错误响应 503 语义由「模型未加载」改为「GPU 不可用，或模型自动加载失败」 | v1.0.0  | — |
| v1.17 | 2026-08-17 | 修复长视频被「卡死清理」误杀（进度清零、GPU 白跑） | 根因：视频/图片 `progress_callback` 只更新内存缓存不写 DB，`tasks.updated_at` 停在上次异步状态更新（任务启动时）；`cleanup_stale_tasks` 仅按 DB `updated_at` 判卡死 → 正常推理的长视频任务被标记 failed、进度清零，底层推理仍跑完浪费 GPU。修复：给 `cleanup_stale_tasks(history_db, threshold_minutes, task_queue)` 增加可选 `task_queue` 参数，跳过 `task_queue.current_task_id()` 正在执行的任务（processing 里唯一合法的是运行中的那个），`app_server.py` 的 `_periodic_stale_cleanup` 调用处传 `app.state.task_queue`；新增 `tests/test_recovery.py::TestCleanupStaleTasks`（跳过运行中长任务 / 清理真卡死任务 / 无 task_queue 兼容）。新增陷阱 #17 | v1.0.0  | — |
| v1.18 | 2026-08-17 | 断点续传进度增强 + 双保险防误杀 | ① `routes/restore/common.py` 新增 `create_db_progress_persister(task_id, history_db, interval_seconds=30)`——捕获主事件循环，返回同步 `persist(progress)`，按间隔通过 `asyncio.run_coroutine_threadsafe` 把 `progress` 写 DB（同时刷新 `updated_at`），future 用 `contextlib.suppress` 消费避免「异常未获取」告警；② 接入三处进度回调：`upload.py` 单图 `_process_image_task`、`upload.py` 单视频 `_process_video_task`、`batch.py` 批量视频——长任务工作期间 DB `updated_at` 保持新鲜（配合 v1.17 的 skip-running 形成双保险：即使 `current_task_id` 瞬时为空也不会误杀），且进度落盘可在服务重启后由 `recover_tasks` 拿到更接近实时的进度；③ 新增 `tests/test_recovery.py::TestCreateDbProgressPersister`（节流只写一次 / 过间隔再写 / 最后进度生效） | v1.0.0  | — |
| v1.19 | 2026-08-17 | **AGENTS.md 自检：同步实际入口 + 端口** | 自检消除陈旧引用：第 1 节「入口文件」/ 默认监听、第 7 节「启动命令」、第 11 节陷阱 #3、**SOP-1 启动步骤**中原 `api/clean_launch.py` / `uvicorn api.main:app` 与 `7860` 均改与实际一致——入口统一为 `bin/clean_launch.py`（推荐）与 `python -m uvicorn bin.integrated_app.app_server:app`，监听端口统一 `7870` | v1.0.0  | — |
| v1.20 | 2026-08-17 | bin→app、models→model_lib、pretrained_models→model 三连目录重命名 | ① `pretrained_models/` → `model/`（权重目录，config.yaml `pretrained_dir` 默认值、下载/校验脚本、.gitignore/.dockerignore、README 同步）；② 项目根 `models/` 架构源码包 → `model_lib/`（全部 `from models.*` import 改 `model_lib.*`、`configs_*`/VAE YAML 路径、pyproject ruff/mypy/coverage、3.1 目录树与 3.2 禁区表、docs 同步；`app/models`（原 bin/models）保持不变，仅被 perf/test_suite.py 以 `app.models.*` 引用）；③ `bin/` → `app/`（全部 `bin.integrated_app` import 改 `app.integrated_app`，start/install/capture/run_checks 脚本、CI workflows、pyproject、Dockerfile CMD、playwright 配置、陷阱 #5/#12、SOP-1/4/5/6 同步）；陷阱 #6 同步为 `app/models` 遮蔽根 `model_lib`；README 目录树与 uvicorn 命令 bin→app | v1.0.0  | — |
| v1.21 | 2026-08-18 | 视频修复常态化三连：无法播放 / 无对比 / 耗时不同步 | ① **视频无法播放**根因是 `templates/base.html` 被我误用 PowerShell `Set-Content`（默认 ANSI/GBK）重写破坏编码 → 所有继承 base 的页面 Jinja 读 UTF-8 报 `'utf-8' codec can't decode`，FastAPI `ValueError` handler 返回 **422**，整页 JS 初始化失效（上传/视频/按钮全点不动）。修复：`data.decode('gbk',errors='replace').encode('gbk',errors='ignore').decode('utf-8',errors='replace')` 还原 + 语义补坏字 + `newline='\n'` 写回 + jinja 渲染冒烟；并补回一张 `var theme` 被同行注释吞掉的行（主题初始化失效）。新增陷阱 #18；② **视频无对比工具**：新增 `static/js/video-compare.js`（VideoCompareSlider 复用图片 CompareSlider 的分割线/缩放/平移/键盘/空格播放暂停），`restore.html` 新增 `videoCompareCard/videoPlainViewer` DOM，`app.js` `showRestoreResult` 视频分支默认进视频对比、编码不支持时回退单视频查看器，`bindCanvasToolbar` 对比开关支持视频双视图切换；③ **耗时不同步**：SSE 进度端点（`routes/restore/task.py`）`data` 增加 `processing_time`，`upload.py` 完成时 `update_task_state(..., processing_time=result.processing_time)`，前端 SSE completed 传 `processingTime`、`showRestoreResult` 优先用后端真实耗时（前端计时兜底） | v1.0.0  | — |
| v1.22 | 2026-08-19 | 测试体系质量加固：消除 11 处测试反模式 | ① **P0-视觉回归门禁虚设**：`e2e.yml` 移除 `--ignore-snapshots`，CI 中视觉回归断言不再被跳过，改用 `maxDiffPixelRatio: 0.01` 容忍跨平台渲染差异（新增陷阱 #19）；② **P0-覆盖率门禁虚设**：`pyproject.toml` omit 从通配符 `*/optimization/inference/*` 改为逐文件精确路径，移除 `progress.py`/`license_compliance.py`/`roadmap.py`/`engine/*` 等 6 个纯 Python 逻辑文件的 omit，`fail_under` 65→70；③ **P1-残缺断言**：`test_sse_integration.py` 两处 `in (200,404,422)` / `in (404,403)` 三态码改为精确 `== 404`；④ **P1-硬编码等待**：7 个 spec 文件共 11 处 `waitForTimeout` 全部消除，替换为 `waitForFunction`/`waitForResponse`/`requestAnimationFrame`/`networkidle` 等条件等待；⑤ **P1-触控目标宽松阈值**：`uiux-compatibility.spec.ts` 6 处 `toBeLessThanOrEqual(10/5)` 改为 `toBe(0)`；⑥ **P2-过度指定断言**：`test_api.py` health/gpu 端点从 15+ 字段名断言减少到 4-5 核心契约字段；⑦ **P2-性能阈值收紧**：FCP 15s→3s、LCP 5s→3s、页面加载 10s→5s、移除 2 处 `test.setTimeout(120000)`；⑧ **P2-flaky retry 消除**：`playwright.config.ts` retries 从 CI=2/local=1 降为 0；⑨ **P3-a11y 串行模式移除**：每个测试有独立 browser context 不需 serial；⑩ **P3-双重 catch 修复**：`image-restore.spec.ts` 双重 catch 改为 `expect(toast).toBeVisible()`；⑪ **P3-marker 文档同步**：第 6 节覆盖率命令从过时的 `tests/unit --cov=core --cov=engines --cov-fail-under=65` 更新为 `tests/ --cov=app/integrated_app --cov-fail-under=70` | v1.0.0  | — |
| v1.23 | 2026-08-20 | 降低使用门槛：模型透明化 + uv 支持 + 工作流可视化 + 文档站 | ① **模型透明化**：README 新增「模型格式、精度与下载直链」章节——明确格式为 safetensors（非 GGUF/PTH）、支持 FP16/FP8(E4M3FN) 且不兼容其他量化，新增各模型/精度显存/内存/效果对比表、下载直链模板（含 hf-mirror 加速）与选型建议；② **uv 支持**：`pyproject.toml` 补全 `[project].dependencies`（由 requirements.txt 迁移）+ `[dependency-groups].dev` + `[tool.uv]`（index 指向 cu128，torch 系列 sources 指定），已生成 `uv.lock`（138 包解析通过），README 快速上手补充 uv 安装方式；③ **demo 工作流可视化**：`demo/index.html` 新增「工作流」页面（nav + i18n 5 语言 + 命令面板）——处理管线 5 阶段可视化（预处理→VAE 编码→DiT 单步→融合/色彩校正→VAE 解码）+ 与 ComfyUI 对比表 + 关键事实卡片；④ **VitePress 文档站**：新建 `website/`（VitePress，config base=/SeedVR2-lite/docs/，9 个 markdown 页面：quickstart/install/models/usage/workflow/vram/faq/architecture/api/security），`pages-deploy.yml` 改为部署 demo+docs（先构建 docs 到 demo/docs/），`.gitignore` 增加 website/demo/docs 忽略；⑤ README 精简：顶部加文档站入口与快速导航表，压缩 .env/共享模式/VRAM/checkpoint/i18n 冗长配置段为要点，修正 bin→app 陈旧路径 | v1.0.0  | — |
| v1.24 | 2026-08-27 | 同源移植幻影清理（家族规范审计 Phase A · T3） | ① **删除从未实现的 gettext i18n 章节**：全仓（排除 `.venv`/`node_modules`）实测 0 个 `.po`/`.mo`/`.pot` 文件 → 第 8 节的 6 步 msgmerge/msgfmt 流程与「CI 阻断漏翻译」承诺作废，改为诚实状态标注 + 真实机制（`app/integrated_app/i18n.py` 的 `t()` + `app/integrated_app/locales/` 5 个 JSON = 中/繁/英/日/**法**，历史文档写的「韩」与 `common/` 下的 i18n 模块均不存在）；并记录 `scripts/check_i18n_keys.py` 虽存在但按 YAML 实现、当前执行即报错退出码 2 → 不构成任何门禁；连带清理第 1 节自检清单与 3.1 目录树中的 gettext 条目。② **测试分层表去幻影**：`tests/integration` 目录不存在 → 集成测试改为 marker 选择（`pytest tests/ -m integration -q`，实测收集 85/945 项）；新增 E2E 行写明真实形态与可执行命令（在 `tests/` 目录内 `npx playwright test`，配置是 playwright.config.**ts** 而非 .js，实测 `--list` 通过）；14.1 的第 4 个 job 由「ci.yml 内 integration-tests」改指独立工作流 e2e.yml。③ **移除 TTS 多引擎移植残留**：3.1 目录树 engines 块改为实际位置与 1 主类 + 5 mixin 的真实文件；新增 **3.4 引擎契约与注册现状**（`engine_interface.py` 的 RestoreEngine/BatchRestoreEngine/EngineRegistry 三 Protocol、唯一实现 `SeedVR2Engine`、由 `model_manager.py` 直接实例化、`_ModelRegistry.register` 全仓无调用点）；3.3 路由自动发现改写为实测机制（`auto_discover_routes` + `pkgutil` 递归扫描，契约是 module 级 `router` 变量而**非**文件名，且 import 失败只 warning 不阻断启动）；删除 `engines/auto_register.py`、`AbstractEngineProtocol`、`engines/_legacy/`（2.2 豁免目录与 3.2 禁区表两处）；SOP-1/SOP-2 同步为真实步骤（含 CSRF/basic_auth 是中间件、无 `require_*` 依赖；水印在引擎管线内调用而非 service 层）。④ **4 处路径过期 RETARGET**：configs 前缀的配置文件引用 → 仓库根级 `config.yaml`（实测已被 Git 跟踪、本仓无 config.example.yaml 模板，SOP-3 校验命令改用 `get_app_config()`，实测返回 AppConfig/11 段）；configs 前缀的 model_checksums.yaml → 真实机制 `app/integrated_app/security/integrity_check.py` 的 `verify_model_files`（期望哈希在 `config.yaml` 的 `sha256_*` 字段，当前一个都没填 → 门禁空转已在 9.3 显式标注）；`bin/clean_launch.py` 的现存式引用已无（仅历史行保留，不篡改进化史）。⑤ **CONTRIBUTING 跨仓复制节**：实测本仓**确有**视觉回归基线（`tests/specs/uiux-compatibility.spec.ts-snapshots/` 12 张 win32 PNG + `update-baselines.yml` 工作流）→ 按台账分支不删除，改为去掉「仅 TTS_MultiModel」误标并写明本仓真实路径与触发方式。⑥ **2 条死链**：README 新手引导 → `docs/project/FIRST_TIME_USER_GUIDE.md`；SECURITY 部署文档 → `docs/plans/DEPLOYMENT.md` | v1.0.0  | — |


## 路线图落地新增模块（2026-08-18，未提交）
- app/integrated_app/mcp_server.py — MCP Server（移植自 TTS_MultiModel）
- app/integrated_app/bad_case_retry.py — 容错重试（移植自 TTS_MultiModel）
- app/integrated_app/spec.py — 领域公式契约层
- scripts/render_pages.py + tests/frontend/smoke.js — 前端冒烟测试
- tests/test_mcp_server.py、tests/test_bad_case_retry.py、tests/test_spec.py

## 📂 文件归档与放置规范（重要：新增文件必须遵守）

> 本仓库目录已于 2026-08-23 系统整理（见 `docs/整理记录_20260823.md`）。后续任何新增/生成文件，**先判断类型再放置**，不要随意丢在仓库根目录或其他位置。

**docs/ 分类（项目文档）**
- `docs/project/`：需求(PRD)、架构、API、技术选型、设计上下文
- `docs/plans/`：实施计划、路线图、指南(Guide)、待办(TASKS)
- `docs/reports/`：评估/审计/安全/测试/优化报告、Lessons
- `docs/repo-analysis/`：仓库学习报告（命名 `{仓库名}_技术学习报告.md`）
- `docs/_devarchive/`：历史/一次性开发产物、交接方案、旧版本文档（**归档而非删除**）

**根目录只允许放置**
- 标准仓库文件：README、LICENSE、NOTICE、CONTRIBUTING、CODE_OF_CONDUCT、CHANGELOG、AGENTS、SECURITY、USER_AGREEMENT
- 构建与配置：build/gradle、pyproject.toml、config.yaml、requirements*.txt、uv.lock、Dockerfile、.gitignore、.env(.example)、启动脚本(start/install)
- 明确被 build/CI 或文档要求从根目录运行的工具（如 `run_checks.bat`、`run_verify.bat`）

**禁止事项（防止回归混乱）**
- ❌ 一次性调试脚本/截图/日志/草稿 → 放 `scripts/` 或 `docs/_devarchive/`，绝不堆在根目录
- ❌ 文档散落到 tests/perf/launcher/model 等业务目录 → 归入 `docs/` 对应分类
- ❌ 移动/删除 gitignored 运行时产物（`.watermark_key`、`.coverage`、`perf/monitoring_plan.md`）
- ❌ 移动被 CI 读取的文件（如 `launcher/release-notes-intro.md`、`model_lib/SOURCE.md`）
- ❌ 删除旧版本文档 → 需要留档移入 `docs/_devarchive/`

> 本仓库特别说明：`run_checks.bat`/`run_verify.bat` 按文档约定须在根目录运行，保留勿动；
> `verify_engine.py` 的权威版本在 `scripts/`，根目录不要再放一份。
> 新增文件前若不确定归属，先询问，不要自作主张放置。

---

## 远程同步铁律（Remote Sync Rule）

> 2026-08-27 家族治理补充：防止 AI 直写远程后本地/远程分叉。

1. **禁止静默直写远程**：任何通过 GitHub API / 网页端直接修改远程 main 的操作（CI 工作流、依赖配置、分支、PR 等），执行前必须向用户说明，执行后必须检查本地与远程差异。
2. **操作远程后必须同步**：直写远程导致本地落后时，必须提醒用户执行 `git pull`，或经用户同意后代为同步；禁止留下分叉状态。
3. **禁止动未提交改动**：用户本地存在未提交修改时，不得擅自 commit / push / stash / checkout 覆盖，必须先征得用户同意。
4. **优先走本地流程**：代码与配置修改默认在本地完成、经用户确认后 push；确需直写远程时，按第 2 条补同步。
