# Install git hooks for this repo (Windows PowerShell)
# Usage: powershell -ExecutionPolicy Bypass -File scripts/install-hooks.ps1
# 编码要求：本文件必须保留 UTF-8 BOM。Windows PowerShell 5.1 读无 BOM 的 .ps1 时按 ANSI
#   代码页解码，下面的中文注释/提示串会变成乱码并破坏词法解析（表现为 here-string 不被识别、
#   报 “'||' 不是此版本中的有效语句分隔符”）。做「全仓 BOM 清理」时请勿动本文件的 BOM。
# 通过 core.hooksPath 指向 scripts/git-hooks/，避免手工拷贝到 .git/hooks
# ⚠️ 遗留脚本：本仓库权威钩子体系是 .githooks/（见 .githooks/README.md / install.bat 警告）。
#    下方前置守卫在检测到 .githooks/ 时拒绝执行，避免误改 core.hooksPath 使钩子静默失效。
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot

# --- 前置守卫：存在 .githooks/ 时拒绝执行（消除双安装器竞争）---
# 背景：.githooks/ 提供完整钩子链（pre-commit / pre-push / commit-msg 等），经
#   `git config core.hooksPath .githooks` 启用。本脚本会把 hooksPath 改指到
#   scripts/git-hooks 子目录，静默禁用上述全部钩子。
$authoritativeHooks = Join-Path $root ".githooks"
if (Test-Path -PathType Container $authoritativeHooks) {
    Write-Host "❌ 检测到权威钩子目录 .githooks/ ——本遗留脚本已停用，拒绝执行。" -ForegroundColor Red
    Write-Host "   运行本脚本会把 core.hooksPath 改指到 scripts/git-hooks，使 .githooks/ 的" -ForegroundColor Yellow
    Write-Host "   完整钩子链（pre-commit / pre-push / commit-msg 等）静默失效。" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   权威启用方式（二选一）：" -ForegroundColor Cyan
    Write-Host "     git config core.hooksPath .githooks"
    Write-Host "     sh .githooks/install.sh"
    exit 1
}
# --- 守卫结束：仅在无 .githooks/ 的遗留仓库中继续 ---

$hooksDir = Join-Path $root "scripts\git-hooks"

if (-not (Test-Path $hooksDir)) {
    New-Item -ItemType Directory -Path $hooksDir -Force | Out-Null
}

# pre-push: sh wrapper（git 用 sh 执行 hook）→ 调 python check_local.py
$prePush = Join-Path $hooksDir "pre-push"
$wrapper = @'
#!/bin/sh
# 本地提交前检查（快检）：ruff / format / compileall / UTF-8 扫描
# 完整检查请手动运行: python scripts/check_local.py --full
# CI 是唯一权威门禁；此 hook 为辅助提醒，可 git push --no-verify 绕过（不推荐）。
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT" || exit 1
PY="$(command -v python || command -v python3)"
if [ -z "$PY" ]; then
  echo "pre-push: python not found, skipping local checks"
  exit 0
fi
EXTRA=""
if [ -f "$ROOT/scripts/check_local.py" ]; then
  # SeedVR2 等仓库启用 mypy 检查
  if grep -q "mypy" "$ROOT/scripts/check_local.py" 2>/dev/null; then
    EXTRA="--mypy"
  fi
  "$PY" "$ROOT/scripts/check_local.py" $EXTRA
  exit $?
fi
exit 0
'@
Set-Content -Path $prePush -Value $wrapper -Encoding UTF8 -NoNewline

# 确保 sh wrapper 无 BOM（git 的 sh 可能不认 BOM）
$bytes = [System.IO.File]::ReadAllBytes($prePush)
if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    [System.IO.File]::WriteAllBytes($prePush, $bytes[3..($bytes.Length - 1)])
}

# 指向 hooks 目录（git 2.9+）
git config core.hooksPath "scripts/git-hooks"

Write-Host "✅ git hooks installed: $hooksDir (core.hooksPath = scripts/git-hooks)"
Write-Host "   下次 git push 前会自动跑本地快检（ruff/format/compileall/UTF-8）"
