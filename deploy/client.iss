#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif

[Setup]
AppId=SUSTechCampusDashboard
AppName=南科大校园面板
AppVersion={#AppVersion}
AppPublisher=Rowan-memoedu
AppPublisherURL=https://github.com/Rowan-memoedu/SUSTechCampusDashboard
DefaultDirName={code:DefaultInstallPath}
DefaultGroupName=南科大校园面板
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
AllowNoIcons=yes
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
RestartIfNeededByRun=no
SetupLogging=yes
UninstallDisplayIcon={app}\campus-client.exe
OutputBaseFilename=SUSTechCampusDashboard-{#AppVersion}-windows-x86_64-setup

[Languages]
Name: english; MessagesFile: compiler:Default.isl

[Tasks]
Name: desktopicon; Description: "创建桌面快捷方式"; Flags: unchecked
Name: autostart; Description: "登录 Windows 后在后台启动（不弹出浏览器）"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\南科大校园面板"; Filename: "{app}\campus-client.exe"
Name: "{autodesktop}\南科大校园面板"; Filename: "{app}\campus-client.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\SUSTechCampusDashboard"; ValueType: string; ValueName: "InstallLocation"; ValueData: "{app}"
Root: HKCU; Subkey: "Software\Classes\sustech-campus"; ValueType: string; ValueData: "URL:SUSTech Campus Dashboard"
Root: HKCU; Subkey: "Software\Classes\sustech-campus"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\sustech-campus"; ValueType: string; ValueName: "InstallOwner"; ValueData: "{app}"
Root: HKCU; Subkey: "Software\Classes\sustech-campus"; ValueType: string; ValueName: "RegisteredCommand"; ValueData: """{app}\campus-client.exe"" --connect-uri ""%1"""
Root: HKCU; Subkey: "Software\Classes\sustech-campus\shell\open\command"; ValueType: string; ValueData: """{app}\campus-client.exe"" --connect-uri ""%1"""
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "SUSTechCampusDashboard"; ValueData: """{app}\campus-client.exe"" --no-browser"; Tasks: autostart
Root: HKCU; Subkey: "Software\SUSTechCampusDashboard"; ValueType: string; ValueName: "AutostartOwner"; ValueData: "{app}"; Tasks: autostart
Root: HKCU; Subkey: "Software\SUSTechCampusDashboard"; ValueType: string; ValueName: "AutostartCommand"; ValueData: """{app}\campus-client.exe"" --no-browser"; Tasks: autostart

[Run]
Filename: "{app}\campus-client.exe"; Flags: nowait runasoriginaluser; Check: LaunchAfterInstall

[Code]
function LaunchAfterInstall: Boolean;
begin
  { Used by isolated package verification; normal installs launch immediately. }
  Result := ExpandConstant('{param:NOLAUNCH|0}') <> '1';
end;

function DefaultInstallPath(Param: String): String;
begin
  if DirExists('D:\') then
    Result := ExpandConstant('D:\Applications\{username}\SUSTechCampusDashboard')
  else
    Result := ExpandConstant('{localappdata}\Programs\SUSTechCampusDashboard');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Owner, Command, Registered: String;
begin
  if CurUninstallStep = usPostUninstall then begin
    Log('Reviewing campus registry ownership in the 64-bit registry view');
    if RegQueryStringValue(HKCU64, 'Software\Classes\sustech-campus', 'InstallOwner', Owner) and
       (CompareText(Owner, ExpandConstant('{app}')) = 0) and
       RegQueryStringValue(HKCU64, 'Software\Classes\sustech-campus', 'RegisteredCommand', Registered) and
       RegQueryStringValue(HKCU64, 'Software\Classes\sustech-campus\shell\open\command', '', Command) and
       (Command = Registered) then
      RegDeleteKeyIncludingSubkeys(HKCU64, 'Software\Classes\sustech-campus');
    if RegQueryStringValue(HKCU64, 'Software\SUSTechCampusDashboard', 'AutostartOwner', Owner) and
       (CompareText(Owner, ExpandConstant('{app}')) = 0) and
       RegQueryStringValue(HKCU64, 'Software\SUSTechCampusDashboard', 'AutostartCommand', Registered) and
       RegQueryStringValue(HKCU64, 'Software\Microsoft\Windows\CurrentVersion\Run', 'SUSTechCampusDashboard', Command) and
       (Command = Registered) then begin
      RegDeleteValue(HKCU64, 'Software\Microsoft\Windows\CurrentVersion\Run', 'SUSTechCampusDashboard');
      RegDeleteValue(HKCU64, 'Software\SUSTechCampusDashboard', 'AutostartOwner');
      RegDeleteValue(HKCU64, 'Software\SUSTechCampusDashboard', 'AutostartCommand');
    end;
    if RegQueryStringValue(HKCU64, 'Software\SUSTechCampusDashboard', 'InstallLocation', Owner) and
       (CompareText(Owner, ExpandConstant('{app}')) = 0) then
      RegDeleteValue(HKCU64, 'Software\SUSTechCampusDashboard', 'InstallLocation');
  end;
end;
