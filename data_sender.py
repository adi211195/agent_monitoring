import requests
import json
import uuid
import platform
import psutil
import subprocess
import os
from datetime import datetime
from app_paths import get_app_data_path

class DataSender:
    def __init__(self, server_url=None):
        self.config_path = self._resolve_config_path()
        config = self._load_config()

        self.server_url = config.get("server_url")
        self.device_id = config.get("device_id")
        self.token = config.get("token")  # Token Sanctum, didapat saat register_device()

        # Jika device_id belum ada di config, hasilkan dari hardware
        if not self.device_id:
            self.device_id = self._generate_hardware_id()

        # Jika ada server_url yang dioper saat init, gunakan itu (biasanya untuk testing)
        if server_url:
            self.server_url = server_url

    def _headers(self, with_json=True):
        """
        Header standar untuk semua request ke API.
        Menyertakan token Sanctum (kalau sudah ada) supaya endpoint yang
        diproteksi auth:sanctum bisa diakses.
        """
        headers = {"Accept": "application/json"}
        if with_json:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _resolve_config_path(self):
        """
        Pakai path absolut di %LOCALAPPDATA%\\MonitoringApp\\device_config.json,
        bukan path relatif "device_config.json".

        Ini penting karena aplikasi sekarang di-install ke Program Files dan/atau
        dijalankan otomatis lewat Windows Startup / Task Scheduler, di mana
        current working directory belum tentu sama dengan folder tempat exe
        berada (dan Program Files sendiri terproteksi, tidak writable untuk user
        biasa) -- kalau tetap pakai path relatif, config bisa gagal
        tersimpan/terbaca dengan PermissionError.
        """
        return get_app_data_path("device_config.json")

    def _generate_hardware_id(self):
        """Menghasilkan ID unik yang konsisten untuk komputer yang sama (Hardware ID)"""
        try:
            # Menggabungkan MAC address, hostname, dan platform
            node = uuid.getnode()
            hostname = platform.node()
            system = platform.system()
            
            # Kita buat string unik berdasarkan hardware
            unique_str = f"{node}-{hostname}-{system}"
            
            # Gunakan uuid5 dengan namespace DNS untuk menghasilkan UUID yang konsisten dari string tersebut
            return str(uuid.uuid5(uuid.NAMESPACE_DNS, unique_str))
        except Exception as e:
            # Fallback jika gagal (sangat jarang)
            return str(uuid.uuid4())

    def _load_config(self):
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "r") as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading config: {e}")
        return {}

    def is_registered(self):
        """Mengecek apakah device sudah terdaftar (punya device_id, server_url, dan token)"""
        return bool(self.device_id and self.server_url and self.token)

    def register_device(self, server_url):
        """Mendaftarkan device ke server baru"""
        # Bersihkan URL (hapus trailing slash)
        base_url = server_url.strip().rstrip('/')
        
        # Tentukan register endpoint dan base monitoring endpoint
        if "/api/monitoring" in base_url:
            register_url = f"{base_url}/register"
            target_server_url = base_url
        else:
            register_url = f"{base_url}/api/monitoring/register"
            target_server_url = f"{base_url}/api/monitoring"

        # Gunakan device_id yang sudah ada (hardware ID) atau buat baru jika belum ada
        if not self.device_id:
            self.device_id = self._generate_hardware_id()
        
        device_id = self.device_id
        
        # Simpan sementara untuk fallback jika gagal
        old_server_url = self.server_url
        
        system_info = self._get_system_info()

        # SESUAIKAN DENGAN EKSPEKTASI SERVER (Key: device_info)
        payload = {
            "device_info": system_info,
            "timestamp": datetime.now().isoformat()
        }

        try:
            response = requests.post(register_url, json=payload, timeout=15)
            if response.status_code in [200, 201]:
                data = response.json()
                token = data.get("token")

                if not token:
                    # Server versi lama belum kirim token -> jangan lanjut,
                    # supaya tidak "sukses" registrasi tapi ujung-ujungnya 401 terus
                    self.server_url = old_server_url
                    return {"success": False, "error": "Server tidak mengembalikan token. Pastikan server sudah menerapkan Sanctum."}

                self.server_url = target_server_url
                self.token = token

                # Simpan permanen ke config
                with open(self.config_path, "w") as f:
                    json.dump({
                        "device_id": self.device_id,
                        "server_url": self.server_url,
                        "token": self.token
                    }, f, indent=4)
                return {"success": True, "device_id": device_id}
            
            # Revert jika gagal
            self.server_url = old_server_url
            return {"success": False, "error": f"Server returned status {response.status_code}"}
        except Exception as e:
            # Revert jika error
            self.server_url = old_server_url
            return {"success": False, "error": str(e)}

    def _get_or_create_device_id(self):
        # Method ini sekarang digantikan oleh logika di init dan register_device
        return self.device_id

    def _get_system_info(self):
        # Mendapatkan info CPU, RAM, Disk
        cpu_usage = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Info Baterai
        battery_info = {}
        try:
            battery = psutil.sensors_battery()
            if battery:
                battery_info = {
                    "percent": battery.percent,
                    "power_plugged": battery.power_plugged,
                    "secsleft": battery.secsleft
                }
        except:
            pass

        # Detail Tambahan via WMIC (Manufacturer, Model)
        manufacturer = "Unknown"
        model = "Unknown"
        try:
            if platform.system() == "Windows":
                # creationflags=CREATE_NO_WINDOW mencegah CMD popup sekilas muncul.
                # Tanpa ini, setiap panggilan wmic (yang terjadi di SETIAP pengiriman
                # data ke server) akan memunculkan jendela console hitam sekilas,
                # karena aplikasi ini dibuild --windowed (tanpa console sendiri).
                no_window = subprocess.CREATE_NO_WINDOW
                manufacturer = subprocess.check_output(
                    'wmic computersystem get manufacturer',
                    creationflags=no_window
                ).decode().split('\n')[1].strip()
                model = subprocess.check_output(
                    'wmic computersystem get model',
                    creationflags=no_window
                ).decode().split('\n')[1].strip()
        except:
            pass

        return {
            "device_id": self.device_id,
            "hostname": platform.node(),
            "manufacturer": manufacturer,
            "model": model,
            "os": f"{platform.system()} {platform.release()}",
            "os_version": platform.version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_usage_percent": cpu_usage,
            "memory": {
                "total": memory.total,
                "available": memory.available,
                "percent": memory.percent
            },
            "disk": {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": disk.percent
            },
            "battery": battery_info,
            "python_version": platform.python_version(),
            "timestamp": datetime.now().isoformat()
        }

    def send_app_usage(self, app_data, location_data=None):
        payload = {
            "device_info": self._get_system_info(),
            "data_type": "app_usage",
            "app_usage": app_data,
            "location": location_data,
            "sent_at": datetime.now().isoformat()
        }

        try:
            response = requests.post(
                self.server_url + "/app-usage",
                json=payload,
                headers=self._headers(),
                timeout=10
            )
            return {"success": True, "status_code": response.status_code}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection refused - server not available"}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Request timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_browsing_history(self, history_data, location_data=None):
        payload = {
            "device_info": self._get_system_info(),
            "data_type": "browsing_history",
            "browsing_history": history_data,
            "location": location_data,
            "sent_at": datetime.now().isoformat()
        }

        try:
            response = requests.post(
                self.server_url + "/browsing-history",
                json=payload,
                headers=self._headers(),
                timeout=15
            )
            return {"success": True, "status_code": response.status_code}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection refused - server not available"}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Request timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_all_data(self, app_usage_data, browsing_history_data, location_data=None):
        results = {
            "app_usage": self.send_app_usage(app_usage_data, location_data),
            "browsing_history": self.send_browsing_history(browsing_history_data, location_data)
        }

        return results

    def verify_registration(self):
        """Memverifikasi apakah device_id masih terdaftar di server"""
        if not self.is_registered():
            return {"success": False, "error": "Not registered localy"}
            
        verify_url = f"{self.server_url}/verify-registration"
        payload = {
            "device_id": self.device_id,
            "device_info": self._get_system_info(),
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            # Gunakan timeout pendek untuk pengecekan berkala
            response = requests.post(verify_url, json=payload, timeout=10)
            if response.status_code == 200:
                return {"success": True}
            elif response.status_code == 404:
                return {"success": False, "error": "Device not found on server", "code": 404}
            else:
                return {"success": False, "error": f"Server error: {response.status_code}"}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection refused", "code": "offline"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def logout(self):
        """Menghapus konfigurasi lokal (logout)"""
        self.device_id = None
        self.server_url = None
        self.token = None
        if os.path.exists(self.config_path):
            try:
                os.remove(self.config_path)
                return True
            except:
                pass
        return False

    def fetch_config(self):
        """Mengambil konfigurasi lengkap dari API"""
        if not self.is_registered():
            return {"success": False, "error": "Not registered"}
            
        # Endpoint ini sekarang mengenali device dari token (Authorization: Bearer),
        # jadi tidak perlu lagi mengirim device_id sebagai parameter.
        config_url = f"{self.server_url}/config"
        
        try:
            response = requests.get(config_url, headers=self._headers(with_json=False), timeout=10)
            if response.status_code == 401:
                return {"success": False, "error": "Unauthorized - token tidak valid, perlu register ulang", "code": 401}
            if response.status_code == 200:
                data = response.json()
                # Ekstrak objek config dari respons: {"status": "success", "config": {...}}
                config_data = data.get("config", {})
                
                # Simpan ke config lokal untuk fallback
                current_config = self._load_config()
                current_config["last_config"] = config_data
                
                with open(self.config_path, "w") as f:
                    json.dump(current_config, f, indent=4)
                    
                return {"success": True, "config": config_data}
            return {"success": False, "error": f"Status {response.status_code}"}
        except Exception as e:
            # Fallback ke config terakhir jika gagal (offline)
            current_config = self._load_config()
            if "last_config" in current_config:
                return {"success": True, "config": current_config["last_config"], "is_fallback": True}
            return {"success": False, "error": str(e)}

    def fetch_interval(self):
        # Method ini sekarang bisa digantikan oleh fetch_config, 
        # tapi tetap dipertahankan untuk kompatibilitas jika perlu
        result = self.fetch_config()
        if result.get("success"):
            interval = result["config"].get("sync_interval", 300)
            return {"success": True, "interval": interval}
        return result

    def send_screenshot(self, screenshot_data, location_data=None):
        if not screenshot_data:
            return {"success": False, "error": "No screenshot data provided"}

        payload = {
            "device_info": self._get_system_info(),
            "data_type": "screenshot",
            "filename": screenshot_data.get("filename", "screenshot.png"),
            "timestamp": screenshot_data.get("timestamp", datetime.now().isoformat()),
            "image_base64": screenshot_data.get("image_base64", ""),
            "location": location_data,
            "sent_at": datetime.now().isoformat()
        }

        try:
            response = requests.post(
                self.server_url + "/screenshot",
                json=payload,
                headers=self._headers(),
                timeout=30
            )
            return {"success": True, "status_code": response.status_code}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection refused - server not available"}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Request timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_all_data_with_screenshot(self, app_usage_data, browsing_history_data, screenshot_data=None, location_data=None):
        results = {
            "app_usage": self.send_app_usage(app_usage_data, location_data),
            "browsing_history": self.send_browsing_history(browsing_history_data, location_data)
        }

        if screenshot_data:
            results["screenshot"] = self.send_screenshot(screenshot_data, location_data)

        return results

    def send_recording(self, recording_data, location_data=None):
        if not recording_data:
            return {"success": False, "error": "No recording data provided"}

        payload = {
            "device_info": self._get_system_info(),
            "data_type": "screen_recording",
            "filename": recording_data.get("filename", "recording.mp4"),
            "start_time": recording_data.get("start_time", ""),
            "end_time": recording_data.get("end_time", ""),
            "duration_seconds": recording_data.get("duration_seconds", 0),
            "frame_count": recording_data.get("frame_count", 0),
            "video_base64": recording_data.get("video_base64", ""),
            "location": location_data,
            "sent_at": datetime.now().isoformat()
        }

        try:
            response = requests.post(
                self.server_url + "/recording",
                json=payload,
                headers=self._headers(),
                timeout=60
            )
            return {"success": True, "status_code": response.status_code}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection refused - server not available"}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Request timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_upload_activity(self, upload_data, location_data=None):
        if not upload_data:
            return {"success": False, "error": "No upload data provided"}

        payload = {
            "device_info": self._get_system_info(),
            "data_type": "file_upload_activity",
            "upload_activities": upload_data,
            "location": location_data,
            "sent_at": datetime.now().isoformat()
        }

        try:
            response = requests.post(
                self.server_url + "/upload-activity",
                json=payload,
                headers=self._headers(),
                timeout=10
            )
            return {"success": True, "status_code": response.status_code}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection refused - server not available"}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Request timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_idle_events(self, idle_data, location_data=None):
        if not idle_data:
            return {"success": False, "error": "No idle data provided"}

        payload = {
            "device_info": self._get_system_info(),
            "data_type": "idle_event",
            "idle_events": idle_data,
            "location": location_data,
            "sent_at": datetime.now().isoformat()
        }

        try:
            response = requests.post(
                self.server_url + "/idle-event",
                json=payload,
                headers=self._headers(),
                timeout=10
            )
            return {"success": True, "status_code": response.status_code}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection refused - server not available"}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Request timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_keystrokes(self, keystrokes_data, location_data=None):
        if not keystrokes_data:
            return {"success": False, "error": "No keystrokes data provided"}

        payload = {
            "device_info": self._get_system_info(),
            "data_type": "keystrokes",
            "keystrokes": keystrokes_data,
            "location": location_data,
            "sent_at": datetime.now().isoformat()
        }

        try:
            response = requests.post(
                self.server_url + "/keystrokes",
                json=payload,
                headers=self._headers(),
                timeout=15
            )
            return {"success": True, "status_code": response.status_code}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection refused - server not available"}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Request timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_location(self, location_data):
        if not location_data:
            return {"success": False, "error": "No location data provided"}

        payload = {
            "device_info": self._get_system_info(),
            "data_type": "location",
            "location": location_data,
            "sent_at": datetime.now().isoformat()
        }

        try:
            response = requests.post(
                self.server_url + "/location",
                json=payload,
                headers=self._headers(),
                timeout=10
            )
            return {"success": True, "status_code": response.status_code}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection refused - server not available"}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Request timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def download_file(self, file_id, local_path):
        """Download file dari server ke path lokal"""
        if not self.is_registered():
            return {"success": False, "error": "Not registered"}
            
        # Dapatkan base URL server (tanpa /api/monitoring)
        base_url = self.server_url.replace("/api/monitoring", "")
        download_url = f"{base_url}/api/monitoring/downloadFile/{file_id}"
        
        try:
            response = requests.get(download_url, headers=self._headers(with_json=False), timeout=60, stream=True)
            
            if response.status_code == 200:
                # Pastikan direktori tujuan ada
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                
                # Tulis file secara streaming
                with open(local_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                return {"success": True, "path": local_path}
            else:
                return {"success": False, "error": f"Server returned status {response.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_download_logs(self, download_logs):
        """Send download activity logs to server"""
        if not download_logs:
            return {"success": True, "message": "No logs to send"}
            
        if not self.is_registered():
            return {"success": False, "error": "Not registered"}
            
        payload = {
            "device_info": self._get_system_info(),
            "download_activities": download_logs
        }
        
        try:
            response = requests.post(
                self.server_url + "/download-activity",
                json=payload,
                headers=self._headers(),
                timeout=30
            )
            if response.status_code in [200, 201]:
                return {"success": True, "status_code": response.status_code}
            else:
                return {"success": False, "error": f"Server returned status {response.status_code}"}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection refused"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # =========================
    # REMOTE CONTROL
    # =========================
    def fetch_pending_actions(self):
        """
        Mengambil daftar perintah remote control yang masih menunggu dieksekusi
        (Shutdown / Restart / Send Messages) dari dashboard admin.
        """
        if not self.is_registered():
            return {"success": False, "error": "Not registered"}

        actions_url = f"{self.server_url}/actions"

        try:
            response = requests.get(actions_url, headers=self._headers(with_json=False), timeout=10)
            if response.status_code == 401:
                return {"success": False, "error": "Unauthorized - token tidak valid", "code": 401}
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "actions": data.get("actions", [])}
            return {"success": False, "error": f"Status {response.status_code}"}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection refused - server not available"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def acknowledge_action(self, action_id, status):
        """
        Melaporkan hasil eksekusi perintah remote control ke server.
        status hanya boleh 'completed' atau 'failed'.
        """
        if not self.is_registered():
            return {"success": False, "error": "Not registered"}

        ack_url = f"{self.server_url}/actions/{action_id}/ack"
        payload = {"status": status}

        try:
            response = requests.post(ack_url, json=payload, headers=self._headers(), timeout=10)
            if response.status_code == 200:
                return {"success": True}
            return {"success": False, "error": f"Status {response.status_code}"}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection refused - server not available"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # =========================
    # REMOTE DESKTOP CONTROL (live screen + mouse/keyboard)
    # =========================
    def fetch_remote_status(self):
        """Cek apakah admin sedang membuka sesi remote control untuk device ini."""
        if not self.is_registered():
            return {"success": False, "error": "Not registered"}

        try:
            response = requests.get(
                f"{self.server_url}/remote/status",
                headers=self._headers(with_json=False),
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "remote_active": data.get("remote_active", False)}
            return {"success": False, "error": f"Status {response.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def upload_remote_frame(self, image_base64, width, height):
        """
        Upload frame layar ke server. Response sekarang langsung berisi
        antrian events (mouse/keyboard/terminate) yang perlu dieksekusi —
        menggantikan panggilan fetch_remote_events() yang terpisah.
        Ini yang paling signifikan mengurangi lag remote control.
        """
        if not self.is_registered():
            return {"success": False, "error": "Not registered", "remote_active": False, "events": []}

        payload = {
            "image_base64": image_base64,
            "screen_width": width,
            "screen_height": height,
        }

        try:
            response = requests.post(
                f"{self.server_url}/remote/frame",
                json=payload,
                headers=self._headers(),
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "remote_active": data.get("remote_active", True),
                    "events": data.get("events", []),
                }
            return {"success": False, "error": f"Status {response.status_code}", "remote_active": True, "events": []}
        except Exception as e:
            return {"success": False, "error": str(e), "remote_active": True, "events": []}

    def fetch_remote_events(self):
        """Ambil antrian event mouse/keyboard yang perlu dieksekusi."""
        if not self.is_registered():
            return {"success": False, "error": "Not registered"}

        try:
            response = requests.get(
                f"{self.server_url}/remote/events",
                headers=self._headers(with_json=False),
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "events": data.get("events", [])}
            return {"success": False, "error": f"Status {response.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_active_apps(self, apps_list):
        """
        Kirim snapshot aplikasi yang sedang terbuka SAAT INI (bukan riwayat).
        Dipanggil berkala (setiap connection_check_interval, default 30 detik)
        supaya dashboard admin bisa menampilkan kondisi "live".
        """
        payload = {
            "device_info": self._get_system_info(),
            "active_apps": apps_list,
        }

        try:
            response = requests.post(
                self.server_url + "/active-apps",
                json=payload,
                headers=self._headers(),
                timeout=10
            )
            return {"success": response.status_code == 200, "status_code": response.status_code}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection refused - server not available"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def terminate_app(self, app_name):
        """Kirim request terminate dari server (setelah admin klik Terminate di dashboard)."""
        if not self.is_registered():
            return {"success": False, "error": "Not registered"}

        payload = {"app_name": app_name}

        try:
            response = requests.post(
                self.server_url + "/terminate-app",
                json=payload,
                headers=self._headers(),
                timeout=10
            )
            return {"success": response.status_code == 200}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def fetch_pending_terminate_actions(self):
        """
        Ambil device_actions dengan type 'Terminate App' yang masih in_progress.
        Dipanggil tiap 5 detik oleh fast_action_loop di main_app.py supaya
        terminate bisa jalan tanpa menunggu connection_check_interval (30 detik).
        """
        if not self.is_registered():
            return {"success": False, "error": "Not registered", "actions": []}

        try:
            response = requests.get(
                f"{self.server_url}/actions",
                headers=self._headers(with_json=False),
                timeout=5
            )
            if response.status_code == 200:
                data    = response.json()
                # Filter hanya Terminate App dari semua pending actions
                actions = [a for a in data.get("actions", [])
                           if a.get("action_type") == "Terminate App"]
                return {"success": True, "actions": actions}
            return {"success": False, "error": f"Status {response.status_code}", "actions": []}
        except Exception as e:
            return {"success": False, "error": str(e), "actions": []}

    # ── Remote Chat ──────────────────────────────────────────────────────────────
    def fetch_chat_messages(self):
        """Ambil pesan masuk dari admin yang belum dibaca."""
        if not self.is_registered():
            return {"success": False, "messages": []}
        try:
            r = requests.get(
                f"{self.server_url}/chat/messages",
                headers=self._headers(with_json=False),
                timeout=5
            )
            if r.status_code == 200:
                return {"success": True, "messages": r.json().get("messages", [])}
            return {"success": False, "messages": []}
        except Exception:
            return {"success": False, "messages": []}

    def send_chat_reply(self, message):
        """Agent kirim balasan ke admin."""
        if not self.is_registered():
            return {"success": False}
        try:
            r = requests.post(
                f"{self.server_url}/chat/reply",
                json={"message": message},
                headers=self._headers(),
                timeout=5
            )
            return {"success": r.status_code == 200}
        except Exception:
            return {"success": False}

    def ack_chat_end(self):
        """Konfirmasi ke server bahwa agent sudah proses __CHAT_ENDED__, hapus dari DB."""
        if not self.is_registered():
            return
        try:
            requests.post(
                f"{self.server_url}/chat/ack-end",
                json={},
                headers=self._headers(),
                timeout=5
            )
        except Exception:
            pass

    def send_agent_chat(self, message):
        """Agent kirim pesan ke admin (bisa initiate duluan)."""
        return self.send_chat_reply(message)

    def fetch_all_chat_messages(self):
        """Ambil semua pesan chat (admin+agent) untuk ditampilkan di tab Monitoring agent."""
        if not self.is_registered():
            return {"success": False, "messages": []}
        try:
            # Gunakan endpoint yang sama tapi parameter berbeda
            r = requests.get(
                f"{self.server_url}/chat/all",
                headers=self._headers(with_json=False),
                timeout=5
            )
            if r.status_code == 200:
                return {"success": True, "messages": r.json().get("messages", [])}
            return {"success": False, "messages": []}
        except Exception:
            return {"success": False, "messages": []}

    def fetch_reverb_config(self):
        """Ambil konfigurasi Reverb untuk connect WebSocket."""
        if not self.is_registered():
            return None
        try:
            r = requests.get(
                f"{self.server_url}/chat/reverb-config",
                headers=self._headers(with_json=False),
                timeout=5
            )
            if r.status_code == 200:
                return r.json()
            return None
        except Exception:
            return None

    def get_channel_auth(self, socket_id, channel_name):
        """Dapatkan auth token untuk private channel Reverb."""
        if not self.is_registered():
            return None
        try:
            r = requests.post(
                f"{self.base_url}/broadcasting/auth",
                json={"socket_id": socket_id, "channel_name": channel_name},
                headers=self._headers(),
                timeout=5
            )
            if r.status_code == 200:
                return r.json().get("auth")
            return None
        except Exception:
            return None
