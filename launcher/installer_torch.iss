; launcher/installer_torch.iss — SeedVR2 Torch GPU 依赖包
; 包含：torch + torchvision + torchaudio (CUDA 12.8)
; 大小：~2.0GB（单独打包因为超过 GitHub 单文件限制）
; 编译：IScc.exe /DAppVer=1.4.3 launcher/installer_torch.iss

#ifndef AppVer
  #define AppVer "1.0.0"
#endif

#define AppName "SeedVR2 Torch"
#define AppPublisher "ReSerendipity"

[Setup]
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\SeedVR2-lite\torch
UninstallDisplayIcon={sys}\python.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=SeedVR2-Torch-Installer-v{#AppVer}
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
InfoBeforeFile=installer_torch_info.txt

[Files]
; Torch 家族（通过 pip 预装到 portable Python 或解压即用的独立环境）
; 方案 A：预装到 WinPython（推荐，但需要复制整个 WinPython）
; Source: "..\WPy64-312101\*"; DestDir: "{app}\WPy64-312101"; Flags: recursesubdirs
; 方案 B：仅含 torch wheel 文件，安装时注入到已存在的 WinPython
Source: "..\dist\torch_wheels\*"; DestDir: "{tmp}\torch_wheels"; Flags: deletafterinstall

[Code]
// 安装完成后将 torch 注入到主程序的 WinPython 环境
procedure CurStepChanged(CurStep: TSetupStep);
var
  pythonExe: String;
  pipCmd: String;
begin
  if CurStep = ssPostInstall then
  begin
    // 检测主程序路径
    pythonExe := ExpandConstant('{localappdata}\SeedVR2-lite\WPy64-312101\python\python.exe');
    if FileExists(pythonExe) then
    begin
      pipCmd := '"' + pythonExe + '" -m pip install --no-index --find-links=' + ExpandConstant('{tmp}\torch_wheels') + ' torch torchvision torchaudio';
      Exec('cmd.exe', '/c ' + pipCmd, '', SW_HIDE, ewWaitUntilTerminated, Nil);
    end;
  end;
end;
