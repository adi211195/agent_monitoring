import PyInstaller.__main__

PyInstaller.__main__.run([
    'main_app.py',
    '--name=MonitoringApp',
    '--onefile',
    '--windowed',
    '--icon=NONE',
    '--add-data=app_monitor.py;.',
    '--add-data=browsing_history.py;.',
    '--add-data=data_sender.py;.',
    '--hidden-import=psutil',
    '--hidden-import=requests',
    '--hidden-import=win32gui',
    '--hidden-import=win32process',
    '--hidden-import=json',
    '--hidden-import=sqlite3',
    '--hidden-import=collections',
    '--hidden-import=urllib',
    '--collect-all=win32ctypes',
    '--noconfirm'
])
