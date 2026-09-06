# 贡献指南（CONTRIBUTING）

感谢关注 SeedVR2-lite！本文件面向**人类贡献者**，给出从克隆到合并的最短路径。
仓库另有 AI 协作协议 `AGENTS.md` 与治理文档（FIX_LOG / KNOWN_ISSUES / docs/agents/），
属维护者本地治理层、未随仓库分发；其纪律要求在下列章节中已摘录为红线。

## 1. 环境搭建

```bat
:: Windows（推荐）
install.bat          :: 探测 Python/CUDA/FFmpeg，建 .venv，装依赖，装两层 git hooks
start.bat            :: 启动 http://127.0.0.1:7870
start.bat --dev      :: 开发模式（uvicorn --reload，改代码即时生效）
```

```bash
# Linux / macOS
./install.sh
./start.sh
```

安装脚本会自动注册提交层钩子 pre-commit（ruff/black/mypy/文件卫生）；推送层 pre-push
快检属维护者本地治理层（远程克隆不可得，推送门禁由 CI 等价执法）。若手动补装提交层：

```bash
.venv/Scripts/python -m pip install pre-commit          # Windows
.venv/Scripts/python -m pre_commit install
```

> 说明：提交层钩子（ruff/black/mypy/文件卫生）来自仓库内 `.pre-commit-config.yaml`，克隆即可用；
> 推送层 pre-push 快检（precheck.ps1）属维护者本地治理层，未随仓库分发——远程贡献者的推送门禁由 CI 等价执法。
> ⚠️ 勿运行 `scripts/install-hooks.ps1`——它会把 `core.hooksPath` 改指到自己的子目录，使钩子静默失效。

## 2. 本地开发循环

| 场景 | 命令 |
|---|---|
| 一键质量门（ruff + black + mypy + pytest） | `run_checks.bat`（或 `.venv/Scripts/python -m` 等价命令） |
| 快速档（只跑 lint + format） | `run_checks.bat --fast` |
| 单测（跳过集成） | `pytest -q -m "not integration"` |
| 集成测试 | `pytest -q -m integration` |
| 引擎自检（需 GPU + 权重） | `run_verify.bat`（可加 `--skip-infer`） |
| 八项环境诊断 | `python scripts/doctor.py`（`--json` 机读） |
| E2E（Playwright） | `cd tests && npm run test`（三浏览器矩阵见 `.github/workflows/e2e.yml`） |

更完整的分类型命令表在维护者本地治理文档 `docs/agents/TEST_COMMANDS.md`（未随仓库分发）。

## 3. 分支与提交规范

- 分支：从最新 `main` 切出（`git pull` 后 `git checkout -b feat/xxx`）。
- 提交信息：**Conventional Commits**，与仓库现有历史一致：
  - `feat(scope): 新功能` / `fix(scope): 修复` / `docs:` / `ci:` / `test:` / `chore:` / `refactor:` / `style:`
  - 标题用一行说清「做了什么」；细节写正文。
- **DCO**：CI 有 DCO 门禁，每个提交必须带签名：
  `git commit -s`（或 `git commit --signoff`，补历史用 `git rebase --signoff`）。
- 提交层钩子会自动跑 ruff --fix / black / mypy；推送层 pre-push 自动跑 `precheck.ps1`。
  **预检失败请修复代码，禁止 `--no-verify` 跳过。**

## 4. Pull Request

PR 模板（[.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md)）会引导你填写：

- 变更类型与动机；
- 测试清单（pytest / ruff / mypy / E2E 实际执行结果）；
- 自查项（是否改动了禁区 `model_lib/`、`security/`、`config.yaml`——见下 §5 红线）。

Issue 请使用现成表单（bug / feature / question 三套模板）。

## 5. 行为红线（速览）

- **禁区目录**：`model_lib/`（上游镜像源码）、`security/`（安全模块）默认禁止修改；
  `config.yaml` 修改必须同步 `app/integrated_app/config.py` / `config_models.py`。
- **单一事实来源**：文档与代码冲突时以代码/配置为准，并按 AGENTS.md 铁律回改文档。
- 引用的脚本/配置路径必须真实存在（`python scripts/check_spec_refs.py` 校验）。
- 含中文的 `.ps1` 必须 UTF-8 with BOM；`.bat` 保持纯 ASCII（GBK 控制台编码陷阱）。
- push ≠ 完成：用 `gh run list` 盯 CI 到终态；红灯按 `FIX_LOG.md` 的「签名→假设→动作→结果」格式交接。

## 6. 需要帮助？

- 用户向常见问题：[README FAQ](README.md) 与[网站文档](https://reserendipity.github.io/SeedVR2-lite/docs/)
- 桌面版：`docs/用户手册.md`、`docs/开发者指南.md`
- 维护者本地治理文档（AGENTS.md / KNOWN_ISSUES / FIX_LOG / docs/agents/）未随仓库分发，
  相关纪律已在本文 §3/§5 摘录；如需了解某条规则的细节，请在 Issue / PR 中直接提问。
