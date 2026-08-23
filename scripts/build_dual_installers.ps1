# scripts/build_dual_installers.ps1 — 生成两个必装安装包
# 用法：.\scripts\build_dual_installers.ps1 -Version "1.4.3"

param(
    [string]$Version = "1.4.3",
    [string]$BuildDir = "$PSScriptRoot\..\dist"
)

Write-Host "🚀 开始构建双安装包 v$Version ..."

$root = "$PSScriptRoot\.."
$fullIscc = "$root\launcher\installer_full.iss"
$torchIscc = "$root\launcher\installer_torch.iss"

# 1. Full 包 (WinPython + app 代码 + 启动器)
Write-Host "📦 构建 SeedVR2-Setup-Full-v$Version.exe (~350MB) ..."
if (Test-Path $fullIscc) {
    & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVer=$Version $fullIscc
    Write-Host "✅ Full 包构建完成"
} else {
    Write-Host "❌ 未找到 installer_full.iss"
}

# 2. Torch 包 (CUDA 依赖，单独因为 >2GB)
Write-Host "📦 构建 SeedVR2-Torch-Installer-v$Version.exe (~2.0GB) ..."
if (Test-Path $torchIscc) {
    & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVer=$Version $torchIscc
    Write-Host "✅ Torch 包构建完成"
} else {
    Write-Host "❌ 未找到 installer_torch.iss"
}

Write-Host ""
Write-Host "=========================================="
Write-Host "✅ 双安装包构建完成！位于 $BuildDir"
Write-Host "请上传到 GitHub Release:"
Write-Host "  1. SeedVR2-Setup-Full-v$Version.exe   (~350MB)"
Write-Host "  2. SeedVR2-Torch-Installer-v$Version.exe (~2.0GB)"
Write-Host "=========================================="
Write-Host "提示：用户需安装这两个包才能运行程序。"
Write-Host "      模型文件可手动放入 model/ 或后续自动下载。"
