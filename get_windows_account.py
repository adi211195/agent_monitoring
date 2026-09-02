"""
Fungsi untuk membaca info akun Windows/Microsoft dari device.
Prioritas nama: Microsoft Account display name > Local account FullName > username
"""

import subprocess
import os


def get_windows_user_info() -> dict:
    username = os.environ.get('USERNAME', '')
    info = {
        'username'            : username,
        'full_name'           : '',
        'email'               : '',
        'is_microsoft_account': False,
        'is_admin'            : False,
    }

    no_window = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

    # 1. Coba Get-LocalUser FullName
    try:
        ps = "Get-LocalUser -Name '" + username + "' | Select-Object -ExpandProperty FullName 2>$null"
        r = subprocess.run(['powershell', '-Command', ps],
                           capture_output=True, text=True, timeout=5,
                           creationflags=no_window)
        val = r.stdout.strip()
        if val and val.lower() not in ('', 'none'):
            info['full_name'] = val
    except Exception:
        pass

    # 2. Coba Microsoft Account display name dari registry
    # (HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList)
    if not info['full_name']:
        try:
            ps = """
$sid = (Get-LocalUser -Name '""" + username + """' -ErrorAction SilentlyContinue).SID.Value
if ($sid) {
    $path = "HKLM:\\SOFTWARE\\Microsoft\\IdentityStore\\Cache\\$sid\\IdentityCache\\$sid"
    $name = (Get-ItemProperty $path -ErrorAction SilentlyContinue).UserName
    if ($name) { Write-Output $name }
}
"""
            r = subprocess.run(['powershell', '-Command', ps],
                               capture_output=True, text=True, timeout=8,
                               creationflags=no_window)
            val = r.stdout.strip()
            if val and '@' not in val:
                info['full_name'] = val
        except Exception:
            pass

    # 3. Coba WinRT UserInformation (Windows 10/11 Microsoft Account)
    if not info['full_name']:
        try:
            ps = """
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction Stop
    $t = [Type]::GetType('Windows.System.UserProfile.UserInformation, Windows.System.UserProfile, ContentType=WindowsRuntime')
    $name = $t::GetDisplayNameAsync().GetAwaiter().GetResult()
    if ($name) { Write-Output $name }
} catch {}
"""
            r = subprocess.run(['powershell', '-Command', ps],
                               capture_output=True, text=True, timeout=8,
                               creationflags=no_window)
            val = r.stdout.strip()
            if val:
                info['full_name'] = val
        except Exception:
            pass

    # 4. Cek Administrator
    try:
        ps = ("Try { (Get-LocalGroupMember -Group 'Administrators' | "
              "Where-Object { $_.Name -like '*" + username + "*' }).Count -gt 0 }"
              " Catch { $false }")
        r = subprocess.run(['powershell', '-Command', ps],
                           capture_output=True, text=True, timeout=5,
                           creationflags=no_window)
        info['is_admin'] = 'True' in r.stdout
    except Exception:
        pass

    # 5. Email dari registry
    try:
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r'SOFTWARE\Microsoft\IdentityCRL\UserExtendedProperties')
            i = 0
            while True:
                try:
                    n, d, _ = winreg.EnumValue(key, i)
                    if isinstance(d, str) and '@' in d and '.' in d:
                        info['email'] = d.strip()
                        info['is_microsoft_account'] = True
                        break
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except Exception:
            pass
    except ImportError:
        pass

    # 6. Fallback email via PowerShell
    if not info['email']:
        try:
            ps = ("Get-ItemProperty 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
                  "\\Authentication\\LogonUI' -ErrorAction SilentlyContinue | "
                  "Select-Object -ExpandProperty LastLoggedOnUser 2>$null")
            r = subprocess.run(['powershell', '-Command', ps],
                               capture_output=True, text=True, timeout=5,
                               creationflags=no_window)
            val = r.stdout.strip()
            if '@' in val:
                info['email'] = val
                info['is_microsoft_account'] = True
        except Exception:
            pass

    # 7. Jika email ada dan nama masih kosong, ambil dari email prefix
    if not info['full_name'] and info['email']:
        # Coba ambil nama dari bagian sebelum @
        prefix = info['email'].split('@')[0]
        # Kalau ada titik, capitalize tiap kata
        if '.' in prefix:
            info['full_name'] = ' '.join(w.capitalize() for w in prefix.split('.'))

    # 8. Fallback ke username
    if not info['full_name']:
        info['full_name'] = username

    return info
