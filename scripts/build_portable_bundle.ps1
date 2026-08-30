#Requires -Version 5.1
# scripts/build_portable_bundle.ps1
# 构建「分卷压缩包」离线便携包，产物直接上传 GitHub Release（单文件恒 < 2 GiB）。
#
# 四个组件（默认全建，合计 6 卷 = 1+2+1+2）：
#   core          应用代码 + 便携 Python 运行时（torch 家族已摘除）      → 1 卷  ~0.2 GB
#   torch         PyTorch cu128 wheels（首次解包时离线 pip --no-index）  → 2 卷  ~2.7 GB
#   model-shared  ema_vae_fp16 + pos_emb + neg_emb                      → 1 卷  ~0.5 GB
#   model-fp8     seedvr2_ema_3b_fp8_e4m3fn（唯一内置主模型，FP8）        → 2 卷  ~3.2 GB
#
# 用法：
#   .\scripts\build_portable_bundle.ps1                       # 自动准备运行时并全量构建
#   .\scripts\build_portable_bundle.ps1 -Component core,model-fp8 -RuntimeDir D:\WPy64-312101
#   .\scripts\build_portable_bundle.ps1 -SkipAutoPrepare -RuntimeDir ... -TorchWheelDir ...

[CmdletBinding()]
param(
    [string]$Version = '',
    [string]$Root = '',
    [string]$OutDir = '',
    [string]$StagingDir = '',
    [string]$RuntimeDir = '',
    [string]$TorchWheelDir = '',
    [string]$ModelDir = '',
    [string[]]$Component = @(),
    [ValidateSet('auto', '7z', 'zip')][string]$Format = 'auto',
    [long]$MaxPartBytes = 0,
    [double]$MinFreeGb = 0,
    [string]$WinPythonUrl = 'https://github.com/winpython/winpython/releases/download/16.5.20250614/Winpython64-3.12.10.1dotb4.exe',
    # 官方 release 资产 digest（GitHub API 实测，2026-08-30），防下载损坏/上游篡改；
    # 升级 WinPython 版本时必须同步更新 URL 与哈希。
    [string]$WinPythonSha256 = '4061f0e936289ca1df48fc8e7357a4c30e6010f053ffd2f986f518a09bbf03e8',
    [string]$TorchIndexUrl = 'https://download.pytorch.org/whl/cu128',
    # torch 三件套钉版（P1-3 复现性）：cu128 索引当前最高版本，两次构建产出一致；
    # 升级时先查 pip index versions torch --index-url <TorchIndexUrl> 再同步三处。
    [string]$TorchVersion = '2.11.0',
    [string]$TorchvisionVersion = '0.26.0',
    [string]$TorchaudioVersion = '2.11.0',
    # 可选 Authenticode 代码签名（P3）：提供代码签名证书 .pfx 时，随包分发的
    # .ps1 助手在生成 SHA256SUMS.txt 之前完成签名（否则清单哈希会失配）。
    [string]$SigningPfxPath = '',
    [string]$SigningPfxPassword = '',
    [switch]$SkipAutoPrepare,
    [switch]$SkipOfflineTorchCheck,
    [switch]$PrintModelFiles,
    [switch]$KeepStaging,
    [switch]$KeepArchive,
    [switch]$SkipSizeGate
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'portable_bundle_lib.ps1')

$PortableRootName = 'SeedVR2-Portable'
$AllComponents = @('core', 'torch', 'model-shared', 'model-fp8')

# ---------------------------------------------------------------- 组件规格 ----
function Get-SeedVR2ComponentSpec {
    param([Parameter(Mandatory = $true)][string]$Name)
    switch ($Name) {
        'core' {
            return [pscustomobject]@{
                Id          = 'core'
                Title       = '应用与便携 Python 运行时（不含 torch）'
                Required    = $true
                Level       = 6
                Description = '应用源码白名单 + WinPython 3.12 便携解释器（已预装全部非 torch 依赖）+ 便携启动脚本。'
            }
        }
        'torch' {
            return [pscustomobject]@{
                Id          = 'torch'
                Title       = 'PyTorch CUDA 12.8 离线 wheels'
                Required    = $true
                Level       = 0
                Description = 'torch / torchvision / torchaudio 的 cu128 wheel。解包脚本用 pip --no-index 离线装入便携解释器，全程不联网。'
            }
        }
        'model-shared' {
            return [pscustomobject]@{
                Id          = 'model-shared'
                Title       = '共享模型组件（VAE + 正负提示词嵌入）'
                Required    = $true
                Level       = 1
                Description = 'ema_vae_fp16.safetensors / pos_emb.pt / neg_emb.pt，任何主模型都必需。'
            }
        }
        'model-fp8' {
            return [pscustomobject]@{
                Id          = 'model-fp8'
                Title       = 'SeedVR2-3B FP8 主模型'
                Required    = $true
                Level       = 1
                Description = '内置的唯一主模型（3.16 GB，压缩后 2.17 GB，显存 8 GB 可跑）。FP16 与 7B 权重不随包分发，按 README 自行下载。'
            }
        }
        default {
            throw "未知组件 '$Name'，可选：$($AllComponents -join ', ')"
        }
    }
}

# ------------------------------------------------------- 仓库/配置事实读取 ----
function Get-SeedVR2ProjectVersion {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    $pyproject = Join-Path $ProjectRoot 'pyproject.toml'
    if (Test-Path -LiteralPath $pyproject) {
        $m = Select-String -LiteralPath $pyproject -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
        if ($m) {
            return $m.Matches[0].Groups[1].Value
        }
    }
    throw "无法从 pyproject.toml 解析版本号，请显式传 -Version"
}

function Clear-SeedVR2YamlValue {
    param([Parameter(Mandatory = $true)][string]$Raw)
    $v = $Raw.Trim()
    $hash = $v.IndexOf('#')
    if ($hash -gt 0) {
        $v = $v.Substring(0, $hash).Trim()
    }
    $v = $v.Trim([char]0x22, [char]0x27)
    return $v
}

function Get-SeedVR2ModelFilesFromConfig {
    <#
        从 config.yaml 读取 3b 条目实际引用的权重文件名，避免打包清单与运行时配置漂移。
        只读不写（config.yaml 属 AGENTS.md 第 3.2 节禁区）。
        用最简的缩进 + 单键匹配，不做完整 YAML 解析。
    #>
    param([Parameter(Mandatory = $true)][string]$ConfigPath)
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        throw "配置文件不存在：$ConfigPath"
    }
    $lines = [System.IO.File]::ReadAllLines($ConfigPath)
    $result = @{ pretrained_dir = 'model'; checkpoint_fp8 = ''; vae_checkpoint = ''; pos_emb = ''; neg_emb = '' }
    foreach ($line in $lines) {
        if ($line -match '^\s*pretrained_dir\s*:\s*(.+)$') {
            $v = Clear-SeedVR2YamlValue -Raw $Matches[1]
            if ($v) {
                $result.pretrained_dir = $v
            }
        }
    }
    $inBlock = $false
    $keys = @('checkpoint_fp8', 'vae_checkpoint', 'pos_emb', 'neg_emb')
    foreach ($line in $lines) {
        if ($line -match '^\s{4}3b\s*:\s*$') {
            $inBlock = $true
            continue
        }
        if ($inBlock -and $line -match '^\s{4}\S') {
            break
        }
        if (-not $inBlock) {
            continue
        }
        foreach ($key in $keys) {
            if ($line -match ('^\s{6}' + $key + '\s*:\s*(.+)$')) {
                $result.$key = Clear-SeedVR2YamlValue -Raw $Matches[1]
            }
        }
    }
    foreach ($key in $keys) {
        if (-not $result.$key) {
            throw "config.yaml 的 model.models.3b 缺少 $key，无法确定要打包的权重文件名"
        }
    }
    return $result
}

# --------------------------------------------------- 组件 payload 组装 ----
# 仓库内嵌权重小资产（pos_emb/neg_emb），随代码一并提交，CI 无需联网拉取。
# HF 社区模型仓库 numz/SeedVR2_comfyUI 缺失这两个文件（CI 已 404），
# 但它们很小且本地模型正常运行必需 → 入库作为权威来源（见 docs/project/PORTABLE_BUNDLES.md）。
$BundleAssetsDir = Join-Path $PSScriptRoot 'bundle_assets'

$CoreIncludeDirs = @('app', 'common', 'model_lib', 'configs_3b', 'configs_7b', 'data')
$CoreIncludeFiles = @('config.yaml', 'pyproject.toml', 'requirements.txt', 'LICENSE', 'NOTICE', 'README.md',
    'USER_AGREEMENT.md', 'SECURITY.md', 'CHANGELOG.md')
$CoreExcludePatterns = @(
    '__pycache__\*', '*.pyc', '*.pyo', '.pytest_cache\*',
    'app\integrated_app\data\*', 'logs\*.log', '*.db', '*.db-wal', '*.db-shm',
    '.setup_state.json', '.torch_cache\*', '*.bak', '*.bak.*',
    'node_modules\*', '.venv\*', 'dist\*', 'build\*'
)

function New-SeedVR2CorePayload {
    param(
        [Parameter(Mandatory = $true)][string]$PayloadDir,
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$Runtime,
        [Parameter(Mandatory = $true)][string]$Ver
    )
    $appDir = Join-Path $PayloadDir $PortableRootName
    New-Item -ItemType Directory -Path $appDir -Force | Out-Null
    $totalFiles = 0
    $totalBytes = 0
    foreach ($d in $CoreIncludeDirs) {
        $src = Join-Path $ProjectRoot $d
        if (-not (Test-Path -LiteralPath $src)) {
            Write-Warning "跳过不存在的源码目录 $d"
            continue
        }
        $stats = Copy-SeedVR2Tree -Source $src -Dest (Join-Path $appDir $d) -ExcludePatterns $CoreExcludePatterns
        $totalFiles += $stats.Files
        $totalBytes += $stats.Bytes
        Write-Host ("    · {0,-12} {1,6} 文件 / {2}" -f $d, $stats.Files, (Format-SeedVR2Size $stats.Bytes))
    }
    foreach ($f in $CoreIncludeFiles) {
        $src = Join-Path $ProjectRoot $f
        if (-not (Test-Path -LiteralPath $src)) {
            continue
        }
        $stats = Copy-SeedVR2Tree -Source $src -Dest $appDir -ExcludePatterns $CoreExcludePatterns
        $totalFiles += $stats.Files
        $totalBytes += $stats.Bytes
    }
    if ($Runtime) {
        $stats = Copy-SeedVR2Tree -Source $Runtime -Dest (Join-Path $appDir (Split-Path -Leaf $Runtime)) `
            -ExcludePatterns @('pip-log.txt', '*.typecheck.log', 'qt.conf', 'WINPYTHON_*', 'apps\*', 'dev\*', 'notebooks\*', 'settings\*', 'data\*')
        Write-Host ("    · {0,-12} {1,6} 文件 / {2}" -f (Split-Path -Leaf $Runtime), $stats.Files, (Format-SeedVR2Size $stats.Bytes))
        $totalFiles += $stats.Files
        $totalBytes += $stats.Bytes
    }
    Set-Content -LiteralPath (Join-Path $appDir 'VERSION.txt') -Value "SeedVR2 Portable $Ver" -Encoding ascii
    Write-SeedVR2StartScript -AppDir $appDir
    Write-SeedVR2Readme -AppDir $appDir -Ver $Ver
    return [pscustomobject]@{ Files = $totalFiles; Bytes = $totalBytes }
}

function Write-SeedVR2StartScript {
    param([Parameter(Mandatory = $true)][string]$AppDir)
    $bat = @(
        '@echo off'
        'setlocal enableextensions'
        'cd /d "%~dp0"'
        'set "PY="'
        'for /d %%P in ("%~dp0WPy64-*") do if exist "%%~fP\python\python.exe" set "PY=%%~fP\python\python.exe"'
        'if not defined PY for /d %%P in ("%~dp0WPy64-*") do for /d %%Q in ("%%~fP\python-*") do if exist "%%~fQ\python.exe" set "PY=%%~fQ\python.exe"'
        'if not defined PY for /d %%P in ("%~dp0WPy64-*") do if exist "%%~fP\python.exe" set "PY=%%~fP\python.exe"'
        'if defined PY set "PYTHONHOME="'
        'if not defined PY (set "PY=python"'
        'echo [WARN] 未找到便携解释器，回退使用系统 python)'
        'if not defined KMP_DUPLICATE_LIB_OK set "KMP_DUPLICATE_LIB_OK=TRUE"'
        'if not defined TORCHINDUCTOR_CACHE_DIR set "TORCHINDUCTOR_CACHE_DIR=%~dp0.torch_cache\inductor"'
        'echo 使用解释器: %PY%'
        'echo 启动 SeedVR2 于 http://127.0.0.1:7870'
        '"%PY%" app\clean_launch.py'
        'if errorlevel 1 pause'
    )
    [System.IO.File]::WriteAllLines((Join-Path $AppDir 'start-portable.bat'), [string[]]$bat, (New-Object System.Text.ASCIIEncoding))
}

function Write-SeedVR2Readme {
    param(
        [Parameter(Mandatory = $true)][string]$AppDir,
        [Parameter(Mandatory = $true)][string]$Ver
    )
    $txt = @"
SeedVR2 便携离线包 v$Ver
================================================================

本目录由分卷压缩包解包而来，包含：
  1. 应用代码与配置（config.yaml）
  2. 便携 Python 解释器 WPy64-*（已预装全部非 torch 依赖）
  3. torch_wheels\（若已解包 torch 组件）
  4. model\（ema_vae_fp16 + pos_emb + neg_emb + seedvr2_ema_3b_fp8_e4m3fn）

启动：双击 start-portable.bat，浏览器打开 http://127.0.0.1:7870

torch 通常无需手动处理：下载目录里的 unpack_portable_bundle.ps1 在解包时已自动
用 .\torch_wheels 离线装好（全程不联网）。若当时用了 -SkipTorchInstall 或报错过，
在便携解释器上手动补装：

  WPy64-XXXX\python-3.12.10.amd64\python.exe -m pip install --no-index ^
      --find-links .\torch_wheels torch torchvision torchaudio

模型说明：
  - 本包只内置 3B FP8 权重（显存 8 GB 即可运行）。config.yaml 默认精度是 fp16，
    在只有 FP8 权重时 model_manager 会自动回退到 FP8，属预期行为（日志有一条 WARNING）。
  - 想要 FP16 或 7B 权重，需自行下载放入 model\，直链见 README.md。
  - 视频修复功能需要系统自行安装 FFmpeg 并置于 PATH（本包依 NOTICE 第 4 条不分发）。
"@
    [System.IO.File]::WriteAllText((Join-Path $AppDir 'README-PORTABLE.txt'), $txt, (New-Object System.Text.UTF8Encoding($false)))
}

function New-SeedVR2TorchPayload {
    param(
        [Parameter(Mandatory = $true)][string]$PayloadDir,
        [Parameter(Mandatory = $true)][string]$WheelDir
    )
    $appDir = Join-Path $PayloadDir $PortableRootName
    $dest = Join-Path $appDir 'torch_wheels'
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    $whl = @(Get-ChildItem -LiteralPath $WheelDir -Recurse -File -Filter '*.whl')
    if ($whl.Count -eq 0) {
        throw "torch 组件：$WheelDir 之下没有 .whl 文件（先跑 pip download 或去掉 torch 组件）"
    }
    foreach ($w in $whl) {
        # 硬链接优先：CI runner 上 wheels 目录与 staging 同盘，可省下 2.8GB 峰值占用。
        New-SeedVR2HardLink -SourceFile $w.FullName -LinkPath (Join-Path $dest $w.Name) | Out-Null
    }
    $bytes = ($whl | Measure-Object -Property Length -Sum).Sum
    return [pscustomobject]@{ Files = $whl.Count; Bytes = [long]$bytes }
}

function New-SeedVR2ModelPayload {
    param(
        [Parameter(Mandatory = $true)][string]$PayloadDir,
        [Parameter(Mandatory = $true)][string[]]$FileNames,
        [Parameter(Mandatory = $true)][string]$SourceModelDir,
        # 文件名 -> 额外来源目录 的映射。给定目录里存在该文件则优先用它，
        # 否则回退到 $SourceModelDir。用于 pos_emb/neg_emb 这类仓库内嵌资产（CI 无需联网）。
        [hashtable]$ExtraSourceDir = @{}
    )
    $appDir = Join-Path $PayloadDir $PortableRootName
    $dest = Join-Path $appDir 'model'
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    $bytes = 0
    $count = 0
    foreach ($n in $FileNames) {
        $src = $null
        if ($ExtraSourceDir.ContainsKey($n) -and $ExtraSourceDir[$n]) {
            $cand = Join-Path $ExtraSourceDir[$n] $n
            if (Test-Path -LiteralPath $cand) {
                $src = $cand
            }
        }
        if (-not $src) {
            $src = Join-Path $SourceModelDir $n
        }
        if (-not (Test-Path -LiteralPath $src)) {
            throw "模型组件：缺少权重文件 $src"
        }
        New-SeedVR2HardLink -SourceFile $src -LinkPath (Join-Path $dest $n) | Out-Null
        $bytes += (Get-Item -LiteralPath $src).Length
        $count += 1
    }
    foreach ($license in @('LICENSE', 'NOTICE')) {
        $lsrc = Join-Path (Split-Path -Parent $SourceModelDir) $license
        if (Test-Path -LiteralPath $lsrc) {
            Copy-Item -LiteralPath $lsrc -Destination (Join-Path $dest $license) -Force
        }
    }
    return [pscustomobject]@{ Files = $count; Bytes = [long]$bytes }
}

# ------------------------------------------------------------ 自动准备 ----
function Start-SeedVR2RuntimePrepare {
    param(
        [Parameter(Mandatory = $true)][string]$WorkDir,
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$Url
    )
    $sevenZip = Find-SeedVR2SevenZip
    $exePath = Join-Path $WorkDir 'WinPython.exe'
    if (-not (Test-Path -LiteralPath $exePath)) {
        Write-Host "  下载 WinPython 便携解释器 ..."
        Invoke-WebRequest -Uri $Url -OutFile $exePath -UseBasicParsing -TimeoutSec 900
    }
    if ($WinPythonSha256) {
        $actual = (Get-SeedVR2FileSha256 -Path $exePath).ToLowerInvariant()
        if ($actual -ne $WinPythonSha256.ToLowerInvariant()) {
            throw ("WinPython 安装器 SHA256 不符（期望 {0}，实际 {1}）——下载损坏或上游被篡改" -f $WinPythonSha256, $actual)
        }
        Write-Host "  WinPython 安装器 SHA256 校验通过"
    }
    $extractDir = Join-Path $WorkDir 'wp'
    if (-not (Test-Path -LiteralPath $extractDir)) {
        New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
        Write-Host "  解压 WinPython ..."
        if ($sevenZip) {
            $res = Invoke-SeedVR2Native -Exe $sevenZip -Arguments @('x', $exePath, "-o$extractDir", '-y')
            if ($res.ExitCode -ne 0) {
                throw "7z 解压 WinPython 失败，退出码 $($res.ExitCode)：$($res.Text.Split("`n")[-4..-1] -join ' | ')"
            }
        } else {
            $psi = Start-Process -FilePath $exePath -ArgumentList '/S', "/D=$extractDir" -PassThru -Wait
            if ($psi.ExitCode -ne 0) {
                throw "WinPython 自解压失败，退出码 $($psi.ExitCode)（可安装 7-Zip 后重试）"
            }
        }
    }
    $info = Resolve-SeedVR2RuntimeRoot -Path $extractDir
    $py = $info.PythonExe
    $runtimeRoot = $info.RuntimeRoot
    $reqSmall = Join-Path $ProjectRoot 'launcher\requirements-small.txt'
    if (Test-Path -LiteralPath $reqSmall) {
        Write-Host "  预装非 torch 依赖（launcher\requirements-small.txt）..."
        Invoke-SeedVR2Native -Exe $py -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip') | Out-Null
        $res = Invoke-SeedVR2Native -Exe $py -Arguments @('-m', 'pip', 'install', '-r', $reqSmall, '--timeout', '300', '--retries', '3')
        if ($res.ExitCode -ne 0) {
            throw "便携解释器依赖安装失败：$($res.Text.Split("`n")[-6..-1] -join ' | ')"
        }
    }
    Write-Host "  摘除 torch 家族（归入独立 torch 组件）..."
    Invoke-SeedVR2Native -Exe $py -Arguments @('-m', 'pip', 'uninstall', '-y', 'torch', 'torchvision', 'torchaudio') | Out-Null
    # 期望 import 失败（证明已摘干净），因此必须走 Invoke-SeedVR2Native：
    # 直接 `& python -c` 在 EAP=Stop 下会把 stderr 的 Traceback 升级成终止错误（CI 实测踩过）。
    $probe = Invoke-SeedVR2Native -Exe $py -Arguments @('-c', 'import torch')
    if ($probe.ExitCode -eq 0) {
        throw "torch 仍存在于便携解释器中，core 组件会体积失控"
    }
    $link = Join-Path $runtimeRoot 'python'
    if (Test-Path -LiteralPath $link) {
        $item = Get-Item -LiteralPath $link -Force
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            Write-Host "  移除 python 目录联接（归档器不跟随 reparse point）..."
            [System.IO.Directory]::Delete($link)
        }
    }
    return $runtimeRoot
}

function Start-SeedVR2TorchWheelPrepare {
    param(
        [Parameter(Mandatory = $true)][string]$WheelDir,
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)][string]$IndexUrl
    )
    New-Item -ItemType Directory -Path $WheelDir -Force | Out-Null
    if (@(Get-ChildItem -LiteralPath $WheelDir -File -Filter '*.whl').Count -gt 0) {
        Write-Host "  复用已有 wheels：$WheelDir"
        return
    }
    Write-Host "  pip download torch 家族（含传递依赖，约 2.8 GB，需联网）..."
    # 关键：不能加 --no-deps。离线安装用 pip --no-index --find-links，torch 的传递依赖
    # （filelock / fsspec / jinja2 / networkx / sympy / typing-extensions）必须一起落盘，
    # 否则解包端 pip 会因找不到依赖而失败（旧 exe 安装器正是踩了这个坑，已删除）。
    $res = Invoke-SeedVR2Native -Exe $PythonExe -Arguments @(
        '-m', 'pip', 'download',
        "torch==$TorchVersion", "torchvision==$TorchvisionVersion", "torchaudio==$TorchaudioVersion",
        '--index-url', $IndexUrl, '-d', $WheelDir, '--timeout', '300', '--retries', '3'
    )
    if ($res.ExitCode -ne 0) {
        throw "pip download torch wheels 失败：$($res.Text.Split("`n")[-8..-1] -join ' | ')"
    }
}

function Test-SeedVR2TorchOfflineInstallable {
    <#
        构建期真实验证：用便携解释器对 wheels 目录做一次 --no-index 解析，
        确认「离线可装」，而不是等用户解包时才发现装不上。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)][string]$WheelDir
    )
    $res = Invoke-SeedVR2Native -Exe $PythonExe -Arguments @(
        '-m', 'pip', 'install', '--no-index', '--find-links', $WheelDir,
        '--dry-run', '--ignore-installed', 'torch', 'torchvision', 'torchaudio'
    )
    if ($res.ExitCode -ne 0) {
        $lines = @($res.Text.Split("`n"))
        $text = ($lines[([math]::Max(0, $lines.Count - 12))..($lines.Count - 1)]) -join "`n  "
        throw "离线可装性验证失败（wheels 缺依赖或平台不匹配）：`n  $text"
    }
    Write-Host '  离线可装性验证通过（pip --no-index --dry-run）' -ForegroundColor Green
}

function Invoke-SeedVR2AuthenticodeSign {
    <#
        可选 Authenticode 签名（P3）：对随包分发的 .ps1 助手脚本签名，需提供
        代码签名证书 .pfx。必须在 SHA256SUMS.txt 生成之前调用——签名会改变
        文件字节，先签名后生成清单才能保证哈希一致。
    #>
    param(
        [Parameter(Mandatory = $true)][string[]]$Files,
        [Parameter(Mandatory = $true)][string]$PfxPath,
        [Parameter(Mandatory = $true)][string]$PfxPassword
    )
    $signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if (-not $signtool) {
        $kits = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10\bin' -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending
        foreach ($kit in $kits) {
            $candidate = Join-Path $kit.FullName 'x64\signtool.exe'
            if (Test-Path -LiteralPath $candidate) { $signtool = Get-Item $candidate; break }
        }
    }
    if (-not $signtool) {
        throw "signtool.exe 不可用：请安装 Windows SDK 或把 signtool 加入 PATH"
    }
    foreach ($f in $Files) {
        if (-not (Test-Path -LiteralPath $f)) { continue }
        $res = Invoke-SeedVR2Native -Exe $signtool.FullName -Arguments @(
            'sign', '/fd', 'SHA256', '/f', $PfxPath, '/p', $PfxPassword, $f
        )
        if ($res.ExitCode -ne 0) {
            throw "Authenticode 签名失败 $(Split-Path -Leaf $f)：$($res.Text.Split("`n")[-5..-1] -join ' | ')"
        }
        Write-Host ("  Authenticode 已签名：{0}" -f (Split-Path -Leaf $f))
    }
}

# ------------------------------------------------------------------ 主流程 ----
if (-not $Root) {
    $Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}
$Root = (Resolve-Path -LiteralPath $Root).Path
if (-not $Version) {
    $Version = Get-SeedVR2ProjectVersion -ProjectRoot $Root
}
if (-not $OutDir) {
    $OutDir = Join-Path $Root 'dist\bundles'
}
if (-not $StagingDir) {
    $StagingDir = Join-Path $Root 'dist\portable-staging'
}
if (-not $MaxPartBytes) {
    $MaxPartBytes = Get-SeedVR2DefaultMaxPart
}
$Limit = Get-SeedVR2GithubAssetLimit
if ($MaxPartBytes -gt $Limit) {
    throw "-MaxPartBytes ($MaxPartBytes) 超过 GitHub 单文件上限 ($Limit)"
}
if ($Component.Count -eq 0) {
    $Component = $AllComponents
}
foreach ($c in $Component) {
    if ($AllComponents -notcontains $c) {
        throw "未知组件 '$c'，可选：$($AllComponents -join ', ')"
    }
}

$modelCfg = Get-SeedVR2ModelFilesFromConfig -ConfigPath (Join-Path $Root 'config.yaml')
if (-not $ModelDir) {
    $ModelDir = Join-Path $Root $modelCfg.pretrained_dir
}
if ($PrintModelFiles) {
    # 供 CI 复用同一份文件名事实来源，避免工作流里再硬编码一遍权重名。
    Write-Output $modelCfg.checkpoint_fp8
    Write-Output $modelCfg.vae_checkpoint
    Write-Output $modelCfg.pos_emb
    Write-Output $modelCfg.neg_emb
    exit 0
}

Write-Host ''
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " SeedVR2 便携分卷包构建 v$Version" -ForegroundColor Cyan
Write-Host " 组件：$($Component -join ', ')" -ForegroundColor Cyan
Write-Host " 分卷上限：$(Format-SeedVR2Size $MaxPartBytes)（GitHub 单文件上限 $(Format-SeedVR2Size $Limit)）" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null

# ------------------------------------------------------ 磁盘空间预检 ----
function Get-SeedVR2PeakGbEstimate {
    <#
        峰值占用 ≈ 所有组件未压缩载荷之和 × 1.3。
        构成：staging（同盘时是硬链接，几乎为 0）+ 归档 + 归档切出的全部分卷
        （归档在切完前与分卷共存，且前序组件的分卷会累积在 OutDir）。
    #>
    param(
        [string]$Runtime,
        [string]$Wheels,
        [string]$ModelDirectory,
        [string[]]$Components,
        [hashtable]$ModelFiles
    )
    $raw = [long]0
    if ($Components -contains 'core' -and $Runtime -and (Test-Path -LiteralPath $Runtime)) {
        $raw += [long](Get-ChildItem -LiteralPath $Runtime -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
    }
    if ($Components -contains 'torch' -and $Wheels -and (Test-Path -LiteralPath $Wheels)) {
        $raw += [long](Get-ChildItem -LiteralPath $Wheels -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
    }
    if ($ModelDirectory -and (Test-Path -LiteralPath $ModelDirectory)) {
        foreach ($key in @('checkpoint_fp8', 'vae_checkpoint', 'pos_emb', 'neg_emb')) {
            $want = ($Components -contains 'model-fp8' -and $key -eq 'checkpoint_fp8') -or
                ($Components -contains 'model-shared' -and $key -ne 'checkpoint_fp8')
            if (-not $want) {
                continue
            }
            $f = Join-Path $ModelDirectory $ModelFiles.$key
            if (Test-Path -LiteralPath $f) {
                $raw += [long](Get-Item -LiteralPath $f).Length
            }
        }
    }
    return [math]::Round(($raw * 1.3) / 1GB, 1)
}

$estimatedPeak = Get-SeedVR2PeakGbEstimate -Runtime $RuntimeDir -Wheels $TorchWheelDir `
    -ModelDirectory $ModelDir -Components $Component -ModelFiles $modelCfg
# 自动准备模式下这两类来源此刻还不存在，按已知量补估，否则预检会严重低估：
# WinPython 解压后约 2 GB、cu128 torch wheels 约 3 GB，且都会与产物同时驻留到构建结束。
if ($Component -contains 'core' -and -not $RuntimeDir -and -not $SkipAutoPrepare) {
    $estimatedPeak += 2.0
}
if ($Component -contains 'torch' -and -not $TorchWheelDir -and -not $SkipAutoPrepare) {
    $estimatedPeak += 3.0
}
$requiredFree = if ($MinFreeGb -gt 0) { [double]$MinFreeGb } else { [math]::Max($estimatedPeak, 2.0) }
Write-Host "`n[预检] 磁盘空间"
Assert-SeedVR2DiskSpace -Path $OutDir -NeededGb $requiredFree | Out-Null
if ((Split-Path -Qualifier $StagingDir) -ne (Split-Path -Qualifier $OutDir)) {
    Assert-SeedVR2DiskSpace -Path $StagingDir -NeededGb $requiredFree | Out-Null
}
Write-Host ("  预估峰值 {0} GB（staging 与 OutDir 同盘时用硬链接，可显著降低实际占用）" -f $estimatedPeak)

$needRuntime = ($Component -contains 'core')
$needWheels = ($Component -contains 'torch')
if ($needRuntime -and -not $RuntimeDir -and $SkipAutoPrepare) {
    throw "core 组件需要 -RuntimeDir（已解压且未装 torch 的便携解释器目录）"
}
if ($needWheels -and -not $TorchWheelDir -and $SkipAutoPrepare) {
    throw "torch 组件需要 -TorchWheelDir（含 cu128 wheels 的目录）"
}

$prepDir = Join-Path $StagingDir '_prepare'
New-Item -ItemType Directory -Path $prepDir -Force | Out-Null
if ($needRuntime -and -not $RuntimeDir) {
    Write-Host "`n[准备] 便携 Python 运行时"
    $RuntimeDir = Start-SeedVR2RuntimePrepare -WorkDir $prepDir -ProjectRoot $Root -Url $WinPythonUrl
}
if ($needWheels -and -not $TorchWheelDir) {
    if (-not $RuntimeDir) {
        throw "torch 组件需要便携解释器执行 pip download：请一并构建 core，或传 -TorchWheelDir"
    }
    $pyInfo = Resolve-SeedVR2RuntimeRoot -Path $RuntimeDir
    $TorchWheelDir = Join-Path $prepDir 'torch_wheels'
    Write-Host "`n[准备] torch wheels"
    Start-SeedVR2TorchWheelPrepare -WheelDir $TorchWheelDir -PythonExe $pyInfo.PythonExe -IndexUrl $TorchIndexUrl
}
if ($TorchWheelDir -and $RuntimeDir -and -not $SkipOfflineTorchCheck) {
    Write-Host "`n[验证] torch wheels 在便携解释器上是否可离线安装"
    $probe = Resolve-SeedVR2RuntimeRoot -Path $RuntimeDir
    $alive = Invoke-SeedVR2Native -Exe $probe.PythonExe -Arguments @('-c', "print('ok')")
    if ($alive.ExitCode -ne 0 -or ($alive.Text -notmatch 'ok')) {
        Write-Warning "$($probe.PythonExe) 不是可执行的解释器，跳过离线可装性验证（真实构建必须通过此检查）"
    } else {
        Test-SeedVR2TorchOfflineInstallable -PythonExe $probe.PythonExe -WheelDir $TorchWheelDir
    }
}

$components = @()
$allParts = @()
foreach ($id in $Component) {
    $spec = Get-SeedVR2ComponentSpec -Name $id
    Write-Host "`n[$id] $($spec.Title)" -ForegroundColor Yellow
    $payload = Join-Path $StagingDir "$id-payload"
    Remove-SeedVR2TreeFast -Path $payload
    New-Item -ItemType Directory -Path $payload -Force | Out-Null

    switch ($id) {
        'core' {
            $built = New-SeedVR2CorePayload -PayloadDir $payload -ProjectRoot $Root -Runtime $RuntimeDir -Ver $Version
        }
        'torch' {
            $built = New-SeedVR2TorchPayload -PayloadDir $payload -WheelDir $TorchWheelDir
        }
        'model-shared' {
            # pos_emb/neg_emb 是仓库内嵌资产（CI 无需联网）；vae_checkpoint 仍来自 HF 下载的 $ModelDir。
            $sharedMap = @{
                $modelCfg.pos_emb = $BundleAssetsDir
                $modelCfg.neg_emb = $BundleAssetsDir
            }
            $built = New-SeedVR2ModelPayload -PayloadDir $payload `
                -FileNames @($modelCfg.vae_checkpoint, $modelCfg.pos_emb, $modelCfg.neg_emb) `
                -SourceModelDir $ModelDir -ExtraSourceDir $sharedMap
        }
        'model-fp8' {
            $built = New-SeedVR2ModelPayload -PayloadDir $payload -FileNames @($modelCfg.checkpoint_fp8) -SourceModelDir $ModelDir
        }
    }
    Write-Host ("  载荷：{0} 文件 / {1}（未压缩）" -f $built.Files, (Format-SeedVR2Size $built.Bytes))

    Assert-SeedVR2NoForbiddenPayload -Path $payload

    $archiveBase = Join-Path $StagingDir "SeedVR2-Portable-v$Version-win-x64-$id"
    $archive = New-SeedVR2Archive -SourceDir $payload -OutFile $archiveBase -Format $Format -Level $spec.Level
    Write-Host ("  归档：{0} → {1}（{2}）" -f $archive.Format, (Format-SeedVR2Size $archive.Bytes), (Split-Path -Leaf $archive.Tool))

    $volumes = @(Split-SeedVR2FileIntoVolumes -File $archive.Path -MaxBytes $MaxPartBytes)
    $volCount = $volumes.Count
    foreach ($v in $volumes) {
        Move-Item -LiteralPath $v.Path -Destination (Join-Path $OutDir $v.Name) -Force
        $allParts += (Join-Path $OutDir $v.Name)
        Write-Host ("    卷 {0}/{1}：{2}  {3}" -f $v.Index, $volCount, (Format-SeedVR2Size $v.Bytes), $v.Name)
    }
    if (-not $KeepArchive) {
        Remove-Item -LiteralPath $archive.Path -Force
    }
    # 载荷清单：小集合逐项记录（模型/wheels 组件），大集合只记总量
    # （core 含整个 WinPython 树，逐项写入会让 manifest.json 膨胀到数 MB）。
    $payloadRoot = (Resolve-Path -LiteralPath $payload).Path
    $payloadEnum = @(Get-ChildItem -LiteralPath $payload -Recurse -File -Force)
    $payloadFiles = @()
    $payloadTruncated = $false
    if ($payloadEnum.Count -le 1000) {
        foreach ($f in $payloadEnum) {
            $payloadFiles += [pscustomobject]@{
                path  = (ConvertTo-SeedVR2Relative -Root $payloadRoot -Full $f.FullName).Replace('\', '/')
                bytes = [long]$f.Length
            }
        }
    } else {
        $payloadTruncated = $true
        foreach ($f in @('config.yaml', 'app\clean_launch.py', 'README-PORTABLE.txt', 'start-portable.bat')) {
            $probePath = Join-Path $payload (Join-Path $PortableRootName $f)
            if (Test-Path -LiteralPath $probePath) {
                $payloadFiles += [pscustomobject]@{
                    path  = "$PortableRootName/$f".Replace('\', '/')
                    bytes = [long](Get-Item -LiteralPath $probePath).Length
                }
            }
        }
    }
    $volInfos = @($volumes | ForEach-Object {
            [pscustomobject]@{
                index  = $_.Index
                file   = $_.Name
                bytes  = $_.Bytes
                sha256 = $_.Sha256
            }
        })
    $components += [pscustomobject]@{
        id                       = $spec.Id
        title                    = $spec.Title
        description              = $spec.Description
        required                 = $spec.Required
        archive                  = (Split-Path -Leaf $archive.Path)
        format                   = $archive.Format
        raw_bytes                = $archive.Bytes
        sha256                   = $archive.Sha256
        payload_dir              = $PortableRootName
        payload_file_count       = $payloadEnum.Count
        payload_files_truncated  = $payloadTruncated
        payload_files            = $payloadFiles
        volume_count             = $volCount
        volumes                  = $volInfos
    }
    if (-not $KeepStaging) {
        # 逐组件释放 staging（同盘为硬链接几乎不占空间；跨盘回退复制时必须及时回收）。
        Remove-SeedVR2TreeFast -Path $payload
    }
}

# --------------------------------------------------------- 体积门禁 ----
$violations = @(Test-SeedVR2AssetSizeGate -Paths $allParts)
if ($violations.Count -gt 0) {
    Write-Host "`n体积门禁未通过：" -ForegroundColor Red
    foreach ($v in $violations) {
        Write-Host ("  {0}  {1}" -f $v.Path, $v.Reason) -ForegroundColor Red
    }
    if (-not $SkipSizeGate) {
        throw "存在超过 GitHub 单文件上限的分卷，禁止上传"
    }
}

# --------------------------------------------------------- 清单与说明 ----
$unpackScript = Join-Path $PSScriptRoot 'unpack_portable_bundle.ps1'
if (Test-Path -LiteralPath $unpackScript) {
    Copy-Item -LiteralPath $unpackScript -Destination $OutDir -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'portable_bundle_lib.ps1') -Destination $OutDir -Force
}
$libName = 'portable_bundle_lib.ps1'

if ($SigningPfxPath) {
    Write-Host ''
    Write-Host '[Authenticode] 签名随包 .ps1 助手（先签名后生成 SHA256SUMS.txt）...' -ForegroundColor Yellow
    Invoke-SeedVR2AuthenticodeSign -Files @(
        (Join-Path $OutDir 'unpack_portable_bundle.ps1'),
        (Join-Path $OutDir $libName)
    ) -PfxPath $SigningPfxPath -PfxPassword $SigningPfxPassword
}

$totalRaw = 0
$totalDist = 0
foreach ($c in $components) {
    $totalRaw += $c.raw_bytes
    foreach ($v in $c.volumes) { $totalDist += $v.bytes }
}
$manifest = [ordered]@{
    schema      = 1
    product     = 'SeedVR2-Portable'
    version     = $Version
    arch        = 'win-x64'
    created_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    volume_limit_bytes = $Limit
    max_part_bytes     = $MaxPartBytes
    unpack_helper   = 'unpack_portable_bundle.ps1'
    unpack_requires = @('Windows PowerShell 5.1 或更高', '同目录的 portable_bundle_lib.ps1')
    total_payload_bytes = $totalRaw
    total_dist_bytes    = $totalDist
    components  = $components
    compliance  = [ordered]@{
        ffmpeg_not_distributed = $true
        watermark_key_excluded = $true
        license_and_notice_included = $true
        note = '构建期由 Assert-SeedVR2NoForbiddenPayload 递归断言，见 NOTICE 第 4 条与 docs/COMPLIANCE_CHECKLIST.md §2'
    }
}
Write-SeedVR2Json -Object $manifest -Path (Join-Path $OutDir 'manifest.json')
$sumFiles = @($allParts)
foreach ($f in @('manifest.json', 'unpack_portable_bundle.ps1', $libName)) {
    $p = Join-Path $OutDir $f
    if (Test-Path -LiteralPath $p) { $sumFiles += $p }
}
$sumsFile = Write-SeedVR2Sha256Sums -Paths $sumFiles -OutFile (Join-Path $OutDir 'SHA256SUMS.txt')
$uploadList = ($allParts + @((Join-Path $OutDir 'manifest.json'), $sumsFile, (Join-Path $OutDir 'unpack_portable_bundle.ps1'), (Join-Path $OutDir $libName)))
[System.IO.File]::WriteAllLines((Join-Path $OutDir 'upload-list.txt'), [string[]]$uploadList, (New-Object System.Text.UTF8Encoding($false)))

if (-not $KeepStaging) {
    Remove-SeedVR2TreeFast -Path $StagingDir
}

Write-Host ''
Write-Host '==============================================' -ForegroundColor Green
Write-Host " 构建完成：$($components.Count) 个组件 / $($allParts.Count) 个分卷 / $(Format-SeedVR2Size $totalDist)" -ForegroundColor Green
Write-Host " 产物目录：$OutDir" -ForegroundColor Green
foreach ($c in $components) {
    $cSum = 0
    foreach ($v in $c.volumes) { $cSum += $v.bytes }
    Write-Host ("   {0,-13} {1} 卷  {2}" -f $c.id, $c.volume_count, (Format-SeedVR2Size $cSum))
}
Write-Host '==============================================' -ForegroundColor Green
Write-Host '下一步：上传到 Release（每个文件均 < 2 GiB；已发布资产不可变，禁用 --clobber）' -ForegroundColor Green
Write-Host '  gh release create v<ver> --target main --generate-notes'
Write-Host "  Get-Content '$(Join-Path $OutDir 'upload-list.txt')' | %{ gh release upload v<ver> `"`$_`" }"
