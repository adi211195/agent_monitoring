import time
import threading
import os
from datetime import datetime
import win32gui
import win32process
import win32api
import win32con
import psutil
import win32clipboard


class FileUploadTracker:
    def __init__(self, target_apps=None):
        self.target_apps = target_apps or [
            "chrome.exe",
            "firefox.exe",
            "msedge.exe",
            "opera.exe",
            "brave.exe",
            "explorer.exe"
        ]

        self.is_tracking = False
        self.callback = None
        self.upload_activities = []

        # anti duplicate
        self.last_events = {}  # key: timestamp

    # =========================
    # START
    # =========================
    def start_tracking(self, callback=None):
        if self.is_tracking:
            return
            
        self.is_tracking = True
        self.callback = callback

        t = threading.Thread(target=self._main_loop, daemon=True)
        t.start()

    def stop_tracking(self):
        self.is_tracking = False

    # =========================
    # MAIN LOOP
    # =========================
    def _main_loop(self):
        while self.is_tracking:
            try:
                self._detect_file_dialog()
                self._detect_clipboard_files()
            except Exception as e:
                print("[ERROR MAIN]", e)

            time.sleep(0.5)

    # =========================
    # FILE DIALOG DETECTION
    # =========================
    def _detect_file_dialog(self):
        hwnd = win32gui.GetForegroundWindow()
        if hwnd == 0:
            return

        title = win32gui.GetWindowText(hwnd)
        class_name = win32gui.GetClassName(hwnd)

        # Cek apakah jendela yang aktif adalah dialog standard Windows
        is_dialog = class_name == "#32770" or "open" in title.lower() or "upload" in title.lower()

        if is_dialog:
            self._handle_dialog(hwnd, title)

    def _handle_dialog(self, hwnd, title):
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid)
            process_name = process.name().lower()

            if process_name not in self.target_apps:
                return

            file_name = self._get_file_from_edit_control(hwnd)
            if not file_name or "." not in file_name:
                return

            # Coba cari path folder (Toolbar Window32 atau Breadcrumb)
            folder_path = self._get_folder_from_dialog(hwnd)
            
            full_path = file_name
            if folder_path:
                full_path = os.path.join(folder_path, file_name)

            self._add_event(process_name, full_path, "file_dialog_upload")

        except:
            pass

    # =========================
    # CLIPBOARD FILE DETECTION (FOR DRAG & DROP)
    # =========================
    def _detect_clipboard_files(self):
        """
        Mendeteksi file yang di-drop ke browser. 
        Pada banyak kasus drag & drop, Windows memasukkan info file ke clipboard CF_HDROP.
        """
        try:
            hwnd = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid)
            process_name = process.name().lower()

            if process_name not in self.target_apps:
                return

            files = self._get_files_from_clipboard()
            if files:
                for file_path in files:
                    self._add_event(process_name, file_path, "drag_drop_upload")
        except:
            pass

    # =========================
    # UTILS
    # =========================
    def _get_file_from_edit_control(self, hwnd):
        """Mencari nama file di Edit box dialog"""
        try:
            edit_hwnd = win32gui.FindWindowEx(hwnd, 0, "ComboBoxEx32", None)
            if edit_hwnd:
                edit_hwnd = win32gui.FindWindowEx(edit_hwnd, 0, "ComboBox", None)
                if edit_hwnd:
                    edit_hwnd = win32gui.FindWindowEx(edit_hwnd, 0, "Edit", None)
            
            if not edit_hwnd:
                edit_hwnd = win32gui.FindWindowEx(hwnd, 0, "Edit", None)

            if edit_hwnd:
                buf_size = 1024
                buffer = win32gui.PyMakeBuffer(buf_size)
                length = win32gui.SendMessage(edit_hwnd, win32con.WM_GETTEXT, buf_size, buffer)
                return buffer[:length*2].tobytes().decode('utf-16').strip('\x00')
        except:
            pass
        return None

    def _get_folder_from_dialog(self, hwnd):
        """Mencari path folder di Toolbar dialog"""
        try:
            # Toolbar Window32 biasanya menyimpan path di dialog standard
            toolbar = win32gui.FindWindowEx(hwnd, 0, "WorkerW", None)
            if toolbar:
                toolbar = win32gui.FindWindowEx(toolbar, 0, "ReBarWindow32", None)
                if toolbar:
                    toolbar = win32gui.FindWindowEx(toolbar, 0, "Address Band Root", None)
                    if toolbar:
                        # Address Band Root -> ToolbarWindow32
                        # Ini cara lama, Windows modern lebih kompleks. 
                        # Fallback ke EnumChild
                        pass

            paths = []
            def enum_proc(h, l):
                txt = win32gui.GetWindowText(h)
                if ":" in txt and "\\" in txt and len(txt) > 3:
                    if os.path.isdir(txt.split(":")[-1].strip() if "Address" in txt else txt):
                        paths.append(txt)
                return True
            
            win32gui.EnumChildWindows(hwnd, enum_proc, None)
            for p in paths:
                if "Address:" in p:
                    return p.split("Address:")[-1].strip()
                if os.path.isdir(p):
                    return p
        except:
            pass
        return None

    def _get_files_from_clipboard(self):
        """Mendapatkan list file dari clipboard (CF_HDROP)"""
        try:
            if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                return []
            
            win32clipboard.OpenClipboard()
            files = win32clipboard.GetClipboardData(win32con.CF_HDROP)
            win32clipboard.CloseClipboard()
            return list(files)
        except:
            try: win32clipboard.CloseClipboard()
            except: pass
            return []

    def _add_event(self, app_name, file_path, event_type):
        if not file_path:
            return

        # Anti duplicate: jangan catat file yang sama untuk app yang sama dalam 2 detik
        now = time.time()
        key = f"{app_name}_{str(file_path)}_{event_type}"
        
        if key in self.last_events:
            if now - self.last_events[key] < 2:
                return

        self.last_events[key] = now
        
        # Bersihkan cache lama
        if len(self.last_events) > 100:
            self.last_events = {k: v for k, v in self.last_events.items() if now - v < 10}

        data = {
            "timestamp": datetime.now().isoformat(),
            "app_name": app_name,
            "file_path": file_path,
            "event": event_type
        }
        
        self.upload_activities.append(data)
        if self.callback:
            self.callback(data)
        print(f"[UPLOAD DETECTED] {data}")

    def get_new_activities(self):
        data = self.upload_activities.copy()
        self.upload_activities.clear()
        return data
