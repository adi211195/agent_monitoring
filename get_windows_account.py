"""
Fungsi untuk membaca info akun Windows/Microsoft dari device.
"""

import subprocess
import os


def get_windows_user_info() -> dict:
    """
    Ambil info akun Windows yang sedang login.
    Return dict: username, full_name, email, is_admin, is_microsoft_account
    """
    username = os.environ.get('USERNAME', '')
    info = {
        'username'            : username,
        'full_name'           : '',
        'email'               : '',
        'is_microsoft_account': False,
        'is_admin'            : False,
    }

    # 1. Full Name via wmic
    try:
        wmic_where = "name='" + username + "'"
        r = subprocess.run(
            ['wmic', 'useraccount', 'where', wmic_where, 'get', 'FullName', '/value'],
            capture_output=True, text=True, timeout=5
        )
        for line in r.stdout.splitlines():
            if line.startswith('FullName='):
                info['full_name'] = line.replace('FullName=', '').strip()
                break
    except Exception:
        pass

    # 2. Cek Administrator via PowerShell
    try:
        ps = (
            "Try { (Get-LocalGroupMember -Group 'Administrators' | "
            "Where-Object { $_.Name -like '*" + username + "*' }).Count -gt 0 }"
            " Catch { $false }"
        )
        r = subprocess.run(['powershell', '-Command', ps],
                           capture_output=True, text=True, timeout=5)
        info['is_admin'] = 'True' in r.stdout
    except Exception:
        pass

    # 3. Email Microsoft Account dari registry
    try:
        import winreg
        paths = [
            (winreg.HKEY_CURRENT_USER,
             r'SOFTWARE\Microsoft\IdentityCRL\UserExtendedProperties'),
        ]
        for hive, path in paths:
            try:
                key = winreg.OpenKey(hive, path)
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
                if info['email']:
                    break
            except Exception:
                continue
    except ImportError:
        pass

    # 4. Fallback email via PowerShell
    if not info['email']:
        try:
            ps = (
                "Get-ItemProperty "
                "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
                "\\Authentication\\LogonUI' "
                "-ErrorAction SilentlyContinue | "
                "Select-Object -ExpandProperty LastLoggedOnUser 2>$null"
            )
            r = subprocess.run(['powershell', '-Command', ps],
                               capture_output=True, text=True, timeout=5)
            val = r.stdout.strip()
            if '@' in val:
                info['email'] = val
                info['is_microsoft_account'] = True
        except Exception:
            pass

    # 5. Fallback full_name = username
    if not info['full_name']:
        info['full_name'] = username

    return info
