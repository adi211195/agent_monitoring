import psutil
import time
import json
from datetime import datetime
from collections import defaultdict

class AppMonitor:
    def __init__(self, interval=5):
        self.interval = interval
        self.app_usage = defaultdict(lambda: {"duration": 0, "first_used": None, "last_used": None})
        self.current_app = None
        self.current_app_start = None
        self.target_apps = ["chrome.exe", "firefox.exe", "msedge.exe", "opera.exe", "brave.exe"]

    def get_active_window_info(self):
        try:
            import win32gui
            import win32process

            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)

            try:
                process = psutil.Process(pid)
                app_name = process.name()
                executable = process.exe()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                app_name = title if title else "Unknown"
                executable = "Unknown"

            return {
                "window_title": title,
                "app_name": app_name,
                "executable": executable,
                "pid": pid
            }
        except ImportError:
            return self._get_active_app_fallback()

    def _get_active_app_fallback(self):
        try:
            proc = psutil.Process()
            children = proc.children(recursive=True)
            for child in children:
                if child.status() == psutil.STATUS_RUNNING:
                    try:
                        return {
                            "window_title": child.name(),
                            "app_name": child.name(),
                            "executable": child.exe() or child.name(),
                            "pid": child.pid
                        }
                    except:
                        pass

            for p in psutil.process_iter(['name', 'exe', 'pid']):
                try:
                    if p.info['exe']:
                        return {
                            "window_title": p.info['name'],
                            "app_name": p.info['name'],
                            "executable": p.info['exe'],
                            "pid": p.info['pid']
                        }
                except:
                    pass
        except:
            pass

        return {"window_title": "Unknown", "app_name": "Unknown", "executable": "Unknown", "pid": 0}

    def update(self):
        info = self.get_active_window_info()
        current_time = time.time()

        if self.current_app and self.current_app != info["app_name"]:
            if self.current_app_start and self.current_app in self.app_usage:
                self.app_usage[self.current_app]["duration"] += current_time - self.current_app_start
                self.app_usage[self.current_app]["last_used"] = datetime.now().isoformat()

        if info["app_name"] != self.current_app:
            self.current_app = info["app_name"]
            self.current_app_start = current_time

            if self.app_usage[info["app_name"]]["first_used"] is None:
                self.app_usage[info["app_name"]]["first_used"] = datetime.now().isoformat()

        return info

    def is_recording_target(self, app_name=None):
        if app_name is None:
            app_name = self.current_app
        if not app_name:
            return False
        return app_name.lower() in self.target_apps

    def get_usage_data(self):
        current_time = time.time()
        if self.current_app and self.current_app_start:
            self.app_usage[self.current_app]["duration"] += current_time - self.current_app_start
            self.current_app_start = current_time

        usage_list = []
        for app_name, data in self.app_usage.items():
            usage_list.append({
                "app_name": app_name,
                "total_duration_seconds": round(data["duration"], 2),
                "first_used": data["first_used"],
                "last_used": data["last_used"],
                "timestamp": datetime.now().isoformat()
            })

        return usage_list

    def reset(self):
        self.app_usage.clear()
        self.current_app = None
        self.current_app_start = None

    def start_monitoring(self, callback=None):
        while True:
            try:
                info = self.update()
                if callback:
                    callback(info)
                time.sleep(self.interval)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Monitor error: {e}")
                time.sleep(self.interval)
