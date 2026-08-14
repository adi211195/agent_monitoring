import os
import time
import threading
import psutil
from datetime import datetime

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class DownloadEventHandler(FileSystemEventHandler):

    def __init__(self, monitor):
        self.monitor = monitor

    def on_created(self, event):
        if not event.is_directory:
            threading.Thread(
                target=self.monitor._process_new_file,
                args=(event.src_path,),
                daemon=True
            ).start()

    def on_modified(self, event):
        if not event.is_directory:
            threading.Thread(
                target=self.monitor._process_new_file,
                args=(event.src_path,),
                daemon=True
            ).start()

    def on_moved(self, event):
        if not event.is_directory:
            threading.Thread(
                target=self.monitor._process_new_file,
                args=(event.dest_path,),
                daemon=True
            ).start()


class BrowserDownloadMonitor:

    def __init__(self, log_callback=None, download_log_callback=None, alert_callback=None):

        self.log_callback = log_callback
        self.download_log_callback = download_log_callback
        self.alert_callback = alert_callback

        self.running = False

        self.observer = Observer()
        self.event_handler = DownloadEventHandler(self)

        self.filter_mode = "off"
        self.filter_list = []

        self.processed_files = set()
        self.alerted_files = {}      # --- PERUBAHAN: Hanya untuk mencatat file yang sudah memicu alert
        self.lock = threading.Lock()

        self.temp_extensions = {
            ".crdownload",
            ".part",
            ".partial",
            ".tmp",
            ".download"
        }

        self.cleanup_thread = None
        self.cleanup_stop_event = threading.Event()

    def _log(self, message):
        print(message)
        if self.log_callback:
            try:
                self.log_callback(message)
            except Exception:
                pass

    def get_process_accessing_file(self, file_path):
        browsers = {"chrome.exe", "firefox.exe", "msedge.exe", "opera.exe", "brave.exe"}
        try:
            for proc in psutil.process_iter(["pid", "name", "exe"]):
                try:
                    proc_name = proc.info["name"].lower()
                    if proc_name in browsers:
                        for open_file in proc.open_files():
                            if file_path in open_file.path:
                                return proc.info["name"]
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            for proc in psutil.process_iter(["pid", "name", "exe"]):
                try:
                    proc_name = proc.info["name"].lower()
                    if proc_name in browsers:
                        return proc.info["name"]
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return "Unknown"
        except Exception:
            return "Unknown"

    def set_config(self, mode, filter_list):
        self.filter_mode = (mode or "off").lower()
        self.filter_list = [
            str(item).strip().lower().replace(".", "")
            for item in (filter_list or [])
        ]

    def _get_download_directories(self):
        paths = []
        try:
            download_path = os.path.join(os.path.expanduser("~"), "Downloads")
            if os.path.exists(download_path):
                paths.append(download_path)
        except Exception as e:
            self._log(f"Download folder error: {e}")
        return paths

    def _should_block(self, extension):
        extension = extension.lower().replace(".", "")
        if self.filter_mode == "block_all":
            return True
        if self.filter_mode == "blacklist":
            return extension in self.filter_list
        if self.filter_mode == "whitelist":
            return extension not in self.filter_list
        return False

    def _delete_file(self, file_path):
        # Loop agresif untuk memastikan file benar-benar terhapus
        file_existed = False
        for attempt in range(100):
            if os.path.exists(file_path):
                file_existed = True
                try:
                    os.remove(file_path)
                    if not os.path.exists(file_path):
                        return True
                except PermissionError:
                    time.sleep(0.05) # Tunggu lock browser lepas
                except Exception:
                    return False
            else:
                if file_existed: 
                    return True # File sempat ada dan sekarang sudah hilang (terhapus)
                time.sleep(0.05)
        return False

    def _process_new_file(self, file_path):
        try:
            filename = os.path.basename(file_path)
            ext = os.path.splitext(filename)[1].lower()

            if ext in self.temp_extensions:
                return

            with self.lock:
                # Mencegah double-proses di milidetik yang sama oleh thread lain
                if file_path in self.processed_files:
                    return
                
                if not os.path.exists(file_path):
                    return
                
                self.processed_files.add(file_path)

            ext_clean = ext.replace(".", "")
            blocked = self._should_block(ext_clean)
            
            if blocked:
                success = self._delete_file(file_path)
                if success:
                    self._log(f"BLOCKED & DELETED: {filename}")
                    
                    # --- LOGIKA DEBOUNCE ALERT ---
                    current_time = time.time()
                    should_trigger_alert = False
                    
                    with self.lock:
                        # Jika belum pernah di-alert, atau alert terakhir sudah lebih dari 10 detik lalu
                        if file_path not in self.alerted_files or (current_time - self.alerted_files[file_path] > 10):
                            should_trigger_alert = True
                            self.alerted_files[file_path] = current_time
                    
                    if should_trigger_alert and self.alert_callback:
                        self.alert_callback(
                            "Security Alert",
                            f"Download Diblokir!\nFile: {filename}\nEkstensi: .{ext_clean}\nAlasan: Melanggar aturan download filter"
                        )
                else:
                    self._log(f"FAILED TO DELETE: {filename}")
                
                # Selalu lepaskan status processed agar jika browser menulis ulang filenya, 
                # script bisa langsung mendeteksi dan menghapusnya lagi.
                with self.lock:
                    self.processed_files.discard(file_path)
            else:
                self._log(f"ALLOWED: {filename} (extension={ext_clean})")
                def remove_later():
                    time.sleep(60)
                    with self.lock:
                        self.processed_files.discard(file_path)
                threading.Thread(target=remove_later, daemon=True).start()

            # Cari app_name setelah penindakan selesai
            app_name = self.get_process_accessing_file(file_path)

            log_record = {
                "file_path": file_path,
                "file_name": filename,
                "extension": ext_clean,
                "app_name": app_name,
                "directory": os.path.dirname(file_path),
                "timestamp": datetime.now().isoformat(),
                "blocked": blocked
            }

            if self.download_log_callback:
                try:
                    self.download_log_callback(log_record)
                except Exception:
                    pass

        except Exception as e:
            self._log(f"PROCESS ERROR: {e}")
            
    def _cleanup_worker(self):
        while not self.cleanup_stop_event.is_set():
            try:
                now = time.time()
                with self.lock:
                    dead_files = {
                        path for path in self.processed_files if not os.path.exists(path)
                    }
                    self.processed_files -= dead_files
                    
                    # Bersihkan cache history alert yang sudah lewat dari 10 detik agar RAM tetap hemat
                    expired_alerts = [
                        path for path, ts in self.alerted_files.items() if now - ts > 10
                    ]
                    for path in expired_alerts:
                        del self.alerted_files[path]
            except Exception as e:
                self._log(f"CLEANUP ERROR: {e}")
            time.sleep(10)

    def start(self):
        if self.running:
            return
        directories = self._get_download_directories()
        if not directories:
            self._log("Downloads directory not found")
            return
        for directory in directories:
            self.observer.schedule(
                self.event_handler, directory, recursive=False
            )
            self._log(f"Monitoring: {directory}")
        self.observer.start()
        self.cleanup_stop_event.clear()
        self.cleanup_thread = threading.Thread(
            target=self._cleanup_worker, daemon=True
        )
        self.cleanup_thread.start()
        self.running = True
        self._log("Browser Download Monitor started")

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.observer.stop()
        self.observer.join()
        self.cleanup_stop_event.set()
        if self.cleanup_thread:
            self.cleanup_thread.join(timeout=2)
        self._log("Browser Download Monitor stopped")