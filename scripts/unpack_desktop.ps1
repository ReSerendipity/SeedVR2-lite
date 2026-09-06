# scripts/unpack_desktop.ps1
# SeedVR2 Tauri 桌面版解包器：校验分卷 SHA256 → 合并 → 解压 → 落地核对。
# 与 unpack_portable_bundle.ps1 的区别：Tauri 版为整体单组件（full），
# torch 已内置在 runtime/ 中，不存在独立 torch 组件，解压后直接双击 SeedVR2.exe。
#
# 用法：
#   把本脚本与所有 .7z.00N 分卷、manifest.json 放同一文件夹，然后：
#   powershell -ExecutionPolicy Bypass -File .\unpack_desktop.ps1 [-TargetDir D:\SeedVR2] [-VerifyOnly]

[CmdletBinding()]
param(
    [string]$TargetDir = '',
    [string]$PortableRootName = 'SeedVR2',
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---------- 载入公共库 ----------
. (Join-Path $scriptDir 'portable_bundle_lib.ps1')

# ---------- 解压工具探测 + 解压函数（与 unpack_portable_bundle.ps1 同逻辑） ----------
$sevenZip = Find-SeedVR2SevenZip
$tar = Find-SeedVR2SystemTar
if (-not $sevenZip -and -not $tar) {
    throw "系统既无 7-Zip 也无 tar.exe，无法解压（Win10 1803+ 自带 tar.exe）"
}

function Expand-SeedVR2Archive {
    param(
        [Parameter(Mandatory = $true)][string]$Archive,
        [Parameter(Mandatory = $true)][string]$Dest
    )
    if ($sevenZip) {
        $res = Invoke-SeedVR2Native -Exe $sevenZip -Arguments @('x', $Archive, "-o$Dest", '-y')
        if ($res.ExitCode -eq 0) { return }
        Write-Warning "7z 解压失败（退出码 $($res.ExitCode)），尝试 tar.exe"
    }
    if (-not $tar) { throw "需要 7-Zip 才能解压归档" }
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    $res2 = Invoke-SeedVR2Native -Exe $tar -Arguments @('-xf', $Archive, '-C', $Dest)
    if ($res2.ExitCode -ne 0) { throw "解压 $Archive 失败（退出码 $($res2.ExitCode)）。$($res2.Text)" }
}

# ---------- 定位 manifest 与分卷 ----------
$manifestPath = Join-Path $scriptDir 'manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "同目录缺少 manifest.json（请确认与分卷在同一文件夹）"
}
$manifest = Read-SeedVR2Json -Path $manifestPath
if (-not $TargetDir) { $TargetDir = $scriptDir }

Write-Host '==============================================' -ForegroundColor Cyan
Write-Host (" SeedVR2 桌面版解包器  v{0}" -f $manifest.version)
Write-Host (" 分卷目录：{0}" -f $scriptDir)
Write-Host (" 安装到　：{0}" -f $TargetDir)
Write-Host '=============================================='

# 磁盘预检
Assert-SeedVR2DiskSpace -Path $TargetDir -NeededGb 12

$selected = @($manifest.components | Where-Object { $_.required })
if ($selected.Count -eq 0) { throw 'manifest.json 中无 required 组件' }

foreach ($c in $selected) {
    Write-Host ''
    Write-Host ("[{0}] {1} —— {2} 个分卷" -f $c.id, $c.title, $c.volume_count) -ForegroundColor Yellow

    # 1) 分卷 SHA256 校验
    $volumePaths = @()
    $bad = @()
    foreach ($v in $c.volumes) {
        $p = Join-Path $scriptDir $v.file
        if (-not (Test-Path -LiteralPath $p)) { $bad += "缺少 $($v.file)"; continue }
        $actual = Get-SeedVR2FileSha256 -Path $p
        if ($actual -ne $v.sha256.ToLowerInvariant()) {
            $bad += "$($v.file) SHA256 不匹配（期望 $($v.sha256.Substring(0,12))…，实际 $($actual.Substring(0,12))…）"
        }
        $volumePaths += $p
    }
    if ($bad.Count -gt 0) {
        throw "分卷校验失败（下载损坏或被篡改）：`n  $(($bad -join "`n  "))"
    }
    Write-Host '  分卷校验通过' -ForegroundColor Green
    if ($VerifyOnly) { continue }

    # 2) 合并
    $merged = Join-Path $TargetDir ("__merge__-" + $c.archive)
    try {
        Join-SeedVR2Volumes -VolumePaths $volumePaths -OutFile $merged -ExpectedBytes ([long]$c.raw_bytes) | Out-Null
        $actual = Get-SeedVR2FileSha256 -Path $merged
        if ($actual -ne $c.sha256.ToLowerInvariant()) {
            throw "合并后归档校验失败（期望 $($c.sha256.Substring(0,12))，实际 $($actual.Substring(0,12))）"
        }
        Write-Host '  合并校验通过' -ForegroundColor Green
        # 3) 解压（归档含 SeedVR2/ 顶层）
        Write-Host '  解压中 ...'
        Expand-SeedVR2Archive -Archive $merged -Dest $TargetDir
    } finally {
        Remove-Item -LiteralPath $merged -Force -ErrorAction SilentlyContinue
    }
}

$appRoot = Join-Path $TargetDir $PortableRootName
if (-not (Test-Path -LiteralPath $appRoot)) {
    throw "解压后未找到 $appRoot，归档布局异常"
}

# ---------- 落地核对 ----------
Write-Host ''
Write-Host '[落地核对]' -ForegroundColor Yellow
$problems = @()
foreach ($need in @('SeedVR2.exe', 'app\start_portable.py', 'app\runtime\python.exe', 'app\config.yaml')) {
    if (-not (Test-Path -LiteralPath (Join-Path $appRoot $need))) {
        $problems += "缺少 $need"
    }
}
if ($problems.Count -gt 0) {
    foreach ($p in $problems) { Write-Host "    - $p" -ForegroundColor Red }
    throw '落地核对未通过，请重新下载分卷后重试。'
}
Set-Content -LiteralPath (Join-Path $appRoot '.portable_ready') -Value (Get-Date).ToString('o') -Encoding ascii

Write-Host '  核心文件核对通过' -ForegroundColor Green
Write-Host ''
Write-Host '==============================================' -ForegroundColor Green
Write-Host (" 解包完成：{0}" -f $appRoot) -ForegroundColor Green
Write-Host ' 启动：双击 SeedVR2.exe（无需安装 Python / 无需联网）' -ForegroundColor Green
Write-Host ' 提示：torch(CUDA) 已内置在 runtime/；图片修复开箱即用；视频修复需自行安装 FFmpeg 并加入 PATH' -ForegroundColor Green
Write-Host ' 用户配置与日志：%APPDATA%\SeedVR2\ 与 app\logs\' -ForegroundColor Green
