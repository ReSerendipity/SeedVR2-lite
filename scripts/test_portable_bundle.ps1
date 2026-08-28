#Requires -Version 5.1
# scripts/test_portable_bundle.ps1
# 便携分卷包链路的端到端自测（无外部依赖、不联网、不入库产物）。
#
# 用「小夹具 + 1 MB 切片上限」等价复现真实 7 GB 构建的多卷路径，验证：
#   切片/合并可逆、逐卷与逐归档 SHA256、2GiB 体积门禁、合规剥离（ffmpeg/密钥）、
#   缺卷被拒、篡改被拒、-VerifyOnly 不落盘、部分组件解包不误报。
#
# 用法：
#   .\scripts\test_portable_bundle.ps1                 # 跑完自动清理
#   .\scripts\test_portable_bundle.ps1 -KeepArtifacts   # 保留夹具与产物便于排查
# 退出码 0 = 全部断言通过。

[CmdletBinding()]
param(
    [switch]$KeepArtifacts,
    [string]$WorkDir = ''
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $WorkDir) {
    $WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) 'seedvr2-bundle-test'
}
$fx = Join-Path $WorkDir 'fixture'
$outBundle = Join-Path $WorkDir 'out-bundle'
$staging = Join-Path $WorkDir 'staging'
$installed = Join-Path $WorkDir 'installed'
$slice = 1MB

$failures = @()

function New-TextFile {
    param([string]$Path, [string]$Text)
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    Set-Content -LiteralPath $Path -Value $Text -Encoding ascii
}

function New-RandomFile {
    param([string]$Path, [long]$Bytes)
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force
    }
    $fs = [System.IO.File]::Create($Path)
    try {
        $rnd = New-Object System.Random(12345)
        $buf = New-Object byte[] 65536
        $left = $Bytes
        while ($left -gt 0) {
            $n = [int][math]::Min([long]$buf.Length, $left)
            for ($i = 0; $i -lt $n; $i++) {
                $buf[$i] = [byte]$rnd.Next(0, 256)
            }
            $fs.Write($buf, 0, $n)
            $left -= $n
        }
    } finally {
        $fs.Dispose()
    }
}

function Assert-True {
    param([bool]$Condition, [string]$What)
    if ($Condition) {
        Write-Host "  PASS  $What" -ForegroundColor Green
    } else {
        Write-Host "  FAIL  $What" -ForegroundColor Red
        $script:failures += $What
    }
}

try {
    Write-Host '=== 1. 夹具 ===' -ForegroundColor Cyan
    if (Test-Path -LiteralPath $WorkDir) {
        Remove-Item -LiteralPath $WorkDir -Recurse -Force
    }
    foreach ($d in @($fx, $outBundle, $staging, $installed)) {
        New-Item -ItemType Directory -Path $d -Force | Out-Null
    }
    New-TextFile -Path (Join-Path $fx 'WPy64-FAKE\python-3.12.10.amd64\python.exe') -Text 'not-a-real-python'
    New-TextFile -Path (Join-Path $fx 'WPy64-FAKE\python-3.12.10.amd64\Lib\site-packages\numpy\num.py') -Text 'VALUE=1'
    # 必须被剥离的伪装 ffmpeg（文件名带版本后缀，精确名匹配会漏）
    New-RandomFile -Path (Join-Path $fx 'WPy64-FAKE\python-3.12.10.amd64\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win64-v7.1.exe') -Bytes 4096
    New-RandomFile -Path (Join-Path $fx 'wheels\torch-2.11.0+cu128-cp312-cp312-win_amd64.whl') -Bytes 1.7MB
    New-RandomFile -Path (Join-Path $fx 'wheels\filelock-3.13.0-py3-none-any.whl') -Bytes 200KB
    New-RandomFile -Path (Join-Path $fx 'model\seedvr2_ema_3b_fp8_e4m3fn.safetensors') -Bytes 3MB
    New-RandomFile -Path (Join-Path $fx 'model\ema_vae_fp16.safetensors') -Bytes 400KB
    New-TextFile -Path (Join-Path $fx 'model\pos_emb.pt') -Text 'pos'
    New-TextFile -Path (Join-Path $fx 'model\neg_emb.pt') -Text 'neg'
    # 真实仓库里 LICENSE/NOTICE 位于 model/ 的上一级，构建时会被复制进包内 model/；夹具需同构。
    New-TextFile -Path (Join-Path $fx 'LICENSE') -Text 'fake-apache-2.0-license'
    New-TextFile -Path (Join-Path $fx 'NOTICE') -Text 'fake-notice'

    $srcFp8 = Join-Path $fx 'model\seedvr2_ema_3b_fp8_e4m3fn.safetensors'
    $srcWheels = Join-Path $fx 'wheels\torch-2.11.0+cu128-cp312-cp312-win_amd64.whl'
    $srcFp8Hash = (Get-FileHash -LiteralPath $srcFp8 -Algorithm SHA256).Hash.ToLower()
    $srcWheelsHash = (Get-FileHash -LiteralPath $srcWheels -Algorithm SHA256).Hash.ToLower()

    Write-Host '=== 2. 构建（切片上限 1MB）===' -ForegroundColor Cyan
    & (Join-Path $repo 'scripts\build_portable_bundle.ps1') `
        -Root $repo `
        -Version '9.9.9-test' `
        -OutDir $outBundle `
        -StagingDir $staging `
        -RuntimeDir (Join-Path $fx 'WPy64-FAKE') `
        -TorchWheelDir (Join-Path $fx 'wheels') `
        -ModelDir (Join-Path $fx 'model') `
        -MaxPartBytes $slice `
        -SkipOfflineTorchCheck `
        -SkipAutoPrepare

    Write-Host ''
    Write-Host '=== 3. 构建期断言 ===' -ForegroundColor Cyan
    . (Join-Path $repo 'scripts\portable_bundle_lib.ps1')
    $manifest = Read-SeedVR2Json -Path (Join-Path $outBundle 'manifest.json')
    Assert-True ($manifest.version -eq '9.9.9-test') 'manifest 版本号正确'
    Assert-True (@($manifest.components).Count -eq 4) 'manifest 含 4 个组件'
    $torchC = $manifest.components | Where-Object { $_.id -eq 'torch' }
    $fp8C = $manifest.components | Where-Object { $_.id -eq 'model-fp8' }
    $sharedC = $manifest.components | Where-Object { $_.id -eq 'model-shared' }
    $coreC = $manifest.components | Where-Object { $_.id -eq 'core' }
    Assert-True ($torchC.volume_count -ge 2) "torch 组件多卷（$($torchC.volume_count)）"
    $fp8Expect = [int][math]::Ceiling($fp8C.raw_bytes / $slice)
    Assert-True ($fp8C.volume_count -eq $fp8Expect) "model-fp8 卷数 = ceil(归档/切片) = $fp8Expect（实为 $($fp8C.volume_count)）"
    Assert-True ($sharedC.volume_count -eq 1) "model-shared 单卷（实为 $($sharedC.volume_count)）"
    foreach ($c in $manifest.components) {
        Assert-True ([long]($c.volumes | ForEach-Object { $_.bytes } | Measure-Object -Sum).Sum -eq [long]$c.raw_bytes) "$($c.id) 分卷字节之和 == 归档字节"
    }
    $names = @($manifest.components | ForEach-Object { $_.archive })
    Assert-True (($names | Sort-Object -Unique).Count -eq 4) '四个组件归档名互不相同（不被互相覆盖）'
    Assert-True (($fp8C.archive -replace '\.zip$', '') -match 'v9\.9\.9-test-win-x64-model-fp8$') "归档名保留完整版本号：$($fp8C.archive)"

    $parts = @(Get-ChildItem -LiteralPath $outBundle -File | Where-Object { $_.Name -match '\.\d{3}$' })
    Assert-True ($parts.Count -ge 7) "分卷总数 $($parts.Count) >= 7"
    Assert-True (@(Test-SeedVR2AssetSizeGate -Paths @($parts.FullName) -LimitBytes $slice).Count -eq 0) '全部分卷 <= 切片上限'
    Assert-True (@(Test-SeedVR2AssetSizeGate -Paths @($parts[0].FullName) -LimitBytes 1000).Count -eq 1) '体积门禁能判定超限'
    Assert-True (@(Test-SeedVR2AssetSizeGate -Paths @((Join-Path $outBundle 'nope.zip.009'))).Count -eq 1) '体积门禁能判定缺文件'

    $sums = @(Get-Content -LiteralPath (Join-Path $outBundle 'SHA256SUMS.txt') -Encoding UTF8)
    Assert-True ($sums.Count -eq ($parts.Count + 3)) "SHA256SUMS 覆盖 $($sums.Count) 个文件"
    $badSums = 0
    foreach ($line in $sums) {
        $hash, $name = $line -split '\s{2,}', 2
        $actual = (Get-FileHash -LiteralPath (Join-Path $outBundle $name) -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $hash.ToLower()) {
            $badSums += 1
        }
    }
    Assert-True ($badSums -eq 0) 'SHA256SUMS 每条哈希均可复算通过'
    $leaked = @($coreC.payload_files | Where-Object { $_.path -match 'ffmpeg|ffprobe|watermark_key' })
    Assert-True ($leaked.Count -eq 0) "ffmpeg/密钥未进 core 载荷清单（core 文件数 $($coreC.payload_file_count)）"

    Write-Host ''
    Write-Host '=== 4. 解包往返 ===' -ForegroundColor Cyan
    & (Join-Path $repo 'scripts\unpack_portable_bundle.ps1') -BundleDir $outBundle -TargetDir $installed -SkipTorchInstall
    $dstFp8 = Join-Path $installed 'SeedVR2-Portable\model\seedvr2_ema_3b_fp8_e4m3fn.safetensors'
    $dstWheels = Join-Path $installed 'SeedVR2-Portable\torch_wheels\torch-2.11.0+cu128-cp312-cp312-win_amd64.whl'
    $dstPy = Join-Path $installed 'SeedVR2-Portable\WPy64-FAKE\python-3.12.10.amd64\Lib\site-packages\numpy\num.py'
    $dstBat = Join-Path $installed 'SeedVR2-Portable\start-portable.bat'
    $dstFfmpeg = Join-Path $installed 'SeedVR2-Portable\WPy64-FAKE\python-3.12.10.amd64\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win64-v7.1.exe'
    Assert-True ((Test-Path -LiteralPath $dstFp8) -and (Get-FileHash -LiteralPath $dstFp8 -Algorithm SHA256).Hash.ToLower() -eq $srcFp8Hash) "权重跨 $($fp8C.volume_count) 卷往返后哈希与源一致"
    Assert-True ((Test-Path -LiteralPath $dstWheels) -and (Get-FileHash -LiteralPath $dstWheels -Algorithm SHA256).Hash.ToLower() -eq $srcWheelsHash) "wheel 跨 $($torchC.volume_count) 卷往返后哈希一致"
    Assert-True (Test-Path -LiteralPath $dstPy) '便携运行时目录结构保留'
    Assert-True ((Test-Path -LiteralPath $dstBat) -and ((Get-Content -LiteralPath $dstBat -Raw) -match 'clean_launch\.py')) 'start-portable.bat 已生成且指向 clean_launch.py'
    Assert-True (-not (Test-Path -LiteralPath $dstFfmpeg)) '解包结果不含 ffmpeg（合规剥离生效）'
    Assert-True (Test-Path -LiteralPath (Join-Path $installed 'SeedVR2-Portable\.portable_ready')) '就绪标记已写入'
    Assert-True (@(Get-ChildItem -LiteralPath $installed -Filter '__merge__-*' -File -ErrorAction SilentlyContinue).Count -eq 0) '合并临时归档已清理'
    Assert-True (Test-Path -LiteralPath (Join-Path $installed 'SeedVR2-Portable\model\LICENSE')) 'LICENSE 随模型组件分发'

    Write-Host ''
    Write-Host '=== 5. 缺卷必须被拒 ===' -ForegroundColor Cyan
    $coreVol1 = Join-Path $outBundle ($coreC.archive + '.001')
    Rename-Item -LiteralPath $coreVol1 'hidden.tmp'
    $errText = ''
    try {
        & (Join-Path $repo 'scripts\unpack_portable_bundle.ps1') -BundleDir $outBundle -TargetDir (Join-Path $WorkDir 'installed2') -SkipTorchInstall -ErrorAction Stop
    } catch {
        $errText = $_.Exception.Message
    }
    Rename-Item -LiteralPath (Join-Path $outBundle 'hidden.tmp') (($coreC.archive) + '.001')
    Assert-True ($errText -match '缺少分卷') '缺卷时拒绝解压'

    Write-Host ''
    Write-Host '=== 6. 篡改必须被哈希拦下 ===' -ForegroundColor Cyan
    $p = $parts[0].FullName
    $orig = [System.IO.File]::ReadAllBytes($p)
    $copy = New-Object byte[] $orig.Length
    [Array]::Copy($orig, $copy, $orig.Length)
    $copy[0] = ($copy[0] -bxor 0xFF)
    [System.IO.File]::WriteAllBytes($p, $copy)
    $errText2 = ''
    try {
        & (Join-Path $repo 'scripts\unpack_portable_bundle.ps1') -BundleDir $outBundle -TargetDir (Join-Path $WorkDir 'installed3') -SkipTorchInstall -ErrorAction Stop
    } catch {
        $errText2 = $_.Exception.Message
    }
    [System.IO.File]::WriteAllBytes($p, $orig)
    Assert-True ($errText2 -match '校验失败') '分卷被改一字节即被拒绝'

    Write-Host ''
    Write-Host '=== 7. VerifyOnly 与部分组件解包 ===' -ForegroundColor Cyan
    & (Join-Path $repo 'scripts\unpack_portable_bundle.ps1') -BundleDir $outBundle -TargetDir (Join-Path $WorkDir 'installed4') -VerifyOnly | Out-Null
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $WorkDir 'installed4\SeedVR2-Portable'))) 'VerifyOnly 不落盘解压内容'
    $partial = Join-Path $WorkDir 'installed5'
    & (Join-Path $repo 'scripts\unpack_portable_bundle.ps1') -BundleDir $outBundle -TargetDir $partial -Component 'model-fp8' -SkipTorchInstall
    Assert-True (Test-Path -LiteralPath (Join-Path $partial 'SeedVR2-Portable\model\seedvr2_ema_3b_fp8_e4m3fn.safetensors')) '单独补解模型组件可用'
    $probe = Get-ChildItem -LiteralPath (Join-Path $fx 'WPy64-FAKE') -Recurse -File -Force
    Assert-True ($probe.Count -ge 3) '夹具未被构建过程破坏（硬链接删除不影响源）'
} catch {
    Write-Host "  ERROR $($_.Exception.Message)" -ForegroundColor Red
    $failures += '脚本异常终止'
} finally {
    if ($KeepArtifacts) {
        Write-Host "`n产物保留于 $WorkDir" -ForegroundColor DarkGray
    } elseif (Test-Path -LiteralPath $WorkDir) {
        Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ''
if ($failures.Count -gt 0) {
    Write-Host "失败 $($failures.Count) 项" -ForegroundColor Red
    foreach ($f in $failures) { Write-Host "  - $f" -ForegroundColor Red }
    exit 1
}
Write-Host '便携分卷包端到端自测：全部断言通过' -ForegroundColor Green
exit 0
