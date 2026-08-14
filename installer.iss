; ============================================================
; Installer untuk MonitoringApp
; Dibuat dengan Inno Setup (https://jrsoftware.org/isinfo.php)
;
; Fitur:
; - Install ke Program Files (butuh admin sekali saat install)
; - Auto-start setiap kali user login (Task Scheduler, jalan dengan flag
;   --silent supaya window tidak muncul)
; - Watchdog: kalau proses di-"End Task" manual, otomatis hidup lagi
;   dalam waktu maksimal 1 menit -- dijalankan lewat wscript.exe (VBS)
;   supaya TIDAK PERNAH memunculkan jendela CMD sekilas
; - Buka aplikasi manual (dobel klik shortcut, tanpa --silent) TETAP
;   menampilkan window seperti biasa, meski device sudah pernah register
; - Tombol X di window aplikasi cuma menyembunyikan window, monitoring
;   tetap jalan di background (lihat perubahan di main_app.py: on_close)
; ============================================================

#define MyAppName "MonitoringApp"
#define MyAppVersion "1.0"
#define MyAppExeName "MonitoringApp.exe"
#define MyTaskStartup "MonitoringAppStartup"
#define MyTaskWatchdog "MonitoringAppWatchdog"

[Setup]
AppId={{A8F2E1B4-3C9D-4E7A-9B1F-6D2C8A0B5E31}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Install ke Program Files, butuh hak admin (akan muncul prompt UAC sekali saat install)
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
; Task 1: jalankan aplikasi otomatis setiap kali user login, dengan flag
; --silent supaya window TIDAK muncul (beda dengan buka manual dari Start
; Menu, yang tetap menampilkan window seperti biasa).
Filename: "{sys}\schtasks.exe"; \
    Parameters: "/create /tn ""{#MyTaskStartup}"" /tr ""\""{app}\{#MyAppExeName}\"" --silent"" /sc onlogon /rl highest /f"; \
    Flags: runhidden

; Task 2: watchdog, cek tiap 1 menit apakah aplikasi masih hidup lewat
; watchdog.vbs (dijalankan via wscript.exe supaya benar-benar tanpa jendela
; sama sekali -- BUKAN lewat .bat, karena .bat selalu jalan di dalam cmd.exe
; dan bisa memunculkan jendela sekilas walau isinya cuma pengecekan biasa).
Filename: "{sys}\schtasks.exe"; \
    Parameters: "/create /tn ""{#MyTaskWatchdog}"" /tr ""wscript.exe \""{app}\watchdog.vbs\"""" /sc minute /mo 1 /rl highest /f"; \
    Flags: runhidden

; Langsung jalankan aplikasi setelah instalasi selesai (tanpa --silent,
; supaya user langsung lihat dialog registrasi)
Filename: "{app}\{#MyAppExeName}"; Description: "Jalankan {#MyAppName} sekarang"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Bersihkan scheduled tasks saat uninstall
Filename: "{sys}\schtasks.exe"; Parameters: "/delete /tn ""{#MyTaskStartup}"" /f"; Flags: runhidden; RunOnceId: "DelStartupTask"
Filename: "{sys}\schtasks.exe"; Parameters: "/delete /tn ""{#MyTaskWatchdog}"" /f"; Flags: runhidden; RunOnceId: "DelWatchdogTask"
; Matikan proses yang masih jalan sebelum file dihapus
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#MyAppExeName} /F"; Flags: runhidden; RunOnceId: "KillProcess"

[UninstallDelete]
; Hapus config device milik user yang menjalankan uninstaller
Type: filesandordirs; Name: "{localappdata}\{#MyAppName}"
