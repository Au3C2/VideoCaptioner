; Inno Setup script for the VideoCaptioner Windows installer.
;
; Build with:  ISCC.exe packaging\installer.iss /DMyAppVersion=<version>
; Expected layout (paths relative to this file):
;   ..\dist\VideoCaptioner\   - PyInstaller COLLECT output (VideoCaptioner.exe + VideoCaptioner-cli.exe + _internal)
;   ..\dist\VideoCaptioner\resource\assets\logo.ico - icon (copied into the bundle by PyInstaller)
;   ..\LICENSE
; Output: ..\artifacts\VideoCaptioner-<version>-windows-setup.exe

#define MyAppName "VideoCaptioner"
#define MyAppPublisher "VideoCaptioner contributors"
#define MyAppURL "https://github.com/Au3C2/VideoCaptioner"
#define MyAppExeName "VideoCaptioner.exe"
#define MyAppCliName "VideoCaptioner-cli.exe"

#ifndef MyAppVersion
#define MyAppVersion "0.0.0"
#endif

[Setup]
; Stable AppId: changing it breaks upgrade/uninstall detection.
; The single closing brace is deliberate: with AppId={{...} the literal GUID
; keeps one brace pair, so the uninstall registry key is {GUID}_is1
; (which scripts/build_desktop.py find_iscc() looks up).
AppId={{8A5D2E9C-4B7F-4E63-9C1A-52D64B7F3A10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\artifacts
OutputBaseFilename=VideoCaptioner-{#MyAppVersion}-windows-setup
; Per-user install: {autopf} resolves to %LOCALAPPDATA%\Programs, no admin needed
PrivilegesRequired=lowest
Compression=lzma2/max
LZMAUseSeparateProcess=yes
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\resource\assets\logo.ico

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "addtopath"; Description: "Add VideoCaptioner CLI to PATH"; GroupDescription: "CLI:"

[Files]
Source: "..\dist\VideoCaptioner\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} CLI"; Filename: "{cmd}"; Parameters: "/k ""{app}\{#MyAppCliName}"" --help"; IconFilename: "{app}\{#MyAppCliName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
const
  EnvironmentKey = 'Environment';
  WM_SETTINGCHANGE = $001A;
  SMTO_ABORTIFHUNG = $0002;

function SendMessageTimeout(hWnd: LongInt; Msg: LongInt; wParam: LongInt; lParam: LongInt; fuFlags: LongInt; uTimeout: LongInt; var lpdwResult: LongInt): LongInt; external 'SendMessageTimeoutW@user32.dll stdcall';

procedure RefreshEnvironment();
var
  Dummy: LongInt;
begin
  { Notify running processes (e.g. Explorer) that the environment changed. }
  SendMessageTimeout(HWND_BROADCAST, WM_SETTINGCHANGE, 0, 0, SMTO_ABORTIFHUNG, 5000, Dummy);
end;

procedure EnvAddPath(AddPath: string);
var
  Paths: string;
begin
  { Retrieve current user PATH (HKEY_CURRENT_USER\Environment). }
  if RegQueryStringValue(HKEY_CURRENT_USER, EnvironmentKey, 'Path', Paths) then
  begin
    { Skip if already present. }
    if Pos(';' + Uppercase(Trim(AddPath)) + ';', ';' + Uppercase(Paths) + ';') > 0 then
      exit;
    if Paths <> '' then
      Paths := Paths + ';';
    Paths := Paths + Trim(AddPath);
  end
  else
    Paths := Trim(AddPath);
  RegWriteStringValue(HKEY_CURRENT_USER, EnvironmentKey, 'Path', Paths);
end;

procedure EnvRemovePath(RemovePath: string);
var
  Paths, NewPaths, Entry: string;
  Sep: Integer;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, EnvironmentKey, 'Path', Paths) then
    exit;
  NewPaths := '';
  while Length(Paths) > 0 do
  begin
    Sep := Pos(';', Paths);
    if Sep = 0 then
    begin
      Entry := Paths;
      Paths := '';
    end
    else
    begin
      Entry := Copy(Paths, 1, Sep - 1);
      Paths := Copy(Paths, Sep + 1, MaxInt);
    end;
    Entry := Trim(Entry);
    if (Entry <> '') and (Uppercase(Entry) <> Uppercase(Trim(RemovePath))) then
    begin
      if NewPaths <> '' then
        NewPaths := NewPaths + ';';
      NewPaths := NewPaths + Entry;
    end;
  end;
  RegWriteStringValue(HKEY_CURRENT_USER, EnvironmentKey, 'Path', NewPaths);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('addtopath') then
      EnvAddPath(ExpandConstant('{app}'));
    RefreshEnvironment();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    EnvRemovePath(ExpandConstant('{app}'));
    RefreshEnvironment();
  end;
end;
