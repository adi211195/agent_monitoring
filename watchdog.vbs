' Watchdog silent untuk MonitoringApp.
' Dijalankan lewat wscript.exe (BUKAN cscript.exe) supaya TIDAK PERNAH
' memunculkan jendela apapun -- beda dengan file .bat yang selalu jalan
' di dalam cmd.exe dan bisa memunculkan jendela sekilas walau isinya cuma
' pengecekan biasa.
'
' Fungsinya: cek apakah MonitoringApp.exe sedang berjalan, kalau tidak,
' jalankan lagi (dengan flag --silent supaya window-nya tidak muncul).

Dim objWMIService, colProcesses, scriptDir, appPath, WshShell

Set objWMIService = GetObject("winmgmts:\\.\root\cimv2")
Set colProcesses = objWMIService.ExecQuery("Select * from Win32_Process Where Name = 'MonitoringApp.exe'")

If colProcesses.Count = 0 Then
    Set WshShell = CreateObject("WScript.Shell")
    scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
    appPath = scriptDir & "\MonitoringApp.exe"

    ' Parameter ke-2 (0) = jendela disembunyikan, parameter ke-3 (False) = tidak menunggu proses selesai
    WshShell.Run """" & appPath & """ --silent", 0, False
End If
