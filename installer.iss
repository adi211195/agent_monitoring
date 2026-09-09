; ============================================================
; Installer untuk MonitoringApp
; ============================================================

#define MyAppName "MonitoringApp"
#define MyAppVersion "1.0"
#define MyAppPublisher "Polindra Monitoring"
#define MyAppExeName "MonitoringApp.exe"
#define MyTaskStartup "MonitoringAppStartup"
#define MyTaskWatchdog "MonitoringAppWatchdog"

[Setup]
AppId={{A8F2E1B4-3C9D-4E7A-9B1F-6D2C8A0B5E31}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://polindra.ac.id
; Entry di Programs and Features
AppSupportURL=https://polindra.ac.id
AppUpdatesURL=https://polindra.ac.id
; Icon di Programs and Features
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
; Pastikan uninstall entry selalu dibuat
CreateUninstallRegKey=yes
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=installer_output
OutputBaseFilename=MonitoringAppSetup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "watchdog.vbs"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
; Task 1: auto-start saat login
Filename: "{sys}\schtasks.exe"; \
    Parameters: "/create /tn ""{#MyTaskStartup}"" /tr ""\\""{app}\{#MyAppExeName}\\"" --silent"" /sc onlogon /rl highest /f"; \
    Flags: runhidden

; Task 2: watchdog tiap 1 menit
Filename: "{sys}\schtasks.exe"; \
    Parameters: "/create /tn ""{#MyTaskWatchdog}"" /tr ""wscript.exe \""{app}\watchdog.vbs\"""" /sc minute /mo 1 /rl highest /f"; \
    Flags: runhidden

; Jalankan langsung setelah install
Filename: "{app}\{#MyAppExeName}"; Description: "Jalankan {#MyAppName} sekarang"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\schtasks.exe"; Parameters: "/delete /tn ""{#MyTaskStartup}"" /f"; Flags: runhidden; RunOnceId: "DelStartupTask"
Filename: "{sys}\schtasks.exe"; Parameters: "/delete /tn ""{#MyTaskWatchdog}"" /f"; Flags: runhidden; RunOnceId: "DelWatchdogTask"
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#MyAppExeName} /F"; Flags: runhidden; RunOnceId: "KillProcess"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\{#MyAppName}"
