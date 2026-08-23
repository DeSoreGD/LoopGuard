#define AppName "LoopGuard"
#define AppVersion "0.1.0"
#define AppPublisher "LoopGuard"
#define AppExeName "LoopGuard.exe"
#define NativeHostExeName "LoopGuardNativeHost.exe"
#define NativeHostName "com.selfboss.native_host"
#define ChromeExtensionId "mcpljcfiphfoapmohiahhfjgcenhckkh"

[Setup]
AppId={{2D851A62-F95F-4F51-9A95-3560C893B811}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist\installer
OutputBaseFilename=LoopGuardSetup
SetupLogging=yes
SetupIconFile=..\..\assets\icons\loopguard.ico
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a Desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "nativehost"; Description: "Prepare Chrome connection for LoopGuard"; GroupDescription: "Browser integration:"

[Files]
Source: "..\..\dist\LoopGuard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Registry]
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\{#NativeHostName}"; ValueType: string; ValueName: ""; ValueData: "{userappdata}\LoopGuard\native_messaging\{#NativeHostName}.chrome.json"; Tasks: nativehost; Flags: uninsdeletekey

[Code]
function JsonEscapePath(Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '\', '\\', True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ManifestDir: String;
  ManifestPath: String;
  NativeHostPath: String;
  Manifest: String;
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('nativehost') then
  begin
    ManifestDir := ExpandConstant('{userappdata}\LoopGuard\native_messaging');
    ManifestPath := ManifestDir + '\{#NativeHostName}.chrome.json';
    NativeHostPath := ExpandConstant('{app}\{#NativeHostExeName}');
    ForceDirectories(ManifestDir);
    Manifest :=
      '{' + #13#10 +
      '  "name": "{#NativeHostName}",' + #13#10 +
      '  "description": "LoopGuard Native Messaging host",' + #13#10 +
      '  "path": "' + JsonEscapePath(NativeHostPath) + '",' + #13#10 +
      '  "type": "stdio",' + #13#10 +
      '  "allowed_origins": [' + #13#10 +
      '    "chrome-extension://{#ChromeExtensionId}/"' + #13#10 +
      '  ]' + #13#10 +
      '}' + #13#10;
    SaveStringToFile(ManifestPath, Manifest, False);
  end;
end;
