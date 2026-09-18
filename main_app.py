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

from usb_monitor import USBMonitorThread
from browser_download_monitor import BrowserDownloadMonitor


class MonitoringApp:
    def __init__(self, root, start_hidden=False):
        self.root = root
        self.start_hidden = start_hidden
        self.root.title("Monitoring Data - System Monitor")

        self.root.state('zoomed')
        self.root.resizable(True, True)
        self.root.minsize(900, 600)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.app_monitor = AppMonitor(interval=5)
        self.browsing_tracker = BrowsingHistoryTracker(days_limit=7)
        self.data_sender = DataSender()
        self.remote_control = RemoteControlAgent(self.data_sender, log_callback=self.log)
        self._active_chat_popup = None

        # WebRTC streamer (opsional)
        self.webrtc = WebRtcStreamer(
            data_sender=self.data_sender,
            log_callback=self.log,
            fps=15
        ) if WEBRTC_AVAILABLE else None
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

        self._chat_text_widget = None
        self._chat_append_fn = None
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
        self.last_screenshot_time = 0
        self.is_recording = False
        self.recording_target_app = None

        # Throttling flags
        self._reverb_connected_flag = [False]
        self._remote_frame_thread_running = False
        self._processed_chat_ids = set()

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
            "download_filter": False,
            "hide_page": False
        }

        self.blocked_apps = []
        self.url_filter_mode = "off"
        self.url_list = []
        self.usb_block_mode = "off"
        self.usb_list = []
        self.block_install_mode = "off"
        self.block_install_list = []
        self.file_sync_data = []

        self.file_versions_path = get_app_data_path("file_versions.json")
        self.file_versions = self._load_file_versions()

        self._denied_pids = set()
        self.download_logs = []

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
            try:
                if len(self.download_logs) >= 500:
                    self.download_logs = self.download_logs[-400:]
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
                self.root.after(100, self.root.withdraw)
            self.root.after(100, self.start_all_services)

    # =========================================================
    # REGISTRATION DIALOG
    # =========================================================
    def show_registration_dialog(self):
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
                    self.root.after(0, lambda: status_label.config(
                        text=f"Failed: URL is incorrect!", fg="red"))

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

    # =========================================================
    # CONFIG
    # =========================================================
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
            self._sync_files()

        if "download_filter" in config:
            df = config["download_filter"]
            mode = df.get("mode", "off").lower()
            flist = [clean_item(ext).replace(".", "").lower() for ext in df.get("list", [])]
            self.browser_download_monitor.set_config(mode, flist)

        if "hide_page" in config:
            hp = config["hide_page"]
            self._apply_hide_page_policy(hp.get("enabled", False), hp.get("pages", []))

        new_features = config.get("features", {})
        for feature in self.enabled_features.keys():
            enabled = new_features.get(feature, False)
            old_status = self.enabled_features[feature]
            self.enabled_features[feature] = enabled

            if old_status != enabled:
                self._toggle_feature(feature, enabled)
            else:
                self._update_feature_ui(feature, enabled)

    # =========================================================
    # WINDOWS USERS
    # =========================================================
    def _collect_windows_users(self) -> list:
        import getpass
        users = []
        current_user = getpass.getuser().lower()

        SKIP_USERS = {
            'defaultaccount', 'wdagutilityaccount', 'wsiaccount',
            'wsi account', 'guest',
        }

        current_email   = None
        current_is_msft = False
        current_is_admin = None
        try:
            from get_windows_account import get_windows_user_info
            info = get_windows_user_info()
            current_email    = info.get('email') or None
            current_is_msft  = bool(info.get('is_microsoft_account'))
            current_is_admin = bool(info.get('is_admin', False))
        except Exception:
            pass

        try:
            import win32net, win32netcon, win32security
            UF_ACCOUNTDISABLE = 0x0002
            resume = 0
            while True:
                data, total, resume = win32net.NetUserEnum(
                    None, 2, win32netcon.FILTER_NORMAL_ACCOUNT, resume
                )
                for u in data:
                    uname = u.get('name', '')
                    if not uname:
                        continue
                    if u.get('flags', 0) & UF_ACCOUNTDISABLE:
                        continue
                    if uname.lower() in SKIP_USERS:
                        continue

                    is_admin = False
                    try:
                        groups, _, _ = win32net.NetUserGetLocalGroups(None, uname, 0)
                        is_admin = any(g.lower() in ('administrators', 'administrator') for g in groups)
                    except Exception:
                        is_admin = u.get('priv', 0) == win32netcon.USER_PRIV_ADMIN

                    sid_str = None
                    try:
                        sid, _, _ = win32security.LookupAccountName(None, uname)
                        sid_str = win32security.ConvertSidToStringSid(sid)
                    except Exception:
                        pass

                    is_current = uname.lower() == current_user
                    email    = current_email if is_current else None
                    acc_type = ('microsoft' if current_is_msft else 'local') if is_current else 'local'

                    if is_current and current_is_admin is not None:
                        final_admin = current_is_admin
                    else:
                        final_admin = is_admin

                    users.append({
                        'sid'          : sid_str,
                        'username'     : uname,
                        'display_name' : u.get('full_name') or uname,
                        'email'        : email,
                        'account_type' : acc_type,
                        'access_level' : 'administrator' if final_admin else 'standard',
                        'is_active'    : is_current,
                    })

                if resume == 0:
                    break

        except ImportError:
            try:
                from get_windows_account import get_windows_user_info
                info = get_windows_user_info()
                users.append({
                    'sid'          : None,
                    'username'     : info.get('username', current_user),
                    'display_name' : info.get('full_name') or info.get('username', current_user),
                    'email'        : info.get('email') or None,
                    'account_type' : 'microsoft' if info.get('is_microsoft_account') else 'local',
                    'access_level' : 'administrator' if info.get('is_admin') else 'standard',
                    'is_active'    : True,
                })
            except Exception as e:
                self.log(f"[Owner] fallback error: {e}")
        except Exception as e:
            self.log(f"[Owner] collect_windows_users error: {e}")

        return users

    # =========================================================
    # HIDE PAGE POLICY
    # =========================================================
    def _apply_hide_page_policy(self, enabled: bool, pages: list):
        import subprocess
        NO_WIN   = 0x08000000
        VAL_NAME = "SettingsPageVisibility"

        def _write_reg(hive, value):
            ps_hive = "HKCU:" if hive == "HKCU" else "HKLM:"
            subkey  = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer"
            ps_path = ps_hive + "\\" + subkey
            ps = (
                '$p = "' + ps_path + '"; '
                'if(!(Test-Path $p)){New-Item -Path $p -Force | Out-Null}; '
                'Set-ItemProperty -Path $p -Name "' + VAL_NAME + '" '
                '-Value "' + value + '" -Type String -Force'
            )
            r = subprocess.run(
                ["powershell", "-NonInteractive", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, creationflags=NO_WIN
            )
            return r.returncode == 0, r.stderr.strip()

        def _delete_reg(hive):
            ps_hive = "HKCU:" if hive == "HKCU" else "HKLM:"
            subkey  = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer"
            ps_path = ps_hive + "\\" + subkey
            ps = (
                '$p = "' + ps_path + '"; '
                'if(Test-Path $p){'
                'Remove-ItemProperty -Path $p -Name "' + VAL_NAME + '" -ErrorAction SilentlyContinue}'
            )
            r = subprocess.run(
                ["powershell", "-NonInteractive", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, creationflags=NO_WIN
            )
            return r.returncode == 0

        try:
            if enabled and pages:
                self.log(f"[HidePage] Policy received")
                self.log(f"[HidePage] Processing {len(pages)} page(s)")

                valid_uris = []
                for p in pages:
                    identifier = p.get("page_identifier") or p.get("settings_uri", "")
                    name       = p.get("name", p.get("slug", "?"))
                    if identifier.startswith("unsupported:"):
                        self.log(f"[HidePage] SKIP (unsupported): {name}")
                        continue
                    if not identifier.startswith("ms-settings:"):
                        self.log(f"[HidePage] SKIP (invalid): {name}")
                        continue
                    self.log(f"[HidePage] Page: {name} | Identifier: {identifier} | Status: Supported")
                    valid_uris.append(identifier)

                if not valid_uris:
                    self.log("[HidePage] No supported identifiers — skipping")
                    return

                page_names = [uri.replace("ms-settings:", "") for uri in valid_uris]
                visibility  = "hide:" + ";".join(page_names)
                names_str   = ", ".join(
                    p.get("name","?") for p in pages
                    if (p.get("page_identifier") or "").startswith("ms-settings:")
                )
                self.log(f"[HidePage] Selected pages: {names_str}")
                self.log(f"[HidePage] Applying: {visibility}")

                ok, err = _write_reg("HKCU", visibility)
                if ok:
                    self.log("[HidePage] Applied via HKCU (no admin required)")
                    self.log("[HidePage] Hide Page applied successfully")
                    return

                self.log(f"[HidePage] HKCU failed: {err} — trying HKLM (needs admin)")

                ok2, err2 = _write_reg("HKLM", visibility)
                if ok2:
                    self.log("[HidePage] Applied via HKLM (admin/SYSTEM)")
                    self.log("[HidePage] Hide Page applied successfully")
                else:
                    self.log(f"[HidePage] HKLM also failed: {err2}")
                    self.log("[HidePage] Run agent as Administrator for Hide Page to work")

            else:
                self.log("[HidePage] Policy disabled - removing SettingsPageVisibility")
                ok1 = _delete_reg("HKCU")
                ok2 = _delete_reg("HKLM")
                if ok1 or ok2:
                    self.log("[HidePage] SettingsPageVisibility removed - pages restored")
                else:
                    self.log("[HidePage] SettingsPageVisibility not found (already clean)")

        except Exception as e:
            self.log(f"[HidePage] Failed: {e}")

    # =========================================================
    # FEATURE TOGGLE
    # =========================================================
    def _update_feature_ui(self, feature, enabled):
        if feature in self.feature_labels:
            color = self.success_color if enabled else self.error_color
            text = "ON" if enabled else "OFF"
            self.root.after(0, lambda: self.feature_labels[feature].config(text=text, fg=color))

    def _toggle_feature(self, feature, enabled):
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

    # =========================================================
    # START SERVICES
    # =========================================================
    def start_all_services(self):
        self.log(f"System initialized. Server: {self.get_base_url()}")

        config_res = self.data_sender.fetch_config_cached(max_age_seconds=60)
        if config_res.get("success"):
            self.apply_config(config_res.get("config"))

        self.start_monitoring()
        self.start_auto_send()

        # Remote frame loop (WebRTC-first + HTTP fallback)
        try:
            self._start_remote_frame_loop()
        except Exception as _e:
            self.log(f"[Warning] remote_frame_loop error: {_e}")
        
        try:
            self._start_fast_action_loop()
        except Exception as _e:
            self.log(f"[Warning] fast_action_loop error: {_e}")

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

        # =========================================================
    # REMOTE INPUT LOOP (HTTP polling fallback)
    # =========================================================
    def _start_remote_input_loop(self):
        """
        Loop terpisah untuk poll input events via HTTP (fallback).

        Kenapa perlu:
        - Browser kirim input via DataChannel (P2P) kalau WebRTC OK
        - Kalau DataChannel gagal → browser kirim HTTP POST /remote/input
        - Server simpan ke DB (remote_input_events) kalau Reverb down
        - Agent HARUS poll GET /remote/events untuk eksekusi

        Loop ini SELALU jalan saat remote aktif, terlepas WebRTC OK atau tidak.
        Kalau Reverb OK, events di DB biasanya kosong (sudah via broadcast),
        jadi polling ini tidak membebani.
        """
        import threading as _t
        import time as _time

        def _loop():
            while True:
                _time.sleep(0.3)  # poll tiap 300ms
                try:
                    if not self.data_sender.is_registered():
                        continue

                    # Cek remote status (hemat request kalau idle)
                    status = self.data_sender.fetch_remote_status()
                    if not status.get("success") or not status.get("remote_active"):
                        _time.sleep(2.0)  # idle lebih lama
                        continue

                    # Ambil events dari DB
                    result = self.data_sender.fetch_remote_events()
                    if not result.get("success"):
                        continue

                    events = result.get("events", [])
                    if not events:
                        continue

                    # Ambil ukuran layar akurat via mss (DPI-safe)
                    sw, sh = 1920, 1080
                    try:
                        import mss
                        with mss.mss() as sct:
                            monitor = sct.monitors[1]
                            sw = monitor["width"]
                            sh = monitor["height"]
                    except Exception:
                        try:
                            sw = win32api.GetSystemMetrics(0)
                            sh = win32api.GetSystemMetrics(1)
                        except Exception:
                            pass

                    # Eksekusi tiap event
                    for ev in events:
                        try:
                            ev_type = ev.get("type", "")
                            payload = ev.get("payload", {})
                            if isinstance(payload, str):
                                try:
                                    payload = json.loads(payload)
                                except Exception:
                                    payload = {}

                            if self.remote_control:
                                self.remote_control._execute_event(
                                    {"type": ev_type, "payload": payload}, sw, sh
                                )
                                if ev_type != "mouse_move":
                                    self.log(f"[RemoteInput] HTTP: {ev_type}")
                        except Exception as _ee:
                            self.log(f"[RemoteInput] Exec error: {_ee}")

                except Exception as _e:
                    self.log(f"[RemoteInput] Loop error: {_e}")
                    _time.sleep(2.0)

        _t.Thread(target=_loop, daemon=True).start()
        self.log("[RemoteInput] HTTP polling loop started")

    # =========================================================
    # REMOTE FRAME LOOP (WebRTC-first + HTTP polling fallback)
    # =========================================================
    def _start_remote_frame_loop(self):
        """
        Loop Remote Desktop Control - WebRTC-first, HTTP polling fallback.
        """
        import threading as _t
        import time as _time
        import io
        import base64

        if self._remote_frame_thread_running:
            return
        self._remote_frame_thread_running = True

        try:
            import mss
            from PIL import Image
            MSS_AVAILABLE = True
        except ImportError:
            MSS_AVAILABLE = False
            self.log("[RemoteFrame] mss/PIL tidak tersedia — HTTP fallback disabled")

        webrtc_active = [False]

        def _loop():
            sct = None
            monitor = None
            screen_w = 0
            screen_h = 0

            idle_interval   = 3.0
            active_interval = 0.08

            remote_was_active = False
            last_log_time = 0

            while True:
                try:
                    if not self.data_sender.is_registered():
                        _time.sleep(idle_interval)
                        continue

                    status = self.data_sender.fetch_remote_status()
                    if not status.get("success"):
                        _time.sleep(idle_interval)
                        continue

                    remote_active = bool(status.get("remote_active"))

                    # Transisi START
                    if remote_active and not remote_was_active:
                        self.log("[RemoteFrame] Session STARTED")
                        remote_was_active = True

                        if MSS_AVAILABLE and sct is None:
                            try:
                                sct = mss.mss()
                                monitor = sct.monitors[1]
                                screen_w = monitor["width"]
                                screen_h = monitor["height"]
                                self.log(f"[RemoteFrame] Screen: {screen_w}x{screen_h}")
                            except Exception as _e:
                                self.log(f"[RemoteFrame] mss init error: {_e}")
                                sct = None

                        if self.webrtc and self.webrtc.is_available():
                            self.log("[RemoteFrame] WebRTC available — starting handshake")
                            try:
                                self.webrtc.start()
                                self.webrtc.on_request()
                            except Exception as _e:
                                self.log(f"[RemoteFrame] WebRTC start error: {_e}")

                    # Transisi STOP
                    elif not remote_active and remote_was_active:
                        self.log("[RemoteFrame] Session STOPPED")
                        remote_was_active = False
                        webrtc_active[0] = False
                        if sct:
                            try:
                                sct.close()
                            except Exception:
                                pass
                            sct = None
                            monitor = None
                        if self.webrtc:
                            self.webrtc.on_stop()

                    if not remote_active:
                        _time.sleep(idle_interval)
                        continue

                    # Cek WebRTC state
                    if self.webrtc and self.webrtc._pc:
                        try:
                            state = self.webrtc._pc.connectionState
                            webrtc_active[0] = (state == "connected")
                        except Exception:
                            webrtc_active[0] = False

                    if webrtc_active[0]:
                        _time.sleep(1.0)
                        continue

                    # HTTP fallback capture & upload
                    if sct is None:
                        _time.sleep(0.5)
                        continue

                    try:
                        shot = sct.grab(monitor)
                        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                        max_w = 1280
                        if img.width > max_w:
                            ratio = max_w / img.width
                            new_size = (max_w, int(img.height * ratio))
                            img = img.resize(new_size, Image.BILINEAR)
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=50, optimize=False)
                        img_bytes = buf.getvalue()
                        img_b64 = base64.b64encode(img_bytes).decode("ascii")
                    except Exception as _e:
                        self.log(f"[RemoteFrame] Capture error: {_e}")
                        _time.sleep(0.5)
                        continue

                    result = self.data_sender.upload_remote_frame(
                        image_base64=img_b64,
                        width=screen_w,
                        height=screen_h,
                    )

                    now_ts = _time.time()
                    if now_ts - last_log_time > 30:
                        self.log(f"[RemoteFrame] HTTP fallback active (WebRTC not connected)")
                        last_log_time = now_ts

                    if not result.get("success"):
                        if not result.get("remote_active", True):
                            remote_was_active = False
                        _time.sleep(idle_interval)
                        continue

                    # Eksekusi events via HTTP (fallback input)
                    events = result.get("events", [])
                    if events and self.remote_control:
                        for ev in events:
                            try:
                                ev_type = ev.get("type", "")
                                payload = ev.get("payload", {})
                                if isinstance(payload, str):
                                    try:
                                        payload = json.loads(payload)
                                    except Exception:
                                        payload = {}
                                self.remote_control._execute_event(
                                    {"type": ev_type, "payload": payload},
                                    screen_w, screen_h
                                )
                            except Exception as _ee:
                                self.log(f"[RemoteFrame] Event exec error: {_ee}")

                    _time.sleep(active_interval)

                except Exception as e:
                    self.log(f"[RemoteFrame] Loop error: {e}")
                    _time.sleep(2.0)

        _t.Thread(target=_loop, daemon=True).start()
        self.log("[RemoteFrame] Loop started (WebRTC-first + HTTP fallback)")

    # =========================================================
    # CONNECTION CHECK
    # =========================================================
    def start_connection_check(self):
        def check_loop():
            consecutive_failures = 0
            max_failures = 3

            while True:
                if self.data_sender.is_registered():
                    result = self.data_sender.verify_registration()

                    if result.get("success"):
                        consecutive_failures = 0
                        self.root.after(0, lambda: self.conn_status_label.config(
                            text="Connected", fg=self.success_color))

                        config_res = self.data_sender.fetch_config_cached(max_age_seconds=300)
                        if config_res.get("success"):
                            self.apply_config(config_res.get("config"))
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
                            self.root.after(0, lambda: self.conn_status_label.config(
                                text="Disconnected (Offline)", fg=self.error_color))
                            config_res = self.data_sender.fetch_config_cached(max_age_seconds=300)
                            if config_res.get("success"):
                                self.apply_config(config_res.get("config"))
                        else:
                            self.root.after(0, lambda: self.conn_status_label.config(
                                text=f"Error ({error_msg})", fg=self.warning_color))

                time.sleep(self.connection_check_interval)

        threading.Thread(target=check_loop, daemon=True).start()

    def handle_auto_logout(self):
        self.log("CRITICAL: Device ID not found on server. Logging out...")
        messagebox.showwarning("Session Expired",
            "Device ID tidak terdaftar di server. Silakan register ulang.")

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

        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))

        self.show_registration_dialog()

    # =========================================================
    # REMOTE ACTIONS (Shutdown/Restart/Message/Terminate)
    # =========================================================
    def _execute_remote_action(self, action):
        action_id = action.get("id")
        action_type = action.get("action_type")
        details = action.get("details") or ""

        self.log(f"Menerima perintah remote control: {action_type} (id={action_id})")

        try:
            if action_type == "Shutdown":
                self.root.after(0, lambda: messagebox.showwarning(
                    "Perintah Admin",
                    "Komputer ini akan dimatikan oleh admin dalam 30 detik."))
                subprocess.run(["shutdown", "/s", "/t", "30"],
                               creationflags=subprocess.CREATE_NO_WINDOW)
                self.data_sender.acknowledge_action(action_id, "completed")

            elif action_type == "Restart":
                self.root.after(0, lambda: messagebox.showwarning(
                    "Perintah Admin",
                    "Komputer ini akan direstart oleh admin dalam 30 detik."))
                subprocess.run(["shutdown", "/r", "/t", "30"],
                               creationflags=subprocess.CREATE_NO_WINDOW)
                self.data_sender.acknowledge_action(action_id, "completed")

            elif action_type in ("Send Messages", "Send Message"):
                self.root.after(0, lambda: self._show_admin_message(details))
                self.data_sender.acknowledge_action(action_id, "completed")

            elif action_type == "Terminate App":
                app_name = (details or "").strip()
                self.log(f"[Terminate] Target: '{app_name}'")
                try:
                    r = subprocess.run(["taskkill", "/F", "/IM", app_name],
                                creationflags=subprocess.CREATE_NO_WINDOW,
                                timeout=8, capture_output=True, text=True)
                    if r.returncode == 0:
                        self.log(f"[Terminate] SUCCESS: {r.stdout.strip()}")
                    else:
                        self.log(f"[Terminate] taskkill rc={r.returncode}: {r.stderr.strip()}")
                        try:
                            for p in psutil.process_iter(["name", "pid"]):
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
        win = tk.Toplevel(self.root)
        win.title("Pesan dari Admin")
        win.geometry("480x280")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.grab_set()

        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 240
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 140
        win.geometry(f"+{x}+{y}")

        tk.Label(win, text="📩 Pesan dari Admin",
                 font=("Segoe UI", 16, "bold")).pack(pady=(20, 10))

        text_frame = tk.Frame(win)
        text_frame.pack(padx=20, pady=5, fill="both", expand=True)

        text_widget = tk.Text(text_frame, wrap="word", font=("Segoe UI", 12),
                              height=7, borderwidth=0, highlightthickness=0, cursor="arrow")
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

    # =========================================================
    # BLOCKING / APP BLOCKER / URL FILTER
    # =========================================================
    def _check_blocking(self):
        try:
            info = self.app_monitor.get_active_window_info()
            if not info or info.get("app_name") == "Unknown":
                return

            app_name = info.get("app_name", "").lower()
            window_title = info.get("window_title", "").lower()
            pid = info.get("pid", 0)

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

            if app_name in system_processes:
                return
            if pid in self._denied_pids:
                return

            if self.enabled_features.get("app_blocker") and self.blocked_apps:
                _exe = (info.get("executable", "") or "").strip()
                if not _exe and pid:
                    try:
                        _exe = psutil.Process(pid).exe() or ""
                    except Exception:
                        pass

                _pname = ""
                _oname = ""
                if _exe:
                    try:
                        _r = subprocess.run(
                            ["powershell", "-NonInteractive", "-NoProfile", "-Command",
                             f'$v=(Get-ItemProperty "{_exe}").VersionInfo;'
                             f'Write-Output ($v.ProductName + "|" + $v.OriginalFilename)'],
                            capture_output=True, text=True, timeout=3,
                            creationflags=0x08000000)
                        if _r.returncode == 0 and "|" in (_r.stdout or ""):
                            _pts = _r.stdout.strip().split("|", 1)
                            _pname = (_pts[0] or "").strip().lower()
                            _oname = (_pts[1] or "").strip().lower()
                    except Exception:
                        pass

                _exe_l = _exe.lower()
                _app_l = app_name.lower()
                _blk = False
                _why = ""

                for _rule in self.blocked_apps:
                    _rl = (_rule or "").lower().strip()
                    _rs = _rl
                    for _ext in (".exe", ".bat", ".com", ".msi"):
                        if _rs.endswith(_ext):
                            _rs = _rs[:-len(_ext)]
                            break

                    if _pname and len(_rl) >= 3:
                        if _rl in _pname or (len(_rs) >= 3 and _rs in _pname):
                            _blk = True; _why = f"ProductName[{_pname}]~rule[{_rule}]"; break
                        for _w in _pname.split():
                            if len(_w) >= 4 and _w in _rl:
                                _blk = True; _why = f"ProductName word[{_w}]~rule[{_rule}]"; break
                        if _blk: break

                    if _oname and (_oname == _rl or _oname.replace(".exe", "") == _rs):
                        _blk = True; _why = f"OrigFilename[{_oname}]~rule[{_rule}]"; break

                    if _app_l == _rl:
                        _blk = True; _why = f"ExeName[{app_name}]~rule[{_rule}]"; break
                    if _rl and _exe_l and _rl in _exe_l:
                        _blk = True; _why = f"ExePath~rule[{_rule}]"; break
                    if len(_rs) >= 4 and (_rs in _exe_l or _rs in _app_l):
                        _blk = True; _why = f"Stem[{_rs}]~rule[{_rule}]"; break

                if _blk:
                    self.log(f"[AppBlocker] BLOCKED — {_why}")
                    self._terminate_process(pid, f"AppBlocker: {_why}")
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
                    if not clean_title or clean_title == "unknown":
                        return
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
        if pid == 0:
            return
        try:
            process = psutil.Process(pid)
            process.terminate()
            try:
                process.wait(timeout=3)
            except psutil.TimeoutExpired:
                try:
                    process.kill()
                    process.wait()
                except Exception:
                    pass
            self._safe_ui_call(self.log, f"BLOCKER: {reason} (Terminated PID {pid})")

            def show_block_msg():
                try:
                    messagebox.showwarning("Security Alert", f"Akses dilarang oleh admin:\n{reason}")
                except Exception:
                    pass
            self._safe_ui_call(show_block_msg)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            pass
        except psutil.AccessDenied:
            self._denied_pids.add(pid)
            self._safe_ui_call(self.log, f"BLOCKER: Skip {reason} (AccessDenied)")
        except Exception:
            pass

    # =========================================================
    # USB MONITOR
    # =========================================================
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

    # =========================================================
    # FILE SYNC
    # =========================================================
    def _load_file_versions(self):
        if os.path.exists(self.file_versions_path):
            try:
                with open(self.file_versions_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading file versions: {e}")
        return {}

    def _save_file_versions(self):
        try:
            with open(self.file_versions_path, "w") as f:
                json.dump(self.file_versions, f, indent=4)
        except Exception as e:
            print(f"Error saving file versions: {e}")

    def _update_file_sync_ui(self):
        for item in self.fs_tree.get_children():
            self.fs_tree.delete(item)
        for sync in self.file_sync_data:
            name = sync.get("name", "N/A")
            description = sync.get("description", "")
            status = sync.get("sync_status", "Pending")
            self.fs_tree.insert("", tk.END, values=(name, description, status))

    def _sync_files(self):
        def sync_thread():
            has_changes = False
            log_buffer = []

            def add_log(msg):
                log_buffer.append(msg)

            add_log("Starting file sync...")
            expected_files = []
            updated_versions = {}

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

            for sync_config in self.file_sync_data:
                sync_id = str(sync_config.get("id", ""))
                sync_status = sync_config.get("sync_status", "").lower()
                target_dir = sync_config.get("file_path")
                files = sync_config.get("files", [])
                add_log(f"  - Processing sync {sync_id} (status: {sync_status})")

                if sync_status != "active":
                    if target_dir and os.path.exists(target_dir):
                        try:
                            add_log(f"  - Cleaning inactive sync {sync_id} in dir: {target_dir}")
                            this_sync_files = set()
                            for file_item in files:
                                filename = file_item.get("file_name")
                                if filename:
                                    this_sync_files.add(os.path.join(target_dir, filename))
                            for version_key in list(self.file_versions.keys()):
                                if version_key.startswith(f"{sync_id}_"):
                                    f_info = self.file_versions.get(version_key, {})
                                    f_path = f_info.get("path")
                                    if f_path:
                                        this_sync_files.add(f_path)
                            for file_path in this_sync_files:
                                if os.path.isfile(file_path) and file_path not in expected_files:
                                    try:
                                        os.remove(file_path)
                                        add_log(f"  - [Deleted] Not active: {file_path}")
                                        has_changes = True
                                    except Exception as e:
                                        add_log(f"  - [Error] Delete {file_path}: {e}")
                            keys_to_remove = [k for k in list(self.file_versions.keys()) if k.startswith(f"{sync_id}_")]
                            add_log(f"  - Removing {len(keys_to_remove)} versions for sync {sync_id}")
                            if keys_to_remove:
                                has_changes = True
                            for k in keys_to_remove:
                                del self.file_versions[k]
                        except Exception as e:
                            add_log(f"  - [Error] Cleaning up inactive sync {sync_id}: {e}")
                    continue

                if not target_dir:
                    continue

                try:
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

                        current_version = self.file_versions.get(version_key, {}).get("version", "")
                        current_hash = self.file_versions.get(version_key, {}).get("hash", "")
                        needs_update = (current_version != file_version) or (current_hash != file_hash)

                        if needs_update:
                            add_log(f"  - [Downloading] {filename} (v{file_version})...")
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

            active_sync_ids = set()
            for sync_config in self.file_sync_data:
                active_sync_ids.add(str(sync_config.get("id", "")))
            add_log(f"  - Active sync IDs in config: {list(active_sync_ids)}")

            keys_to_remove = []
            for version_key in list(self.file_versions.keys()):
                sync_id_from_key = version_key.split("_")[0]
                if sync_id_from_key not in active_sync_ids:
                    keys_to_remove.append(version_key)
            add_log(f"  - Syncs to remove from versions: {keys_to_remove}")

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

            self.file_versions.update(updated_versions)
            self._save_file_versions()
            add_log("File sync complete.")

            if has_changes:
                for msg in log_buffer:
                    self.root.after(0, lambda m=msg: self.log(m))

        threading.Thread(target=sync_thread, daemon=True).start()

    # =========================================================
    # BLOCK NEW INSTALL
    # =========================================================
    def _check_installs(self):
        if not self.enabled_features.get("block_new_install") or self.block_install_mode == "off":
            return

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

        try:
            for proc in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    proc_name = proc.info['name'].lower()
                    proc_exe = proc.info['exe'].lower() if proc.info['exe'] else ""
                    pid = proc.info['pid']

                    if proc_name in system_processes:
                        continue
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
            traceback.print_exc()

    def _on_idle_detected(self, event):
        def show_alert():
            try:
                messagebox.showwarning("Idle Alert", "Sistem mendeteksi tidak ada aktivitas selama 30 detik!")
            except Exception:
                pass
        self._safe_ui_call(show_alert)
        self._safe_ui_call(self.log, f"IDLE DETECTED: No activity for 30s")

    # =========================================================
    # UI SETUP
    # =========================================================
    def setup_ui(self):
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

        style = ttk.Style()
        style.configure('TNotebook.Tab', font=('Segoe UI', 12), padding=[20, 10])

        self.notebook = ttk.Notebook(main_frame, style='TNotebook')
        self.notebook.pack(fill="both", expand=True)

        # Tab 1: Monitoring
        self.tab_monitoring = tk.Frame(self.notebook, bg=self.bg_color)
        self.notebook.add(self.tab_monitoring, text="Monitoring")

        status_container = tk.LabelFrame(
            self.tab_monitoring,
            text=" Monitoring Status ",
            font=("Segoe UI", 12, "bold"),
            padx=20, pady=20,
            bg=self.card_color, fg=self.header_color,
            relief="flat", highlightthickness=1,
            highlightbackground=self.border_color
        )
        status_container.pack(fill="x", pady=(0, 20))

        grid_frame = tk.Frame(status_container, bg=self.card_color)
        grid_frame.pack(fill="x")

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

        chat_support_frame = tk.Frame(self.tab_monitoring, bg=self.bg_color)
        chat_support_frame.pack(fill="x", pady=(8, 0), padx=20)

        tk.Button(
            chat_support_frame,
            text="💬  Chat Support",
            font=("Segoe UI", 10, "bold"),
            bg="#3b82f6", fg="white",
            relief="flat", cursor="hand2",
            pady=8, padx=16,
            command=self._open_chat_support
        ).pack(side="left")

        tk.Label(
            chat_support_frame,
            text="  Kirim pesan langsung ke admin",
            font=("Segoe UI", 9),
            fg=self.secondary_text_color,
            bg=self.bg_color
        ).pack(side="left", padx=(8, 0))

        # Tab 2: Log
        self.tab_log = tk.Frame(self.notebook, bg=self.bg_color)
        self.notebook.add(self.tab_log, text="Log")

        log_container = tk.LabelFrame(
            self.tab_log,
            text=" System Activities ",
            font=("Segoe UI", 12, "bold"),
            padx=12, pady=12,
            bg=self.card_color, fg=self.header_color,
            relief="flat", highlightthickness=1,
            highlightbackground=self.border_color
        )
        log_container.pack(fill="both", expand=True)

        self.log_text = scrolledtext.ScrolledText(
            log_container,
            font=("Consolas", 10),
            state="disabled",
            bg="#0f172a", fg="#94a3b8",
            insertbackground="#e2e8f0",
            relief="flat", padx=12, pady=12
        )
        self.log_text.pack(fill="both", expand=True)

        # Tab 3: File Sync
        self.tab_file_sync = tk.Frame(self.notebook, bg=self.bg_color)
        self.notebook.add(self.tab_file_sync, text="File Sync")

        fs_container = tk.LabelFrame(
            self.tab_file_sync,
            text=" File Synchronization ",
            font=("Segoe UI", 12, "bold"),
            padx=12, pady=12,
            bg=self.card_color, fg=self.header_color,
            relief="flat", highlightthickness=1,
            highlightbackground=self.border_color
        )
        fs_container.pack(fill="both", expand=True)

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

        # Tab 4: Features
        self.tab_features = tk.Frame(self.notebook, bg=self.bg_color)
        self.notebook.add(self.tab_features, text="Features")

        feature_container = tk.LabelFrame(
            self.tab_features,
            text=" Features Status ",
            font=("Segoe UI", 12, "bold"),
            padx=12, pady=12,
            bg=self.card_color, fg=self.header_color,
            relief="flat", highlightthickness=1,
            highlightbackground=self.border_color
        )
        feature_container.pack(fill="both", expand=True)

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
            ("download_filter", "Download Filter"),
            ("hide_page", "Hide Page")
        ]

        row, col = 0, 0
        for key, display_name in features_list:
            frame = tk.Frame(features_container, bg=self.card_color, bd=1, relief="solid",
                             highlightbackground=self.border_color)
            frame.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)
            tk.Label(frame, text=display_name, font=("Segoe UI", 9, "bold"),
                     bg=self.card_color, fg=self.text_color).pack(pady=(4, 2), padx=8, anchor="w")
            status_lbl = tk.Label(frame, text="OFF", font=("Segoe UI", 9, "bold"),
                                  bg=self.card_color, fg=self.error_color)
            status_lbl.pack(pady=(0, 4), padx=8, anchor="w")
            self.feature_labels[key] = status_lbl
            col += 1
            if col >= 2:
                col = 0
                row += 1

        features_container.columnconfigure(0, weight=1)
        features_container.columnconfigure(1, weight=1)
        self.update_clock()

    def update_clock(self):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.time_label.config(text=current_time)
        self.root.after(1000, self.update_clock)

    def _safe_ui_call(self, func, *args, **kwargs):
        try:
            if not hasattr(self, 'root') or not self.root or not self.root.winfo_exists():
                return

            def wrapped():
                try:
                    func(*args, **kwargs)
                except Exception as e:
                    print(f"UI execution failed: {e}")

            self.root.after(0, wrapped)
        except Exception as e:
            print(f"UI call scheduling failed: {e}")

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        try:
            if not hasattr(self, 'log_text') or not self.log_text or not self.log_text.winfo_exists():
                print(f"[LOG] {message}")
                return
            self.log_text.config(state="normal")
            self.log_text.insert("end", f"[{timestamp}] {message}\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        except Exception as e:
            print(f"Log failed: {e}")

    # =========================================================
    # MONITORING
    # =========================================================
    def start_monitoring(self):
        if self.is_monitoring:
            return
        self.is_monitoring = True
        self.status_label.config(text="Monitoring Active", fg=self.success_color)
        self.log("Monitoring started...")

        threading.Thread(target=self._send_owner_on_startup, daemon=True).start()
        threading.Thread(target=self._send_apps_on_startup, daemon=True).start()

        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def _send_owner_on_startup(self):
        import time as _time
        _time.sleep(3)
        try:
            from app_paths import get_app_data_path
            stamp_file = get_app_data_path("last_owner_send.json")
            now = _time.time()
            last_send = 0
            try:
                if os.path.exists(stamp_file):
                    with open(stamp_file) as f:
                        last_send = json.load(f).get("ts", 0)
            except Exception:
                pass

            if now - last_send < 86400:
                self.log("[Owner] Skip — sudah dikirim < 24 jam lalu")
                return

            if hasattr(self.data_sender, 'send_device_owner'):
                self.data_sender.send_device_owner()
                self.log("[Owner] Info pemilik device dikirim")

            users = self._collect_windows_users()
            if users and hasattr(self.data_sender, 'send_windows_users'):
                result = self.data_sender.send_windows_users(users)
                if result.get("success"):
                    self.log(f"[Owner] {len(users)} Windows user(s) synced")
                else:
                    self.log(f"[Owner] Windows users sync failed: {result.get('error','')}")

            with open(stamp_file, "w") as f:
                json.dump({"ts": now}, f)
        except Exception as e:
            self.log(f"[Owner] Error: {e}")

    def _send_apps_on_startup(self):
        import time as _time
        _time.sleep(10)
        try:
            from app_paths import get_app_data_path
            stamp_file = get_app_data_path("last_apps_send.json")
            now = _time.time()
            last_send = 0
            try:
                if os.path.exists(stamp_file):
                    with open(stamp_file) as f:
                        last_send = json.load(f).get("ts", 0)
            except Exception:
                pass

            if now - last_send < 86400:
                self.log("[Apps] Skip — sudah dikirim < 24 jam lalu")
                return

            if hasattr(self.data_sender, 'send_installed_apps'):
                self.log("[Apps] Mengambil daftar aplikasi...")
                self.data_sender.send_installed_apps()
                with open(stamp_file, "w") as f:
                    json.dump({"ts": now}, f)
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
                now_ts = time.time()
                if now_ts - self.last_screenshot_time >= self.screenshot_interval:
                    screenshot_data = self.screenshot_capture.capture_and_encode()
                    if screenshot_data:
                        self.last_screenshot_time = now_ts

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

            download_count_sent = 0
            if self.download_logs:
                batch = self.download_logs[:100]
                download_count_sent = len(batch)
                dl_result = self.data_sender.send_download_logs(batch)
                results["download_logs"] = dl_result
                results["download_count_sent"] = download_count_sent
                if dl_result.get("success"):
                    self._safe_ui_call(self.log, f"Download logs sent: SUCCESS ({download_count_sent} items)")
                    self.download_logs = self.download_logs[download_count_sent:]
                else:
                    self.persistence.add_to_queue("download_logs", {"download_logs": batch, "location": location_data})
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
        if self.enabled_features.get("download_filter"):
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

    # =========================================================
    # AUTO SEND
    # =========================================================
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
                    feat = self.enabled_features

                    location_data = self.location_tracker.get_location() if feat.get("location") else None
                    app_usage_data = self.app_monitor.get_usage_data() if feat.get("app_usage") else []
                    browsing_data = self.browsing_tracker.get_new_history() if feat.get("browsing_history") else []
                    upload_data = self.upload_tracker.get_new_activities() if feat.get("upload_activity") else []
                    keystrokes_data = self.keylogger.get_logs() if feat.get("keylogger") else []
                    idle_data = self.idle_tracker.get_new_events() if feat.get("idle_tracker") else []
                    count = len(browsing_data)
                    upload_count = len(upload_data)
                    keystroke_count = len(keystrokes_data)
                    idle_count = len(idle_data)
                    download_count = len(self.download_logs)

                    screenshot_data = None
                    if feat.get("screenshot"):
                        now_ts = time.time()
                        if now_ts - self.last_screenshot_time >= self.screenshot_interval:
                            screenshot_data = self.screenshot_capture.capture_and_encode()
                            if screenshot_data:
                                self.last_screenshot_time = now_ts

                    results = self.data_sender.send_all_data_with_screenshot(
                        app_usage_data, browsing_data, screenshot_data, location_data)

                    self._handle_send_results(results, app_usage_data, browsing_data, screenshot_data, location_data)

                    if feat.get("location") and location_data:
                        loc_result = self.data_sender.send_location(location_data)
                        results["location_data"] = loc_result
                        if not loc_result.get("success"):
                            self.persistence.add_to_queue("location", {"location": location_data})

                    if feat.get("keylogger") and keystrokes_data:
                        key_result = self.data_sender.send_keystrokes(keystrokes_data, location_data)
                        results["keystrokes"] = key_result
                        if not key_result.get("success"):
                            self.persistence.add_to_queue("keystrokes", {"keystrokes": keystrokes_data, "location": location_data})

                    if feat.get("idle_tracker") and idle_data:
                        idle_result = self.data_sender.send_idle_events(idle_data, location_data)
                        results["idle_event"] = idle_result
                        if not idle_result.get("success"):
                            self.persistence.add_to_queue("idle_event", {"idle_events": idle_data, "location": location_data})

                    if feat.get("upload_activity") and upload_data:
                        up_result = self.data_sender.send_upload_activity(upload_data, location_data)
                        results["upload_activity"] = up_result
                        if not up_result.get("success"):
                            self.persistence.add_to_queue("upload_activity", {"upload_activities": upload_data, "location": location_data})

                    download_count_sent = 0
                    if self.download_logs and (feat.get("browsing_history") or feat.get("upload_activity") or feat.get("download_filter")):
                        batch = self.download_logs[:100]
                        download_count_sent = len(batch)
                        dl_result = self.data_sender.send_download_logs(batch)
                        results["download_logs"] = dl_result
                        results["download_count_sent"] = download_count_sent
                        if dl_result.get("success"):
                            self.root.after(0, lambda c=download_count_sent: self.log(f"Download logs sent: SUCCESS ({c} items)"))
                            self.download_logs = self.download_logs[download_count_sent:]
                        else:
                            self.persistence.add_to_queue("download_logs", {"download_logs": batch, "location": location_data})
                            self.root.after(0, lambda e=dl_result.get('error'): self.log(f"Download logs failed: {e} - Saved to queue"))

                    history_result = results.get("browsing_history", {})
                    if history_result.get("success"):
                        if browsing_data:
                            latest_ts = max([h["timestamp"] for h in browsing_data if h.get("timestamp")])
                            self.browsing_tracker.mark_as_sent(latest_ts)
                        else:
                            self.browsing_tracker.mark_as_sent()

                    self.root.after(0, lambda: self._update_send_result(
                        results, count, upload_count, keystroke_count, idle_count, download_count))
            except Exception as e:
                self.root.after(0, lambda: self.log(f"Auto-send error: {e}"))
            time.sleep(self.current_interval)

    # =========================================================
    # CHAT
    # =========================================================
    def on_close(self):
        self.root.withdraw()

    def _close_chat_popup(self):
        popup = getattr(self, '_active_chat_popup', None)
        if popup:
            try:
                if popup.winfo_exists():
                    popup.destroy()
            except Exception:
                pass
            self._active_chat_popup = None
            self._chat_text_widget = None
            self.log("[Chat] Jendela chat ditutup")

    def _show_chat_message(self, message):
        try:
            self.root.deiconify()

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
                win.update_idletasks()
                sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
                win.geometry(f"460x520+{(sw//2)-230}+{(sh//2)-260}")

                hdr = tk.Frame(win, bg="#1e293b", height=48)
                hdr.pack(fill="x", side="top")
                hdr.pack_propagate(False)
                tk.Label(hdr, text="Chat dengan Admin",
                         font=("Segoe UI", 12, "bold"),
                         bg="#1e293b", fg="white").pack(expand=True)

                inp_frame = tk.Frame(win, bg="#f8fafc", bd=1, relief="flat", pady=8, padx=10)
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
                chat_frame.pack(side="top", fill="both", expand=True, padx=0, pady=0)

                scrollbar = tk.Scrollbar(chat_frame)
                scrollbar.pack(side="right", fill="y")

                txt = tk.Text(chat_frame, wrap="word", font=("Segoe UI", 10),
                              state="disabled", yscrollcommand=scrollbar.set,
                              bg="#ffffff", relief="flat", padx=12, pady=8,
                              spacing1=2, spacing3=2)
                txt.pack(side="left", fill="both", expand=True)
                scrollbar.config(command=txt.yview)

                txt.tag_config("admin_name", foreground="#2563eb", font=("Segoe UI", 9, "bold"))
                txt.tag_config("agent_name", foreground="#16a34a", font=("Segoe UI", 9, "bold"))
                txt.tag_config("msg_text", foreground="#1e293b", font=("Segoe UI", 10))
                txt.tag_config("time_tag", foreground="#94a3b8", font=("Segoe UI", 8))
                txt.tag_config("divider", foreground="#e2e8f0", font=("Segoe UI", 6))

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
                    self._chat_text_widget = None
                    win.destroy()

                win.protocol("WM_DELETE_WINDOW", on_close)
                win.lift()
                win.focus_force()

            def _append_message(sender, text):
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

            _append_message("admin", message)
            fn = getattr(self, '_chat_append_fn', None)
            if fn:
                try: fn("admin", message)
                except Exception: pass
            win.lift()
            win.attributes("-topmost", True)
            self.log(f"[Chat] Pesan diterima: {message[:30]}")
        except Exception as _e:
            self.log(f"[Chat] Error tampilkan window: {_e}")

    # =========================================================
    # FAST ACTION LOOP (Terminate App + Active Apps + Chat polling)
    # =========================================================
    def _start_fast_action_loop(self):
        """
        Loop dedicated untuk:
        - Poll chat messages (SELALU jalan — tidak bergantung Reverb)
        - Poll pending actions (Terminate App, dll)
        - Kirim active apps snapshot (throttle 30s + dedup)
        - Setelah terminate sukses → langsung kirim active apps (skip throttle)

        Dedup chat pakai `_processed_chat_ids` supaya tidak dobel dengan Reverb.
        """
        import threading as _t
        import subprocess as _sp
        import time as _time

        _last_active_apps_send = [0.0]
        _last_active_apps_hash = [None]

        ACTIVE_APPS_INTERVAL = 30
        CHAT_POLL_INTERVAL = 3
        _last_chat_poll = [0.0]

        if not hasattr(self, '_processed_chat_ids'):
            self._processed_chat_ids = set()

        def _loop():
            while True:
                _time.sleep(2)
                try:
                    if not self.data_sender.is_registered():
                        continue

                    now = _time.time()

                    # Chat polling: SELALU jalan
                    if now - _last_chat_poll[0] >= CHAT_POLL_INTERVAL:
                        _last_chat_poll[0] = now
                        try:
                            chat_result = self.data_sender.fetch_chat_messages()
                            msgs = chat_result.get("messages", [])
                            for msg in msgs:
                                msg_id = msg.get("id")

                                if msg_id and msg_id in self._processed_chat_ids:
                                    continue
                                if msg_id:
                                    self._processed_chat_ids.add(msg_id)
                                    if len(self._processed_chat_ids) > 200:
                                        self._processed_chat_ids = set(
                                            list(self._processed_chat_ids)[-100:]
                                        )

                                if msg.get("message") == "__CHAT_ENDED__":
                                    self.root.after(0, self._close_chat_popup)
                                    self.data_sender.ack_chat_end()
                                    self.log("[Chat] Sesi chat diakhiri admin, window ditutup")
                                else:
                                    self.root.after(0, lambda m=msg["message"]: self._show_chat_message(m))
                                    self.log(f"[Chat] Pesan masuk: {msg.get('message','')[:30]}")
                        except Exception as _ce:
                            self.log(f"[Chat] Error cek pesan: {_ce}")

                    # Active apps: throttle 30s + dedup
                    if now - _last_active_apps_send[0] >= ACTIVE_APPS_INTERVAL:
                        try:
                            apps = self.app_monitor.get_active_apps_snapshot()
                            sig = tuple(sorted(a["app_name"].lower() for a in apps))
                            if sig != _last_active_apps_hash[0]:
                                self.data_sender.send_active_apps(apps)
                                _last_active_apps_hash[0] = sig
                            _last_active_apps_send[0] = now
                        except Exception:
                            pass

                    # Pending actions (Terminate App)
                    result = self.data_sender.fetch_pending_actions()
                    if not result.get("success"):
                        continue
                    actions = result.get("actions", [])
                    for action in actions:
                        atype = action.get("action_type", "")
                        details = action.get("details") or ""
                        aid = action.get("id")

                        if atype == "Terminate App":
                            app_name = details.strip()
                            self.log(f"[FastAction] Terminate: '{app_name}'")
                            terminated_ok = False

                            try:
                                r = _sp.run(
                                    ["taskkill", "/F", "/IM", app_name],
                                    creationflags=_sp.CREATE_NO_WINDOW,
                                    timeout=8, capture_output=True, text=True
                                )
                                if r.returncode == 0:
                                    self.log(f"[FastAction] SUCCESS: {r.stdout.strip()}")
                                    terminated_ok = True
                                else:
                                    self.log(f"[FastAction] taskkill rc={r.returncode}: {r.stderr.strip()}")
                                    try:
                                        for p in psutil.process_iter(["name", "pid"]):
                                            try:
                                                if p.info["name"].lower() == app_name.lower():
                                                    p.kill()
                                                    self.log(f"[FastAction] psutil killed {p.info['pid']}")
                                                    terminated_ok = True
                                            except Exception:
                                                pass
                                    except Exception:
                                        pass
                            except Exception as _e:
                                self.log(f"[FastAction] Error: {_e}")

                            self.data_sender.acknowledge_action(aid, "completed")

                            # Langsung kirim active apps setelah terminate
                            if terminated_ok:
                                try:
                                    _time.sleep(0.3)
                                    apps = self.app_monitor.get_active_apps_snapshot()
                                    sig = tuple(sorted(a["app_name"].lower() for a in apps))
                                    self.data_sender.send_active_apps(apps)
                                    _last_active_apps_hash[0] = sig
                                    _last_active_apps_send[0] = _time.time()
                                    self.log(f"[FastAction] Active apps refreshed setelah terminate")
                                except Exception as _ae:
                                    self.log(f"[FastAction] Gagal refresh active apps: {_ae}")

                except Exception as _e:
                    self.log(f"[FastAction] Loop error: {_e}")

        _t.Thread(target=_loop, daemon=True).start()
        self.log("Fast action loop started (chat poll 3s, actions 2s, active apps 30s)")

    # =========================================================
    # CHAT SUPPORT POPUP (agent bisa initiate)
    # =========================================================
    def _open_chat_support(self):
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
        txt.tag_config("msg_text", foreground="#1e293b", font=("Segoe UI", 10))
        txt.tag_config("time_tag", foreground="#94a3b8", font=("Segoe UI", 8))
        self._chat_text_widget = txt

        def _append(sender, text):
            tw = getattr(self, '_chat_text_widget', None)
            if not tw: return
            try:
                now = datetime.now().strftime("%H:%M")
                tw.config(state="normal")
                name = "Admin" if sender == "admin" else "Kamu"
                tag = "admin_name" if sender == "admin" else "agent_name"
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
            self._chat_text_widget = None
            self._chat_append_fn = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        win.lift()
        win.focus_force()
        self.log("[Chat] Popup chat support dibuka")

    # =========================================================
    # REVERB WEBSOCKET LISTENER
    # =========================================================
    def _start_reverb_listener(self):
        import threading as _t
        import json as _json_mod

        reverb_flag = self._reverb_connected_flag

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

            host = cfg.get("host", "127.0.0.1")
            port = cfg.get("port", 8080)
            app_key = cfg.get("app_key", "")

            if host in ("localhost", "127.0.0.1"):
                try:
                    import urllib.parse as _up
                    _parsed = _up.urlparse(self.data_sender.server_url or "")
                    if _parsed.hostname and _parsed.hostname not in ("localhost", "127.0.0.1"):
                        self.log(f"[Reverb] Host fallback: {host} → {_parsed.hostname}")
                        host = _parsed.hostname
                except Exception:
                    pass

            device_id = cfg.get("device_id")
            channel = f"private-device.{device_id}"
            ws_url = f"ws://{host}:{port}/app/{app_key}?protocol=7&client=python&version=1.0"

            socket_id_holder = [None]
            subscribed = [False]

            def on_open(ws):
                self.log(f"[Reverb] Connection successful — {ws_url}")

            def on_message(ws, raw):
                try:
                    data = _json_mod.loads(raw)
                    event = data.get("event", "")
                    inner = data.get("data", {})
                    if isinstance(inner, str):
                        inner = _json_mod.loads(inner)

                    if event == "pusher:connection_established":
                        socket_id = inner.get("socket_id")
                        socket_id_holder[0] = socket_id
                        auth = self.data_sender.get_channel_auth(socket_id, channel)
                        if not auth:
                            self.log("[Reverb] Channel auth gagal")
                            return
                        ws.send(_json_mod.dumps({
                            "event": "pusher:subscribe",
                            "data": {"channel": channel, "auth": auth}
                        }))
                    elif event == "pusher_internal:subscription_succeeded":
                        subscribed[0] = True
                        reverb_flag[0] = True
                        self.log(f"[Reverb] Berhasil subscribe channel {channel}")

                        

                    # Chat message dari admin
                    elif (
                        event == "App\\Events\\ChatMessageSent"
                        or event.endswith("ChatMessageSent")
                        or event == "chat.message"
                        or event == ".chat.message"
                        or event.endswith(".chat.message")
                    ):
                        msg = inner.get("message", "")
                        msg_id = inner.get("id")

                        if not hasattr(self, '_processed_chat_ids'):
                            self._processed_chat_ids = set()
                        if msg_id and msg_id in self._processed_chat_ids:
                            return
                        if msg_id:
                            self._processed_chat_ids.add(msg_id)
                            if len(self._processed_chat_ids) > 200:
                                self._processed_chat_ids = set(
                                    list(self._processed_chat_ids)[-100:]
                                )

                        if msg and msg != "__CHAT_ENDED__":
                            self.root.after(0, lambda m=msg: self._show_chat_message(m))
                            self.log(f"[Reverb] Pesan masuk: {msg[:30]}")
                        elif msg == "__CHAT_ENDED__":
                            self.root.after(0, self._close_chat_popup)
                            self.data_sender.ack_chat_end()
                            self.log("[Reverb] Chat diakhiri admin")

                        # Remote input via Reverb (mouse/keyboard real-time                               
                        elif (
                            event == "remote.input"
                            or event == ".remote.input"
                            or event.endswith(".remote.input")
                        ):
                            ev_type = inner.get("type", "")
                            payload = inner.get("payload", {})
                            if self.remote_control:
                                # Ambil ukuran layar akurat via mss (DPI-safe)
                                sw, sh = 1920, 1080
                                try:
                                    import mss
                                    with mss.mss() as sct:
                                        monitor = sct.monitors[1]
                                        sw = monitor["width"]
                                        sh = monitor["height"]
                                except Exception:
                                    try:
                                        sw = win32api.GetSystemMetrics(0)
                                        sh = win32api.GetSystemMetrics(1)
                                    except Exception:
                                        pass

                                try:
                                    self.remote_control._execute_event(
                                        {"type": ev_type, "payload": payload}, sw, sh
                                    )
                                    if ev_type != "mouse_move":
                                        self.log(f"[Reverb] Remote input: {ev_type}")
                                except Exception as _ee:
                                    self.log(f"[Reverb] Remote input error: {_ee}")

                    # Terminate signal via Reverb (real-time)
                    elif (
                        event.endswith(".terminate")
                        or "webrtc.terminate" in event
                        or event == "terminate"
                    ):
                        app_name = inner.get("app_name", "")
                        action_id = inner.get("action_id")
                        self.log(f"[Reverb] Terminate signal: '{app_name}'")

                        terminated_ok = False
                        try:
                            r = subprocess.run(
                                ["taskkill", "/F", "/IM", app_name],
                                creationflags=subprocess.CREATE_NO_WINDOW,
                                timeout=8, capture_output=True, text=True
                            )
                            if r.returncode == 0:
                                self.log(f"[Reverb] Terminate SUCCESS: {r.stdout.strip()}")
                                terminated_ok = True
                            else:
                                self.log(f"[Reverb] taskkill rc={r.returncode}: {r.stderr.strip()}")
                                try:
                                    for p in psutil.process_iter(["name", "pid"]):
                                        try:
                                            if p.info["name"].lower() == app_name.lower():
                                                p.kill()
                                                self.log(f"[Reverb] psutil killed {p.info['pid']}")
                                                terminated_ok = True
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                        except Exception as _e:
                            self.log(f"[Reverb] Terminate error: {_e}")

                        if action_id:
                            self.data_sender.acknowledge_action(action_id, "completed")

                        if terminated_ok:
                            import time as _time_rv
                            _time_rv.sleep(0.3)
                            try:
                                apps = self.app_monitor.get_active_apps_snapshot()
                                self.data_sender.send_active_apps(apps)
                                self.log(f"[Reverb] Active apps refreshed setelah terminate")
                            except Exception as _ae:
                                self.log(f"[Reverb] Gagal refresh active apps: {_ae}")

                    elif "webrtc.browser-offer" in event or "webrtc.request" in event:
                        sdp = inner.get("sdp")
                        sdp_type = inner.get("type", "offer")
                        if sdp:
                            if self.webrtc:
                                self.webrtc.on_browser_offer(sdp, sdp_type)
                        else:
                            if self.webrtc:
                                self.webrtc.on_request()
                    elif "webrtc.answer" in event:
                        sdp = inner.get("sdp", "")
                        sdp_type = inner.get("type", "answer")
                        if self.webrtc:
                            self.webrtc.on_answer(sdp, sdp_type)
                    elif "webrtc.ice" in event:
                        candidate = inner.get("candidate")
                        if self.webrtc and candidate:
                            self.webrtc.on_ice_candidate(candidate)
                    elif "webrtc.stop" in event:
                        if self.webrtc:
                            self.webrtc.on_stop()
                    elif event == "pusher:ping":
                        ws.send(_json_mod.dumps({"event": "pusher:pong", "data": {}}))
                    elif "file.browse" in event:
                        path = inner.get("path", "C:\\")
                        search = inner.get("search")
                        if self.file_manager:
                            self.file_manager.browse(path, search)
                    elif "file.download_request" in event:
                        path = inner.get("path", "")
                        transfer_id = inner.get("transfer_id", "")
                        if self.file_manager and path:
                            self.file_manager.download(path, transfer_id)
                    elif "file.upload_chunk" in event:
                        if self.file_manager:
                            self.file_manager.receive_chunk(
                                transfer_id=inner.get("transfer_id", ""),
                                filename=inner.get("filename", ""),
                                dest_path=inner.get("dest_path", "C:\\"),
                                chunk_index=int(inner.get("chunk_index", 0)),
                                total_chunks=int(inner.get("total_chunks", 1)),
                                data=inner.get("data", ""),
                            )
                    elif "file.app_integrity_check" in event:
                        if self.app_integrity:
                            self.app_integrity.check_single(
                                app_db_id=inner.get("app_db_id"),
                                app_name=inner.get("app_name", ""),
                                install_path=inner.get("install_path"),
                                app_id=inner.get("app_id"),
                                publisher=inner.get("publisher"),
                            )
                    elif "file.app_integrity_batch" in event:
                        if self.app_integrity:
                            self.app_integrity.check_batch(inner.get("apps", []))
                    elif "file.terminal_command" in event:
                        if self.terminal:
                            self.terminal.execute(
                                cmd_id=inner.get("cmd_id", ""),
                                command=inner.get("command", ""),
                                cwd=inner.get("cwd"),
                            )
                except Exception as _e:
                    self.log(f"[Reverb] Message error: {_e}")

            def on_error(ws, err):
                reverb_flag[0] = False
                self.log(f"[Reverb] Error: {err}")

            def on_close(ws, code, msg):
                subscribed[0] = False
                reverb_flag[0] = False
                self.log(f"[Reverb] Disconnected (code={code}), reconnect 10 detik...")
                import time
                time.sleep(10)
                _ws_loop()

            import time
            while True:
                try:
                    self.log(f"[Reverb] Connecting to: {ws_url}")
                    ws_app = websocket.WebSocketApp(
                        ws_url,
                        on_open=on_open,
                        on_message=on_message,
                        on_error=on_error,
                        on_close=on_close,
                    )
                    ws_app.run_forever(ping_interval=30, ping_timeout=10)
                except Exception as _e:
                    reverb_flag[0] = False
                    self.log(f"[Reverb] Connect error: {_e}, retry 10 detik...")
                    time.sleep(10)

        _t.Thread(target=_ws_loop, daemon=True).start()
        self.log("[Reverb] WebSocket listener started")


# Global mutex handle
_SINGLE_INSTANCE_MUTEX_HANDLE = None


def _ensure_single_instance():
    global _SINGLE_INSTANCE_MUTEX_HANDLE
    try:
        import win32event
        import winerror
        mutex_name = "Global\\MonitoringApp_SingleInstance_Mutex"
        handle = win32event.CreateMutex(None, False, mutex_name)
        last_error = win32api.GetLastError()
        if last_error == winerror.ERROR_ALREADY_EXISTS:
            return False
        _SINGLE_INSTANCE_MUTEX_HANDLE = handle
        return True
    except Exception:
        return True


def main():
    if not _ensure_single_instance():
        return

    start_hidden = "--silent" in sys.argv
    root = tk.Tk()
    app = MonitoringApp(root, start_hidden=start_hidden)

    import signal
    def _on_sigint(sig, frame):
        try:
            app.on_close()
        except Exception:
            pass
        root.quit()
        root.destroy()

    signal.signal(signal.SIGINT, _on_sigint)
    def _poll_signals():
        root.after(200, _poll_signals)
    root.after(200, _poll_signals)

    root.mainloop()


if __name__ == "__main__":
    main()