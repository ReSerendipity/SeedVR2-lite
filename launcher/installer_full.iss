; launcher/installer_full.iss — SeedVR2 完整程序包（不含 Torch）
; 包含：WinPython (预装小依赖) + app/代码 + 启动器引导页
; 大小：~350MB
; 编译：ISCC.exe /DAppVer=1.4.3 launcher/installer_full.iss

#ifndef AppVer
  #define AppVer "1.0.0"
#endif

#define AppName "SeedVR2"
#define AppPublisher "ReSerendipity"
#define AppExeName "SeedVR2.exe"

[Setup]
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\SeedVR2-lite
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=SeedVR2-Setup-Full-v{#AppVer}
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
InfoBeforeFile=installer_full_info.txt

[Run]
Filename: "{app}\{#AppExeName}"; Description: "首次启动将检测组件完整性"; Flags: nowait postinstall skipifsilent

[Icons]
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: checkedonce

[Files]
; WinPython（已预装小依赖：FastAPI, Jinja2, diffusers, transformers 等）
Source: "..\WPy64-312101\*"; DestDir: "{app}\WPy64-312101"; Flags: recursesubdirs
; 应用本体
Source: "..\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs
Source: "..\common\*"; DestDir: "{app}\common"; Flags: recursesubdirs
Source: "..\model_lib\*"; DestDir: "{app}\model_lib"; Flags: recursesubdirs
Source: "..\configs_3b\*"; DestDir: "{app}\configs_3b"; Flags: recursesubdirs
Source: "..\configs_7b\*"; DestDir: "{app}\configs_7b"; Flags: recursesubdirs
Source: "..\config.yaml"; DestDir: "{app}"
Source: "..\.env.example"; DestDir: "{app}"
; 启动器
Source: "..\dist\SeedVR2.exe"; DestDir: "{app}"
; 引导页静态资源
Source: "..\launcher\static\*"; DestDir: "{app}\launcher\static"; Flags: recursesubdirs
; 冒烟测试图（不存在则跳过，避免编译失败）
Source: "..\demo\assets\inputs\input-1.jpg"; DestDir: "{app}\launcher\test-assets"; DestName: "test-input.jpg"; Flags: skipifsourcedoesntexist
Source: "..\demo\assets\inputs\input-2.jpg"; DestDir: "{app}\launcher\test-assets"; DestName: "test-input-2.jpg"; Flags: skipifsourcedoesntexist
Source: "..\demo\assets\inputs\input-3.jpg"; DestDir: "{app}\launcher\test-assets"; DestName: "test-input-3.jpg"; Flags: skipifsourcedoesntexist

[Dirs]
Name: "{app}\model"
Name: "{app}\data"
Name: "{app}\logs"
Name: "{app}\launcher"
