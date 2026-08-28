#Requires -Version 5.1
# scripts/unpack_portable_bundle.ps1
# 便携分卷包解包器：校验每个分卷的 SHA256 → 按序合并 → 解压 → 离线安装 torch。
#
# 用法（把某次发布的所有分卷 + manifest.json + SHA256SUMS.txt + 本脚本放在同一目录）：
#   powershell -ExecutionPolicy Bypass -File .\unpack_portable_bundle.ps1
#   powershell -ExecutionPolicy Bypass -File .\unpack_portable_bundle.ps1 -TargetDir D:\SeedVR2
#   ... -Component core,model-fp8 -SkipTorchInstall
#
# 只依赖 Windows 自带的 PowerShell 与 tar.exe；若系统装有 7-Zip 则自动使用。

[CmdletBinding()]
param(
    [string]$BundleDir = '',
    [string]$TargetDir = '',
    [string[]]$Component = @(),
    [string]$PortableRootName = 'SeedVR2-Portable',
    [switch]$SkipTorchInstall,
    [switch]$SkipVerify,
    [switch]$KeepArchive,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'
if (-not $BundleDir) {
    $BundleDir = (Resolve-Path -LiteralPath $PSScriptRoot).Path
}
. (Join-Path $BundleDir 'portable_bundle_lib.ps1')
if (-not (Test-Path -LiteralPath (Join-Path $BundleDir 'portable_bundle_lib.ps1'))) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'portable_bundle_lib.ps1') -Destination $BundleDir -Force
    . (Join-Path $BundleDir 'portable_bundle_lib.ps1')
}
$manifestPath = Join-Path $BundleDir 'manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "找不到 $manifestPath —— 请把 manifest.json 与所有分卷放在同一目录"
}
$manifest = Read-SeedVR2Json -Path $manifestPath
if (-not $TargetDir) {
    # 默认解到分卷所在目录（脚本所在目录）本身：运行完直接在该文件夹下出现 SeedVR2-Portable
    # （appRoot = TargetDir\SeedVR2-Portable），不套两层、不落到桌面。
    $TargetDir = $BundleDir
}
New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
$TargetDir = (Resolve-Path -LiteralPath $TargetDir).Path

Write-Host ''
Write-Host '==============================================' -ForegroundColor Cyan
Write-Host " SeedVR2 便携包解包器  v$($manifest.version)" -ForegroundColor Cyan
Write-Host " 分卷目录：$BundleDir" -ForegroundColor Cyan
Write-Host " 安装到　：$TargetDir" -ForegroundColor Cyan
Write-Host '==============================================' -ForegroundColor Cyan

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
    $fmt = [System.IO.Path]::GetExtension($Archive)
    if ($sevenZip) {
        $res = Invoke-SeedVR2Native -Exe $sevenZip -Arguments @('x', $Archive, "-o$Dest", '-y')
        if ($res.ExitCode -eq 0) {
            return
        }
        Write-Warning "7z 解压失败（退出码 $($res.ExitCode)），尝试 tar.exe"
    }
    if (-not $tar) {
        throw "需要 7-Zip 才能解压 $fmt 归档"
    }
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    $res2 = Invoke-SeedVR2Native -Exe $tar -Arguments @('-xf', $Archive, '-C', $Dest)
    if ($res2.ExitCode -ne 0) {
        throw "解压 $Archive 失败（退出码 $($res2.ExitCode)）。$fmt 归档需安装 7-Zip 后重试。`n$($res2.Text)"
    }
}

$selected = @($manifest.components)
if ($Component.Count -gt 0) {
    $selected = @($selected | Where-Object { $Component -contains $_.id })
    if ($selected.Count -eq 0) {
        throw "-Component 指定的组件在 manifest 中不存在：$($Component -join ', ')"
    }
}

# ------------------------------------------------------ 磁盘空间预检 ----
# 峰值 = 全部待解压载荷 + 当前正在合并的归档（每解完一个组件即删，故峰值再加最大者）。
if (-not $VerifyOnly) {
    $needBytes = [long]0
    $maxArchive = [long]0
    foreach ($c in $selected) {
        $needBytes += [long]$c.raw_bytes
        if ([long]$c.raw_bytes -gt $maxArchive) {
            $maxArchive = [long]$c.raw_bytes
        }
    }
    $needGb = [math]::Round((($needBytes + $maxArchive) * 1.15) / 1GB, 1)
    Assert-SeedVR2DiskSpace -Path $TargetDir -NeededGb $needGb | Out-Null
}

$installed = @()
foreach ($c in $selected) {
    Write-Host ''
    Write-Host "[$($c.id)] $($c.title) —— $($c.volume_count) 个分卷" -ForegroundColor Yellow
    $missing = @()
    $bad = @()
    foreach ($v in $c.volumes) {
        $vp = Join-Path $BundleDir $v.file
        if (-not (Test-Path -LiteralPath $vp)) {
            $missing += $v.file
            continue
        }
        if (-not $SkipVerify) {
            $actual = Get-SeedVR2FileSha256 -Path $vp
            if ($actual -ne $v.sha256.ToLowerInvariant()) {
                $bad += ('{0}（期望 {1}，实际 {2}）' -f $v.file, $v.sha256.Substring(0, 12), $actual.Substring(0, 12))
            }
        }
    }
    if ($missing.Count -gt 0) {
        throw "缺少分卷：$($missing -join ', ')`n请到 Releases 页面下载该组件的全部 $($c.volume_count) 个分卷，放在同一目录后重试。"
    }
    if ($bad.Count -gt 0) {
        throw "分卷校验失败（下载损坏或被篡改）：`n  $(($bad -join "`n  "))"
    }
    Write-Host '  分卷校验通过' -ForegroundColor Green
    if ($VerifyOnly) {
        $installed += [pscustomobject]@{ Component = $c.id; Volumes = $c.volume_count; Bytes = 0; State = 'verified' }
        continue
    }

    $volumePaths = @($c.volumes | Sort-Object { [int]$_.index } | ForEach-Object { Join-Path $BundleDir $_.file })
    $merged = Join-Path $TargetDir ("__merge__-" + $c.archive)
    try {
        Join-SeedVR2Volumes -VolumePaths $volumePaths -OutFile $merged -ExpectedBytes ([long]$c.raw_bytes) | Out-Null
        if (-not $SkipVerify) {
            $actual = Get-SeedVR2FileSha256 -Path $merged
            if ($actual -ne $c.sha256.ToLowerInvariant()) {
                throw "合并后归档校验失败（期望 $($c.sha256.Substring(0,12))，实际 $($actual.Substring(0,12))）"
            }
            Write-Host '  合并校验通过' -ForegroundColor Green
        }
        Write-Host '  解压中 ...'
        Expand-SeedVR2Archive -Archive $merged -Dest $TargetDir
        Write-Host ("  已解压到 {0}\{1}" -f $TargetDir, $PortableRootName) -ForegroundColor Green
    } finally {
        if (-not $KeepArchive) {
            Remove-Item -LiteralPath $merged -Force -ErrorAction SilentlyContinue
        }
    }
    $installed += [pscustomobject]@{ Component = $c.id; Volumes = $c.volume_count; Bytes = [long]$c.raw_bytes; State = 'unpacked' }
}

if ($VerifyOnly) {
    Write-Host "`n全部组件校验通过（-VerifyOnly 模式，未解压）。" -ForegroundColor Green
    return
}

$appRoot = Join-Path $TargetDir $PortableRootName
if (-not (Test-Path -LiteralPath $appRoot)) {
    throw "解压后未找到 $appRoot，归档布局异常"
}

# ------------------------------------------------------- 离线安装 torch ----
$wheelsDir = Join-Path $appRoot 'torch_wheels'
if ((Test-Path -LiteralPath $wheelsDir) -and -not $SkipTorchInstall) {
    Write-Host ''
    Write-Host '[torch] 向便携解释器离线安装 torch（全程不联网）' -ForegroundColor Yellow
    $pyInfo = Resolve-SeedVR2RuntimeRoot -Path $appRoot
    $inst = Invoke-SeedVR2Native -Exe $pyInfo.PythonExe -Arguments @(
        '-m', 'pip', 'install', '--no-index', '--find-links', $wheelsDir, 'torch', 'torchvision', 'torchaudio'
    )
    if ($inst.ExitCode -ne 0) {
        throw "torch 离线安装失败。可手动执行：$($pyInfo.PythonExe) -m pip install --no-index --find-links `"$wheelsDir`" torch torchvision torchaudio`n$($inst.Text.Split("`n")[-10..-1] -join "`n")"
    }
    $verRes = Invoke-SeedVR2Native -Exe $pyInfo.PythonExe -Arguments @('-c', 'import torch;print(torch.__version__)')
    if ($verRes.ExitCode -ne 0) {
        throw "torch 安装后无法 import，请检查 NVIDIA 驱动与 VC++ 运行库`n$($verRes.Text)"
    }
    Write-Host "  torch $($verRes.Text.Trim()) 可用" -ForegroundColor Green
    $cudaRes = Invoke-SeedVR2Native -Exe $pyInfo.PythonExe -Arguments @(
        '-c', "import torch;print('yes' if torch.cuda.is_available() else 'no')"
    )
    if ($cudaRes.Text.Trim() -ne 'yes') {
        Write-Warning "torch.cuda.is_available() 为 False：本机可能缺 NVIDIA 驱动或 CUDA 版本不匹配（本包为 cu128）。CPU 仍可运行但极慢。"
    }
}
elseif (-not (Test-Path -LiteralPath $wheelsDir)) {
    Write-Warning "未解包 torch 组件，程序无法推理。请补下 v$($manifest.version) 的 torch 组件分卷后重跑本脚本。"
}

# ----------------------------------------------------- 按清单核对落地 ----
Write-Host ''
Write-Host '[落地核对]' -ForegroundColor Yellow
$problems = @()
# 只核对本次真正解出来的组件；未选 core 时不应要求应用入口存在（补下模型组件是合法用法）。
$ids = @($selected | ForEach-Object { $_.id })
foreach ($c in $selected) {
    if (-not $c.payload_files) {
        continue
    }
    foreach ($f in $c.payload_files) {
        $disk = Join-Path $TargetDir ($f.path.Replace('/', '\'))
        if (-not (Test-Path -LiteralPath $disk)) {
            $problems += "[$($c.id)] 缺少 $((Split-Path -Leaf $disk))"
            continue
        }
        if ((Get-Item -LiteralPath $disk).Length -ne [long]$f.bytes) {
            $problems += ("[{0}] {1} 字节不符（期望 {2}）" -f $c.id, $f.path, $f.bytes)
        }
    }
    if ($c.payload_files_truncated) {
        Write-Host ("  [{0}] 载荷 {1} 个文件，清单已截断，仅核对关键入口" -f $c.id, $c.payload_file_count)
    }
}
if ($ids -contains 'core') {
    foreach ($need in @('config.yaml', 'app\clean_launch.py', 'start-portable.bat')) {
        if (-not (Test-Path -LiteralPath (Join-Path $appRoot $need))) {
            $problems += "缺少 $need"
        }
    }
}
if ($problems.Count -gt 0) {
    Write-Host '  以下项目不完整：' -ForegroundColor Red
    foreach ($p in $problems) { Write-Host "    - $p" -ForegroundColor Red }
    throw "落地核对未通过，请重新下载对应分卷后重试。"
}
Write-Host ("  本次解包的 {0} 个组件载荷已全部落地并核对通过" -f $selected.Count) -ForegroundColor Green
Set-Content -LiteralPath (Join-Path $appRoot '.portable_ready') -Value (Get-Date).ToString('o') -Encoding ascii

Write-Host ''
Write-Host '==============================================' -ForegroundColor Green
Write-Host " 解包完成：$appRoot" -ForegroundColor Green
if ($ids -contains 'core') {
    Write-Host ' 启动：双击 start-portable.bat → 浏览器打开 http://127.0.0.1:7870' -ForegroundColor Green
    Write-Host ' 提示：图片修复开箱即用；视频修复需自行安装 FFmpeg 并加入 PATH' -ForegroundColor Green
} else {
    Write-Host " 本次只解包了：$($ids -join ', ')（未含 core，不能启动程序）" -ForegroundColor Yellow
    Write-Host ' 若这是补下模型/torch 组件，则已合并进现有安装目录，无需重复解包 core' -ForegroundColor Yellow
}
Write-Host '==============================================' -ForegroundColor Green
