# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('remote_control.py', '.'), ('app_paths.py', '.')]
binaries = []
hiddenimports = ['psutil', 'requests', 'win32api', 'win32con', 'win32file', 'win32gui', 'win32process', 'win32clipboard', 'win32timezone', 'PIL', 'PIL.Image', 'PIL.ImageGrab', 'mss', 'cv2', 'numpy', 'pynput', 'pynput.keyboard', 'pynput.mouse', 'watchdog', 'watchdog.observers', 'watchdog.events', 'sqlite3', 'webbrowser', 'win32event', 'win32security', 'winerror']
tmp_ret = collect_all('win32ctypes')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('numpy')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('cv2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MonitoringApp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
