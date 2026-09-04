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
    # Function keys
    "F1":0x70, "F2":0x71, "F3":0x72, "F4":0x73, "F5":0x74,
    "F6":0x75, "F7":0x76, "F8":0x77, "F9":0x78, "F10":0x79,
    "F11":0x7A, "F12":0x7B,
    # Special
    "Insert": win32con.VK_INSERT, "PrintScreen": win32con.VK_SNAPSHOT,
    "NumLock": win32con.VK_NUMLOCK, "ScrollLock": win32con.VK_SCROLL,
    "Pause": win32con.VK_PAUSE,
    # Numpad
    "Numpad0":0x60,"Numpad1":0x61,"Numpad2":0x62,"Numpad3":0x63,
    "Numpad4":0x64,"Numpad5":0x65,"Numpad6":0x66,"Numpad7":0x67,
    "Numpad8":0x68,"Numpad9":0x69,"NumpadMultiply":0x6A,
    "NumpadAdd":0x6B,"NumpadSubtract":0x6D,"NumpadDecimal":0x6E,
    "NumpadDivide":0x6F, "NumpadEnter": win32con.VK_RETURN,
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
        # Virtual cursor position (tidak memindahkan cursor fisik Windows)
        self._vx = 0
        self._vy = 0

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
        """
        Eksekusi mouse/keyboard event dari DataChannel.
        Pakai SendInput dengan MOUSEEVENTF_ABSOLUTE untuk click yang reliable.
        mouse_move: hanya update virtual position, tidak pindahkan cursor fisik.
        """
        etype   = event.get("type")
        payload = event.get("payload") or {}

        if etype != "mouse_move":
            self.log(f"[REMOTE INPUT] execute: {etype}")

        try:
            if etype == "mouse_move":
                # Simpan posisi virtual, TIDAK pindahkan cursor fisik
                self._vx = int(float(payload.get("x", 0)) * screen_width)
                self._vy = int(float(payload.get("y", 0)) * screen_height)

            elif etype in ("mouse_down", "mouse_up", "mouse_dblclick"):
                x = int(float(payload.get("x", self._vx / max(screen_width, 1))) * screen_width)
                y = int(float(payload.get("y", self._vy / max(screen_height, 1))) * screen_height)
                self._vx, self._vy = x, y
                btn = payload.get("button", "left")

                if etype == "mouse_down":
                    self._send_mouse_input(x, y, screen_width, screen_height, btn, down=True)
                elif etype == "mouse_up":
                    self._send_mouse_input(x, y, screen_width, screen_height, btn, down=False)
                elif etype == "mouse_dblclick":
                    # Double click: down-up-down-up di posisi sama
                    self._send_mouse_input(x, y, screen_width, screen_height, "left", down=True)
                    self._send_mouse_input(x, y, screen_width, screen_height, "left", down=False)
                    import time as _t; _t.sleep(0.05)
                    self._send_mouse_input(x, y, screen_width, screen_height, "left", down=True)
                    self._send_mouse_input(x, y, screen_width, screen_height, "left", down=False)

            elif etype == "mouse_scroll":
                delta = 120 if payload.get("delta", 0) > 0 else -120
                # Scroll di virtual position
                _saved = win32api.GetCursorPos()
                win32api.SetCursorPos((self._vx, self._vy))
                win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
                win32api.SetCursorPos(_saved)

            elif etype in ("key_down", "key_up"):
                key     = payload.get("key", "")
                is_down = (etype == "key_down")
                flag    = 0 if is_down else win32con.KEYEVENTF_KEYUP

                # Press modifiers first (on keydown)
                if is_down:
                    if payload.get("ctrl")  and key != "Control":
                        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
                    if payload.get("alt")   and key != "Alt":
                        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                    if payload.get("shift") and key != "Shift":
                        win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
                    if payload.get("meta")  and key != "Meta":
                        win32api.keybd_event(win32con.VK_LWIN, 0, 0, 0)

                vk = _key_to_vk(key)
                if vk:
                    win32api.keybd_event(vk, 0, flag, 0)

                # Release modifiers after keyup
                if not is_down:
                    if payload.get("ctrl")  and key != "Control":
                        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
                    if payload.get("alt")   and key != "Alt":
                        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
                    if payload.get("shift") and key != "Shift":
                        win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
                    if payload.get("meta")  and key != "Meta":
                        win32api.keybd_event(win32con.VK_LWIN, 0, win32con.KEYEVENTF_KEYUP, 0)

            elif etype == "terminate_app":
                app_name = (payload.get("app_name") or "").strip()
                if app_name:
                    try:
                        import psutil as _ps
                        for p in _ps.process_iter(["name", "pid"]):
                            try:
                                if p.info["name"].lower() == app_name.lower():
                                    p.kill()
                            except Exception:
                                pass
                    except Exception:
                        subprocess.run(
                            ["taskkill", "/F", "/IM", app_name],
                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=5
                        )

        except Exception as e:
            self.log(f"[REMOTE INPUT] Event error ({etype}): {e}")

    def _send_mouse_input(self, x: int, y: int,
                          screen_width: int, screen_height: int,
                          button: str, down: bool):
        """
        Kirim klik mouse menggunakan SendInput dengan koordinat absolut.
        Lebih reliable dari mouse_event karena atomik dan tidak ada race condition.
        """
        import ctypes
        from ctypes import wintypes

        class _MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx",          ctypes.c_long),
                ("dy",          ctypes.c_long),
                ("mouseData",   wintypes.DWORD),
                ("dwFlags",     wintypes.DWORD),
                ("time",        wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class _INPUT(ctypes.Structure):
            class _UNION(ctypes.Union):
                _fields_ = [("mi", _MOUSEINPUT)]
            _anonymous_ = ("_u",)
            _fields_ = [("type", wintypes.DWORD), ("_u", _UNION)]

        # Normalize ke 0-65535 (range SendInput absolute)
        nx = int(x * 65535 / max(screen_width  - 1, 1))
        ny = int(y * 65535 / max(screen_height - 1, 1))

        # Tentukan flag
        MOVE = 0x0001   # MOUSEEVENTF_MOVE
        ABS  = 0x8000   # MOUSEEVENTF_ABSOLUTE

        if button == "right":
            act = 0x0008 if down else 0x0010  # RIGHTDOWN / RIGHTUP
        elif button == "middle":
            act = 0x0020 if down else 0x0040  # MIDDLEDOWN / MIDDLEUP
        else:
            act = 0x0002 if down   else 0x0004  # LEFTDOWN / LEFTUP

        mi  = _MOUSEINPUT(dx=nx, dy=ny, mouseData=0,
                          dwFlags=MOVE | ABS | act,
                          time=0, dwExtraInfo=None)
        inp = _INPUT(type=0)  # INPUT_MOUSE = 0
        inp.mi = mi

        result = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
        if result != 1:
            self.log(f"[REMOTE INPUT] SendInput failed: {ctypes.GetLastError()}")

