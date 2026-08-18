"""
Remote Desktop Control - sisi agent (versi stabil, single-thread capture).

Alur:
1. _watch_loop polling status tiap 2 detik (hemat resource)
2. Begitu sesi aktif -> _run_active_session():
   - Capture layar di thread yang SAMA dengan mss context (mss tidak thread-safe)
   - Upload frame + langsung dapat events mouse/keyboard/terminate dalam 1 request
   - Eksekusi events langsung tanpa jeda
3. Terminate App bisa datang dari remote_input_events (saat sesi aktif, respons cepat)
   atau dari device_actions (saat sesi tidak aktif, lewat fast_action_loop tiap 5 detik)
"""
import time
import threading
import base64
import io
import ctypes
import subprocess

import mss
from PIL import Image
import win32api
import win32con


# ─── DPI Awareness ──────────────────────────────────────────────────────────
# Penting supaya mss capture di resolusi FISIK (bukan scaled).
# Kalau agent pakai DPI scaling 125%/150%, tanpa ini frame yang ditangkap
# akan berukuran berbeda dari resolusi layar sesungguhnya.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware v2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# ─── Invisible cursor (lebih reliable dari ShowCursor) ──────────────────────
# ShowCursor hanya menurunkan counter visibility di thread tertentu, sedangkan
# SetSystemCursor mengganti kursor secara global di seluruh sistem.

_blank_cursor_handle = None

def _create_blank_cursor():
    global _blank_cursor_handle
    if _blank_cursor_handle:
        return _blank_cursor_handle
    try:
        # Buat cursor 32x32 transparan (AND=0xFF semua, XOR=0x00 semua)
        AND_mask = (ctypes.c_ubyte * 128)(*([0xFF] * 128))
        XOR_mask = (ctypes.c_ubyte * 128)(*([0x00] * 128))
        _blank_cursor_handle = ctypes.windll.user32.CreateCursor(
            0, 0, 0, 32, 32, AND_mask, XOR_mask
        )
    except Exception:
        pass
    return _blank_cursor_handle

def _hide_cursor():
    """Ganti kursor sistem dengan kursor transparan — tidak terlihat sama sekali."""
    try:
        blank = _create_blank_cursor()
        if blank:
            # Ganti semua jenis kursor standar jadi blank
            for cursor_id in [32512, 32513, 32514, 32515, 32516, 32642, 32643, 32644, 32645, 32646, 32648, 32649, 32650, 32651]:
                ctypes.windll.user32.SetSystemCursor(blank, cursor_id)
    except Exception:
        pass

def _show_cursor():
    """Kembalikan kursor sistem ke default."""
    try:
        # SPI_SETCURSORS (0x0057) — restore semua kursor ke default sistem
        ctypes.windll.user32.SystemParametersInfoW(0x0057, 0, None, 3)
    except Exception:
        pass


# --- Key mapping browser -> Windows VK ---
_KEY_MAP = {
    "Enter": win32con.VK_RETURN, "Backspace": win32con.VK_BACK,
    "Tab": win32con.VK_TAB, "Escape": win32con.VK_ESCAPE,
    " ": win32con.VK_SPACE, "ArrowLeft": win32con.VK_LEFT,
    "ArrowRight": win32con.VK_RIGHT, "ArrowUp": win32con.VK_UP,
    "ArrowDown": win32con.VK_DOWN, "Shift": win32con.VK_SHIFT,
    "Control": win32con.VK_CONTROL, "Alt": win32con.VK_MENU,
    "Delete": win32con.VK_DELETE, "Home": win32con.VK_HOME,
    "End": win32con.VK_END, "PageUp": win32con.VK_PRIOR,
    "PageDown": win32con.VK_NEXT, "CapsLock": win32con.VK_CAPITAL,
    "Meta": win32con.VK_LWIN,
}
for _i in range(1, 13):
    _v = getattr(win32con, f"VK_F{_i}", None)
    if _v:
        _KEY_MAP[f"F{_i}"] = _v


def _key_to_vk(key):
    if key in _KEY_MAP:
        return _KEY_MAP[key]
    if len(key) == 1:
        vk = win32api.VkKeyScan(key)
        if vk != -1:
            return vk & 0xFF
    return None


class RemoteControlAgent:
    def __init__(self, data_sender, log_callback=None):
        self.data_sender  = data_sender
        self.log          = log_callback or (lambda m: None)
        self._active      = False
        self._stop_flag   = threading.Event()

    def stop_watching(self):
        """Dipanggil saat logout/shutdown."""
        self._stop_flag.set()
        self._active = False
        _show_cursor()

    def _start_session_from_outside(self):
        """
        Dipanggil oleh check_loop di main_app.py (lewat thread baru) saat
        mendeteksi admin membuka sesi remote. Tidak ada thread watcher
        tersendiri yang terus-menerus polling /remote/status — cukup
        check_loop biasa yang sudah ada.
        """
        if self._active:
            return  # sesi sudah jalan, abaikan

        self._active = True
        self._stop_flag.clear()
        # VIEW-ONLY: tidak hide cursor di agent karena admin tidak kontrol mouse
        self.log("Remote control: sesi dimulai (view-only)")
        try:
            self._run_active_session()
        finally:
            self._active = False
            self.log("Remote control: sesi selesai")

    def _run_active_session(self):
        """
        Loop capture-upload-execute dalam satu thread.
        mss HARUS dipakai di thread yang sama dengan pembuatan konteksnya.
        """
        try:
            # Buat konteks mss BARU di thread ini
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                width   = monitor["width"]
                height  = monitor["height"]

                while self._active and not self._stop_flag.is_set():
                    t0 = time.time()
                    try:
                        # 1. Capture
                        shot = sct.grab(monitor)
                        img  = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

                        # Scale down: max 960px lebar, kualitas 30 → file kecil & cepat transfer
                        if img.width > 960:
                            h = int(img.height * 960 / img.width)
                            img = img.resize((960, h), Image.BILINEAR)

                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=30)
                        b64 = base64.b64encode(buf.getvalue()).decode()

                        # 2. Upload frame + dapat events dalam 1 request (tidak ada request kedua)
                        result = self.data_sender.upload_remote_frame(b64, width, height)

                        if not result.get("remote_active", True):
                            self._active = False
                            self.log("Remote control: dihentikan admin")
                            break

                        # 3. Eksekusi events langsung
                        for ev in result.get("events", []):
                            self._execute_event(ev, width, height)

                    except Exception as e:
                        self.log(f"Remote loop error: {e}")
                        time.sleep(0.2)

                    # Target ~5 FPS; sisanya diisi transfer network
                    time.sleep(max(0, 0.2 - (time.time() - t0)))

        except Exception as e:
            self.log(f"Remote session error: {e}")
        finally:
            self._active = False

    def _execute_event(self, event, screen_width, screen_height):
        etype   = event.get("type")
        payload = event.get("payload") or {}

        try:
            if etype == "mouse_move":
                x = int(float(payload.get("x", 0)) * screen_width)
                y = int(float(payload.get("y", 0)) * screen_height)
                win32api.SetCursorPos((x, y))

            elif etype == "mouse_down":
                if "x" in payload and "y" in payload:
                    x = int(float(payload["x"]) * screen_width)
                    y = int(float(payload["y"]) * screen_height)
                    win32api.SetCursorPos((x, y))
                flag = win32con.MOUSEEVENTF_RIGHTDOWN if payload.get("button") == "right" \
                       else win32con.MOUSEEVENTF_LEFTDOWN
                win32api.mouse_event(flag, 0, 0, 0, 0)

            elif etype == "mouse_up":
                if "x" in payload and "y" in payload:
                    x = int(float(payload["x"]) * screen_width)
                    y = int(float(payload["y"]) * screen_height)
                    win32api.SetCursorPos((x, y))
                flag = win32con.MOUSEEVENTF_RIGHTUP if payload.get("button") == "right" \
                       else win32con.MOUSEEVENTF_LEFTUP
                win32api.mouse_event(flag, 0, 0, 0, 0)

            elif etype == "mouse_scroll":
                delta = 120 if payload.get("delta", 0) > 0 else -120
                win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, delta, 0)

            elif etype in ("key_down", "key_up"):
                vk = _key_to_vk(payload.get("key", ""))
                if vk:
                    flag = 0 if etype == "key_down" else win32con.KEYEVENTF_KEYUP
                    win32api.keybd_event(vk, 0, flag, 0)

            elif etype == "terminate_app":
                app_name = (payload.get("app_name") or "").strip()
                if app_name:
                    killed = []
                    try:
                        import psutil as _ps
                        for p in _ps.process_iter(["name", "pid"]):
                            try:
                                if p.info["name"].lower() == app_name.lower():
                                    p.kill()
                                    killed.append(p.info["pid"])
                            except (_ps.NoSuchProcess, _ps.AccessDenied):
                                pass
                    except Exception:
                        pass

                    if not killed:
                        # Fallback: pakai taskkill (lebih kuat, bisa terminate proses sistem)
                        try:
                            subprocess.run(
                                ["taskkill", "/F", "/IM", app_name],
                                creationflags=subprocess.CREATE_NO_WINDOW,
                                timeout=5
                            )
                            killed = ["via taskkill"]
                        except Exception:
                            pass

                    self.log(f"Terminate '{app_name}': {killed or 'tidak ditemukan'}")

        except Exception as e:
            self.log(f"Event error ({etype}): {e}")
