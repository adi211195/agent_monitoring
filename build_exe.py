"""
Build MonitoringApp.exe

Jalankan HANYA di Windows (butuh pywin32 dan target platform Windows).
Pastikan dependency sudah terinstall dulu: pip install -r requirements.txt

Hasil exe ada di folder dist/MonitoringApp.exe
"""
import PyInstaller.__main__

PyInstaller.__main__.run([
    'main_app.py',
    '--name=MonitoringApp',
    '--onefile',
    '--windowed',
    '--clean',
    '--noconfirm',

    # === Hidden imports ===
    # Modul lokal (app_monitor, browsing_history, data_sender, dst) otomatis
    # terdeteksi PyInstaller lewat analisis import di main_app.py, tidak perlu
    # didaftarkan manual. Yang perlu didaftarkan manual di sini adalah
    # dependency pihak ketiga yang kadang tidak otomatis kedeteksi PyInstaller.
    '--hidden-import=psutil',
    '--hidden-import=requests',

    # pywin32
    '--hidden-import=win32api',
    '--hidden-import=win32con',
    '--hidden-import=win32file',
    '--hidden-import=win32gui',
    '--hidden-import=win32process',
    '--hidden-import=win32clipboard',
    '--hidden-import=win32timezone',   # sering kelupaan, bikin exe crash saat start kalau tidak ada

    # Screenshot & recording
    '--hidden-import=PIL',
    '--hidden-import=PIL.Image',
    '--hidden-import=PIL.ImageGrab',
    '--hidden-import=mss',
    '--hidden-import=cv2',
    '--hidden-import=numpy',

    # Keylogger & idle tracker
    '--hidden-import=pynput',
    '--hidden-import=pynput.keyboard',
    '--hidden-import=pynput.mouse',

    # File upload tracker
    '--hidden-import=watchdog',
    '--hidden-import=watchdog.observers',
    '--hidden-import=watchdog.events',

    # Stdlib yang kadang perlu didorong manual di beberapa versi PyInstaller
    '--hidden-import=sqlite3',
    '--hidden-import=webbrowser',
    '--hidden-import=win32event',
    '--hidden-import=win32security',
    '--hidden-import=winerror',

    # Modul lokal project yang tidak otomatis terdeteksi PyInstaller
    '--add-data=remote_control.py:.',
    '--add-data=app_paths.py:.',

    '--collect-all=win32ctypes',
    '--collect-all=numpy',
    '--collect-all=cv2',
])
