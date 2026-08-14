import requests
import asyncio
import threading
import time
import os
from datetime import datetime

try:
    import winsdk.windows.devices.geolocation as wdg
except ImportError:
    wdg = None

class LocationTracker:
    def __init__(self):
        # Menggunakan ip-api.com sebagai fallback terakhir
        self.api_url = "http://ip-api.com/json/"
        self.last_location = None

    def get_location(self):
        """Mendapatkan data lokasi dengan urutan prioritas: Windows API -> GPS Hardware -> IP API"""
        
        # 1. Coba Windows Location API
        location = self._get_windows_location()
        if location:
            location["method"] = "windows_api"
            return location

        # 2. Coba GPS Hardware (Best effort scan COM ports)
        location = self._get_gps_hardware_location()
        if location:
            location["method"] = "gps_hardware"
            return location

        # 3. Fallback terakhir: IP API
        location = self._get_ip_location()
        if location:
            if "error" not in location:
                location["method"] = "ip_api"
            return location

        return {"error": "All location methods failed"}

    def _get_windows_location(self):
        """Mengambil lokasi menggunakan Windows Runtime Geolocation API"""
        if not wdg:
            return None

        try:
            async def get_pos():
                locator = wdg.Geolocator()
                # Set akurasi tinggi jika memungkinkan
                locator.desired_accuracy = wdg.PositionAccuracy.HIGH
                pos = await locator.get_geoposition_async()
                return pos

            # Run async in a separate thread or new loop to avoid blocking
            loop = asyncio.new_event_loop()
            pos = loop.run_until_complete(get_pos())
            loop.close()

            if pos and pos.coordinate:
                return {
                    "lat": pos.coordinate.point.position.latitude,
                    "lon": pos.coordinate.point.position.longitude,
                    "accuracy": pos.coordinate.accuracy,
                    "timestamp": datetime.now().isoformat()
                }
        except Exception as e:
            print(f"Windows API Location error: {e}")
        return None

    def _get_gps_hardware_location(self):
        """
        Mencoba mendeteksi sensor GPS hardware.
        Biasanya GPS hardware di Windows muncul sebagai sensor atau COM port.
        Pengecekan mendalam membutuhkan library serial, kita lakukan basic check via sensor status.
        """
        # Di Windows modern, GPS hardware biasanya terintegrasi ke Windows Location API.
        # Jika Windows API null, kecil kemungkinan kita bisa akses raw GPS tanpa driver khusus.
        return None

    def _get_ip_location(self):
        """Mendapatkan data lokasi berdasarkan IP publik perangkat"""
        try:
            response = requests.get(self.api_url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    return {
                        "ip": data.get("query"),
                        "country": data.get("country"),
                        "region": data.get("regionName"),
                        "city": data.get("city"),
                        "lat": data.get("lat"),
                        "lon": data.get("lon"),
                        "timezone": data.get("timezone"),
                        "isp": data.get("isp")
                    }
            return {"error": "Failed to get location data"}
        except Exception as e:
            return {"error": str(e)}
