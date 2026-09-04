"""
Fungsi untuk mengambil daftar aplikasi terinstall dari Windows.
Mengambil dari:
1. Windows Registry (Win32 apps - HKLM/HKCU Uninstall)
2. AppxPackage (Windows Store apps) via PowerShell
"""

import subprocess
import json


def get_installed_apps() -> list:
    """
    Return list of dicts: {name, version, app_id, status, last_updated}
    """
    apps = []
    seen_names = set()
    no_window  = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0

    # ── 1. Win32 apps dari Registry ─────────────────────────────
    try:
        ps_reg = """
$paths = @(
    'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',
    'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',
    'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*'
)
$result = @()
foreach ($path in $paths) {
    try {
        Get-ItemProperty $path -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -and $_.DisplayName.Trim() -ne '' } |
        ForEach-Object {
            $result += [PSCustomObject]@{
                name         = $_.DisplayName
                version      = $_.DisplayVersion
                app_id       = $_.PSChildName
                last_updated = $_.InstallDate
                install_path = $_.InstallLocation
                publisher    = $_.Publisher
                source       = 'registry'
            }
        }
    } catch {}
}
$result | ConvertTo-Json -Depth 2
"""
        r = subprocess.run(
            ['powershell', '-Command', ps_reg],
            capture_output=True, text=True, timeout=30,
            creationflags=no_window
        )
        if r.stdout.strip():
            data = json.loads(r.stdout.strip())
            if isinstance(data, dict):
                data = [data]
            for item in data:
                name = (item.get('name') or '').strip()
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                # Parse tanggal InstallDate (format YYYYMMDD)
                raw_date = item.get('last_updated') or ''
                parsed_date = None
                if raw_date and len(str(raw_date)) == 8 and str(raw_date).isdigit():
                    d = str(raw_date)
                    parsed_date = f"{d[:4]}-{d[4:6]}-{d[6:8]} 00:00:00"

                apps.append({
                    'name'        : name,
                    'version'     : (item.get('version') or '').strip() or None,
                    'app_id'      : (item.get('app_id') or '').strip() or None,
                    'status'      : 'Installed',
                    'last_updated': parsed_date,
                    'install_path': (item.get('install_path') or '').strip() or None,
                    'publisher'   : (item.get('publisher') or '').strip() or None,
                })
    except Exception:
        pass

    # ── 2. Windows Store / AppX apps ────────────────────────────
    try:
        ps_appx = """
Get-AppxPackage | Select-Object Name,Version,PackageFullName,InstallLocation,Publisher |
ConvertTo-Json -Depth 1
"""
        r2 = subprocess.run(
            ['powershell', '-Command', ps_appx],
            capture_output=True, text=True, timeout=30,
            creationflags=no_window
        )
        if r2.stdout.strip():
            data2 = json.loads(r2.stdout.strip())
            if isinstance(data2, dict):
                data2 = [data2]
            for item in data2:
                name = (item.get('Name') or '').strip()
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                apps.append({
                    'name'        : name,
                    'version'     : (item.get('Version') or '').strip() or None,
                    'app_id'      : (item.get('PackageFullName') or '').strip() or None,
                    'status'      : 'Installed',
                    'last_updated': None,
                    'install_path': (item.get('InstallLocation') or '').strip() or None,
                    'publisher'   : (item.get('Publisher') or '').strip() or None,
                })
    except Exception:
        pass

    return sorted(apps, key=lambda x: x['name'].lower())
