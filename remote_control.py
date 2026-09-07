"""
remote_control.py - Agent remote desktop control (DataChannel input handler).

Flow:
  Browser Admin → DataChannel JSON event → _execute_event() → Windows native input

Input methods:
  - mouse: win32api.SetCursorPos + win32api.mouse_event (reliable, no ctypes struct issue)
  - keyboard: win32api.keybd_event
"""
import time
import threading
import ctypes
import subprocess

import win32api
import win32con

# ─── DPI Awareness ────────────────────────────────────────────────────────────
# Per-monitor DPI aware v2: GetSystemMetrics & SetCursorPos pakai physical pixels.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ─── Key mapping browser key name → Windows VK code ──────────────────────────
_KEY_MAP = {
    "Enter": win32con.VK_RETURN,    "Backspace": win32con.VK_BACK,
    "Tab":   win32con.VK_TAB,       "Escape":    win32con.VK_ESCAPE,
    " ":     win32con.VK_SPACE,
    "ArrowLeft":  win32con.VK_LEFT,  "ArrowRight": win32con.VK_RIGHT,
    "ArrowUp":    win32con.VK_UP,    "ArrowDown":  win32con.VK_DOWN,
    "Shift":   win32con.VK_SHIFT,   "Control": win32con.VK_CONTROL,
    "Alt":     win32con.VK_MENU,    "Meta":    win32con.VK_LWIN,
    "Delete":  win32con.VK_DELETE,  "Insert":  win32con.VK_INSERT,
    "Home":    win32con.VK_HOME,    "End":     win32con.VK_END,
    "PageUp":  win32con.VK_PRIOR,   "PageDown": win32con.VK_NEXT,
    "CapsLock":    win32con.VK_CAPITAL,
    "PrintScreen": win32con.VK_SNAPSHOT,
    "NumLock":     win32con.VK_NUMLOCK,
    "ScrollLock":  win32con.VK_SCROLL,
    "Pause":       win32con.VK_PAUSE,
    "F1":  0x70, "F2":  0x71, "F3":  0x72, "F4":  0x73,
    "F5":  0x74, "F6":  0x75, "F7":  0x76, "F8":  0x77,
    "F9":  0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "Numpad0":0x60,"Numpad1":0x61,"Numpad2":0x62,"Numpad3":0x63,
    "Numpad4":0x64,"Numpad5":0x65,"Numpad6":0x66,"Numpad7":0x67,
    "Numpad8":0x68,"Numpad9":0x69,
    "NumpadMultiply":0x6A,"NumpadAdd":0x6B,
    "NumpadSubtract":0x6D,"NumpadDecimal":0x6E,"NumpadDivide":0x6F,
    "NumpadEnter": win32con.VK_RETURN,
}

def _key_to_vk(key: str):
    if key in _KEY_MAP:
        return _KEY_MAP[key]
    if len(key) == 1:
        vk = win32api.VkKeyScan(key)
        if vk != -1:
            return vk & 0xFF
    return None


class RemoteControlAgent:
    """
    Menerima event dari DataChannel dan mengeksekusi sebagai Windows native input.
    Tidak punya thread capture sendiri — capture dilakukan oleh RemoteControlAgent
    versi lama atau WebRTC ScreenCaptureTrack.
    """

    def __init__(self, data_sender, log_callback=None):
        self.data_sender    = data_sender
        self.log            = log_callback or (lambda m: print(m))
        self._active        = False
        self._stop_flag     = threading.Event()

        # Virtual cursor (posisi mouse admin, tidak gerakkan cursor Windows)
        self._vx            = 0
        self._vy            = 0

        # Cursor fisik tidak di-restore setelah click — tetap di posisi klik terakhir
        # Ini adalah behavior remote desktop yang correct (1x klik cukup)

    # ── Lifecycle ────────────────────────────────────────────────────────────
    def stop_watching(self):
        self._stop_flag.set()
        self._active = False

    def _start_session_from_outside(self):
        """Dipanggil dari main_app.py saat admin mulai remote session."""
        if self._active:
            return
        self._active = True
        self._stop_flag.clear()
        self.log("[RemoteCtrl] Session started (WebRTC DataChannel mode)")

    # ── Main event handler ───────────────────────────────────────────────────
    def _execute_event(self, event: dict, screen_width: int, screen_height: int):
        """
        Entry point dari DataChannel.
        event = { "type": "mouse_down"|"mouse_up"|..., "payload": {...} }
        screen_width/height = resolusi FISIK monitor agent (dari mss).
        """
        etype   = event.get("type", "")
        payload = event.get("payload") or {}

        if etype not in ("mouse_move",):
            self.log(f"[Agent Remote Input] RECEIVED {etype} payload={payload}")

        try:
            # ── Mouse move: hanya update virtual position ─────────────────
            if etype == "mouse_move":
                self._vx = int(float(payload.get("x", 0)) * screen_width)
                self._vy = int(float(payload.get("y", 0)) * screen_height)
                # Tidak ada SetCursorPos — cursor fisik tidak bergerak

            # ── Mouse down ───────────────────────────────────────────────
            elif etype == "mouse_down":
                x   = int(float(payload.get("x", self._vx / max(screen_width,  1))) * screen_width)
                y   = int(float(payload.get("y", self._vy / max(screen_height, 1))) * screen_height)
                btn = payload.get("button", "left")
                self._vx, self._vy = x, y

                # Pindahkan cursor ke target, cursor TETAP di sana setelah down
                # (tidak di-restore agar Windows melihat DOWN+UP di posisi yang SAMA)
                self.log(f"[Agent Remote Input] EXECUTING mouse_down {btn} at screen ({x},{y})")
                self._do_mouse_down(x, y, btn)
                self.log(f"[Agent Remote Input] NATIVE INPUT SUCCESS mouse_down")

            # ── Mouse up ─────────────────────────────────────────────────
            elif etype == "mouse_up":
                x   = int(float(payload.get("x", self._vx / max(screen_width,  1))) * screen_width)
                y   = int(float(payload.get("y", self._vy / max(screen_height, 1))) * screen_height)
                btn = payload.get("button", "left")
                self._vx, self._vy = x, y

                # UP di posisi yang sama dengan DOWN — cursor tetap di (x,y) setelah ini
                # Windows akan melihat DOWN(x,y) + UP(x,y) = klik valid 1x
                self.log(f"[Agent Remote Input] EXECUTING mouse_up {btn} at screen ({x},{y})")
                self._do_mouse_up(x, y, btn)
                # Cursor sengaja dibiarkan di (x,y) — tidak di-restore
                # Ini membuat remote desktop terasa lebih natural
                self.log(f"[Agent Remote Input] NATIVE INPUT SUCCESS mouse_up - cursor stays at ({x},{y})")

            # ── Double click ──────────────────────────────────────────────
            elif etype == "mouse_dblclick":
                x   = int(float(payload.get("x", self._vx / max(screen_width,  1))) * screen_width)
                y   = int(float(payload.get("y", self._vy / max(screen_height, 1))) * screen_height)
                self._vx, self._vy = x, y

                self.log(f"[Agent Remote Input] EXECUTING dblclick at screen ({x},{y})")
                # SetCursorPos sekali, lalu kirim DOWN+UP+DOWN+UP tanpa restore
                win32api.SetCursorPos((x, y))
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP,   0, 0, 0, 0)
                time.sleep(0.05)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP,   0, 0, 0, 0)
                # Cursor tetap di (x,y)
                self.log(f"[Agent Remote Input] NATIVE INPUT SUCCESS dblclick")

            # ── Scroll ───────────────────────────────────────────────────
            elif etype == "mouse_scroll":
                raw_delta = payload.get("delta", 0)
                # Browser delta: positive = scroll up (away from user)
                # Windows WHEEL: positive = forward/up, negative = backward/down
                wheel_delta = 120 if raw_delta > 0 else -120

                self.log(f"[Agent Remote Input] EXECUTING scroll delta={wheel_delta} at virtual ({self._vx},{self._vy})")
                try:
                    saved = win32api.GetCursorPos()
                    win32api.SetCursorPos((self._vx, self._vy))
                    win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, wheel_delta, 0)
                    win32api.SetCursorPos(saved)
                except Exception as e:
                    self.log(f"[Agent Remote Input] scroll error: {e}")
                self.log(f"[Agent Remote Input] NATIVE INPUT SUCCESS scroll")

            # ── Keyboard ─────────────────────────────────────────────────
            elif etype in ("key_down", "key_up"):
                key     = payload.get("key", "")
                is_down = (etype == "key_down")
                flag    = 0 if is_down else win32con.KEYEVENTF_KEYUP

                if etype not in ("mouse_move",):
                    self.log(f"[Agent Remote Input] EXECUTING {etype} key='{key}' ctrl={payload.get('ctrl')} alt={payload.get('alt')} shift={payload.get('shift')}")

                # Press modifiers BEFORE key (on keydown)
                if is_down:
                    if payload.get("ctrl")  and key not in ("Control",):
                        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
                    if payload.get("alt")   and key not in ("Alt",):
                        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                    if payload.get("shift") and key not in ("Shift",):
                        win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
                    if payload.get("meta")  and key not in ("Meta",):
                        win32api.keybd_event(win32con.VK_LWIN, 0, 0, 0)

                vk = _key_to_vk(key)
                if vk:
                    win32api.keybd_event(vk, 0, flag, 0)
                    self.log(f"[Agent Remote Input] NATIVE INPUT SUCCESS {etype} vk=0x{vk:02X} ('{key}')")
                else:
                    self.log(f"[Agent Remote Input] WARNING: no VK mapping for key='{key}'")

                # Release modifiers AFTER key (on keyup)
                if not is_down:
                    if payload.get("ctrl")  and key not in ("Control",):
                        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
                    if payload.get("alt")   and key not in ("Alt",):
                        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
                    if payload.get("shift") and key not in ("Shift",):
                        win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
                    if payload.get("meta")  and key not in ("Meta",):
                        win32api.keybd_event(win32con.VK_LWIN, 0, win32con.KEYEVENTF_KEYUP, 0)

            # ── Terminate app ────────────────────────────────────────────
            elif etype == "terminate_app":
                app_name = (payload.get("app_name") or "").strip()
                if app_name:
                    self.log(f"[Agent Remote Input] EXECUTING terminate_app: {app_name}")
                    killed = []
                    try:
                        import psutil as _ps
                        for p in _ps.process_iter(["name", "pid"]):
                            try:
                                if p.info["name"].lower() == app_name.lower():
                                    p.kill()
                                    killed.append(p.info["pid"])
                            except Exception:
                                pass
                    except ImportError:
                        pass
                    if not killed:
                        try:
                            subprocess.run(["taskkill", "/F", "/IM", app_name],
                                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
                            killed = ["taskkill"]
                        except Exception:
                            pass
                    self.log(f"[Agent Remote Input] terminate_app '{app_name}': {killed or 'not found'}")

        except Exception as e:
            self.log(f"[Agent Remote Input] ERROR in {etype}: {e}")
            import traceback
            self.log(traceback.format_exc())

    # ── Mouse helpers ─────────────────────────────────────────────────────────
    def _do_mouse_down(self, x: int, y: int, button: str):
        """
        Kirim mouse_down di koordinat (x,y) layar agent.
        Menggunakan SetCursorPos lalu mouse_event — reliable untuk semua app Windows.
        """
        win32api.SetCursorPos((x, y))

        if button == "right":
            win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
        elif button == "middle":
            win32api.mouse_event(win32con.MOUSEEVENTF_MIDDLEDOWN, 0, 0, 0, 0)
        else:
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)

    def _do_mouse_up(self, x: int, y: int, button: str):
        """
        Kirim mouse_up. Cursor sudah ada di (x,y) dari _do_mouse_down.
        Tidak restore cursor — cursor tetap di posisi klik (remote desktop behavior).
        """
        if button == "right":
            win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
        elif button == "middle":
            win32api.mouse_event(win32con.MOUSEEVENTF_MIDDLEUP, 0, 0, 0, 0)
        else:
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
