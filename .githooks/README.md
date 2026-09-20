# .githooks

本仓库的 Git 钩子源码。启用一次即可：

```sh
git config core.hooksPath .githooks      # 或执行 ./.githooks/install.sh
```

启用后钩子直接从本目录读取，**改这里立即生效**，不需要再往 `.git/hooks` 复制文件。

| 钩子 | 作用 |
| --- | --- |
| `pre-commit` | 分发器按「仓库 `.venv` → PATH 上的 python → 控制台 `pre-commit` → `pre-commit-lite`」四级回退执行框架检查 |
| `pre-push` | gitleaks 待推送扫描 + 执行 `precheck.ps1`（维护者本地预检脚本，未随仓库分发；传入推送范围 `-FromSha/-ToSha`，仅文档类变更走快速跳检；无该脚本则退回根目录守卫，其余门禁由 CI 兜底） |
| `prepare-commit-msg` | 自动追加 `Signed-off-by`（幂等，插在注释块之前） |
| `commit-msg` | DCO 硬校验（缺签名阻断）+ conventional 规范软提示 |
| `post-merge` / `post-checkout` | 依赖清单变更提醒（pip / pnpm / npm / cargo / gradle） |
| `pre-commit-lite` | 轻量检查：大文件、私钥与令牌、冲突标记 + 根目录守卫 |

## 说明

- `pre-commit` 分发器的框架解析优先级为四级回退：① 仓库 `.venv` 内的 python → ② PATH 上的
  `python` → ③ 控制台命令 `pre-commit`（uv tool / pipx / 系统安装）→ ④ 均不可用时退回
  `pre-commit-lite` 兜底。**要点**：③ 这一级保证「装了 pre-commit 但没建 `.venv`」的机器
  仍然跑完整框架钩子，而不是静默退化成 lite 的几项检查。
- `pre-commit-lite` 找不到 Python 时会打印醒目 `[WARN]` 并放行；设 `GUARD_STRICT=1` 可让它改为阻断。
- 大文件阈值默认 5MB，可用 `GUARD_MAX_FILE_MB` 调整。
- `core.hooksPath` 是**本地**配置，不会随克隆传播——换机器请重新执行上面那行命令。
