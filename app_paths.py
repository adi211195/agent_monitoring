"""
Helper terpusat untuk menentukan folder data aplikasi yang PASTI bisa ditulis.

Kenapa perlu ini:
Aplikasi sekarang di-install ke Program Files (lewat installer.iss), yang di
Windows adalah folder terproteksi -- user biasa (non-admin) tidak boleh
menulis file baru ke situ. Kalau modul-modul (screenshot, recording, offline
queue, dst) masih pakai path relatif seperti "screenshots/" atau
"offline_queue.json", Python akan mencoba menulis relatif ke folder tempat
exe berada (Program Files) dan gagal dengan PermissionError.

Solusinya: semua file yang dibuat/ditulis aplikasi saat runtime (bukan file
program itu sendiri) disimpan di %LOCALAPPDATA%\\MonitoringApp, folder milik
user yang sedang login dan selalu writable tanpa perlu admin.
"""
import os


def get_app_data_dir():
    """
    Mengembalikan path folder %LOCALAPPDATA%\\MonitoringApp, dan otomatis
    membuatnya kalau belum ada. Folder ini dipakai untuk menyimpan semua
    data yang dihasilkan aplikasi saat runtime: device_config.json,
    screenshots/, recordings/, offline_queue.json, file_versions.json,
    history_config.json, dst.
    """
    try:
        base_dir = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
        app_dir = os.path.join(base_dir, "MonitoringApp")
        os.makedirs(app_dir, exist_ok=True)
        return app_dir
    except Exception:
        # Fallback ke folder kerja saat ini kalau %LOCALAPPDATA% tidak bisa diakses
        return "."


def get_app_data_path(*parts):
    """Shortcut: get_app_data_path("screenshots") -> %LOCALAPPDATA%\\MonitoringApp\\screenshots"""
    return os.path.join(get_app_data_dir(), *parts)
