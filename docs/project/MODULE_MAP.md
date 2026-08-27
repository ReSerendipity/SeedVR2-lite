# 模块边界 & 关键规则

> 本文由 2026-08-27 家族治理 E3 从 AGENTS.md 移出，内容逐字保留。

### 3.1 目录结构 & 职责
```
Seedvr2/
├── api/                 ← FastAPI 入口（只做组装，不写业务逻辑）
│   ├── main.py          ← create_app() + lifespan（启动时预加载引擎）
│   ├── clean_launch.py  ← 推荐启动入口（含健康检查 + 环境自检）
│   └── routes/          ← 路由（实际在 app/integrated_app/routes/，契约见 3.3 节：module 级 router 变量即自动注册）
├── common/              ← 公共基础设施（config.py / logger.py / exceptions.py）
│   ├── config.py        ← Pydantic BaseSettings 读 config.yaml（单例）
│   └── security.py      ← 通用安全工具（CSRF token 生成、密码哈希）
├── core/                ← 业务逻辑层（services + repositories + workflows）
│   ├── services/        ← 业务服务（SceneGenerateService / HistoryService）
│   ├── repositories/    ← 数据访问（HistoryRepoDB / CacheRepo）
│   └── workflows/       ← 编排多步任务（VR 场景生成 pipeline）
├── app/integrated_app/engines/  ← 模型引擎层（**实际位置**；单引擎 = SeedVR2 视频/图像超分修复）
│   ├── seedvr2_engine.py        ← SeedVR2Engine 主类（组合下列 mixin）
│   ├── _memory_utils.py         ← 显存监控函数、数据变换类、常量、ImageInferenceConfig
│   ├── _vae_pipeline.py         ← VAE 编解码管线 mixin
│   ├── _dit_pipeline.py         ← DiT 采样管线 mixin
│   ├── _video_pipeline.py       ← 视频推理管线 mixin（水印调用点）
│   └── _image_pipeline.py       ← 图像推理管线 mixin（水印调用点）
│   （接口契约在同包上一级：app/integrated_app/engine_interface.py 的 3 个 Protocol，见 3.4 节）
├── model_lib/           ← 第三方模型权重 & 代码（🚫 禁区：AI 不允许自动修改）
│   ├── diffusion/
│   └── llm/
├── security/            ← 安全模块（独立层，不能依赖 core/engines 以外的业务层）
│   ├── path_guard.py    ← 路径安全校验（防路径穿越，所有文件 IO 必须过 safe_join）
│   ├── csrf.py          ← CSRF 中间件（SSE 接口 + 表单提交必须校验）
│   ├── integrity.py     ← SHA-256 文件完整性校验（模型文件 + 输出作品）
│   └── watermark.py     ← 不可见水印嵌入（所有生成图像必须加水印）
├── db/                  ← 数据库
│   └── history.db       ← AioSQLite，存生成历史 + 用户作品元数据
├── config.yaml          ← 全局配置（**仓库根级、唯一、且已提交 Git**；本仓无 `configs/` 目录，也没有 config.example.yaml 模板）
├── configs_3b/          ← SeedVR2-3B 的模型架构配置（随第三方权重，属禁区）
├── configs_7b/          ← SeedVR2-7B 的模型架构配置（随第三方权重，属禁区）
├── tests/               ← 测试（第 4 节详细说明）
├── scripts/             ← 辅助脚本（模型下载 / 完整性校验 / DB 迁移 / 备份）
├── install.bat / start.bat   ← Windows 一键脚本
├── install.sh  / start.sh    ← Linux/macOS 一键脚本
├── requirements.txt          ← 生产依赖
├── requirements-dev.txt      ← 开发依赖（pytest + ruff + mypy + coverage）
├── requirements-lock.txt     ← 锁定依赖版本（generate_lock.py 生成）
└── pyproject.toml            ← 项目元数据 + 工具配置
```

### 3.2 禁区目录（禁止 AI 自动修改，必须人工确认）
| 目录 / 文件 | 原因 | 例外情况 |
|------------|------|---------|
| `model_lib/` 整个目录 | 第三方模型权重和研究代码，修改会直接影响生成效果和合规性 | 用户明确要求时，可以只改配置类（模型路径、超参数），不动模型推理代码 |
| `common/config.py` + 仓库根级 `config.yaml` | 配置结构变动会破坏所有依赖 settings 的代码 | 新增配置项时必须同步更新 `app/integrated_app/config.py` / `config_models.py` 的 Pydantic 模型 + 第 7 节启动命令说明（本仓没有 config.example.yaml 模板，只有根级 `config.yaml`） |
| `security/` 整个目录 | 安全模块（路径安全、CSRF、完整性、水印），改一个条件判断就可能出合规漏洞 | Bug 修复必须加攻击测试 + 人工 review |
| `api/main.py` 的 lifespan 回调 | 预加载引擎顺序不能乱，乱了会导致 GPU OOM | 调整预加载顺序必须测试后人工确认 |

### 3.3 路由自动发现 & 注册规则（极重要，AI 必须遵循）
**新增路由不需要手动 `include_router`**：`app/integrated_app/routes/__init__.py` 的 `auto_discover_routes(app)`（由 `app_server.py` 创建应用时调用）用 `pkgutil.iter_modules` 递归扫描 `routes` 包及其子包，凡模块内有名为 `router` 的属性即自动注册。

注册规则（不遵守则路由不会生效）：
1. 文件放在 `app/integrated_app/routes/` 下或其子包内（现有子包：`restore/`、`system/`、`ui/`）；**文件名不要求 `_router.py` 后缀**（实际如 `upload.py`、`batch.py`、`task.py`、`health.py`、`gpu.py`）
2. 必须定义 **module-level** 的 `router = APIRouter(prefix="/api/xxx", tags=["xxx"])`，变量名只能是 `router`，不能叫 `restore_router`
3. 前缀不能重复：两个模块都写 `prefix="/api/restore"` 肯定冲突
4. ⚠️ 扫描时 import 失败**只打 `logger.warning`、不阻断启动** → 路由会静默消失（Swagger UI 查不到但服务照常起）。排查顺序：启动日志「自动发现路由: xxx (prefix=...)」的条数 → 「导入路由模块失败」告警 → 最后才怀疑路由代码本身

### 3.4 引擎契约与注册现状（2026-08-27 核实）
本仓库为**单模型（SeedVR2 超分修复）服务**，不存在多引擎并存：

| 事实 | 实际位置（已核实） |
|------|------------------|
| 引擎接口契约 | `app/integrated_app/engine_interface.py` 的 `RestoreEngine` / `BatchRestoreEngine` / `EngineRegistry` 三个 `Protocol`（均 `@runtime_checkable`，不是 ABC） |
| 唯一引擎实现 | `app/integrated_app/engines/seedvr2_engine.py` 的 `SeedVR2Engine`（mixin 组合，见 3.1 节） |
| 实际加载方式 | `app/integrated_app/model_manager.py` 直接 `SeedVR2Engine(self.config)` —— 显式构造，不扫描、不注册 |
| 注册表实现 | `app/integrated_app/model_registry.py` 的 `_ModelRegistry` 实现了 `EngineRegistry` 的 `register` / `get` / `list_engines`，但**全仓找不到任何 `register(...)` 调用点** → 它当前只承载模型状态，不是引擎发现机制 |

> ⚠️ 历史文档里的「engines 自动发现与注册」机制 → **不存在，不得作为工作依据**：
> `engines/auto_register.py`、`base.py` 的 `AbstractEngineProtocol`、`diffusion_engine/`、`llm_engine/`、`_legacy/` → 均系从家族仓库 TTS_MultiModel（多引擎终态）移植时的残留，已移除。
> 真要加第二个引擎时不要指望任何自动发现：实现 `RestoreEngine` 协议 → 在 `ModelManager` 内按配置显式选择实例化 → 补 `config.yaml` 的 `model.models.<size>` 条目 → 补测试。

---

