# SeedVR2-lite · 开发者体验（DX）评估报告

> 评估对象：`C:/Users/Doro/SeedVR2-lite`（v1.5.1，Apache-2.0）
> 评估依据：`C:/Users/Doro/Desktop/提示词/SeedVR2-lite_开发者体验评估提示词.md`（v1.5.0 · 2026-09-03）
> 实测日期：2026-09-06 ｜ 方法：§4 验收命令全部实测 + 三个只读子探查（文档/CI/测试诊断）+ 实跑 `run_checks.bat`（1m01s 全绿）与 `run_verify.bat`（exit 1）
> 证据绑定：`python scripts/check_spec_refs.py` 退出码 0（130 份规范文件，phantom=0 dead_links=0 bad_workflow=0 bad_hook=0）

---

## 1. 事实核对表（含提示词锚点勘误）

### 1.1 提示词 §0 锚点核对

| 项 | 提示词锚点 | 实测（2026-09-06） | 判定 |
|---|---|---|---|
| 一键安装 | install.bat / install.sh | 均存在；install.bat 262 行、探测链完整 | ✅ |
| Python 探测顺序 | .venv → 系统 → WinPython，建 .venv | install.bat:18-149 实际为 **.venv → 系统（C:\Python312 等 4 路径 + where PATH 排除 TRAE/IDE）→ WinPython 4 分支**，无 venv 则创建 | ✅（比锚点更精确） |
| VC++ Runtime | 安装前检查 | install.bat:162-185，含交互式安装（延迟展开 bug 已修，注释自证"DX 评估 P1-5 修复"） | ✅ |
| CUDA 探测 | nvidia-smi → cu118/121/128/132 | install.bat:187-219；已处理新驱动 "CUDA UMD Version" 格式（token 10）；无 nvidia-smi 时 WARN 默认 cu128 + 手动指引 | ✅ |
| 安装顺序 | torch → requirements → hooks → 可选 pre-commit | install.bat:220-255 一致；**但 hooks 步骤（:244）调用的是 AGENTS.md 明令禁用的 install-hooks.ps1**（见 P1-3） | ⚠️ 一致但有雷 |
| 失败处理 | WARN 不静默退出 | torch/依赖安装失败均 [WARN] + 手动命令指引 | ✅ |
| 启动 | start.bat 起 app_server | start.bat:151 起 `app\clean_launch.py`；clean_launch.py **无 argparse/--reload**（grep 零命中） | ✅（无 dev 模式） |
| 本地校验 | run_checks.bat / run_verify.bat | run_checks.bat = ruff+black+mypy+pytest(`-m "not integration"`)+`--fast` 档；**run_verify.bat 实测 exit 1（双重失效，见 P1-2）** | ⚠️ |
| pre-commit | ruff v0.16.2 + hooks v4.6.0 | 一致；另有 **black 26.5.1**（非 ruff-format，注释引 KNOWN_ISSUES #61 双 formatter 冲突）；**无 mypy** | ✅ |
| 测试 | 74 py + 13 spec | 74 test_*.py ✅；Playwright **spec 15 个**（find *.spec.* 命中 20 含杂项）；pytest 实测 **1121 passed / 1 skipped / 122 deselected** | ✅（spec 数已变） |
| 覆盖率 | 0.6635 / fail_under=50 | coverage.xml line-rate **0.6638**；≥50 为 ci.yml:104-119 硬门禁 | ✅ |
| CI 数量 | 13 | **14 个**（新增 dco.yml） | ⚠️ 已变 |
| AGENTS.md | v1.51 / 约 114KB | **v1.58 / 9,360 B**（2026-09-03 瘦身重构：主干+§4 索引 8 子文档，内容下沉 docs/agents/）；15 个章节 | ⚠️ 已重写（见 Q3） |
| 旧路径幻觉 | bootstrap_server.py / python_env.py / installer.iss / installer_full.iss / dependency_check.py / FIRST_TIME_USER_GUIDE.md 不存在 | 前五个实测仍不存在 ✅；**`docs/project/FIRST_TIME_USER_GUIDE.md` 现已存在**（check_spec_refs 130 份规范清单内含） | ⚠️ 纠错本身需再纠正 |

### 1.2 提示词未覆盖、但影响 DX 的新资产（2026-09-03 之后）

- `desktop/`（Tauri v2 桌面壳，09-04 落地）：README.md:38-49 已收录；`desktop/README.md` 新增未提交；cargo test 32 项 + clippy 零警告门禁（AGENTS.md §2）。
- `SECURITY_AUDIT_SeedVR2-lite_2026-09-06.md`（当日安全审计报告，未提交）。
- `launcher/torch_wheels/` 离线兜底轮子 = **torch 2.11.0+cu128**，而 pyproject.toml:96-98 已声明现行策略为 cu132（torch 2.13.0，cu128 ≤2.12.1 有漏洞告警）→ 兜底轮子过期（P2-5）。
- 根目录遗留 `requirements-lock.txt.bak.20260903`（小卫生问题）。

---

## 2. 六维度评分（0–5）

| 维度 | 得分 | 核心证据 |
|---|---|---|
| 首次安装 | **2.5** | 正：install.bat 探测链+双 Option 失败指引+CUDA 四档映射+WARN 不静默+离线轮子兜底；install.sh 平价（venv 优先+发行版安装提示）。负：**ffmpeg 全链路零预检**（P1-1）、**无 torch.cuda 安装后冒烟**（P1-5）、run_verify.bat 实测跑不起来（P1-2）、install.bat 亲手挂载会破坏钩子链的脚本（P1-3） |
| 本地开发循环 | **3.5** | 正：**run_checks.bat 一键四检实测 61.3s 全绿**（ruff+black 309 文件+mypy 108 文件+pytest 1121 用例 48.4s），`--fast` 档只跑 ruff+black；TEST_COMMANDS.md 按类型列命令；tests/package.json 五个 playwright 入口；pre-commit 本地即反馈 ruff+black。负：mypy 不在 pre-commit（P2-1）；start.bat/clean_launch.py 无热重载入口（P2-7），只能手动 `uvicorn --reload`（AGENTS.md §2 有写） |
| 调试与诊断 | **4.0** | 正：GET /api/system/gpu（显存/利用率/CUDA 版本）、/api/system/metrics（vram_leak 峰值趋势）、Prometheus 指标端点；vram_peak_mb 落库+泄漏告警（restore_service.py:289-318）；**OOM 自动降级阶梯**（blocks_to_swap↑→resolution↓→种子轮换，restore_service.py:399,512）；结构化日志双通道+`LOG_FORMAT=json` 开关+req/trace id（app_server.py:40-125）；GPU 测试全 mock，**无 GPU 机器全量 pytest 可过**，skipif 均带 reason（test_gpu_backend.py 等仅 7 处 skip）。负：引擎层 RoPE OOM 只 `logger.warning(... falling back to CPU)` 无用户指引（blockswap.py:689-692）；ffmpeg 失败一句话报错（_video_pipeline.py:641，P2-8） |
| 文档与知识传递 | **3.0** | 正：README 467 行覆盖安装 6 步/模型下载/Docker/桌面版/FAQ 5 条，版本 badge 1.5.1 与 pyproject 一致；ADR 机制规范（2 条 ADR+README 写明触发条件）；**AGENTS.md 瘦身成功**（9.4KB 主干+§4 索引）；PR/Issue 模板高质量。负：FAQ 无 ffmpeg 条目（README:36 仅一句带过）；**website/ 桌面版 0 覆盖且滞后 docs**（09-03 vs 09-04）；demo/ 落后约两周（08-20，缺 INT8-convrot/MXFP8/NVFP4）；**3 份中文用户文档未提交但 README:49 已链接 → 远程死链**（P1-4）；无 CONTRIBUTING.md |
| 自动化与门禁 | **4.0** | 正：14 workflows，`gh run list` 近 12 次**全 success**；CI 门禁 ~6m52s-7m45s（双 OS 矩阵）、e2e ~15m（三浏览器 fail-fast:false）、pip/npm/Playwright 浏览器缓存齐全；pip-audit/Trivy(exit 1)/GPG 签名均为硬门禁；**FIX_LOG.md「日期\|失败签名\|假设\|动作→结果」闭环真实运转**（08-30/08-31/09-04 三批）；pre-push 自动跑 precheck.ps1（.git/hooks/pre-push 实存，core.hooksPath 未被劫持）；docs-consistency 专项 workflow + 契约审计显式化（94dbfe8）。负：**semgrep `scan` 无 `--error`，findings 不改退出码，SAST 仍软门禁**（security.yml:24-31 注释自认"仅上报不阻断"；8ad0a4c 只消除了假绿静默）；update-baselines.yml（视觉基线，仅手动 dispatch）知会渠道仅 CONFIG.md:72 一处 |
| 贡献流程 | **2.5** | 正：PR 模板（变更类型/pytest+ruff+mypy 测试清单/review 检查项）+ bug/feature/question 三套 Issue 模板 + DCO workflow；近 30 条提交 Conventional Commits 执行良好。负：**无 CONTRIBUTING.md**（提交规范藏在 AI 向的 docs/agents/SOPS.md:11-16，人类不可发现）；install.bat 给新贡献者的第一份"礼物"是破坏钩子链（P1-3）；LOCAL_RULES.md 家族隔离规则对新人不可见（gitignore 设计使然，但 README 至少该有一句） |
| **合计** | **19.5/30** | 强项：门禁自动化、运行时可观测、本地检查速度；弱项：首次安装链路、人类贡献者入口 |

---

## 3. 缺陷清单

### P1（新用户可复现失败 / 门禁静默失效）

| # | 缺陷 | 文件与证据 | 复现/验收命令 |
|---|---|---|---|
| P1-1 | **ffmpeg 全链路零预检**：install/install.sh/start/start.sh/precheck/clean_launch 均不检查；README 仅 :36 一句、FAQ 无条目；失败报错在视频合成阶段且无指引。Windows 无包管理器，这是最高概率的首次失败 | `install.bat`、`start.bat`、`precheck.ps1`、`app/clean_launch.py`（grep ffmpeg 全空）；`NOTICE:30-32` 只说"置于 PATH 即可"；`app/integrated_app/video_processor.py:97-129` 有 `is_available()` 但没人调用它做预检 | `grep -in ffmpeg install.bat install.sh start.bat start.sh precheck.ps1 app/clean_launch.py`（输出为空） |
| P1-2 | **run_verify.bat 双重失效**：① Python 探测只认 WinPython（.venv 优先策略未同步，注释"WinPython ONLY"是旧设计）；② 第 36 行调用根目录 `verify_engine.py`，实际文件在 `scripts/verify_engine.py` | `run_verify.bat:4-32,36`；实测本机（有 .venv）`exit 1`，输出 `[ERROR] WinPython not found!` | `cmd //c run_verify.bat`（本机实测 verify_exit=1） |
| P1-3 | **install.bat:244 调用 scripts/install-hooks.ps1**，后者第 47 行 `git config core.hooksPath "scripts/git-hooks"` → 现行两层钩子（pre-commit + .git/hooks/pre-push→precheck.ps1）**静默失效**。AGENTS.md v1.58 已明令"勿运行（含经 install.bat 第 244 行间接调用）"，但安装脚本未改 | `install.bat:240-247`；`scripts/install-hooks.ps1:15-47`；AGENTS.md §5 | `grep -n "install-hooks" install.bat`；`sed -n '40,50p' scripts/install-hooks.ps1` |
| P1-4 | **README.md:49 链接的 3 份中文文档未提交**（docs/用户手册.md、docs/开发者指南.md、docs/发布检查清单.md 均 `??`）→ 远程 README 死链 | `git status --porcelain -- docs/` | `git status --porcelain -- docs/` |
| P1-5 | **无安装后 CUDA 冒烟验证**：install.bat 结尾直接宣告完成，无 `torch.cuda.is_available()` 检查；选错 index（如无 nvidia-smi 默认 cu128）要到运行时才炸 | `install.bat:257-262` | `grep -n "cuda" install.bat`（仅安装期探测，无验证） |

### P2

| # | 缺陷 | 文件与证据 |
|---|---|---|
| P2-1 | mypy 不在 pre-commit，类型问题等 CI 才发现（mypy 对 engines.*/optimization.* 已 ignore_errors，成本可控） | `.pre-commit-config.yaml` 全文（仅 ruff/black/hooks v4.6.0） |
| P2-2 | semgrep 软门禁：`semgrep scan` 无 `--error`，findings 不改退出码 | `.github/workflows/security.yml:24-31` |
| P2-3 | website/ 未覆盖桌面版（grep Tauri/桌面版 0 命中）且滞后 docs；demo/ 08-20 后未更新（缺五精度新词） | `website/docs/guide/` 13 篇；`demo/index.html` |
| P2-4 | ModelScope 直下通道无进度显示（1MB 分块静默流式写，GB 级下载像卡死） | `scripts/download_model.py:107-123` |
| P2-5 | 离线兜底轮子过期：torch_wheels 为 2.11.0+cu128，现行策略 cu132/2.13.0（cu128 ≤2.12.1 有漏洞告警） | `launcher/torch_wheels/`；`pyproject.toml:96-98` |
| P2-6 | pyproject.toml:34 注释"Runtime is the bundled WinPython 3.12"与 .venv 统一策略矛盾；同段引 `docs/CONSTRAINTS.md` 实际路径为 `docs/project/CONSTRAINTS.md` | `pyproject.toml:34-38` |
| P2-7 | start.bat/clean_launch.py 无开发模式（--reload）入口，改一行要手动拼 uvicorn 命令 | `start.bat:149-151`；`app/clean_launch.py`（grep reload/argparse 零命中） |
| P2-8 | 引擎层错误文案无可操作指引：RoPE OOM 仅 warning 回退 CPU；ffmpeg 合成失败一句话 | `app/integrated_app/optimization/gpu/blockswap.py:689-692`；`engines/_video_pipeline.py:641` |

### P3

- run_verify.bat `pause` 阻塞无人值守场景；pytest 31 条 warnings（Windows GBK 子进程解码 UnicodeDecodeError 噪声、Duplicate Operation ID×4）；无 Makefile/justfile（run_checks.bat + TEST_COMMANDS.md 已基本覆盖，降为 P3）；根目录 `requirements-lock.txt.bak.20260903` 未清理。

---

## 4. 改进路线图（按「减少新用户流失」ROI 排序）

| 序 | 动作 | 成本 | 消掉的缺陷 |
|---|---|---|---|
| 1 | **ffmpeg 预检三件套**：install.bat/install.sh 加 `ffmpeg -version` 探测+gyan.dev 下载指引；clean_launch.py 启动横幅调 `video_processor.is_available()` 打 [WARN]；`_video_pipeline.py:641` 失败文案附"如何安装" | ~0.5 天 | P1-1、P2-8 半 |
| 2 | **安装后冒烟 + 修 run_verify.bat**：install.bat 末尾加 `"%PYTHON_CMD%" -c "import torch;print(torch.__version__, torch.cuda.is_available())"`（False 时打印排障指引）；run_verify.bat 改 .venv 优先 + 调 `scripts\verify_engine.py` | ~0.5 天 | P1-5、P1-2 |
| 3 | **install.bat 换钩子安装**：删 install-hooks.ps1 调用，改为 `pre_commit install` + 复制 `docs/agents/GIT_HOOK_PRE_PUSH.sh` → `.git/hooks/pre-push`（与 AGENTS.md §5 钩子复现条款对齐） | ~1 小时 | P1-3 |
| 4 | 提交 3 份中文用户文档（或先撤 README:49 链接） | ~10 分钟 | P1-4 |
| 5 | 新建 CONTRIBUTING.md：从 SOPS.md 抽人类可见版（分支策略/Conventional Commits/DCO signoff/指向 PR 模板） | ~0.5 天 | 贡献流程短板 |
| 6 | website/ 补桌面版指南页 + demo/ 刷新五精度工作流 | ~1 天 | P2-3 |
| 7 | 门禁补强（各 ~1h）：pre-commit 加 mypy；semgrep 加 `--error`（或显式承认软门禁并同步到 README 安全章节）；download_model.py ModelScope 通道加 tqdm；torch_wheels 刷新 cu132 | 各 ~1h | P2-1/2/4/5 |
| 8 | pyproject.toml 注释勘误 + start.bat 加 `--dev` 分支（uvicorn --reload） | ~1h | P2-6/7 |

---

## 5. 三个必答问题

### Q1 首次安装到出第一张图，哪一步流失率最高？

**输出阶段的 ffmpeg 缺失**，不是安装也不是模型下载。推理链：目标用户（Windows 桌面用户为主，pyproject classifiers 仅列 Windows）默认没有 ffmpeg；README 只在 :36 一句带过、FAQ 五条无 ffmpeg；install.bat/install.sh/start/clean_launch/precheck 全程不查；于是用户走完 30-70 分钟（依赖 10-25min + 模型 5-10GB 下载 10-40min + 加载 1-3min）后，**第一个视频任务在最后一步合成时**收到一句 "ffmpeg 视频合成失败"（`_video_pipeline.py:641`），无法自助归因——失败点离价值终点最近、离排障知识最远，情绪成本最大。次高流失点是模型下载，但已被 `--endpoint hf-mirror.com`（P1-3 修复遗留）+ Comfy-Org 量化走 ModelScope 双源路由 + .part/Range 断点续传 + 下载后 SHA256 校验大幅缓解（`download_model.py:29-38,107-123`），只剩 ModelScope 直下无进度条（P2-4）。**最小改进**即提示词三件套：ffmpeg 检测（装完就查 + 启动横幅）+ torch.cuda 安装后验证（P1-5）+ 模型下载镜像（已有，补进度条）。

### Q2 website/（VitePress）与 docs/ 是否应合并为单一文档源？

**不建议物理合并，建议"双源 + 门禁同步"**。实测依据：① 受众与性质不同——website/docs/guide/ 13 篇是面向最终用户的成体系指南，docs/ 是工程内档（adr/plans/reports/agents/GOTCHAS 等，含不宜公开的治理内容）；② 重叠面其实很小——抽查"安装"主题，website/docs/guide/install.md 与 README:112-186 几乎逐字同步，docs/README.md:34 已明示两套文档分工；③ 真正的问题是**覆盖缺口与滞后**（website 桌面版 0 命中、比 docs 慢一天；demo 落后两周），物理合并不解决"要写而未写"。**建议**：把「website 必须覆盖的功能面」（桌面版、五精度、快捷入口）纳入已存在的 `docs-consistency.yml` 门禁做关键词/链接检查，并在 docs/README.md 分工表加同步责任。**迁移成本（若硬合并）**：VitePress 需为 docs/ 建白名单过滤治理文档，约 1-2 天改造 + 长期过滤维护负担，ROI 低于门禁方案。

### Q3 AGENTS.md 114KB 是否需要拆分或加导航索引？

**此问题已过时——2026-09-03 瘦身重构恰好完成了提示词想要的终态**。实测：AGENTS.md 现 9,360 B（v1.58）、15 个章节、§4 明确索引 8 个子文档（ARCH_MAP/CODE_STYLE/CONFIG/TEST_COMMANDS/GOTCHAS/SOPS/QUALITY_CONTRACT/REVISION_LOG），详细内容逐字下沉 docs/agents/；且 `check_spec_refs.py` 门禁（130 份规范、phantom=0 dead_links=0）保证所有被引用路径真实存在。当前形态对 AI 与人类均可导航，无需再拆。

---

## 6. 与提示词判定口径的对照

- 口径①「install.bat 无 ffmpeg 检查须列 P1」→ **已列 P1-1** ✅
- 口径⑤「AGENTS.md 超 100KB 且无导航须指出认知负担」→ **不适用**（9.4KB + §4 索引）⚠️ 提示词基于 v1.51 快照，评估时已重构
- 提示词 §3 反模式逐条判定：#1 ffmpeg 未检 → **成立（P1-1）**；#2 CUDA 变体矩阵缺安装后验证 → **成立（P1-5）**，但探测/映射本身质量高；#3 mypy 未进 pre-commit → **成立（P2-1）**；#4 无快速测试入口 → **基本不成立**（run_checks.bat+`--fast`+markers 文档化，仅无 Makefile）；#5 文档双份失同步 → **部分成立**（重叠小、缺口真，P2-3）；#6 AGENTS.md 114KB → **已过时**（瘦身完成）；#7 Semgrep 不阻断 → **半成立**（`|| true` 已删，但 scan 无 --error 仍不红，P2-2）；#8 无 GPU 机器测试可读性 → **不成立**（GPU 测试全 mock，skipif 带 reason，实测无卡可跑）；#9 旧提示词路径幻觉 → 前五个路径确认不存在 ✅，但 FIRST_TIME_USER_GUIDE.md 现已存在。
- 本次新发现、提示词未列的 P1：**P1-2（run_verify.bat 双重失效）、P1-3（install.bat 挂载禁用钩子的脚本）、P1-4（README 远程死链）**。
