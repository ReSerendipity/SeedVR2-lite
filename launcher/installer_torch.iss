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
; 启用多卷拆分：torch wheel 单文件 2.7GB 超 GitHub 2GB 单文件限制，
; DiskSpanning 自动拆分到 setup.exe + 多个 .bin 分卷，每卷 < 2GB。
DiskSpanning=yes
DiskSliceSize=1900000000

[Files]
; Torch 家族 wheels，安装后注入 WinPython（分卷由 DiskSpanning 自动生成）
Source: "..\dist\torch_wheels\*.whl"; DestDir: "{tmp}\torch_wheels"; Flags: recursesubdirs

[Code]
// 在指定目录下递归查找 python.exe（WinPython 的 python 目录名不固定）
function FindPythonExe(Path: String): String;
var
  FindRec: TFindRec;
  p: String;
  sub: String;
begin
  Result := '';
  if FindFirst(Path + '\*', FindRec) then
  try
    repeat
      if FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then
      begin
        if FindRec.Name <> '.' then
        begin
          sub := FindPythonExe(Path + '\' + FindRec.Name);
          if sub <> '' then begin Result := sub; Exit; end;
        end;
      end
      else if CompareText(FindRec.Name, 'python.exe') = 0 then
      begin
        Result := Path + '\' + FindRec.Name;
        Exit;
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

// 安装完成后将 torch 注入到主程序的 WinPython 环境
procedure CurStepChanged(CurStep: TSetupStep);
var
  pythonExe: String;
  pipCmd: String;
  wpDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    wpDir := ExpandConstant('{localappdata}\SeedVR2-lite\WPy64-312101');
    if DirExists(wpDir) then
    begin
      pythonExe := FindPythonExe(wpDir);
      if pythonExe <> '' then
      begin
        pipCmd := '"' + pythonExe + '" -m pip install --no-index --find-links=' + ExpandConstant('{tmp}\torch_wheels') + ' torch torchvision torchaudio';
        Exec('cmd.exe', '/c ' + pipCmd, '', SW_HIDE, ewWaitUntilTerminated, Nil);
      end;
    end;
  end;
end;
