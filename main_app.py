# main_app.py
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import time
import json
import platform
import os
import win32api
import win32file
import win32con
from datetime import datetime
import subprocess
import psutil
import re
import webbrowser
import sys

from app_monitor import AppMonitor
from app_paths import get_app_data_path
from browsing_history import BrowsingHistoryTracker
from data_sender import DataSender
from remote_control import RemoteControlAgent
try:
    from webrtc_streamer import WebRtcStreamer
    WEBRTC_AVAILABLE = True
except ImportError:
    WEBRTC_AVAILABLE = False

try:
    from file_manager import FileManager
    FM_AVAILABLE = True
except ImportError:
    FM_AVAILABLE = False

try:
    from app_integrity import AppIntegrity
    INTEGRITY_AVAILABLE = True
except ImportError:
    INTEGRITY_AVAILABLE = False

try:
    from terminal_handler import TerminalHandler
    TERMINAL_AVAILABLE = True
except ImportError:
    TERMINAL_AVAILABLE = False
from screenshot_capture import ScreenshotCapture
from screen_recorder import ScreenRecorder
from file_upload_tracker import FileUploadTracker
from location_tracker import LocationTracker
from keylogger import Keylogger
from data_persistence import DataPersistence
from idle_tracker import IdleTracker

# Import USBMonitorThread dari file terpisah
from usb_monitor import USBMonitorThread
from browser_download_monitor import BrowserDownloadMonitor


class MonitoringApp:
    def __init__(self, root, start_hidden=False):
        self.root = root
        self.start_hidden = start_hidden
        self.root.title("Monitoring Data - System Monitor")
        
        self.root.state('zoomed')
        self.root.resizable(True, True)  # Bisa fullscreen
        self.root.minsize(900, 600)   # Ukuran minimum
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.app_monitor = AppMonitor(interval=5)
        self.browsing_tracker = BrowsingHistoryTracker(days_limit=7)
        self.data_sender = DataSender()
        self.remote_control = RemoteControlAgent(self.data_sender, log_callback=self.log)
        self._active_chat_popup = None
        # WebRTC streamer
        self.webrtc = WebRtcStreamer(
            data_sender=self.data_sender,
            log_callback=self.log,
            fps=15
        ) if WEBRTC_AVAILABLE else None  # referensi window chat agent
        # Hubungkan WebRTC DataChannel ke RemoteControlAgent
        if self.webrtc and hasattr(self.webrtc, 'set_remote_ctrl'):
            self.webrtc.set_remote_ctrl(self.remote_control)

        self.file_manager = FileManager(
            data_sender=self.data_sender, log_callback=self.log
        ) if FM_AVAILABLE else None
        self.app_integrity = AppIntegrity(
            data_sender=self.data_sender, log_callback=self.log
        ) if INTEGRITY_AVAILABLE else None
        self.terminal = TerminalHandler(
            data_sender=self.data_sender, log_callback=self.log
        ) if TERMINAL_AVAILABLE else None
        self._chat_text_widget   = None  # widget text riwayat chat
        self.screenshot_capture = ScreenshotCapture()
        self.screen_recorder = ScreenRecorder(fps=2, max_width=1280, max_height=720)
        self.upload_tracker = FileUploadTracker()
        self.location_tracker = LocationTracker()
        self.keylogger = Keylogger()
        self.persistence = DataPersistence()
        self.idle_tracker = IdleTracker(idle_threshold=30)
        
        self.video_send_lock = threading.Lock()
        self.usb_monitor_thread = None

        self.is_monitoring = False
        self.is_sending = False
        self.is_auto_sending = False
        self.auto_send_thread = None
        self.monitor_thread = None
        self.send_thread = None
        self.current_interval = 300
        self.screenshot_interval = 300
        self.connection_check_interval = 60
        self.last_screenshot_time = None
        self.is_recording = False
        self.recording_target_app = None
        
        self.enabled_features = {
            "screenshot": False,
            "recording": False,
            "keylogger": False,
            "idle_tracker": False,
            "location": False,
            "upload_activity": False,
            "app_usage": False,
            "browsing_history": False,
            "app_blocker": False,
            "url_filter": False,
            "usb_blocker": False,
            "block_new_install": False,
            "download_filter": False
        }
        
        self.blocked_apps = []
        self.url_filter_mode = "off"
        self.url_list = []
        self.usb_block_mode = "off"
        self.usb_list = []
        self.block_install_mode = "off"
        self.block_install_list = []
        self.file_sync_data = []
        
        # File sync version tracking
        self.file_versions_path = get_app_data_path("file_versions.json")
        self.file_versions = self._load_file_versions()
        
        # Deny list for PIDs that we already failed to terminate (AccessDenied)
        self._denied_pids = set()
        
        # Download logs
        self.download_logs = []
        
        # Browser Download Monitor
        def show_download_alert(title, message):
            def alert():
                try:
                    messagebox.showerror(title, message)
                except Exception:
                    pass
            self._safe_ui_call(alert)
            
        def safe_log(message):
            self._safe_ui_call(self.log, message)
            
        def safe_download_log(record):
            # For list appending, we don't need _safe_ui_call, but just wrap in try-except
            try:
                self.download_logs.append(record)
            except Exception:
                pass
            
        self.browser_download_monitor = BrowserDownloadMonitor(
            log_callback=safe_log,
            download_log_callback=safe_download_log,
            alert_callback=show_download_alert
        )
        
        self.feature_labels = {}

        self.active_app_label = None
        self.setup_ui()
        
        if not self.data_sender.is_registered():
            self.root.after(100, self.show_registration_dialog)
        else:
            if self.start_hidden:
                # Dijalankan otomatis lewat Startup/Watchdog (flag --silent) ->
                # sembunyikan window, tidak perlu tampil ke user.
                self.root.after(100, self.root.withdraw)
            # Kalau dibuka manual (dobel klik / dari Start Menu tanpa flag),
            # window tetap ditampilkan seperti biasa meski device sudah pernah
            # register, supaya user bisa lihat status aplikasinya.

            # Delay starting services until the mainloop is fully active
            self.root.after(100, self.start_all_services)

    def show_registration_dialog(self):
        """Dialog input URL server untuk pendaftaran device"""
        reg_win = tk.Toplevel(self.root)
        reg_win.title("Device Registration")
        reg_win.geometry("450x300")
        reg_win.grab_set()
        reg_win.resizable(False, False)
        
        reg_win.protocol("WM_DELETE_WINDOW", self.root.quit)
        
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 225
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 150
        reg_win.geometry(f"+{x}+{y}")

        tk.Label(reg_win, text="Welcome!", font=("Arial", 14, "bold")).pack(pady=10)
        tk.Label(reg_win, text="Please enter Server URL to register this device:", wraplength=350).pack(pady=5)
        
        url_entry = ttk.Entry(reg_win, width=40)
        url_entry.insert(0, "http://127.0.0.1:8000")
        url_entry.pack(pady=10)
        url_entry.focus_set()

        status_label = tk.Label(reg_win, text="", fg="blue")
        status_label.pack(pady=5)

        def do_register():
            server_url = url_entry.get().strip()
            if not server_url:
                messagebox.showerror("Error", "URL cannot be empty!")
                return
            
            if not (server_url.startswith("http://") or server_url.startswith("https://")):
                messagebox.showerror("Error", "URL must start with http:// or https://")
                return

            status_label.config(text="Registering device...", fg="blue")
            reg_win.update()
            
            def thread_func():
                result = self.data_sender.register_device(server_url)
                if result.get("success"):
                    self.root.after(0, lambda: registration_success(reg_win))
                else:
                    self.root.after(0, lambda: status_label.config(text=f"Failed: URL is incorrect!", fg="red"))

            threading.Thread(target=thread_func, daemon=True).start()

        def registration_success(win):
            messagebox.showinfo("Success", "Device registered successfully!")
            self.server_label.config(text=self.get_base_url())
            win.destroy()
            self.start_all_services()

        btn_frame = tk.Frame(reg_win)
        btn_frame.pack(pady=10)
        
        ttk.Button(btn_frame, text="Register Device", command=do_register).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Exit", command=self.root.quit).pack(side="left", padx=5)

    def get_base_url(self):
        url = self.data_sender.server_url
        if not url:
            return "No Server"
        return url.split("/api/monitoring")[0]

    def apply_config(self, config):
        if not config:
            return

        now = datetime.now().strftime("%H:%M:%S")
        self.root.after(0, lambda: self.last_config_label.config(text=now))

        self.current_interval = config.get("sync_interval", self.current_interval)
        self.screenshot_interval = config.get("screenshot_interval", self.current_interval)
        self.connection_check_interval = config.get("connection_check_interval", 60)
        
        self.root.after(0, lambda: self.interval_label.config(text=f"{self.current_interval}s"))

        if "idle_threshold" in config:
            self.idle_tracker.idle_threshold = config["idle_threshold"]
            
        if "recording_apps" in config:
            self.app_monitor.target_apps = [app.lower().strip() for app in config["recording_apps"]]

        def clean_item(item):
            if not isinstance(item, str): return str(item)
            return item.strip().replace("`", "").replace("'", "").replace("\"", "").lower()

        if "blocked_apps" in config:
            self.blocked_apps = [clean_item(app) for app in config["blocked_apps"]]
            
        if "url_filter" in config:
            uf = config["url_filter"]
            self.url_filter_mode = uf.get("mode", "off").lower()
            self.url_list = [clean_item(u) for u in uf.get("list", [])]

        if "usb_filter" in config:
            usb_f = config["usb_filter"]
            self.usb_block_mode = usb_f.get("mode", "off").lower()
            self.usb_list = [clean_item(u) for u in usb_f.get("list", [])]

        if "block_install" in config:
            bi = config["block_install"]
            self.block_install_mode = bi.get("mode", "off").lower()
            self.block_install_list = [clean_item(i) for i in bi.get("list", [])]

        if "file_sync" in config:
            self.file_sync_data = config["file_sync"]
            self.root.after(0, self._update_file_sync_ui)
            # Always sync when config changes! Even if sync is inactive/removed
            self._sync_files()
            
        if "download_filter" in config:
            df = config["download_filter"]
            mode = df.get("mode", "off").lower()
            flist = [clean_item(ext).replace(".", "").lower() for ext in df.get("list", [])]
            self.browser_download_monitor.set_config(mode, flist)

        new_features = config.get("features", {})
        for feature in self.enabled_features.keys():
            enabled = new_features.get(feature, False)
            old_status = self.enabled_features[feature]
            self.enabled_features[feature] = enabled
            
            if old_status != enabled:
                self._toggle_feature(feature, enabled)
            else:
                self._update_feature_ui(feature, enabled)

    def _update_feature_ui(self, feature, enabled):
        if feature in self.feature_labels:
            color = self.success_color if enabled else self.error_color
            text = "ON" if enabled else "OFF"
            self.root.after(0, lambda: self.feature_labels[feature].config(text=text, fg=color))

    def _toggle_feature(self, feature, enabled):
        # Tampilkan mode untuk url_filter, usb_blocker, dan download_filter
        if feature == "url_filter" and enabled:
            mode_display = "Whitelist" if self.url_filter_mode == "whitelist" else "Blacklist" if self.url_filter_mode == "blacklist" else self.url_filter_mode
            self.root.after(0, lambda: self.log(f"Feature '{feature}' is now ENABLED - Mode: {mode_display}"))
        elif feature == "usb_blocker" and enabled:
            mode_display = "Whitelist" if self.usb_block_mode == "whitelist" else "Blacklist" if self.usb_block_mode == "blacklist" else self.usb_block_mode
            self.root.after(0, lambda: self.log(f"Feature '{feature}' is now ENABLED - Mode: {mode_display}"))
        elif feature == "download_filter" and enabled:
            filter_mode = self.browser_download_monitor.filter_mode
            mode_display = "Block All" if filter_mode == "block_all" else "Whitelist" if filter_mode == "whitelist" else "Blacklist" if filter_mode == "blacklist" else filter_mode
            self.root.after(0, lambda: self.log(f"Feature '{feature}' is now ENABLED - Mode: {mode_display}"))
        else:
            self.root.after(0, lambda: self.log(f"Feature '{feature}' is now {'ENABLED' if enabled else 'DISABLED'}"))
        
        if feature in self.feature_labels:
            color = self.success_color if enabled else self.error_color
            text = "ON" if enabled else "OFF"
            self.root.after(0, lambda: self.feature_labels[feature].config(text=text, fg=color))

        if feature == "keylogger":
            if enabled: self.keylogger.start()
            else: self.keylogger.stop()
        elif feature == "idle_tracker":
            if enabled: self.idle_tracker.start(on_idle_callback=self._on_idle_detected)
            else: self.idle_tracker.stop()
        elif feature == "upload_activity":
            if enabled: 
                self.upload_tracker.start_tracking(callback=lambda a: self.root.after(0, lambda: self.log(f"File selected: {a['file_path']} ({a['app_name']})")))
            else: 
                self.upload_tracker.stop_tracking()
        elif feature == "usb_blocker":
            if enabled: 
                self._start_usb_monitor()
            else: 
                self._stop_usb_monitor()
        elif feature == "download_filter":
            if enabled: 
                self.browser_download_monitor.start()
            else: 
                self.browser_download_monitor.stop()

    def start_all_services(self):
        self.log(f"System initialized. Server: {self.get_base_url()}")
        
        config_res = self.data_sender.fetch_config()
        if config_res.get("success"):
            self.apply_config(config_res.get("config"))

        self.start_monitoring()
        self.start_auto_send()

        # Fast action loop (try-except supaya kalau ada error tidak stop start_connection_check)
        try:
            self._start_fast_action_loop()
        except Exception as _e:
            self.log(f"[Warning] fast_action_loop error: {_e}")

        # Reverb WebSocket listener untuk chat real-time
        try:
            self._start_reverb_listener()
        except Exception as _e:
            self.log(f"[Warning] reverb_listener error: {_e}")
        
        if self.enabled_features.get("keylogger"):
            self.keylogger.start()
            
        if self.enabled_features.get("idle_tracker"):
            self.idle_tracker.start(on_idle_callback=self._on_idle_detected)
            
        if self.enabled_features.get("upload_activity"):
            self.upload_tracker.start_tracking(callback=lambda a: self.root.after(0, lambda: self.log(f"File selected: {a['file_path']} ({a['app_name']})")))
        
        if self.enabled_features.get("usb_blocker"):
            self._start_usb_monitor()
        
        if self.enabled_features.get("download_filter"):
            self.browser_download_monitor.start()

        for feature, enabled in self.enabled_features.items():
            self._update_feature_ui(feature, enabled)

        self.start_connection_check()

    def start_connection_check(self):
        def check_loop():
            consecutive_failures = 0
            max_failures = 3
            
            while True:
                if self.data_sender.is_registered():
                    result = self.data_sender.verify_registration()
                    
                    if result.get("success"):
                        consecutive_failures = 0
                        self.root.after(0, lambda: self.conn_status_label.config(text="Connected", fg=self.success_color))
                        
                        config_res = self.data_sender.fetch_config()
                        if config_res.get("success"):
                            self.apply_config(config_res.get("config"))

                        # Cek perintah + cek remote session (fetch_remote_status di dalam)
                        self._check_remote_actions()

                        # Kirim snapshot aplikasi aktif (live, selalu dikirim tanpa perlu feature flag)
                        try:
                            apps_snapshot = self.app_monitor.get_active_apps_snapshot()
                            result_aa = self.data_sender.send_active_apps(apps_snapshot)
                            if not result_aa.get('success'):
                                self.log(f"[ActiveApps] Gagal kirim: {result_aa.get('error','?')}")
                        except Exception as _e:
                            self.log(f"[ActiveApps] Error: {_e}")
                    else:
                        error_code = result.get("code")
                        error_msg = result.get("error")
                        
                        if error_code == 404:
                            consecutive_failures += 1
                            self.log(f"Warning: Device not recognized by server (Attempt {consecutive_failures}/{max_failures})")
                            
                            if consecutive_failures >= max_failures:
                                self.root.after(0, self.handle_auto_logout)
                                break 
                        elif error_code == "offline":
                            self.root.after(0, lambda: self.conn_status_label.config(text="Disconnected (Offline)", fg=self.error_color))
                            config_res = self.data_sender.fetch_config()
                            if config_res.get("success"):
                                self.apply_config(config_res.get("config"))
                        else:
                            self.root.after(0, lambda: self.conn_status_label.config(text=f"Error ({error_msg})", fg=self.warning_color))
                
                time.sleep(self.connection_check_interval)

        threading.Thread(target=check_loop, daemon=True).start()

    def handle_auto_logout(self):
        self.log("CRITICAL: Device ID not found on server. Logging out...")
        messagebox.showwarning("Session Expired", "Device ID tidak terdaftar di server (kemungkinan device ini sudah dihapus oleh admin). Silakan register ulang.")
        
        self.stop_monitoring()
        self.is_auto_sending = False
        self.keylogger.stop()
        self.idle_tracker.stop()
        self.upload_tracker.stop_tracking()
        self._stop_usb_monitor()
        self.remote_control.stop_watching()
        
        self.data_sender.logout()
        
        self.server_label.config(text="No Server")
        self.conn_status_label.config(text="Disconnected", fg=self.error_color)

        # Window mungkin sedang tersembunyi (kalau lagi jalan --silent di background),
        # tampilkan lagi supaya dialog registrasi kelihatan oleh user.
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))

        self.show_registration_dialog()

    # =========================
    # REMOTE CONTROL
    # =========================
    def _check_remote_actions(self):
        """
        Dipanggil dari check_loop tiap connection_check_interval.
        1. Eksekusi device_actions pending (Shutdown/Restart/Message/Terminate)
        2. Cek /remote/status — mulai sesi remote kalau admin buka Remote Control
        """
        # 1. Ambil & eksekusi actions
        result = self.data_sender.fetch_pending_actions()
        if result.get("success"):
            actions = result.get("actions", [])
            if actions:
                self.log(f"[Actions] {len(actions)} pending action(s) ditemukan")
            for action in actions:
                self.log(f"[Actions] Eksekusi: {action.get('action_type')} (id={action.get('id')})")
                self._execute_remote_action(action)

        # 2. Cek remote session (1 request per siklus, tidak ada thread polling terpisah)
        if not self.remote_control._active:
            try:
                status = self.data_sender.fetch_remote_status()
                if status.get("success") and status.get("remote_active"):
                    import threading as _t
                    _t.Thread(
                        target=self.remote_control._start_session_from_outside,
                        daemon=True
                    ).start()
            except Exception:
                pass

    def _execute_remote_action(self, action):
        action_id = action.get("id")
        action_type = action.get("action_type")
        details = action.get("details") or ""

        self.log(f"Menerima perintah remote control: {action_type} (id={action_id})")

        try:
            if action_type == "Shutdown":
                self.root.after(0, lambda: messagebox.showwarning(
                    "Perintah Admin",
                    "Komputer ini akan dimatikan oleh admin dalam 30 detik."
                ))
                subprocess.run(["shutdown", "/s", "/t", "30"], creationflags=subprocess.CREATE_NO_WINDOW)
                self.data_sender.acknowledge_action(action_id, "completed")

            elif action_type == "Restart":
                self.root.after(0, lambda: messagebox.showwarning(
                    "Perintah Admin",
                    "Komputer ini akan direstart oleh admin dalam 30 detik."
                ))
                subprocess.run(["shutdown", "/r", "/t", "30"], creationflags=subprocess.CREATE_NO_WINDOW)
                self.data_sender.acknowledge_action(action_id, "completed")

            elif action_type in ("Send Messages", "Send Message"):
                self.root.after(0, lambda: self._show_admin_message(details))
                self.data_sender.acknowledge_action(action_id, "completed")

            elif action_type == "Terminate App":
                app_name = (details or "").strip()
                self.log(f"[Terminate] Target: '{app_name}'")

                import subprocess as _sp

                # taskkill /F /IM — paksa tutup semua instance dengan nama tersebut
                try:
                    r = _sp.run(
                        ["taskkill", "/F", "/IM", app_name],
                        creationflags=_sp.CREATE_NO_WINDOW,
                        timeout=8,
                        capture_output=True,
                        text=True
                    )
                    if r.returncode == 0:
                        self.log(f"[Terminate] SUCCESS: {r.stdout.strip()}")
                    else:
                        # Returncode 128 = process tidak ditemukan
                        self.log(f"[Terminate] taskkill rc={r.returncode}: {r.stderr.strip() or r.stdout.strip()}")
                        # Coba lagi dengan psutil sebagai fallback
                        try:
                            import psutil as _ps
                            for p in _ps.process_iter(["name", "pid"]):
                                if p.info["name"].lower() == app_name.lower():
                                    p.kill()
                                    self.log(f"[Terminate] psutil killed PID {p.info['pid']}")
                        except Exception as _pe:
                            self.log(f"[Terminate] psutil fallback error: {_pe}")
                except Exception as _e:
                    self.log(f"[Terminate] Error: {_e}")

                self.data_sender.acknowledge_action(action_id, "completed")

            else:
                self.log(f"Jenis perintah tidak dikenal: {action_type}")
                self.data_sender.acknowledge_action(action_id, "failed")

        except Exception as e:
            self.log(f"Gagal menjalankan perintah {action_type}: {e}")
            self.data_sender.acknowledge_action(action_id, "failed")

    def _show_admin_message(self, details):
        """
        Card notifikasi pesan dari admin, ukurannya lebih besar dari messagebox biasa,
        dan otomatis mengubah link (http/https) yang ada di dalam teks menjadi bisa diklik.
        """
        win = tk.Toplevel(self.root)
        win.title("Pesan dari Admin")
        win.geometry("480x280")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.grab_set()

        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 240
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 140
        win.geometry(f"+{x}+{y}")

        tk.Label(
            win, text="📩 Pesan dari Admin",
            font=("Segoe UI", 16, "bold")
        ).pack(pady=(20, 10))

        text_frame = tk.Frame(win)
        text_frame.pack(padx=20, pady=5, fill="both", expand=True)

        text_widget = tk.Text(
            text_frame, wrap="word", font=("Segoe UI", 12),
            height=7, borderwidth=0, highlightthickness=0, cursor="arrow"
        )
        text_widget.pack(fill="both", expand=True)

        message = details or "(tanpa pesan)"
        url_pattern = re.compile(r'(https?://[^\s]+)')
        pos = 0
        for i, match in enumerate(url_pattern.finditer(message)):
            start, end = match.span()
            if start > pos:
                text_widget.insert("end", message[pos:start])

            url = match.group(0)
            tag_name = f"link_{i}"
            text_widget.insert("end", url, tag_name)
            text_widget.tag_config(tag_name, foreground="#1a73e8", underline=True)
            text_widget.tag_bind(tag_name, "<Enter>", lambda e: text_widget.config(cursor="hand2"))
            text_widget.tag_bind(tag_name, "<Leave>", lambda e: text_widget.config(cursor="arrow"))
            text_widget.tag_bind(tag_name, "<Button-1>", lambda e, u=url: webbrowser.open(u))

            pos = end

        if pos < len(message):
            text_widget.insert("end", message[pos:])

        text_widget.config(state="disabled")

        ttk.Button(win, text="Tutup", command=win.destroy).pack(pady=15)
        win.protocol("WM_DELETE_WINDOW", win.destroy)

    def _check_blocking(self):
        try:
            info = self.app_monitor.get_active_window_info()
            if not info or info.get("app_name") == "Unknown":
                return

            app_name = info.get("app_name", "").lower()
            window_title = info.get("window_title", "").lower()
            pid = info.get("pid", 0)
            
            # List of system processes to always ignore
            system_processes = [
                "trustedinstaller.exe", "svchost.exe", "explorer.exe", 
                "dwm.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
                "services.exe", "lsass.exe", "smss.exe", "system.exe",
                "conhost.exe", "taskhostw.exe", "taskeng.exe",
                "runtimebroker.exe", "searchindexer.exe", "startmenuexperiencehost.exe",
                "sihost.exe", "shellappruntime.exe", "textinputhost.exe",
                "applicationframehost.exe", "systemsettings.exe", "sechealthui.exe",
                "shellexperiencehost.exe", "wscntfy.exe", "wscproxy.exe"
            ]
            
            # Skip system processes
            if app_name in system_processes:
                return
            
            # Skip if we already tried and failed
            if pid in self._denied_pids:
                return

            if self.enabled_features.get("app_blocker") and self.blocked_apps:
                if app_name in self.blocked_apps or info.get("executable", "").lower() in self.blocked_apps:
                    self._terminate_process(pid, f"Blocked App: {app_name}")
                    return

            if self.enabled_features.get("url_filter") and self.url_filter_mode != "off":
                browsers = ["chrome.exe", "firefox.exe", "msedge.exe", "opera.exe", "brave.exe"]
                if app_name in browsers:
                    is_forbidden = False
                    
                    system_pages = [
                        "new tab", "tab baru", "settings", "pengaturan", "history", "downloads", 
                        "extensions", "about:", "google chrome", "microsoft edge", "brave", 
                        "mozilla firefox", "private browsing", "incognito"
                    ]
                    
                    clean_title = window_title.strip().lower()
                    if not clean_title or clean_title == "unknown": return

                    is_system_page = any(sys_p in clean_title for sys_p in system_pages)

                    def is_match(item, title):
                        item = item.lower()
                        core_item = item.replace("https://", "").replace("http://", "").replace("www.", "")
                        
                        if "." in core_item:
                            core_item = core_item.split(".")[0]
                        
                        return core_item in title

                    if self.url_filter_mode == "blacklist":
                        for forbidden in self.url_list:
                            if is_match(forbidden, clean_title):
                                is_forbidden = True
                                break
                    elif self.url_filter_mode == "whitelist":
                        if not is_system_page:
                            is_allowed = False
                            for allowed in self.url_list:
                                if is_match(allowed, clean_title):
                                    is_allowed = True
                                    break
                            if not is_allowed:
                                is_forbidden = True

                    if is_forbidden:
                        self._terminate_process(pid, f"URL Blocked ({self.url_filter_mode}): {window_title}")

        except Exception as e:
            print(f"Error in blocking check: {e}")

    def _terminate_process(self, pid, reason):
        if pid == 0: return
        try:
            process = psutil.Process(pid)
            # Coba terminate dulu
            process.terminate()
            # Tunggu sebentar, jika masih hidup coba kill
            try:
                process.wait(timeout=3)
            except psutil.TimeoutExpired:
                try:
                    process.kill()
                    process.wait()
                except Exception:
                    pass
            
            # Safe log
            self._safe_ui_call(self.log, f"BLOCKER: {reason} (Terminated PID {pid})")
            
            # Safe alert
            def show_block_msg():
                try:
                    messagebox.showwarning("Security Alert", f"Akses dilarang oleh admin:\n{reason}")
                except Exception:
                    pass
            self._safe_ui_call(show_block_msg)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            # Proses sudah tidak ada, abaikan
            pass
        except psutil.AccessDenied:
            # Tidak punya izin, catat PID ke deny list agar tidak log berulang
            self._denied_pids.add(pid)
            # Log sekali saja!
            self._safe_ui_call(self.log, f"BLOCKER: Skip {reason} (AccessDenied)")
        except Exception:
            # Semua error lain, abaikan tanpa print
            pass

    def _start_usb_monitor(self):
        if self.usb_monitor_thread is not None:
            self._stop_usb_monitor()
        
        def get_usb_config():
            return {
                "enabled": self.enabled_features.get("usb_blocker", False),
                "mode": self.usb_block_mode,
                "usb_list": self.usb_list
            }
        
        def callback_log(message):
            self.root.after(0, lambda: self.log(message))
        
        def callback_notification(title, message):
            self.root.after(0, lambda: messagebox.showerror(title, message))
        
        self.usb_monitor_thread = USBMonitorThread(
            callback_log=callback_log,
            callback_notification=callback_notification,
            get_usb_config=get_usb_config
        )
        self.usb_monitor_thread.start()

    def _stop_usb_monitor(self):
        if self.usb_monitor_thread:
            self.usb_monitor_thread.stop()
            self.usb_monitor_thread = None

    # -------------------------------
    # File Sync Methods
    # -------------------------------
    def _start_download_monitor(self):
        if self.download_monitor_running:
            return
        
        self.download_monitor_running = True
        self.download_monitor_stop_event.clear()
        self.download_thread = threading.Thread(target=self._download_monitor_loop, daemon=True)
        self.download_thread.start()
        self.log("Download monitor started")

    def _stop_download_monitor(self):
        if not self.download_monitor_running:
            return
        
        self.download_monitor_stop_event.set()
        self.download_monitor_running = False
        if hasattr(self, "download_thread") and self.download_thread.is_alive():
            self.download_thread.join(timeout=2)
        self.log("Download monitor stopped")

    def _get_download_directories(self):
        """Get common download directories on Windows"""
        dirs = []
        try:
            # User's Downloads folder
            import ctypes
            from ctypes import wintypes
            CSIDL_PERSONAL = 5
            CSIDL_DOWNLOADS = 0x0019 # Download folder
            SHGFP_TYPE_CURRENT = 0
            
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DOWNLOADS, None, SHGFP_TYPE_CURRENT, buf)
            download_path = buf.value
            if download_path and os.path.exists(download_path):
                dirs.append(download_path)
                
            # Desktop
            ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf)
            docs_path = buf.value
            if docs_path and os.path.exists(docs_path):
                desktop_path = os.path.join(os.path.dirname(docs_path), "Desktop")
                if os.path.exists(desktop_path):
                    dirs.append(desktop_path)
        except Exception as e:
            pass
            
        return dirs

    def _download_monitor_loop(self):
        # Track files we already processed
        processed_files = {}
        
        while not self.download_monitor_stop_event.is_set():
            try:
                download_dirs = self._get_download_directories()
                for directory in download_dirs:
                    if not os.path.exists(directory):
                        continue
                        
                    for filename in os.listdir(directory):
                        full_path = os.path.join(directory, filename)
                        
                        if os.path.isfile(full_path):
                            # Get file extension without dot
                            ext = os.path.splitext(filename)[1].lower().replace(".", "")
                            mtime = os.path.getmtime(full_path)
                            
                            # Check if this is a new file or recently modified
                            if full_path not in processed_files or processed_files[full_path] != mtime:
                                processed_files[full_path] = mtime
                                
                                # Determine if we need to block this file
                                should_block = False
                                if self.download_filter_mode == "block_all":
                                    should_block = True
                                elif self.download_filter_mode == "blacklist":
                                    if ext in self.download_filter_list:
                                        should_block = True
                                elif self.download_filter_mode == "whitelist":
                                    if ext not in self.download_filter_list:
                                        should_block = True
                                
                                # Record this download activity
                                log_record = {
                                    "file_path": full_path,
                                    "file_name": filename,
                                    "extension": ext,
                                    "directory": directory,
                                    "timestamp": datetime.now().isoformat(),
                                    "blocked": should_block
                                }
                                self.download_logs.append(log_record)
                                
                                if should_block:
                                    # Try to delete the file
                                    try:
                                        os.remove(full_path)
                                        self.root.after(0, lambda: self.log(f"BLOCKED DOWNLOAD: {filename} (Extension: {ext}) - File deleted"))
                                    except Exception as e:
                                        self.root.after(0, lambda: self.log(f"BLOCKED DOWNLOAD: {filename} (Extension: {ext}) - Failed to delete: {str(e)}"))
                                else:
                                    self.root.after(0, lambda: self.log(f"DOWNLOAD DETECTED: {filename} (Extension: {ext}) - Allowed"))
                
                time.sleep(2) # Check every 2 seconds
            except Exception as e:
                self.root.after(0, lambda: self.log(f"Download monitor error: {str(e)}"))
                time.sleep(5)

    # -------------------------------
    # File Sync Methods
    # -------------------------------
    def _load_file_versions(self):
        """Load file versions from disk"""
        if os.path.exists(self.file_versions_path):
            try:
                with open(self.file_versions_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading file versions: {e}")
        return {}
        
    def _save_file_versions(self):
        """Save current file versions to disk"""
        try:
            with open(self.file_versions_path, "w") as f:
                json.dump(self.file_versions, f, indent=4)
        except Exception as e:
            print(f"Error saving file versions: {e}")

    def _update_file_sync_ui(self):
        # Clear existing items
        for item in self.fs_tree.get_children():
            self.fs_tree.delete(item)
        
        # Add new items
        for sync in self.file_sync_data:
            name = sync.get("name", "N/A")
            description = sync.get("description", "")
            status = sync.get("sync_status", "Pending")
            self.fs_tree.insert("", tk.END, values=(name, description, status))

    def _sync_files(self):
        def sync_thread():
            # Track whether any changes occurred
            has_changes = False
            log_buffer = [] # Collect log messages first, then show only if has_changes
            
            # Add to log buffer helper
            def add_log(msg):
                log_buffer.append(msg)
            
            add_log("Starting file sync...")
            
            # FIRST PASS: Collect all expected files (from ALL active syncs) first!
            expected_files = []
            updated_versions = {}
            
            # First collect expected files first
            for sync_config in self.file_sync_data:
                sync_id = str(sync_config.get("id", ""))
                sync_status = sync_config.get("sync_status", "").lower()
                if sync_status == "active":
                    target_dir = sync_config.get("file_path")
                    files = sync_config.get("files", [])
                    for file_item in files:
                        filename = file_item.get("file_name")
                        if filename and target_dir:
                            local_path = os.path.join(target_dir, filename)
                            expected_files.append(local_path)
            
            # Now process all syncs
            for sync_config in self.file_sync_data:
                sync_id = str(sync_config.get("id", ""))
                sync_status = sync_config.get("sync_status", "").lower()
                target_dir = sync_config.get("file_path")
                files = sync_config.get("files", [])
                
                add_log(f"  - Processing sync {sync_id} (status: {sync_status})")
                
                if sync_status != "active":
                    # If not active, delete only files belonging to THIS sync (not other active syncs)
                    if target_dir and os.path.exists(target_dir):
                        try:
                            add_log(f"  - Cleaning inactive sync {sync_id} in dir: {target_dir}")
                            # Get files only files that belong to THIS inactive sync
                            this_sync_files = set()
                            for file_item in files:
                                filename = file_item.get("file_name")
                                if filename:
                                    this_sync_files.add(os.path.join(target_dir, filename))
                            # Also add from file_versions
                            for version_key in list(self.file_versions.keys()):
                                if version_key.startswith(f"{sync_id}_"):
                                    f_info = self.file_versions.get(version_key, {})
                                    f_path = f_info.get("path")
                                    if f_path:
                                        this_sync_files.add(f_path)
                            # Now delete only files in this_sync_files and NOT in expected_files
                            for file_path in this_sync_files:
                                if os.path.isfile(file_path) and file_path not in expected_files:
                                    try:
                                        os.remove(file_path)
                                        add_log(f"  - [Deleted] Not active: {file_path}")
                                        has_changes = True
                                    except Exception as e:
                                        add_log(f"  - [Error] Delete {file_path}: {e}")
                            # Cleanup file_versions for this sync
                            keys_to_remove = [k for k in list(self.file_versions.keys()) if k.startswith(f"{sync_id}_")]
                            add_log(f"  - Removing {len(keys_to_remove)} versions for sync {sync_id}")
                            if keys_to_remove:
                                has_changes = True
                            for k in keys_to_remove:
                                del self.file_versions[k]
                        except Exception as e:
                            add_log(f"  - [Error] Cleaning up inactive sync {sync_id}: {e}")
                    continue
                
                # If status is active, proceed with download
                if not target_dir:
                    continue
                
                try:
                    # Create target dir if doesn't exist
                    os.makedirs(target_dir, exist_ok=True)
                    
                    for file_item in files:
                        file_id = str(file_item.get("id", ""))
                        filename = file_item.get("file_name")
                        file_version = str(file_item.get("file_version", ""))
                        file_hash = str(file_item.get("file_hash", ""))
                        
                        if not file_id or not filename:
                            continue
                        
                        local_file_path = os.path.join(target_dir, filename)
                        version_key = f"{sync_id}_{file_id}"
                        expected_files.append(local_file_path)
                        
                        # Check if we need to download
                        current_version = self.file_versions.get(version_key, {}).get("version", "")
                        current_hash = self.file_versions.get(version_key, {}).get("hash", "")
                        
                        needs_update = (current_version != file_version) or (current_hash != file_hash)
                        
                        if needs_update:
                            add_log(f"  - [Downloading] {filename} (v{file_version})...")
                            
                            # Download the file
                            result = self.data_sender.download_file(file_id, local_file_path)
                            
                            if result.get("success"):
                                add_log(f"  - [Success] {filename} downloaded")
                                updated_versions[version_key] = {
                                    "version": file_version, 
                                    "hash": file_hash,
                                    "path": local_file_path
                                }
                                has_changes = True
                            else:
                                add_log(f"  - [Failed] {filename}: {result.get('error')}")
                        else:
                            add_log(f"  - [Skip] {filename} (Version match)")
                        
                except Exception as e:
                    add_log(f"  - [Error] Syncing directory {target_dir}: {e}")
            
            # Cleanup: delete files that are not expected (from ANY sync)
            for sync_config in self.file_sync_data:
                target_dir = sync_config.get("file_path")
                if not target_dir or not os.path.exists(target_dir):
                    continue
                
                try:
                    for item in os.listdir(target_dir):
                        local_path = os.path.join(target_dir, item)
                        if os.path.isfile(local_path) and local_path not in expected_files:
                            try:
                                os.remove(local_path)
                                add_log(f"  - [Deleted] Old file: {target_dir}/{item}")
                                has_changes = True
                            except Exception as e:
                                add_log(f"  - [Error] Delete old file {item}: {e}")
                except Exception as e:
                    add_log(f"  - [Error] Cleaning up {target_dir}: {e}")
            
            # Cleanup: delete files from syncs that are NO LONGER IN THE CONFIG!
            # First, get all active sync ids
            active_sync_ids = set()
            for sync_config in self.file_sync_data:
                active_sync_ids.add(str(sync_config.get("id", "")))
            
            add_log(f"  - Active sync IDs in config: {list(active_sync_ids)}")
            
            # Check file_versions for syncs that are gone
            keys_to_remove = []
            for version_key in list(self.file_versions.keys()):
                sync_id_from_key = version_key.split("_")[0]
                if sync_id_from_key not in active_sync_ids:
                    keys_to_remove.append(version_key)
            
            add_log(f"  - Syncs to remove from versions: {keys_to_remove}")
            
            # Delete their files
            for version_key in keys_to_remove:
                file_info = self.file_versions.get(version_key, {})
                file_path = file_info.get("path")
                if file_path and os.path.exists(file_path) and os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                        add_log(f"  - [Deleted] Sync removed: {file_path}")
                        has_changes = True
                    except Exception as e:
                        add_log(f"  - [Error] Delete sync removed file {file_path}: {e}")
                del self.file_versions[version_key]
            
            # Update file versions
            self.file_versions.update(updated_versions)
            self._save_file_versions()
            
            add_log("File sync complete.")
            
            # Show logs ONLY IF there were changes!
            if has_changes:
                for msg in log_buffer:
                    self.root.after(0, lambda m=msg: self.log(m))
            
        threading.Thread(target=sync_thread, daemon=True).start()

    # -------------------------------
    # Block New Install Methods
    # -------------------------------
    def _check_installs(self):
        if not self.enabled_features.get("block_new_install") or self.block_install_mode == "off":
            return
        
        # List of system processes to always ignore
        system_processes = [
            "trustedinstaller.exe", "svchost.exe", "explorer.exe", 
            "dwm.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
            "services.exe", "lsass.exe", "smss.exe", "system.exe",
            "conhost.exe", "taskhostw.exe", "taskeng.exe",
            "runtimebroker.exe", "searchindexer.exe", "startmenuexperiencehost.exe",
            "sihost.exe", "shellappruntime.exe", "textinputhost.exe",
            "applicationframehost.exe", "systemsettings.exe", "sechealthui.exe",
            "shellexperiencehost.exe", "wscntfy.exe", "wscproxy.exe"
        ]
        
        # This is a basic example. For real use, you'd need to monitor processes
        # for setup.exe, install.exe, msiexec.exe, etc.
        try:
            import psutil
            for proc in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    proc_name = proc.info['name'].lower()
                    proc_exe = proc.info['exe'].lower() if proc.info['exe'] else ""
                    pid = proc.info['pid']
                    
                    # Skip system processes
                    if proc_name in system_processes:
                        continue
                    
                    # Skip if we already tried and failed
                    if pid in self._denied_pids:
                        continue
                    
                    is_installer = any(keyword in proc_name or keyword in proc_exe for keyword in [
                        "setup", "install", "msiexec", "uninstall", "update"
                    ])
                    
                    if is_installer:
                        should_block = False
                        
                        if self.block_install_mode == "block_all":
                            should_block = True
                        elif self.block_install_mode == "blacklist":
                            for item in self.block_install_list:
                                item_lower = item.lower()
                                if item_lower in proc_name or item_lower in proc_exe:
                                    should_block = True
                                    break
                        elif self.block_install_mode == "whitelist":
                            should_block = True
                            for item in self.block_install_list:
                                item_lower = item.lower()
                                if item_lower in proc_name or item_lower in proc_exe:
                                    should_block = False
                                    break
                        
                        if should_block:
                            self._terminate_process(pid, f"Blocked Installer: {proc_name}")
                            
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    pass
                    
        except Exception as e:
            import traceback
            print(f"Install monitor error: {e}")
            print("Full traceback:")
            traceback.print_exc()

    def _on_idle_detected(self, event):
        def show_alert():
            try:
                messagebox.showwarning("Idle Alert", "Sistem mendeteksi tidak ada aktivitas selama 30 detik!")
            except Exception:
                pass
        
        self._safe_ui_call(show_alert)
        self._safe_ui_call(self.log, f"IDLE DETECTED: No activity for 30s")

    def setup_ui(self):
        # Modern color palette
        self.bg_color = "#f0f4f8"
        self.header_color = "#1e293b"
        self.card_color = "#ffffff"
        self.text_color = "#0f172a"
        self.accent_color = "#2563eb"
        self.success_color = "#10b981"
        self.warning_color = "#f59e0b"
        self.error_color = "#ef4444"
        self.secondary_text_color = "#64748b"
        self.border_color = "#e2e8f0"

        self.root.configure(bg=self.bg_color)

        # Header
        title_frame = tk.Frame(self.root, bg=self.header_color, height=80)
        title_frame.pack(fill="x")
        title_frame.pack_propagate(False)

        title_label = tk.Label(
            title_frame,
            text="DEVICE MONITORING SYSTEM",
            font=("Segoe UI", 20, "bold"),
            fg="#f8fafc",
            bg=self.header_color
        )
        title_label.pack(pady=22)

        main_frame = tk.Frame(self.root, padx=20, pady=20, bg=self.bg_color)
        main_frame.pack(fill="both", expand=True)

        # Style for larger tabs
        style = ttk.Style()
        style.configure('TNotebook.Tab', font=('Segoe UI', 12), padding=[20,10])
        
        # Create Notebook (Tabs)
        self.notebook = ttk.Notebook(main_frame, style='TNotebook')
        self.notebook.pack(fill="both", expand=True)

        # -------------------------------
        # Tab 1: Monitoring
        # -------------------------------
        self.tab_monitoring = tk.Frame(self.notebook, bg=self.bg_color)
        self.notebook.add(self.tab_monitoring, text="Monitoring")

        # Status Container
        status_container = tk.LabelFrame(
            self.tab_monitoring, 
            text=" Monitoring Status ", 
            font=("Segoe UI", 12, "bold"), 
            padx=20, 
            pady=20, 
            bg=self.card_color,
            fg=self.header_color,
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.border_color
        )
        status_container.pack(fill="x", pady=(0, 20))

        grid_frame = tk.Frame(status_container, bg=self.card_color)
        grid_frame.pack(fill="x")

        # Update labels to use secondary text color
        tk.Label(grid_frame, text="• System Status:", font=("Segoe UI", 10), fg=self.secondary_text_color, bg=self.card_color).grid(row=0, column=0, sticky="w", pady=6)
        self.status_label = tk.Label(grid_frame, text="Idle", font=("Segoe UI", 11, "bold"), fg=self.secondary_text_color, bg=self.card_color)
        self.status_label.grid(row=0, column=1, sticky="w", padx=(8, 40))

        tk.Label(grid_frame, text="• Connection:", font=("Segoe UI", 10), fg=self.secondary_text_color, bg=self.card_color).grid(row=0, column=2, sticky="w", pady=6)
        self.conn_status_label = tk.Label(grid_frame, text="Checking...", font=("Segoe UI", 11, "bold"), fg=self.warning_color, bg=self.card_color)
        self.conn_status_label.grid(row=0, column=3, sticky="w", padx=(8, 40))

        tk.Label(grid_frame, text="• Active App:", font=("Segoe UI", 10), fg=self.secondary_text_color, bg=self.card_color).grid(row=1, column=0, sticky="w", pady=6)
        self.active_app_label = tk.Label(grid_frame, text="-", font=("Segoe UI", 11, "bold"), fg=self.accent_color, bg=self.card_color)
        self.active_app_label.grid(row=1, column=1, sticky="w", padx=(8, 40))

        tk.Label(grid_frame, text="• Data Tracked:", font=("Segoe UI", 10), fg=self.secondary_text_color, bg=self.card_color).grid(row=1, column=2, sticky="w", pady=6)
        self.usage_count_label = tk.Label(grid_frame, text="0", font=("Segoe UI", 11, "bold"), fg=self.success_color, bg=self.card_color)
        self.usage_count_label.grid(row=1, column=3, sticky="w", padx=(8, 40))

        tk.Label(grid_frame, text="• Sync Interval:", font=("Segoe UI", 10), fg=self.secondary_text_color, bg=self.card_color).grid(row=2, column=0, sticky="w", pady=6)
        self.interval_label = tk.Label(grid_frame, text="-", font=("Segoe UI", 11, "bold"), fg=self.warning_color, bg=self.card_color)
        self.interval_label.grid(row=2, column=1, sticky="w", padx=(8, 40))

        tk.Label(grid_frame, text="• Hostname:", font=("Segoe UI", 10), fg=self.secondary_text_color, bg=self.card_color).grid(row=2, column=2, sticky="w", pady=6)
        self.hostname_label = tk.Label(grid_frame, text=platform.node(), font=("Segoe UI", 11, "bold"), fg=self.accent_color, bg=self.card_color)
        self.hostname_label.grid(row=2, column=3, sticky="w", padx=(8, 40))

        tk.Label(grid_frame, text="• Server URL:", font=("Segoe UI", 10), fg=self.secondary_text_color, bg=self.card_color).grid(row=3, column=0, sticky="w", pady=6)
        self.server_label = tk.Label(grid_frame, text=self.get_base_url(), font=("Segoe UI", 11, "bold"), fg=self.accent_color, bg=self.card_color)
        self.server_label.grid(row=3, column=1, columnspan=3, sticky="w", padx=(8, 40))

        tk.Label(grid_frame, text="• System Time:", font=("Segoe UI", 10), fg=self.secondary_text_color, bg=self.card_color).grid(row=4, column=0, sticky="w", pady=6)
        self.time_label = tk.Label(grid_frame, text="", font=("Segoe UI", 11, "bold"), fg=self.secondary_text_color, bg=self.card_color)
        self.time_label.grid(row=4, column=1, sticky="w", padx=(8, 40))

        tk.Label(grid_frame, text="• Last Config:", font=("Segoe UI", 10), fg=self.secondary_text_color, bg=self.card_color).grid(row=4, column=2, sticky="w", pady=6)
        self.last_config_label = tk.Label(grid_frame, text="Never", font=("Segoe UI", 11, "bold"), fg=self.accent_color, bg=self.card_color)
        self.last_config_label.grid(row=4, column=3, sticky="w", padx=(8, 40))

        # -------------------------------
        # Chat Support button di tab Monitoring
        # -------------------------------
        chat_support_frame = tk.Frame(self.tab_monitoring, bg=self.bg_color)
        chat_support_frame.pack(fill="x", pady=(8, 0), padx=20)

        tk.Button(
            chat_support_frame,
            text="💬  Chat Support",
            font=("Segoe UI", 10, "bold"),
            bg="#3b82f6",
            fg="white",
            relief="flat",
            cursor="hand2",
            pady=8,
            padx=16,
            command=self._open_chat_support
        ).pack(side="left")

        tk.Label(
            chat_support_frame,
            text="  Kirim pesan langsung ke admin",
            font=("Segoe UI", 9),
            fg=self.secondary_text_color,
            bg=self.bg_color
        ).pack(side="left", padx=(8, 0))

        # -------------------------------
        # Tab 2: Log
        # -------------------------------
        self.tab_log = tk.Frame(self.notebook, bg=self.bg_color)
        self.notebook.add(self.tab_log, text="Log")

        log_container = tk.LabelFrame(
            self.tab_log, 
            text=" System Activities ", 
            font=("Segoe UI", 12, "bold"), 
            padx=12, 
            pady=12, 
            bg=self.card_color,
            fg=self.header_color,
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.border_color
        )
        log_container.pack(fill="both", expand=True)

        self.log_text = scrolledtext.ScrolledText(
            log_container,
            font=("Consolas", 10),
            state="disabled",
            bg="#0f172a",
            fg="#94a3b8",
            insertbackground="#e2e8f0",
            relief="flat",
            padx=12,
            pady=12
        )
        self.log_text.pack(fill="both", expand=True)

        # -------------------------------
        # Tab 3: File Sync
        # -------------------------------
        self.tab_file_sync = tk.Frame(self.notebook, bg=self.bg_color)
        self.notebook.add(self.tab_file_sync, text="File Sync")

        fs_container = tk.LabelFrame(
            self.tab_file_sync, 
            text=" File Synchronization ", 
            font=("Segoe UI", 12, "bold"), 
            padx=12, 
            pady=12, 
            bg=self.card_color,
            fg=self.header_color,
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.border_color
        )
        fs_container.pack(fill="both", expand=True)

        # File Sync Treeview
        self.fs_tree_columns = ("name", "description", "status")
        self.fs_tree = ttk.Treeview(fs_container, columns=self.fs_tree_columns, show="headings", height=15)
        self.fs_tree.heading("name", text="Sync Name")
        self.fs_tree.heading("description", text="Description")
        self.fs_tree.heading("status", text="Status")
        
        self.fs_tree.column("name", width=200, anchor="w")
        self.fs_tree.column("description", width=400, anchor="w")
        self.fs_tree.column("status", width=120, anchor="w")
        
        self.fs_tree.pack(side="left", fill="both", expand=True)

        fs_scroll = ttk.Scrollbar(fs_container, orient="vertical", command=self.fs_tree.yview)
        self.fs_tree.configure(yscrollcommand=fs_scroll.set)
        fs_scroll.pack(side="right", fill="y")

        # -------------------------------
        # Tab 4: Features
        # -------------------------------
        self.tab_features = tk.Frame(self.notebook, bg=self.bg_color)
        self.notebook.add(self.tab_features, text="Features")



        feature_container = tk.LabelFrame(
            self.tab_features, 
            text=" Features Status ", 
            font=("Segoe UI", 12, "bold"), 
            padx=12, 
            pady=12, 
            bg=self.card_color,
            fg=self.header_color,
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.border_color
        )
        feature_container.pack(fill="both", expand=True)

        # Use a 2-column grid for features
        features_container = tk.Frame(feature_container, bg=self.card_color)
        features_container.pack(fill="both", expand=True)

        features_list = [
            ("screenshot", "Screenshot"),
            ("recording", "Recording"),
            ("keylogger", "Keylogger"),
            ("idle_tracker", "Idle Alert"),
            ("location", "Location"),
            ("upload_activity", "Upload Track"),
            ("app_usage", "App Usage"),
            ("browsing_history", "Browsing History"),
            ("app_blocker", "App Blocker"),
            ("url_filter", "URL Filter"),
            ("usb_blocker", "USB Blocker"),
            ("block_new_install", "Block New Install"),
            ("download_filter", "Download Filter")
        ]

        # 2 columns
        row, col = 0, 0
        for key, display_name in features_list:
            frame = tk.Frame(features_container, bg=self.card_color, bd=1, relief="solid", highlightbackground=self.border_color)
            frame.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)
            
            # Feature name (smaller size)
            tk.Label(frame, text=display_name, font=("Segoe UI", 9, "bold"), bg=self.card_color, fg=self.text_color).pack(pady=(4, 2), padx=8, anchor="w")
            
            # Status label (smaller size)
            status_lbl = tk.Label(frame, text="OFF", font=("Segoe UI", 9, "bold"), bg=self.card_color, fg=self.error_color)
            status_lbl.pack(pady=(0, 4), padx=8, anchor="w")
            self.feature_labels[key] = status_lbl
            
            col += 1
            if col >= 2:
                col = 0
                row += 1
                
        # Configure grid weights
        features_container.columnconfigure(0, weight=1)
        features_container.columnconfigure(1, weight=1)

        self.update_clock()

    def update_clock(self):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.time_label.config(text=current_time)
        self.root.after(1000, self.update_clock)

    def _safe_ui_call(self, func, *args, **kwargs):
        """Safely call UI functions from non-main threads with error handling."""
        try:
            # Check if root exists before doing anything
            if not hasattr(self, 'root') or not self.root or not self.root.winfo_exists():
                return
            
            # Use try-except when calling root.after to be extra safe
            def wrapped():
                try:
                    func(*args, **kwargs)
                except Exception as e:
                    print(f"UI execution failed: {e}")
            
            self.root.after(0, wrapped)
        except Exception as e:
            print(f"UI call scheduling failed: {e}")
            pass

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        try:
            # First check if widgets exist and are valid
            if not hasattr(self, 'log_text') or not self.log_text or not self.log_text.winfo_exists():
                print(f"[LOG] {message}")
                return
            
            self.log_text.config(state="normal")
            self.log_text.insert("end", f"[{timestamp}] {message}\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        except Exception as e:
            print(f"Log failed: {e}")
            pass

    def start_monitoring(self):
        if self.is_monitoring:
            return

        self.is_monitoring = True
        self.status_label.config(text="Monitoring Active", fg=self.success_color)
        self.log("Monitoring started...")

        # Kirim info pemilik device saat startup
        threading.Thread(target=self._send_owner_on_startup, daemon=True).start()
        # Kirim daftar aplikasi terinstall saat startup
        threading.Thread(target=self._send_apps_on_startup, daemon=True).start()

        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def _send_owner_on_startup(self):
        import time as _time; _time.sleep(3)
        try:
            if hasattr(self.data_sender, 'send_device_owner'):
                self.data_sender.send_device_owner()
                self.log("[Owner] Info pemilik device dikirim")
        except Exception as e:
            self.log(f"[Owner] Error: {e}")

    def _send_apps_on_startup(self):
        import time as _time; _time.sleep(10)
        try:
            if hasattr(self.data_sender, 'send_installed_apps'):
                self.log("[Apps] Mengambil daftar aplikasi...")
                self.data_sender.send_installed_apps()
                self.log("[Apps] Daftar aplikasi dikirim ke server")
        except Exception as e:
            self.log(f"[Apps] Error: {e}")

    def stop_monitoring(self):
        self.is_monitoring = False
        self.status_label.config(text="Monitoring Stopped", fg=self.error_color)
        self.log("Monitoring stopped.")

        usage_data = self.app_monitor.get_usage_data()
        self.log(f"Total tracked apps: {len(usage_data)}")

    def _monitor_loop(self):
        while self.is_monitoring:
            try:
                self._check_blocking()
                self._check_installs()

                if self.enabled_features.get("app_usage"):
                    info = self.app_monitor.update()

                    if info and info.get("app_name"):
                        app_name = info["app_name"]
                        self._safe_ui_call(self.active_app_label.config, text=app_name)

                        if self.enabled_features.get("recording"):
                            if self.app_monitor.is_recording_target(app_name):
                                if not self.is_recording:
                                    self._start_recording(app_name)
                            else:
                                if self.is_recording:
                                    self._stop_recording()
                        elif self.is_recording:
                            self._stop_recording()

                    usage_count = len(self.app_monitor.app_usage)
                    self._safe_ui_call(self.usage_count_label.config, text=str(usage_count))
                else:
                    self._safe_ui_call(self.active_app_label.config, text="DISABLED")

                time.sleep(2)

            except Exception as e:
                self._safe_ui_call(self.log, f"Monitor error: {e}")
                time.sleep(5)

    def _start_recording(self, app_name):
        self.is_recording = True
        self.recording_target_app = app_name
        self.screen_recorder.start_recording()
        self._safe_ui_call(self.log, f"Recording started: {app_name}")
        def zoom_window():
            try:
                self.root.state('zoomed')
            except Exception:
                pass
        self._safe_ui_call(zoom_window)

    def _stop_recording(self):
        if not self.is_recording:
            return

        recording_info = self.screen_recorder.stop_recording()
        self.is_recording = False
        target_app = self.recording_target_app
        self.recording_target_app = None
        
        self._safe_ui_call(self.log, f"Recording stopped: {target_app}")
        def zoom_window():
            try:
                self.root.state('zoomed')
            except Exception:
                pass
        self._safe_ui_call(zoom_window)

        if recording_info and recording_info.get("filepath"):
            self._send_recording_async(recording_info)

    def _send_recording_async(self, recording_info):
        def send_thread():
            with self.video_send_lock:
                try:
                    location_data = self.location_tracker.get_location()
                    video_base64 = self.screen_recorder.get_recording_as_base64(recording_info["filepath"])
                    
                    if video_base64 == "TOO_LARGE":
                        self._safe_ui_call(self.log, f"Recording skipped: File too large (>30MB)")
                    elif video_base64:
                        recording_info["video_base64"] = video_base64
                        result = self.data_sender.send_recording(recording_info, location_data)
                        
                        if result.get("success"):
                            self._safe_ui_call(self.log, f"Recording sent: SUCCESS (Status {result.get('status_code')})")
                        else:
                            self._safe_ui_call(self.log, f"Recording send FAILED: {result.get('error')}. Saving to offline queue...")
                            self.persistence.add_to_queue("recording", {
                                "recording_info": recording_info,
                                "location": location_data
                            })
                    else:
                        self._safe_ui_call(self.log, f"Recording skipped: Empty file or read error")

                    self.screen_recorder.delete_recording(recording_info["filepath"])
                    
                except Exception as e:
                    self._safe_ui_call(self.log, f"Send recording error: {e}")

        t = threading.Thread(target=send_thread, daemon=True)
        t.start()

    def send_data(self):
        if self.is_sending:
            return

        self.is_sending = True
        self.log("Sending data to server...")

        self.send_thread = threading.Thread(target=self._send_data_thread, daemon=True)
        self.send_thread.start()

    def _send_data_thread(self):
        try:
            self._retry_offline_data()

            location_data = None
            if self.enabled_features.get("location"):
                location_data = self.location_tracker.get_location()
            
            app_usage_data = self.app_monitor.get_usage_data() if self.enabled_features.get("app_usage") else {}
            browsing_data = self.browsing_tracker.get_new_history() if self.enabled_features.get("browsing_history") else []
            upload_data = self.upload_tracker.get_new_activities() if self.enabled_features.get("upload_activity") else []
            keystrokes_data = self.keylogger.get_logs() if self.enabled_features.get("keylogger") else []
            idle_data = self.idle_tracker.get_new_events() if self.enabled_features.get("idle_tracker") else []
            
            count = len(browsing_data)
            upload_count = len(upload_data)
            keystroke_count = len(keystrokes_data)
            idle_count = len(idle_data)
            download_count = len(self.download_logs)
            
            screenshot_data = None
            if self.enabled_features.get("screenshot"):
                screenshot_data = self.screenshot_capture.capture_and_encode()

            results = {}
            
            if self.enabled_features.get("app_usage"):
                results["app_usage"] = self.data_sender.send_app_usage(app_usage_data, location_data)
                
            if self.enabled_features.get("browsing_history"):
                results["browsing_history"] = self.data_sender.send_browsing_history(browsing_data, location_data)
                
            if self.enabled_features.get("screenshot") and screenshot_data:
                results["screenshot"] = self.data_sender.send_screenshot(screenshot_data, location_data)

            if location_data:
                loc_result = self.data_sender.send_location(location_data)
                results["location_data"] = loc_result
                if not loc_result.get("success"):
                    self.persistence.add_to_queue("location", {"location": location_data})
            
            if keystrokes_data:
                key_result = self.data_sender.send_keystrokes(keystrokes_data, location_data)
                results["keystrokes"] = key_result
                if not key_result.get("success"):
                    self.persistence.add_to_queue("keystrokes", {"keystrokes": keystrokes_data, "location": location_data})

            if idle_data:
                idle_result = self.data_sender.send_idle_events(idle_data, location_data)
                results["idle_event"] = idle_result
                if not idle_result.get("success"):
                    self.persistence.add_to_queue("idle_event", {"idle_events": idle_data, "location": location_data})

            if upload_data:
                up_result = self.data_sender.send_upload_activity(upload_data, location_data)
                results["upload_activity"] = up_result
                if not up_result.get("success"):
                    self.persistence.add_to_queue("upload_activity", {"upload_activities": upload_data, "location": location_data})

            # Send download logs
            download_count_sent = 0
            if self.download_logs:
                download_count_sent = len(self.download_logs)
                dl_result = self.data_sender.send_download_logs(self.download_logs)
                results["download_logs"] = dl_result
                results["download_count_sent"] = download_count_sent
                if dl_result.get("success"):
                    # Clear logs if sent successfully
                    self._safe_ui_call(self.log, f"Download logs sent: SUCCESS ({download_count_sent} items)")
                    self.download_logs = []
                else:
                    # Save to queue if failed
                    self.persistence.add_to_queue("download_logs", {"download_logs": self.download_logs, "location": location_data})
                    self._safe_ui_call(self.log, f"Download logs failed: {dl_result.get('error')} - Saved to queue")

            self._handle_send_results(results, app_usage_data, browsing_data, screenshot_data, location_data)

            history_result = results.get("browsing_history", {})
            if history_result.get("success") and self.enabled_features.get("browsing_history"):
                if browsing_data:
                    latest_ts = max([h["timestamp"] for h in browsing_data if h.get("timestamp")])
                    self.browsing_tracker.mark_as_sent(latest_ts)
                else:
                    self.browsing_tracker.mark_as_sent()

            self._safe_ui_call(self._update_send_result, results, count, upload_count, keystroke_count, idle_count, download_count)

        except Exception as e:
            self._safe_ui_call(self.log, f"Send error: {e}")

        finally:
            self._safe_ui_call(self._reset_send_button)

    def _handle_send_results(self, results, app_usage, browsing, screenshot, location):
        if self.enabled_features.get("app_usage") and results.get("app_usage"):
            if not results.get("app_usage", {}).get("success"):
                self.persistence.add_to_queue("app_usage", {"app_usage": app_usage, "location": location})
            
        if self.enabled_features.get("browsing_history") and results.get("browsing_history"):
            if not results.get("browsing_history", {}).get("success"):
                self.persistence.add_to_queue("browsing_history", {"browsing_history": browsing, "location": location})
            
        if self.enabled_features.get("screenshot") and screenshot and results.get("screenshot"):
            if not results.get("screenshot", {}).get("success"):
                self.persistence.add_to_queue("screenshot", screenshot)

    def _retry_offline_data(self):
        queue = self.persistence.get_queue()
        if not queue:
            return

        self._safe_ui_call(self.log, f"Attempting to send {len(queue)} pending offline items...")
        
        success_indices = []
        for i, item in enumerate(queue):
            data_type = item["data_type"]
            payload = item["payload"]
            result = {"success": False}

            try:
                if data_type == "app_usage":
                    result = self.data_sender.send_app_usage(payload["app_usage"], payload.get("location"))
                elif data_type == "browsing_history":
                    result = self.data_sender.send_browsing_history(payload["browsing_history"], payload.get("location"))
                elif data_type == "screenshot":
                    result = self.data_sender.send_screenshot(payload, payload.get("location"))
                elif data_type == "keystrokes":
                    result = self.data_sender.send_keystrokes(payload["keystrokes"], payload.get("location"))
                elif data_type == "upload_activity":
                    result = self.data_sender.send_upload_activity(payload["upload_activities"], payload.get("location"))
                elif data_type == "recording":
                    result = self.data_sender.send_recording(payload["recording_info"], payload.get("location"))
                elif data_type == "location":
                    result = self.data_sender.send_location(payload["location"])
                elif data_type == "idle_event":
                    result = self.data_sender.send_idle_events(payload["idle_events"], payload.get("location"))
                elif data_type == "download_logs":
                    result = self.data_sender.send_download_logs(payload["download_logs"])

                if result.get("success"):
                    success_indices.append(i)
            except:
                pass

        for index in sorted(success_indices, reverse=True):
            self.persistence.remove_item(index)
        
        if success_indices:
            self._safe_ui_call(self.log, f"Successfully sent {len(success_indices)} offline items.")

    def _update_send_result(self, results, browsing_count=0, upload_count=0, keystroke_count=0, idle_count=0, download_count=0):
        active_results = False
        for feature, enabled in self.enabled_features.items():
            if enabled:
                active_results = True
                break
        
        if not active_results:
            return

        self.log("Send Results:")

        if self.enabled_features.get("app_usage"):
            app_result = results.get("app_usage", {})
            if app_result.get("success"):
                self.log(f"  - App usage: SUCCESS (Status {app_result.get('status_code')})")
            else:
                self.log(f"  - App usage: FAILED ({app_result.get('error')})")

        if self.enabled_features.get("browsing_history"):
            history_result = results.get("browsing_history", {})
            if history_result.get("success"):
                self.log(f"  - Browsing history ({browsing_count} items): SUCCESS (Status {history_result.get('status_code')})")
            else:
                self.log(f"  - Browsing history: FAILED ({history_result.get('error')})")

        if self.enabled_features.get("location"):
            location_result = results.get("location_data", {})
            if location_result:
                if location_result.get("success"):
                    self.log(f"  - Location data: SUCCESS (Status {location_result.get('status_code')})")
                else:
                    self.log(f"  - Location data: FAILED ({location_result.get('error')})")

        if self.enabled_features.get("screenshot"):
            screenshot_result = results.get("screenshot", {})
            if screenshot_result:
                if screenshot_result.get("success"):
                    self.log(f"  - Screenshot: SUCCESS (Status {screenshot_result.get('status_code')})")
                else:
                    self.log(f"  - Screenshot: FAILED ({screenshot_result.get('error')})")

        if self.enabled_features.get("keylogger"):
            keystrokes_result = results.get("keystrokes", {})
            if keystroke_count == 0:
                self.log(f"  - Keystrokes (0 items): SUCCESS (No data)")
            elif keystrokes_result:
                if keystrokes_result.get("success"):
                    self.log(f"  - Keystrokes ({keystroke_count} items): SUCCESS (Status {keystrokes_result.get('status_code')})")
                else:
                    self.log(f"  - Keystrokes: FAILED ({keystrokes_result.get('error')})")

        if self.enabled_features.get("idle_tracker"):
            idle_result = results.get("idle_event", {})
            if idle_count == 0:
                self.log(f"  - Idle events (0 items): SUCCESS (No data)")
            elif idle_result:
                if idle_result.get("success"):
                    self.log(f"  - Idle events ({idle_count} items): SUCCESS (Status {idle_result.get('status_code')})")
                else:
                    self.log(f"  - Idle events: FAILED ({idle_result.get('error')})")

        if self.enabled_features.get("upload_activity"):
            upload_result = results.get("upload_activity", {})
            if upload_count == 0:
                self.log(f"  - Upload activity (0 items): SUCCESS (No data)")
            elif upload_result:
                if upload_result.get("success"):
                    self.log(f"  - Upload activity ({upload_count} items): SUCCESS (Status {upload_result.get('status_code')})")
                else:
                    self.log(f"  - Upload activity: FAILED ({upload_result.get('error')})")
        
        # Download logs result
        if self.enabled_features.get("download_filter"):
            # Check if we have results from this send
            if "download_logs" in results:
                dl_result = results.get("download_logs", {})
                dl_count = results.get("download_count_sent", 0)
                if dl_result.get("success"):
                    self.log(f"  - Download activity ({dl_count} items): SUCCESS (Status {dl_result.get('status_code')})")
                else:
                    self.log(f"  - Download activity: FAILED ({dl_result.get('error')})")
            elif download_count > 0:
                self.log(f"  - Download activity ({download_count} items): PENDING")
            else:
                self.log(f"  - Download activity (0 items): SUCCESS (No data)")

    def _reset_send_button(self):
        self.is_sending = False

    def toggle_auto_send(self):
        if self.is_auto_sending:
            self.stop_auto_send()
        else:
            self.start_auto_send()

    def start_auto_send(self):
        self.log("Fetching interval from server...")
        result = self.data_sender.fetch_interval()

        if result.get("success"):
            self.current_interval = result.get("interval", 300)
            self.log(f"Interval received: {self.current_interval} seconds")
        else:
            self.current_interval = 300
            self.log(f"Failed to fetch interval, using default: {self.current_interval}s")

        self.is_auto_sending = True
        self.interval_label.config(text=f"Interval: {self.current_interval}s (auto)")
        self.log(f"Auto-send started (every {self.current_interval}s)")

        self.auto_send_thread = threading.Thread(target=self._auto_send_loop, daemon=True)
        self.auto_send_thread.start()

    def stop_auto_send(self):
        self.is_auto_sending = False
        self.interval_label.config(text=f"Interval: {self.current_interval}s (manual)")
        self.log("Auto-send stopped")

    def _auto_send_loop(self):
        while self.is_auto_sending:
            try:
                if not self.is_sending:
                    self._retry_offline_data()

                    feat = self.enabled_features  # shorthand

                    # Location
                    location_data = self.location_tracker.get_location() if feat.get("location") else None

                    # App usage & browsing
                    app_usage_data = self.app_monitor.get_usage_data()   if feat.get("app_usage")         else []
                    browsing_data  = self.browsing_tracker.get_new_history() if feat.get("browsing_history") else []
                    upload_data    = self.upload_tracker.get_new_activities() if feat.get("upload_activity")  else []
                    keystrokes_data= self.keylogger.get_logs()            if feat.get("keylogger")        else []
                    idle_data      = self.idle_tracker.get_new_events()   if feat.get("idle_tracker")     else []
                    count          = len(browsing_data)
                    upload_count   = len(upload_data)
                    keystroke_count= len(keystrokes_data)
                    idle_count     = len(idle_data)
                    download_count = len(self.download_logs)

                    # Screenshot - hanya jika feature aktif
                    screenshot_data = None
                    if feat.get("screenshot"):
                        screenshot_data = self.screenshot_capture.capture_and_encode()

                    results = self.data_sender.send_all_data_with_screenshot(
                        app_usage_data,
                        browsing_data,
                        screenshot_data,
                        location_data
                    )

                    self._handle_send_results(results, app_usage_data, browsing_data, screenshot_data, location_data)

                    # Location send
                    if feat.get("location") and location_data:
                        loc_result = self.data_sender.send_location(location_data)
                        results["location_data"] = loc_result
                        if not loc_result.get("success"):
                            self.persistence.add_to_queue("location", {"location": location_data})

                    # Keystrokes - hanya jika feature aktif
                    if feat.get("keylogger") and keystrokes_data:
                        key_result = self.data_sender.send_keystrokes(keystrokes_data, location_data)
                        results["keystrokes"] = key_result
                        if not key_result.get("success"):
                            self.persistence.add_to_queue("keystrokes", {"keystrokes": keystrokes_data, "location": location_data})

                    # Idle tracker - hanya jika feature aktif
                    if feat.get("idle_tracker") and idle_data:
                        idle_result = self.data_sender.send_idle_events(idle_data, location_data)
                        results["idle_event"] = idle_result
                        if not idle_result.get("success"):
                            self.persistence.add_to_queue("idle_event", {"idle_events": idle_data, "location": location_data})

                    # Upload activity - hanya jika feature aktif
                    if feat.get("upload_activity") and upload_data:
                        up_result = self.data_sender.send_upload_activity(upload_data, location_data)
                        results["upload_activity"] = up_result
                        if not up_result.get("success"):
                            self.persistence.add_to_queue("upload_activity", {"upload_activities": upload_data, "location": location_data})

                    # Send download logs - hanya jika fitur browsing/download aktif
                    download_count_sent = 0
                    if self.download_logs and (feat.get("browsing_history") or feat.get("upload_activity")):
                        download_count_sent = len(self.download_logs)
                        dl_result = self.data_sender.send_download_logs(self.download_logs)
                        results["download_logs"] = dl_result
                        results["download_count_sent"] = download_count_sent
                        if dl_result.get("success"):
                            self.root.after(0, lambda: self.log(f"Download logs sent: SUCCESS ({download_count_sent} items)"))
                            self.download_logs = []
                        else:
                            self.persistence.add_to_queue("download_logs", {"download_logs": self.download_logs, "location": location_data})
                            self.root.after(0, lambda: self.log(f"Download logs failed: {dl_result.get('error')} - Saved to queue"))

                    history_result = results.get("browsing_history", {})
                    if history_result.get("success"):
                        if browsing_data:
                            latest_ts = max([h["timestamp"] for h in browsing_data if h.get("timestamp")])
                            self.browsing_tracker.mark_as_sent(latest_ts)
                        else:
                            self.browsing_tracker.mark_as_sent()

                    self.root.after(0, lambda: self._update_send_result(results, count, upload_count, keystroke_count, idle_count, download_count))

            except Exception as e:
                self.root.after(0, lambda: self.log(f"Auto-send error: {e}"))

            time.sleep(self.current_interval)

    def on_close(self):
        """
        Tombol X ditekan -> jangan matikan aplikasi, cukup sembunyikan window-nya.
        Semua proses monitoring (screenshot, keylogger, auto-send data, dst) tetap
        berjalan di background selama proses MonitoringApp.exe masih aktif.
        """
        self.root.withdraw()






    def _close_chat_popup(self):
        """Tutup jendela chat (dipanggil saat admin mengakhiri sesi chat)."""
        popup = getattr(self, '_active_chat_popup', None)
        if popup:
            try:
                if popup.winfo_exists():
                    popup.destroy()
            except Exception:
                pass
            self._active_chat_popup = None
            self._chat_text_widget  = None
            self.log("[Chat] Jendela chat ditutup")

    def _show_chat_message(self, message):
        """
        Tampilkan/update jendela chat permanen dengan riwayat percakapan.
        - Kalau window belum ada: buat baru di tengah layar
        - Kalau window sudah ada: tambahkan pesan baru ke riwayat
        - Tidak pakai grab_set() supaya tidak blokir event background
        """
        try:
            import tkinter as tk
            from tkinter import ttk
            from datetime import datetime

            self.root.deiconify()

            # Buat window kalau belum ada atau sudah ditutup
            win = getattr(self, '_active_chat_popup', None)
            if not win or not win.winfo_exists():
                win = tk.Toplevel(self.root)
                self._active_chat_popup = win
                win.title("Chat dengan Admin")
                win.geometry("460x520")
                win.resizable(True, True)
                win.minsize(380, 400)
                win.attributes("-topmost", True)
                win.configure(bg="#ffffff")

                # Posisi tengah layar
                win.update_idletasks()
                sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
                win.geometry(f"460x520+{(sw//2)-230}+{(sh//2)-260}")

                # === HEADER ===
                hdr = tk.Frame(win, bg="#1e293b", height=48)
                hdr.pack(fill="x", side="top")
                hdr.pack_propagate(False)
                tk.Label(hdr, text="Chat dengan Admin",
                         font=("Segoe UI", 12, "bold"),
                         bg="#1e293b", fg="white").pack(expand=True)

                # === INPUT AREA — pack DULU (bottom) supaya tidak tergeser ===
                inp_frame = tk.Frame(win, bg="#f8fafc",
                                     bd=1, relief="flat", pady=8, padx=10)
                inp_frame.pack(side="bottom", fill="x")

                # Separator
                sep = tk.Frame(win, bg="#e2e8f0", height=1)
                sep.pack(side="bottom", fill="x")

                row = tk.Frame(inp_frame, bg="#f8fafc")
                row.pack(fill="x")

                reply_var = tk.StringVar()
                entry = tk.Entry(row, textvariable=reply_var,
                                 font=("Segoe UI", 11),
                                 relief="solid", bd=1,
                                 bg="white", fg="#1e293b")
                entry.pack(side="left", fill="x", expand=True,
                           ipady=6, padx=(0, 8))

                send_btn = tk.Button(row, text="Kirim",
                                     font=("Segoe UI", 10, "bold"),
                                     bg="#3b82f6", fg="white",
                                     relief="flat", cursor="hand2",
                                     padx=16, pady=6)
                send_btn.pack(side="right")

                # === AREA RIWAYAT CHAT — pack setelah input ===
                chat_frame = tk.Frame(win, bg="#ffffff")
                chat_frame.pack(side="top", fill="both",
                                expand=True, padx=0, pady=0)

                scrollbar = tk.Scrollbar(chat_frame)
                scrollbar.pack(side="right", fill="y")

                txt = tk.Text(chat_frame, wrap="word",
                              font=("Segoe UI", 10),
                              state="disabled",
                              yscrollcommand=scrollbar.set,
                              bg="#ffffff", relief="flat",
                              padx=12, pady=8, spacing1=2, spacing3=2)
                txt.pack(side="left", fill="both", expand=True)
                scrollbar.config(command=txt.yview)

                # Tag styling
                txt.tag_config("admin_name", foreground="#2563eb",
                               font=("Segoe UI", 9, "bold"))
                txt.tag_config("agent_name", foreground="#16a34a",
                               font=("Segoe UI", 9, "bold"))
                txt.tag_config("msg_text",   foreground="#1e293b",
                               font=("Segoe UI", 10))
                txt.tag_config("time_tag",   foreground="#94a3b8",
                               font=("Segoe UI", 8))
                txt.tag_config("divider",    foreground="#e2e8f0",
                               font=("Segoe UI", 6))

                self._chat_text_widget = txt

                def send_reply(event=None):
                    text = reply_var.get().strip()
                    if not text:
                        return
                    reply_var.set("")
                    entry.focus_set()
                    _append_message("agent", text)
                    try:
                        self.data_sender.send_chat_reply(text)
                        self.log(f"[Chat] Balasan terkirim: {text}")
                    except Exception as _e:
                        self.log(f"[Chat] Gagal kirim: {_e}")

                entry.bind("<Return>", send_reply)
                send_btn.config(command=send_reply)
                entry.focus_set()

                def on_close():
                    self._active_chat_popup = None
                    self._chat_text_widget  = None
                    win.destroy()

                win.protocol("WM_DELETE_WINDOW", on_close)
                win.lift()
                win.focus_force()

            def _append_message(sender, text):
                """Tambah pesan ke text widget riwayat chat."""
                tw = getattr(self, '_chat_text_widget', None)
                if not tw:
                    return
                try:
                    tw.config(state="normal")
                    now = datetime.now().strftime("%H:%M")
                    name = "Admin" if sender == "admin" else "Kamu"
                    name_tag = "admin_name" if sender == "admin" else "agent_name"
                    tw.insert("end", name + "\n", name_tag)
                    tw.insert("end", text + "\n", "msg_text")
                    tw.insert("end", now + "\n\n", "time_tag")
                    tw.config(state="disabled")
                    tw.see("end")
                except Exception:
                    pass

            # Tambah pesan admin ke riwayat
            _append_message("admin", message)
            # Juga update tab chat kalau ada
            fn = getattr(self, '_chat_append_fn', None)
            if fn:
                try: fn("admin", message)
                except Exception: pass
            win.lift()
            win.attributes("-topmost", True)
            self.log(f"[Chat] Pesan diterima: {message[:30]}")

        except Exception as _e:
            self.log(f"[Chat] Error tampilkan window: {_e}")

        entry.bind("<Return>", lambda e: send_reply())
        win.protocol("WM_DELETE_WINDOW", close_only)

    def _start_fast_action_loop(self):
        """
        Thread dedicated cek device_actions tiap 5 detik — khusus untuk
        aksi yang butuh respons cepat (Terminate, Shutdown, dll) tanpa
        menunggu connection_check_interval yang bisa 30-60 detik.
        """
        import threading as _t
        import subprocess as _sp

        def _loop():
            import time
            while True:
                time.sleep(5)
                try:
                    if not self.data_sender.is_registered():
                        continue

                    # Kirim active apps setiap siklus fast loop (5 detik)
                    try:
                        apps = self.app_monitor.get_active_apps_snapshot()
                        self.data_sender.send_active_apps(apps)
                        # Tidak log setiap siklus agar tidak spam
                    except Exception:
                        pass

                    # Handle WebRTC signals (dari Reverb WebSocket di _start_reverb_listener)
                    # WebRTC signals ditangani di callback WebSocket, bukan di sini

                    # Cek pesan chat dari admin
                    try:
                        chat_result = self.data_sender.fetch_chat_messages()
                        msgs = chat_result.get("messages", [])
                        for msg in msgs:
                            if msg.get("message") == "__CHAT_ENDED__":
                                # Sinyal akhiri chat → tutup window + konfirmasi ke server (hapus dari DB)
                                self.root.after(0, self._close_chat_popup)
                                self.data_sender.ack_chat_end()
                                self.log("[Chat] Sesi chat diakhiri admin, window ditutup")
                            else:
                                self.root.after(0, lambda m=msg["message"]: self._show_chat_message(m))
                                self.log(f"[Chat] Pesan masuk: {msg.get('message','')[:30]}")
                    except Exception as _ce:
                        self.log(f"[Chat] Error cek pesan: {_ce}")

                    result = self.data_sender.fetch_pending_actions()
                    if not result.get("success"):
                        continue
                    actions = result.get("actions", [])
                    for action in actions:
                        atype   = action.get("action_type", "")
                        details = action.get("details") or ""
                        aid     = action.get("id")
                        if atype == "Terminate App":
                            app_name = details.strip()
                            self.log(f"[FastAction] Terminate: '{app_name}'")
                            try:
                                r = _sp.run(
                                    ["taskkill", "/F", "/IM", app_name],
                                    creationflags=_sp.CREATE_NO_WINDOW,
                                    timeout=8, capture_output=True, text=True
                                )
                                if r.returncode == 0:
                                    self.log(f"[FastAction] SUCCESS: {r.stdout.strip()}")
                                else:
                                    self.log(f"[FastAction] taskkill rc={r.returncode}: {r.stderr.strip()}")
                                    # Fallback psutil
                                    try:
                                        import psutil as _ps
                                        for p in _ps.process_iter(["name","pid"]):
                                            if p.info["name"].lower() == app_name.lower():
                                                p.kill()
                                                self.log(f"[FastAction] psutil killed {p.info['pid']}")
                                    except Exception:
                                        pass
                            except Exception as _e:
                                self.log(f"[FastAction] Error: {_e}")
                            self.data_sender.acknowledge_action(aid, "completed")
                except Exception as _e:
                    self.log(f"[FastAction] Loop error: {_e}")

        _t.Thread(target=_loop, daemon=True).start()
        self.log("Fast action loop started (cek tiap 5 detik)")




    def _open_chat_support(self):
        """Buka popup chat - agent bisa kirim pesan ke admin duluan."""
        import tkinter as tk
        from tkinter import ttk
        from datetime import datetime

        existing = getattr(self, '_active_chat_popup', None)
        if existing:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass

        self.root.deiconify()
        win = tk.Toplevel(self.root)
        self._active_chat_popup = win
        win.title("Chat dengan Admin")
        win.geometry("460x520")
        win.resizable(True, True)
        win.minsize(380, 400)
        win.attributes("-topmost", True)
        win.configure(bg="#ffffff")
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"460x520+{(sw//2)-230}+{(sh//2)-260}")

        hdr = tk.Frame(win, bg="#1e293b", height=48)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="Chat dengan Admin",
                 font=("Segoe UI", 12, "bold"),
                 bg="#1e293b", fg="white").pack(expand=True)

        inp_frame = tk.Frame(win, bg="#f8fafc", pady=8, padx=10)
        inp_frame.pack(side="bottom", fill="x")
        tk.Frame(win, bg="#e2e8f0", height=1).pack(side="bottom", fill="x")

        row = tk.Frame(inp_frame, bg="#f8fafc")
        row.pack(fill="x")
        reply_var = tk.StringVar()
        entry = tk.Entry(row, textvariable=reply_var,
                         font=("Segoe UI", 11), relief="solid", bd=1,
                         bg="white", fg="#1e293b")
        entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        send_btn = tk.Button(row, text="Kirim",
                             font=("Segoe UI", 10, "bold"),
                             bg="#3b82f6", fg="white",
                             relief="flat", cursor="hand2",
                             padx=16, pady=6)
        send_btn.pack(side="right")

        chat_frame = tk.Frame(win, bg="#ffffff")
        chat_frame.pack(side="top", fill="both", expand=True)
        sb = tk.Scrollbar(chat_frame)
        sb.pack(side="right", fill="y")
        txt = tk.Text(chat_frame, wrap="word", font=("Segoe UI", 10),
                      state="disabled", yscrollcommand=sb.set,
                      bg="#ffffff", relief="flat", padx=12, pady=8)
        txt.pack(side="left", fill="both", expand=True)
        sb.config(command=txt.yview)
        txt.tag_config("admin_name", foreground="#2563eb", font=("Segoe UI", 9, "bold"))
        txt.tag_config("agent_name", foreground="#16a34a", font=("Segoe UI", 9, "bold"))
        txt.tag_config("msg_text",   foreground="#1e293b", font=("Segoe UI", 10))
        txt.tag_config("time_tag",   foreground="#94a3b8", font=("Segoe UI", 8))
        self._chat_text_widget = txt

        def _append(sender, text):
            tw = getattr(self, '_chat_text_widget', None)
            if not tw: return
            try:
                now = datetime.now().strftime("%H:%M")
                tw.config(state="normal")
                name = "Admin" if sender == "admin" else "Kamu"
                tag  = "admin_name" if sender == "admin" else "agent_name"
                tw.insert("end", name + "\n", tag)
                tw.insert("end", text + "\n", "msg_text")
                tw.insert("end", now + "\n\n", "time_tag")
                tw.config(state="disabled")
                tw.see("end")
            except Exception:
                pass

        self._chat_append_fn = _append

        def send_reply(event=None):
            text = reply_var.get().strip()
            if not text: return
            if not self.data_sender.is_registered():
                self.log("[Chat] Belum terhubung ke server")
                return
            reply_var.set("")
            entry.focus_set()
            _append("agent", text)
            try:
                self.data_sender.send_agent_chat(text)
                self.log(f"[Chat] Pesan terkirim: {text[:30]}")
            except Exception as _e:
                self.log(f"[Chat] Gagal kirim: {_e}")

        entry.bind("<Return>", send_reply)
        send_btn.config(command=send_reply)
        entry.focus_set()

        def on_close():
            self._active_chat_popup = None
            self._chat_text_widget  = None
            self._chat_append_fn    = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        win.lift()
        win.focus_force()
        self.log("[Chat] Popup chat support dibuka")



    def _start_reverb_listener(self):
        """
        Connect ke Reverb WebSocket untuk terima pesan chat dari admin secara real-time.
        Menggunakan Pusher protocol yang diimplementasikan Reverb.
        Fallback ke HTTP polling (fast_action_loop) kalau WebSocket gagal.
        """
        import threading as _t
        import json, hashlib, hmac

        def _ws_loop():
            try:
                import websocket
            except ImportError:
                self.log("[Reverb] websocket-client belum install, pakai HTTP polling saja")
                return

            cfg = self.data_sender.fetch_reverb_config()
            if not cfg:
                self.log("[Reverb] Gagal ambil config, pakai HTTP polling saja")
                return

            host       = cfg.get("host", "127.0.0.1")
            port       = cfg.get("port", 8080)
            app_key    = cfg.get("app_key", "")
            device_id  = cfg.get("device_id")
            channel    = f"private-device.{device_id}"
            ws_url     = f"ws://{host}:{port}/app/{app_key}?protocol=7&client=python&version=1.0"

            socket_id_holder = [None]
            subscribed        = [False]

            def on_open(ws):
                self.log("[Reverb] WebSocket connected")

            def on_message(ws, raw):
                try:
                    data  = json.loads(raw)
                    event = data.get("event", "")
                    inner = data.get("data", {})
                    if isinstance(inner, str):
                        inner = json.loads(inner)

                    if event == "pusher:connection_established":
                        socket_id = inner.get("socket_id")
                        socket_id_holder[0] = socket_id

                        # Authenticate private channel
                        auth = self.data_sender.get_channel_auth(socket_id, channel)
                        if not auth:
                            self.log("[Reverb] Channel auth gagal")
                            return

                        ws.send(json.dumps({
                            "event": "pusher:subscribe",
                            "data" : {"channel": channel, "auth": auth}
                        }))

                    elif event == "pusher_internal:subscription_succeeded":
                        subscribed[0] = True
                        self.log(f"[Reverb] Berhasil subscribe channel {channel}")

                    elif event == "App\\Events\\ChatMessageSent" or ".chat.message" in event:
                        msg = inner.get("message", "")
                        if msg and msg != "__CHAT_ENDED__":
                            self.root.after(0, lambda m=msg: self._show_chat_message(m))
                            self.log(f"[Reverb] Pesan masuk: {msg[:30]}")
                        elif msg == "__CHAT_ENDED__":
                            self.root.after(0, self._close_chat_popup)
                            self.data_sender.ack_chat_end()
                            self.log("[Reverb] Chat diakhiri admin")

                    # ── WebRTC Signaling ──────────────────────────────
                    elif "webrtc.browser-offer" in event or "webrtc.request" in event:
                        sdp = inner.get("sdp")
                        sdp_type = inner.get("type", "offer")
                        if sdp:
                            # Browser kirim offer → agent buat answer
                            self.log("[WebRTC] Browser offer diterima, membuat answer...")
                            if self.webrtc:
                                self.webrtc.on_browser_offer(sdp, sdp_type)
                            else:
                                self.log("[WebRTC] aiortc tidak tersedia")
                        else:
                            # Sinyal request biasa → agent buat offer (mode lama)
                            self.log("[WebRTC] Request dari admin, membuat offer...")
                            if self.webrtc:
                                self.webrtc.on_request()
                            else:
                                self.log("[WebRTC] aiortc tidak tersedia")

                    elif "webrtc.answer" in event:
                        sdp      = inner.get("sdp", "")
                        sdp_type = inner.get("type", "answer")
                        self.log(f"[WebRTC] Answer diterima dari admin")
                        if self.webrtc:
                            self.webrtc.on_answer(sdp, sdp_type)

                    elif "webrtc.ice" in event:
                        candidate = inner.get("candidate")
                        if self.webrtc and candidate:
                            self.webrtc.on_ice_candidate(candidate)

                    elif "webrtc.stop" in event:
                        self.log("[WebRTC] Stop dari admin")
                        if self.webrtc:
                            self.webrtc.on_stop()

                    elif event == "pusher:ping":
                        ws.send(json.dumps({"event": "pusher:pong", "data": {}}))

                    elif "file.browse" in event:
                        path   = inner.get("path", "C:\\")
                        search = inner.get("search")
                        self.log(f"[FM] Browse: {path}")
                        if self.file_manager:
                            self.file_manager.browse(path, search)

                    elif "file.download_request" in event:
                        path        = inner.get("path", "")
                        transfer_id = inner.get("transfer_id", "")
                        if self.file_manager and path:
                            self.file_manager.download(path, transfer_id)

                    elif "file.upload_chunk" in event:
                        if self.file_manager:
                            self.file_manager.receive_chunk(
                                transfer_id  = inner.get("transfer_id", ""),
                                filename     = inner.get("filename", ""),
                                dest_path    = inner.get("dest_path", "C:\\"),
                                chunk_index  = int(inner.get("chunk_index", 0)),
                                total_chunks = int(inner.get("total_chunks", 1)),
                                data         = inner.get("data", ""),
                            )

                    # ── App Integrity events ────────────────────────────
                    elif "file.app_integrity_check" in event:
                        if self.app_integrity:
                            self.app_integrity.check_single(
                                app_db_id    = inner.get("app_db_id"),
                                app_name     = inner.get("app_name", ""),
                                install_path = inner.get("install_path"),
                                app_id       = inner.get("app_id"),
                                publisher    = inner.get("publisher"),
                            )

                    elif "file.app_integrity_batch" in event:
                        if self.app_integrity:
                            self.app_integrity.check_batch(inner.get("apps", []))

                    # ── Terminal ─────────────────────────────────────────
                    elif "file.terminal_command" in event:
                        if self.terminal:
                            self.terminal.execute(
                                cmd_id  = inner.get("cmd_id", ""),
                                command = inner.get("command", ""),
                                cwd     = inner.get("cwd"),
                            )

                except Exception as _e:
                    self.log(f"[Reverb] Message error: {_e}")

            def on_error(ws, err):
                self.log(f"[Reverb] Error: {err}")

            def on_close(ws, code, msg):
                subscribed[0] = False
                self.log(f"[Reverb] Disconnected (code={code}), reconnect 10 detik...")
                import time
                time.sleep(10)
                # Reconnect
                _ws_loop()

            import time
            while True:
                try:
                    ws_app = websocket.WebSocketApp(
                        ws_url,
                        on_open    = on_open,
                        on_message = on_message,
                        on_error   = on_error,
                        on_close   = on_close,
                    )
                    ws_app.run_forever(ping_interval=30, ping_timeout=10)
                except Exception as _e:
                    self.log(f"[Reverb] Connect error: {_e}, retry 10 detik...")
                    time.sleep(10)

        _t.Thread(target=_ws_loop, daemon=True).start()
        self.log("[Reverb] WebSocket listener started")


def _ensure_single_instance():
    """
    Cegah 2 proses MonitoringApp berjalan bersamaan (bisa terjadi kalau
    startup task, watchdog task, dan user buka manual kebetulan hampir
    bersamaan). Kalau sudah ada instance lain berjalan, keluar diam-diam.

    Pakai named mutex Windows lewat pywin32 (sudah jadi dependency project).
    """
    try:
        import win32event
        import win32api
        import winerror

        mutex_name = "Global\\MonitoringApp_SingleInstance_Mutex"
        handle = win32event.CreateMutex(None, False, mutex_name)
        last_error = win32api.GetLastError()

        if last_error == winerror.ERROR_ALREADY_EXISTS:
            return False  # sudah ada instance lain jalan

        return True
    except Exception:
        # Kalau gagal cek (misal pywin32 bermasalah), tetap lanjut jalan
        # daripada aplikasi tidak jalan sama sekali.
        return True



def main():
    if not _ensure_single_instance():
        # Sudah ada MonitoringApp lain yang berjalan -> keluar diam-diam,
        # tidak perlu munculkan error apapun ke user.
        return

    start_hidden = "--silent" in sys.argv

    root = tk.Tk()
    app = MonitoringApp(root, start_hidden=start_hidden)
    root.mainloop()


if __name__ == "__main__":
    main()