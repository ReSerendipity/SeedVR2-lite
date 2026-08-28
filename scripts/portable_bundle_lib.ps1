#Requires -Version 5.1
# scripts/portable_bundle_lib.ps1
# 便携分卷发布包的公共函数库（被 build_portable_bundle.ps1 / unpack_portable_bundle.ps1 dot-source）。
#
# 设计约束：
#   1. GitHub Release 单文件硬上限 2 GiB = 2147483648 字节；超过必须以「顺序字节切片」分卷。
#   2. 分卷命名统一为 `<archive>.001` / `<archive>.002` …，按序拼接即可还原原文件，
#      因此 .7z 与 .zip 两种容器共用同一套合并/校验逻辑。
#   3. 只依赖 Windows PowerShell 5.1 + 系统自带 tar.exe（bsdtar）；7-Zip 存在时优先用（压缩率更高）。
#      禁止使用 PowerShell 7 专属语法（?? 、三元、-Parallel、[IO.Path]::GetRelativePath）。

$script:GithubAssetLimitBytes = 2147483648
$script:DefaultMaxPartBytes = 1900MB
# 禁止随包分发的文件（NOTICE 第 4 条：ffmpeg/ffprobe 仅本地开发依赖，最终用户自行安装）。
# 必须用通配：imageio-ffmpeg 的 wheel 内自带 ffmpeg-win64-v7.1.exe，精确名匹配会漏。
$script:ForbiddenLeafPatterns = @('ffmpeg*.exe', 'ffprobe*.exe')
# 禁止进入任何分发物的本机私有文件（密钥 / 真实环境变量）。
$script:DeniedLeafNames = @('.watermark_key', '.env', 'config.yaml.bak')

function Get-SeedVR2GithubAssetLimit {
    <# 返回 GitHub Release 单文件字节上限。 #>
    return [long]$script:GithubAssetLimitBytes
}

function Get-SeedVR2DefaultMaxPart {
    <# 返回默认分卷大小（1900MB，对 2GiB 留有余量）。 #>
    return [long]$script:DefaultMaxPartBytes
}

function Find-SeedVR2SevenZip {
    <# 探测 7z.exe：PATH → 常见安装目录 → CI runner 固定路径。找不到返回 $null。 #>
    foreach ($name in @('7z', '7za')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            return $cmd.Source
        }
    }
    $candidates = @(
        "$env:ProgramFiles\7-Zip\7z.exe",
        "${env:ProgramFiles(x86)}\7-Zip\7z.exe",
        "$env:LOCALAPPDATA\Programs\7-Zip\7z.exe",
        'C:\Program Files\7-Zip\7z.exe'
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) {
            return $c
        }
    }
    return $null
}

function Find-SeedVR2SystemTar {
    <# 系统自带 bsdtar（Win10 1803+ 提供），可读 zip / 写 zip。 #>
    $cmd = Get-Command tar.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    if (Test-Path -LiteralPath "$env:SystemRoot\System32\tar.exe") {
        return "$env:SystemRoot\System32\tar.exe"
    }
    return $null
}

function Get-SeedVR2FileSha256 {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Get-SeedVR2FileSha256: 文件不存在 $Path"
    }
    $hash = Get-FileHash -LiteralPath $Path -Algorithm SHA256
    return $hash.Hash.ToLowerInvariant()
}

function Format-SeedVR2Size {
    param([Parameter(Mandatory = $true)][long]$Bytes)
    if ($Bytes -ge 1GB) {
        return ('{0:N2} GB' -f ($Bytes / 1GB))
    }
    if ($Bytes -ge 1MB) {
        return ('{0:N1} MB' -f ($Bytes / 1MB))
    }
    return "$Bytes B"
}

function Write-SeedVR2Json {
    <# 以 UTF-8 无 BOM 写 JSON（BOM 会让 Python/Node 侧 json.load 报错）。 #>
    param(
        [Parameter(Mandatory = $true)][object]$Object,
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$Depth = 12
    )
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $json = $Object | ConvertTo-Json -Depth $Depth
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json, $enc)
}

function Read-SeedVR2Json {
    param([Parameter(Mandatory = $true)][string]$Path)
    $text = [System.IO.File]::ReadAllText($Path)
    return $text | ConvertFrom-Json
}

function ConvertTo-SeedVR2Relative {
    <# 计算 $Full 相对 $Root 的路径（不依赖 .NET Core 的 GetRelativePath）。 #>
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Full
    )
    $rootFull = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\', '/')
    $fullResolved = [System.IO.Path]::GetFullPath($Full)
    if ($fullResolved.Length -le $rootFull.Length) {
        return ''
    }
    if ($fullResolved.Substring(0, $rootFull.Length).ToLowerInvariant() -ne $rootFull.ToLowerInvariant()) {
        throw "ConvertTo-SeedVR2Relative: $fullResolved 不在 $rootFull 之下"
    }
    return $fullResolved.Substring($rootFull.Length).TrimStart('\', '/')
}

function Test-SeedVR2PathExcluded {
    <# 排除判定：命中禁止随包分发的模式、本机私有文件名，或调用方传入的通配模式。 #>
    param(
        [Parameter(Mandatory = $true)][string]$Relative,
        [string[]]$ExcludePatterns = @()
    )
    $norm = $Relative.Replace('/', '\')
    $leaf = (Split-Path -Leaf $norm).ToLowerInvariant()
    foreach ($denied in $script:DeniedLeafNames) {
        if ($leaf -eq $denied -or $leaf -like "$denied*") {
            return $true
        }
    }
    foreach ($forbidden in $script:ForbiddenLeafPatterns) {
        if ($leaf -like $forbidden) {
            return $true
        }
    }
    foreach ($p in $ExcludePatterns) {
        if (-not $p) {
            continue
        }
        $pattern = $p.Replace('/', '\')
        if ($norm -like $pattern -or $leaf -like $pattern.ToLowerInvariant()) {
            return $true
        }
    }
    return $false
}

function Assert-SeedVR2NoForbiddenPayload {
    <#
        打包前后各跑一次：递归确认目录内没有任何 ffmpeg/ffprobe 可执行文件与私有密钥。
        这是 docs/COMPLIANCE_CHECKLIST.md「便携包分发检查项」第 1、2 条的真实实现，
        失败即抛异常中断构建，而不是留一个勾选项在文档里空转。
    #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Assert-SeedVR2NoForbiddenPayload: 目录不存在 $Path"
    }
    $hits = @()
    foreach ($pattern in ($script:ForbiddenLeafPatterns + $script:DeniedLeafNames)) {
        $found = Get-ChildItem -LiteralPath $Path -Recurse -Force -File -Filter $pattern -ErrorAction SilentlyContinue
        foreach ($f in $found) {
            $hits += $f.FullName
        }
    }
    if ($hits.Count -gt 0) {
        $list = ($hits | Select-Object -First 10) -join "`n  "
        throw "Assert-SeedVR2NoForbiddenPayload: 发现 $($hits.Count) 个禁止随包分发的文件（NOTICE 第 4 条 / 密钥外泄）:`n  $list"
    }
    return $true
}

function New-SeedVR2HardLink {
    <#
        同盘时优先建硬链接（staging 几乎零成本、零额外磁盘），失败回退复制。
        PowerShell 5.1 没有 New-Item -ItemType HardLink，走 cmd 的 mklink /H。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$SourceFile,
        [Parameter(Mandatory = $true)][string]$LinkPath
    )
    $srcDir = (Split-Path -Parent $SourceFile).Split('\')[0]
    $dstDir = (Split-Path -Parent $LinkPath).Split('\')[0]
    if ($srcDir -ne $dstDir) {
        Copy-Item -LiteralPath $SourceFile -Destination $LinkPath -Force
        return 'copy'
    }
    if (Test-Path -LiteralPath $LinkPath) {
        Remove-Item -LiteralPath $LinkPath -Force
    }
    $out = & cmd.exe /c "mklink /H `"$LinkPath`" `"$SourceFile`"" 2>$null
    if ((Test-Path -LiteralPath $LinkPath) -and ($LASTEXITCODE -eq 0)) {
        return 'hardlink'
    }
    Remove-Item -LiteralPath $LinkPath -Force -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $SourceFile -Destination $LinkPath -Force
    return 'copy'
}

function Copy-SeedVR2Tree {
    <#
        按相对路径把 $Source 下的文件搬进 $Dest，应用排除规则，能用硬链接就用硬链接。
        返回 @{ Files = n; Bytes = n; HardLinks = n; Copies = n; Skipped = @() }
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Dest,
        [string[]]$ExcludePatterns = @(),
        [switch]$FollowSourceRoot
    )
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Copy-SeedVR2Tree: 源不存在 $Source"
    }
    $stats = [ordered]@{ Files = 0; Bytes = 0; HardLinks = 0; Copies = 0; Skipped = @() }
    $root = (Resolve-Path -LiteralPath $Source).Path
    $src = Get-Item -LiteralPath $root -Force
    if (-not $src.PSIsContainer) {
        $rel = $src.Name
        if (Test-SeedVR2PathExcluded -Relative $rel -ExcludePatterns $ExcludePatterns) {
            return [pscustomobject]$stats
        }
        New-Item -ItemType Directory -Path $Dest -Force | Out-Null
        $mode = New-SeedVR2HardLink -SourceFile $src.FullName -LinkPath (Join-Path $Dest $rel)
        $stats.Files = 1
        $stats.Bytes = $src.Length
        if ($mode -eq 'hardlink') { $stats.HardLinks = 1 } else { $stats.Copies = 1 }
        return [pscustomobject]$stats
    }

    $stack = New-Object System.Collections.Stack
    $stack.Push($root)
    while ($stack.Count -gt 0) {
        $current = $stack.Pop()
        foreach ($file in (Get-ChildItem -LiteralPath $current -Force -File -ErrorAction SilentlyContinue)) {
            $relative = ConvertTo-SeedVR2Relative -Root $root -Full $file.FullName
            if (Test-SeedVR2PathExcluded -Relative $relative -ExcludePatterns $ExcludePatterns) {
                $stats.Skipped += $relative
                continue
            }
            $target = Join-Path $Dest $relative
            $targetDir = Split-Path -Parent $target
            if (-not (Test-Path -LiteralPath $targetDir)) {
                New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
            }
            $mode = New-SeedVR2HardLink -SourceFile $file.FullName -LinkPath $target
            $stats.Files += 1
            $stats.Bytes += [long]$file.Length
            if ($mode -eq 'hardlink') { $stats.HardLinks += 1 } else { $stats.Copies += 1 }
        }
        foreach ($sub in (Get-ChildItem -LiteralPath $current -Force -Directory -ErrorAction SilentlyContinue)) {
            $relativeDir = ConvertTo-SeedVR2Relative -Root $root -Full $sub.FullName
            $marker = "$relativeDir\*"
            $allChildren = @(Get-ChildItem -LiteralPath $sub.FullName -Force -Recurse -ErrorAction SilentlyContinue)
            $alive = @($allChildren | Where-Object {
                    -not (Test-SeedVR2PathExcluded -Relative (ConvertTo-SeedVR2Relative -Root $sub.FullName -Full $_.FullName) -ExcludePatterns $ExcludePatterns)
                })
            if ($alive.Count -eq 0) {
                if ($allChildren.Count -gt 0) {
                    $stats.Skipped += $marker
                }
                continue
            }
            $stack.Push($sub.FullName)
        }
    }
    return [pscustomobject]$stats
}

function New-SeedVR2Archive {
    <#
        把 $SourceDir 目录内容打成单文件归档。
        优先 7-Zip（-t7z，压缩率高）；无 7-Zip 时用系统 tar.exe 写 zip。
        返回 @{ Path = ...; Format = '7z'|'zip'; Bytes = ...; Sha256 = ...; Tool = ... }
        注意：归档内条目为 $SourceDir 的直接子项（不含 $SourceDir 本身）。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$SourceDir,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [ValidateSet('auto', '7z', 'zip')][string]$Format = 'auto',
        [ValidateRange(0, 9)][int]$Level = 4,
        [switch]$Quiet
    )
    if (-not (Test-Path -LiteralPath $SourceDir -PathType Container)) {
        throw "New-SeedVR2Archive: 源目录不存在 $SourceDir"
    }
    $out = [System.IO.Path]::GetFullPath($OutFile)
    $outDir = Split-Path -Parent $out
    if (-not (Test-Path -LiteralPath $outDir)) {
        New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    }
    if (Test-Path -LiteralPath $out) {
        Remove-Item -LiteralPath $out -Force
    }

    $sevenZip = $null
    if ($Format -ne 'zip') {
        $sevenZip = Find-SeedVR2SevenZip
    }
    if ($Format -eq '7z' -and -not $sevenZip) {
        throw "New-SeedVR2Archive: 指定 -Format 7z 但未找到 7-Zip"
    }

    if ($sevenZip) {
        $usedFormat = '7z'
        if ($out -notmatch '\.7z$') {
            $out = [System.IO.Path]::ChangeExtension($out, '7z')
        }
        $arguments = @('a', '-t7z', "-mx=$Level", '-mmt=on', '-bd', '-y', $out, '.')
        if (-not $Quiet) {
            $arguments = $arguments + @('-bsl1', '-bso0')
        }
        $prev = Get-Location
        try {
            Set-Location -LiteralPath $SourceDir
            & $sevenZip $arguments | Out-Null
        } finally {
            Set-Location -LiteralPath $prev
        }
        if ($LASTEXITCODE -ne 0) {
            throw "New-SeedVR2Archive: 7z 归档失败，退出码 $LASTEXITCODE"
        }
        $tool = $sevenZip
    } else {
        $tar = Find-SeedVR2SystemTar
        if (-not $tar) {
            throw "New-SeedVR2Archive: 既无 7-Zip 也无系统 tar.exe，无法生成归档"
        }
        $usedFormat = 'zip'
        if ($out -notmatch '\.zip$') {
            $out = [System.IO.Path]::ChangeExtension($out, 'zip')
        }
        $names = @(Get-ChildItem -LiteralPath $SourceDir -Force | ForEach-Object { $_.Name })
        if ($names.Count -eq 0) {
            throw "New-SeedVR2Archive: $SourceDir 为空，无内容可归档"
        }
        $base = @('-a', '-cf', $out)
        # bsdtar 的 zip 压缩级别走 libarchive 写选项；老版本不支持该选项时退回默认 deflate。
        $attempt = @($base + @('--options', "zip:compression-level=$Level") + $names)
        $prev = Get-Location
        try {
            Set-Location -LiteralPath $SourceDir
            & $tar $attempt | Out-Null
            $tarExit = $LASTEXITCODE
            if ($tarExit -ne 0 -or -not (Test-Path -LiteralPath $out)) {
                Remove-Item -LiteralPath $out -Force -ErrorAction SilentlyContinue
                & $tar ($base + $names) | Out-Null
                $tarExit = $LASTEXITCODE
            }
        } finally {
            Set-Location -LiteralPath $prev
        }
        if ($tarExit -ne 0 -or -not (Test-Path -LiteralPath $out)) {
            throw "New-SeedVR2Archive: tar 归档失败，退出码 $tarExit"
        }
        $tool = $tar
    }

    $item = Get-Item -LiteralPath $out
    return [pscustomobject]@{
        Path   = $out
        Format = $usedFormat
        Bytes  = [long]$item.Length
        Sha256 = Get-SeedVR2FileSha256 -Path $out
        Tool   = $tool
    }
}

function Split-SeedVR2FileIntoVolumes {
    <#
        顺序字节切片：$File → $File.001 / $File.002 …
        返回每个分卷的 @{ Index; Name; Path; Bytes; Sha256 }
    #>
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][long]$MaxBytes
    )
    if ($MaxBytes -lt 1MB) {
        throw "Split-SeedVR2FileIntoVolumes: MaxBytes 过小（$MaxBytes）"
    }
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) {
        throw "Split-SeedVR2FileIntoVolumes: 文件不存在 $File"
    }
    $src = Get-Item -LiteralPath $File
    $total = [long]$src.Length
    $partCount = [int][math]::Ceiling($total / [double]$MaxBytes)
    if ($partCount -lt 1) {
        $partCount = 1
    }
    $results = @()
    $in = [System.IO.File]::OpenRead($src.FullName)
    try {
        $buffer = New-Object byte[] 4194304
        for ($i = 1; $i -le $partCount; $i++) {
            $volPath = "$($src.FullName).{0:D3}" -f $i
            if (Test-Path -LiteralPath $volPath) {
                Remove-Item -LiteralPath $volPath -Force
            }
            $out = [System.IO.File]::Create($volPath)
            try {
                $remaining = $MaxBytes
                while ($remaining -gt 0) {
                    $toRead = [int][math]::Min([long]$buffer.Length, $remaining)
                    $read = $in.Read($buffer, 0, $toRead)
                    if ($read -le 0) {
                        break
                    }
                    $out.Write($buffer, 0, $read)
                    $remaining -= $read
                }
            } finally {
                $out.Dispose()
            }
            $vi = Get-Item -LiteralPath $volPath
            $results += [pscustomobject]@{
                Index  = $i
                Name   = $vi.Name
                Path   = $vi.FullName
                Bytes  = [long]$vi.Length
                Sha256 = Get-SeedVR2FileSha256 -Path $vi.FullName
            }
        }
    } finally {
        $in.Dispose()
    }
    $sum = 0
    foreach ($r in $results) { $sum += $r.Bytes }
    if ($sum -ne $total) {
        throw "Split-SeedVR2FileIntoVolumes: 分卷总字节 $sum != 原文件 $total"
    }
    return $results
}

function Join-SeedVR2Volumes {
    <# 按 Index 升序拼接分卷 → $OutFile，并校验总字节。 #>
    param(
        [Parameter(Mandatory = $true)][string[]]$VolumePaths,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [long]$ExpectedBytes = 0
    )
    $out = [System.IO.Path]::GetFullPath($OutFile)
    $outDir = Split-Path -Parent $out
    if (-not (Test-Path -LiteralPath $outDir)) {
        New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    }
    if (Test-Path -LiteralPath $out) {
        Remove-Item -LiteralPath $out -Force
    }
    $stream = [System.IO.File]::Create($out)
    try {
        foreach ($vol in $VolumePaths) {
            if (-not (Test-Path -LiteralPath $vol -PathType Leaf)) {
                throw "Join-SeedVR2Volumes: 缺少分卷 $vol"
            }
            $fs = [System.IO.File]::OpenRead($vol)
            try {
                $fs.CopyTo($stream)
            } finally {
                $fs.Dispose()
            }
        }
    } finally {
        $stream.Dispose()
    }
    $bytes = (Get-Item -LiteralPath $out).Length
    if ($ExpectedBytes -gt 0 -and $bytes -ne $ExpectedBytes) {
        throw "Join-SeedVR2Volumes: 合并后 $bytes 字节 != 期望 $ExpectedBytes"
    }
    return $out
}

function Test-SeedVR2AssetSizeGate {
    <#
        断言所有产物单文件不超过上限。返回违规项数组（空数组 = 通过）。
        这是整条发布链路唯一的体积门禁，必须真实执行而非写在文档里。
    #>
    param(
        [Parameter(Mandatory = $true)][string[]]$Paths,
        [long]$LimitBytes = 0
    )
    if ($LimitBytes -le 0) {
        $LimitBytes = [long]$script:GithubAssetLimitBytes
    }
    $violations = @()
    foreach ($p in $Paths) {
        if (-not (Test-Path -LiteralPath $p -PathType Leaf)) {
            $violations += [pscustomobject]@{ Path = $p; Bytes = -1; Limit = $LimitBytes; Reason = 'missing' }
            continue
        }
        $bytes = [long](Get-Item -LiteralPath $p).Length
        if ($bytes -gt $LimitBytes) {
            $violations += [pscustomobject]@{ Path = $p; Bytes = $bytes; Limit = $LimitBytes; Reason = 'oversize' }
        }
    }
    return @($violations)
}

function Write-SeedVR2Sha256Sums {
    <# 生成经典 `hash  filename` 清单（不含路径，便于 sha256sum -c / Get-FileHash 比对）。 #>
    param(
        [Parameter(Mandatory = $true)][string[]]$Paths,
        [Parameter(Mandatory = $true)][string]$OutFile
    )
    $lines = @()
    foreach ($p in $Paths) {
        $item = Get-Item -LiteralPath $p
        $lines += ('{0}  {1}' -f (Get-SeedVR2FileSha256 -Path $item.FullName), $item.Name)
    }
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($OutFile, [string[]]$lines, $enc)
    return $OutFile
}

function Remove-SeedVR2TreeFast {
    <# 长路径安全删除（torch 的 site-packages 常见超 260 字符）。 #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    try {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
        return
    } catch {
        & cmd.exe /c "rmdir /s /q `"$(Resolve-Path -LiteralPath $Path)`"" | Out-Null
        if (Test-Path -LiteralPath $Path) {
            throw "Remove-SeedVR2TreeFast: 无法删除 $Path"
        }
    }
}
