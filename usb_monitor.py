# usb_monitor.py
import threading
import time
import subprocess
import win32api
import win32file
import win32con
import psutil
import re

class USBMonitorThread(threading.Thread):
    """Thread terpisah untuk monitoring dan eject USB - ULTRA FAST VERSION"""
    
    def __init__(self, callback_log, callback_notification, get_usb_config):
        super().__init__(daemon=True)
        self.callback_log = callback_log
        self.callback_notification = callback_notification
        self.get_usb_config = get_usb_config
        self.is_running = True
        self.daemon = True
        # Track ejected drives with timestamp untuk reset otomatis
        self.ejected_drives = {}  # drive -> timestamp
        self.ejected_cooldown = 3  # cooldown 3 detik sebelum bisa eject lagi
        
    def run(self):
        """Main loop untuk monitoring USB - SUPER FAST"""
        try:
            old_drives = set(win32api.GetLogicalDriveStrings().split('\000')[:-1])
        except Exception as e:
            self.callback_log(f"USB Monitor init error: {e}")
            old_drives = set()
        
        while self.is_running:
            try:
                # Cleanup old ejected drives (reset setelah cooldown)
                current_time = time.time()
                to_remove = []
                for drive, timestamp in self.ejected_drives.items():
                    if current_time - timestamp > self.ejected_cooldown:
                        to_remove.append(drive)
                for drive in to_remove:
                    del self.ejected_drives[drive]
                    self.callback_log(f"USB {drive} cooldown expired, can be detected again")
                
                current_drives = set(win32api.GetLogicalDriveStrings().split('\000')[:-1])
                new_drives = current_drives - old_drives
                
                if new_drives:
                    for drive in new_drives:
                        try:
                            if win32file.GetDriveType(drive) == win32file.DRIVE_REMOVABLE:
                                self._handle_usb_detected_ultra_fast(drive)
                        except Exception:
                            pass
                
                old_drives = current_drives
                time.sleep(0.3)
                
            except Exception:
                time.sleep(0.5)
    
    def _handle_usb_detected_ultra_fast(self, drive_path):
        """Ultra fast USB blocking - target under 1 second"""
        config = self.get_usb_config()
        
        if not config.get("enabled") or config.get("mode") == "off":
            return
        
        drive_letter = drive_path.strip('\\')
        
        # Skip if recently ejected (masih dalam cooldown)
        if drive_letter in self.ejected_drives:
            return
        
        # Get UNIQUE identifier (Serial Number)
        usb_info = self._get_usb_unique_identifier(drive_path)
        serial_number = usb_info.get("serial_number", "unknown")
        
        mode = config.get("mode", "off")
        usb_list = [item.lower() for item in config.get("usb_list", [])]
        
        should_eject = False
        reason = ""
        
        identifier = serial_number.lower()
        
        if mode == "block_all":
            should_eject = True
            reason = "Block All USB Storage active"
        elif mode == "blacklist":
            if identifier in usb_list:
                should_eject = True
                reason = f"USB Blacklisted (SN: {serial_number})"
        elif mode == "whitelist":
            if identifier not in usb_list and identifier != "unknown":
                should_eject = True
                reason = f"USB Not Whitelisted (SN: {serial_number})"
        
        if should_eject:
            self.ejected_drives[drive_letter] = time.time()
            self.callback_log(f"USB BLOCKER: Ejecting {drive_letter} [SN: {serial_number}] - {reason}")
            
            # Execute all eject methods in parallel
            threading.Thread(target=self._eject_direct_device, args=(drive_letter,), daemon=True).start()
            threading.Thread(target=self._eject_powershell_fast, args=(drive_letter,), daemon=True).start()
            threading.Thread(target=self._eject_mountvol_fast, args=(drive_letter,), daemon=True).start()
            threading.Thread(target=self._kill_and_remove, args=(drive_letter,), daemon=True).start()
            
            self.callback_notification("Security Alert", f"Perangkat USB Diblokir!\nDrive: {drive_letter}\nSN: {serial_number}\n{reason}")
    
    def _get_usb_unique_identifier(self, drive_path):
        """Mendapatkan unique identifier USB (Serial Number) yang permanen"""
        drive_letter = drive_path.strip('\\')
        
        result = {
            "serial_number": "unknown",
            "device_id": "unknown",
            "volume_name": "unknown",
            "vendor": "unknown",
            "product": "unknown"
        }
        
        try:
            # Method 1: Menggunakan PowerShell untuk mendapatkan Serial Number
            ps_command = f'''
            $drive = "{drive_letter}"
            $driveInfo = Get-WmiObject Win32_LogicalDisk -Filter "DeviceID='$drive'"
            if ($driveInfo) {{
                $driveInfo.DeviceID
                $volume = Get-WmiObject Win32_Volume -Filter "DriveLetter='$drive'"
                if ($volume) {{
                    $volume.SerialNumber
                }}
            }}
            '''
            
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags = subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            
            ps_result = subprocess.run(
                ["powershell", "-Command", ps_command],
                capture_output=True,
                text=True,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=5
            )
            
            if ps_result.stdout:
                lines = ps_result.stdout.strip().split('\n')
                if len(lines) >= 2:
                    result["serial_number"] = lines[1].strip()
            
            # Method 2: Menggunakan WMI untuk mendapatkan informasi lebih detail
            wmi_command = f'''
            $drive = "{drive_letter}"
            $disk = Get-WmiObject Win32_DiskDrive | Where-Object {{ $_.InterfaceType -eq "USB" }}
            if ($disk) {{
                $disk.SerialNumber
                $disk.PNPDeviceID
            }}
            '''
            
            wmi_result = subprocess.run(
                ["powershell", "-Command", wmi_command],
                capture_output=True,
                text=True,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=5
            )
            
            if wmi_result.stdout:
                lines = wmi_result.stdout.strip().split('\n')
                if len(lines) >= 1:
                    if lines[0] and lines[0] != result["serial_number"]:
                        result["serial_number"] = lines[0]
                if len(lines) >= 2:
                    result["device_id"] = lines[1]
                    
        except Exception:
            pass
        
        # Method 3: Fallback menggunakan DeviceIoControl
        if result["serial_number"] == "unknown":
            try:
                handle = win32file.CreateFile(
                    f"\\\\.\\{drive_letter.replace(':', '')}:",
                    win32con.GENERIC_READ,
                    win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
                    None,
                    win32con.OPEN_EXISTING,
                    0,
                    None
                )
                
                if handle:
                    buffer = win32file.DeviceIoControl(handle, 0x2D1400, None, 1024)
                    win32file.CloseHandle(handle)
                    
                    buffer_str = buffer.decode('utf-16-le', errors='ignore')
                    serial_match = re.search(r'[A-Z0-9]{8,}', buffer_str)
                    if serial_match:
                        result["serial_number"] = serial_match.group()
            except Exception:
                pass
        
        # Method 4: Coba dapatkan dari registry
        if result["serial_number"] == "unknown":
            try:
                registry_command = f'''
                $drive = "{drive_letter}"
                $driveLetter = $drive[0]
                $regPath = "HKLM:\\SYSTEM\\CurrentControlSet\\Enum\\USBSTOR\\*\\*"
                $usbDevices = Get-ChildItem $regPath -ErrorAction SilentlyContinue
                foreach ($device in $usbDevices) {{
                    $deviceProps = Get-ItemProperty $device.PSPath
                    if ($deviceProps.FriendlyName -like "*$driveLetter*") {{
                        $device.PSChildName
                        break
                    }}
                }}
                '''
                
                reg_result = subprocess.run(
                    ["powershell", "-Command", registry_command],
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=5
                )
                
                if reg_result.stdout:
                    result["serial_number"] = reg_result.stdout.strip()
            except Exception:
                pass
        
        # Method 5: Fallback terakhir menggunakan Volume Serial Number
        if result["serial_number"] == "unknown":
            try:
                volume_info = win32api.GetVolumeInformation(drive_path)
                if volume_info and volume_info[1]:
                    result["serial_number"] = str(volume_info[1])
            except Exception:
                pass
        
        # Log untuk debugging
        if result["serial_number"] != "unknown":
            self.callback_log(f"USB Detected: {drive_letter} - SN: {result['serial_number']}")
        else:
            self.callback_log(f"USB Detected: {drive_letter} - SN: UNKNOWN (using Device ID)")
            if result["device_id"] != "unknown":
                result["serial_number"] = result["device_id"].split('\\')[-1] if '\\' in result["device_id"] else result["device_id"]
        
        return result
    
    def _eject_direct_device(self, drive_letter):
        """Direct device removal - fastest method"""
        try:
            drive_clean = drive_letter.replace(':', '')
            subprocess.run(
                f'subst {drive_letter} /d',
                shell=True,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=1
            )
            subprocess.run(
                f'mountvol {drive_clean}: /d',
                shell=True,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=1
            )
        except:
            pass
    
    def _eject_powershell_fast(self, drive_letter):
        """Fast PowerShell eject"""
        try:
            ps_command = f'''
            $drive = "{drive_letter}"
            $vol = Get-WmiObject -Class Win32_Volume | Where-Object {{ $_.DriveLetter -eq $drive }}
            if($vol) {{ $vol.Dismount($true, $true) | Out-Null }}
            (New-Object -ComObject Shell.Application).Namespace(17).ParseName($drive).InvokeVerb("Eject")
            '''
            subprocess.Popen(
                ["powershell", "-Command", ps_command],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        except:
            pass
    
    def _eject_mountvol_fast(self, drive_letter):
        """Fast mountvol eject"""
        try:
            drive_clean = drive_letter.replace(':', '')
            subprocess.Popen(
                f'mountvol {drive_clean}: /p',
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        except:
            pass
    
    def _kill_and_remove(self, drive_letter):
        """Kill processes using the drive and remove"""
        try:
            drive_lower = drive_letter.lower()
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    for item in proc.open_files():
                        if item.path and drive_lower in item.path.lower():
                            proc.kill()
                            break
                except:
                    pass
            
            drive_clean = drive_letter.replace(':', '')
            script = f'select volume {drive_clean}\noffline volume\nremove letter={drive_clean}\nexit\n'
            subprocess.Popen(
                f'echo {script} | diskpart',
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        except:
            pass
    
    def stop(self):
        self.is_running = False