# scripts/release_tauri.ps1
# SeedVR2 Tauri 桌面版发布打包脚本
# 将组装好的发布目录（SeedVR2.exe + app/）整体 7z 分卷，产出 GitHub Release 资产。
# 依赖：scripts/portable_bundle_lib.ps1（分卷/7z/SHA256 公共库）。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\release_tauri.ps1 -Version 1.5.1
#
# 参数：
#   -StagingDir  发布目录根（默认 dist\tauri-release\staging，含 SeedVR2.exe 与 app/）
#   -OutDir      分卷输出目录（默认 dist\tauri-release\bundles）
#   -MaxPartBytes 分卷上限（默认 1900MB，GitHub 2GiB 限制内留余量）

[CmdletBinding()]
param(
    [string]$Version = '',
    [string]$StagingDir = '',
    [string]$OutDir = '',
    [long]$MaxPartBytes = 0
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

# ---------- 载入公共库 ----------
$lib = Join-Path $PSScriptRoot 'portable_bundle_lib.ps1'
if (-not (Test-Path -LiteralPath $lib)) {
    throw "缺少公共库: $lib"
}
. $lib

if (-not $StagingDir) { $StagingDir = Join-Path $root 'dist\tauri-release\staging' }
if (-not $OutDir)    { $OutDir = Join-Path $root 'dist\tauri-release\bundles' }
if ($MaxPartBytes -le 0) { $MaxPartBytes = Get-SeedVR2DefaultMaxPart }

# ---------- 版本号 ----------
if (-not $Version) {
    $vfile = Join-Path $StagingDir 'app\VERSION.txt'
    if (Test-Path -LiteralPath $vfile) {
        $Version = (Get-Content $vfile -Raw).Trim()
    }
}
if (-not $Version) {
    throw '无法确定版本号，请用 -Version 显式指定'
}
if ($Version -notmatch '^v?\d+\.\d+\.\d+') {
    throw "非法版本号: $Version"
}
$ver = $Version.TrimStart('v')

# ---------- 断言 staging 结构 ----------
$exePath = Join-Path $StagingDir 'SeedVR2.exe'
$appPy = Join-Path $StagingDir 'app\start_portable.py'
$runtimePy = Join-Path $StagingDir 'app\runtime\python.exe'
foreach ($p in @($exePath, $appPy, $runtimePy)) {
    if (-not (Test-Path -LiteralPath $p)) {
        throw "发布目录不完整，缺少: $p"
    }
}

# 创建输出目录 + 磁盘空间预检
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
Assert-SeedVR2DiskSpace -Path $OutDir -NeededGb 12

# ---------- 应用版本清单 ----------
# 壳依赖 app/version.json 识别本地版本，缺失会导致更新被静默跳过——必须随发布物分发。
$verJson = Join-Path $StagingDir 'app\version.json'
if (-not (Test-Path -LiteralPath $verJson)) {
    $srcVer = Join-Path $root 'version.json'
    if (Test-Path -LiteralPath $srcVer) {
        Copy-Item -LiteralPath $srcVer -Destination $verJson -Force
        Write-Host "[release_tauri] 已将根 version.json 复制到 app\（本地版本识别所需）"
    } else {
        Write-Warning "[release_tauri] 未找到项目根 version.json，发布物将无法自动更新"
    }
}

# ---------- 打包 + 分卷 ----------
$base = "SeedVR2-Desktop-v$ver-win-x64"
$archivePath = Join-Path $OutDir "$base.7z"

# 归档需含 SeedVR2/ 顶层（unpack 脚本按 PortableRootName 定位解压目录）：
# 把 staging 内容复制到 _pkg\SeedVR2\ 后再归档 _pkg。
$pkgParent = Join-Path $OutDir '_pkg'
$pkgRoot = Join-Path $pkgParent 'SeedVR2'
if (Test-Path -LiteralPath $pkgParent) { Remove-Item -LiteralPath $pkgParent -Recurse -Force }
New-Item -ItemType Directory -Path $pkgRoot -Force | Out-Null
Write-Host "[release_tauri] 包装发布目录 -> $pkgRoot"
& robocopy $StagingDir $pkgRoot /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy 复制 $StagingDir 到 $pkgRoot 失败（exit=$LASTEXITCODE）" }

Write-Host "[release_tauri] 打包 $pkgParent -> $archivePath"
$arc = New-SeedVR2Archive -SourceDir $pkgParent -OutFile $archivePath -Format 7z -Level 4
Remove-Item -LiteralPath $pkgParent -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ("[release_tauri] 归档 {0:N2} GB，开始分卷（每卷 <= {1:N0} MB）" -f ($arc.Bytes/1GB), ($MaxPartBytes/1MB))
$volumes = Split-SeedVR2FileIntoVolumes -File $arc.Path -MaxBytes $MaxPartBytes
if (Test-Path -LiteralPath $arc.Path) {
    Remove-Item -LiteralPath $arc.Path -Force
}

# ---------- 复制解压辅助 ----------
$unpack = Join-Path $PSScriptRoot 'unpack_desktop.ps1'
if (Test-Path -LiteralPath $unpack) {
    Copy-Item -LiteralPath $unpack -Destination (Join-Path $OutDir 'unpack_desktop.ps1') -Force
}
Copy-Item -LiteralPath $lib -Destination (Join-Path $OutDir 'portable_bundle_lib.ps1') -Force

# ---------- SHA256SUMS ----------
$sumFile = Join-Path $OutDir 'SHA256SUMS.txt'
$sumLines = @()
foreach ($v in $volumes) {
    $sumLines += "{0}  {1}" -f $v.Sha256, $v.Name
}
# 归档整体哈希也记录（供人工核验）
$sumLines += "{0}  {1}" -f $arc.Sha256, "$base.7z"
[System.IO.File]::WriteAllLines($sumFile, $sumLines)

# ---------- manifest.json ----------
$manifest = [ordered]@{
    schema = 1
    product = 'SeedVR2-Desktop'
    version = $ver
    arch = 'win-x64'
    created_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    volume_limit_bytes = [long](Get-SeedVR2GithubAssetLimit)
    max_part_bytes = $MaxPartBytes
    unpack_helper = 'unpack_desktop.ps1'
    unpack_requires = @('Windows PowerShell 5.1 或更高', '同目录的 portable_bundle_lib.ps1')
    total_payload_bytes = [long]$arc.Bytes
    total_dist_bytes = [long]($volumes | Measure-Object -Property Bytes -Sum).Sum
    components = @(
        [ordered]@{
            id = 'full'
            title = 'SeedVR2 桌面版完整包（壳 + 应用 + 便携 Python 运行时 + 内置 MXFP8 模型）'
            description = 'Tauri 壳 SeedVR2.exe + app/（FastAPI 应用 + runtime/ 侧载 Python + model/ 内置模型），解压即用。增量更新仅替换应用代码。'
            required = $true
            archive = "$base.7z"
            format = '7z'
            raw_bytes = [long]$arc.Bytes
            sha256 = $arc.Sha256
            payload_dir = 'SeedVR2'
            volume_count = $volumes.Count
            volumes = @(
                foreach ($v in $volumes) {
                    [ordered]@{ index = $v.Index; file = $v.Name; bytes = [long]$v.Bytes; sha256 = $v.Sha256 }
                }
            )
        }
    )
}
Write-SeedVR2Json -Object $manifest -Path (Join-Path $OutDir 'manifest.json')

# ---------- upload-list.txt ----------
$upload = @()
foreach ($v in $volumes) {
    $upload += Join-Path $OutDir $v.Name
}
$upload += Join-Path $OutDir 'SHA256SUMS.txt'
$upload += Join-Path $OutDir 'manifest.json'
$upload += Join-Path $OutDir 'unpack_portable_bundle.ps1'
$upload += Join-Path $OutDir 'portable_bundle_lib.ps1'
[System.IO.File]::WriteAllLines((Join-Path $OutDir 'upload-list.txt'), $upload)

Write-Host "[release_tauri] 完成！共 $($volumes.Count) 个分卷，总 $('{0:N2} GB' -f (($volumes | Measure-Object -Property Bytes -Sum).Sum/1GB))"
Write-Host "[release_tauri] 输出目录: $OutDir"
