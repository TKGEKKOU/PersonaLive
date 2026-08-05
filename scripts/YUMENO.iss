; YUMENO 安装脚本（Inno Setup 7）
; 用法：ISCC.exe scripts\YUMENO.iss（先运行 build_windows.ps1 生成 dist\YUMENO）

#define MyAppName "YUMENO"
#define MyAppVersion "0.1.1"
#define MyAppExeName "YUMENO.exe"
#define MyAppId "6B4BDE24-1C3E-4E0A-9A3D-0F0C8C7E5A21"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=YUMENO
DefaultDirName={localappdata}\Programs\YUMENO
DefaultGroupName=YUMENO
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName=YUMENO
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=YUMENO-Setup-{#MyAppVersion}
SetupIconFile=..\resources\app.ico
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName=YUMENO

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
; 程序主体（不含 data，live2d 素材单独处理）
Source: "..\dist\YUMENO\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "data"
; live2d 素材：安装时写入，卸载时保留（与运行时数据同目录）
Source: "..\dist\YUMENO\data\*"; DestDir: "{app}\data"; Flags: ignoreversion recursesubdirs createallsubdirs uninsneveruninstall

[Icons]
Name: "{group}\YUMENO"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 YUMENO"; Filename: "{uninstallexe}"
Name: "{autodesktop}\YUMENO"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 YUMENO"; Flags: nowait postinstall skipifsilent
