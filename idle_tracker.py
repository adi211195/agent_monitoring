import time
import threading
from datetime import datetime
from pynput import mouse, keyboard

class IdleTracker:
    def __init__(self, idle_threshold=30):
        self.idle_threshold = idle_threshold
        self.last_activity_time = time.time()
        self.is_running = False
        self.is_idle = False
        self.mouse_listener = None
        self.key_listener = None
        self.on_idle_callback = None
        self.idle_events = []
        self.lock = threading.Lock()

    def start(self, on_idle_callback=None):
        if self.is_running:
            return
        
        self.on_idle_callback = on_idle_callback
        self.is_running = True
        self.last_activity_time = time.time()

        # Listener untuk mouse
        self.mouse_listener = mouse.Listener(
            on_move=self._reset_idle,
            on_click=self._reset_idle,
            on_scroll=self._reset_idle
        )
        
        # Listener untuk keyboard
        self.key_listener = keyboard.Listener(
            on_press=self._reset_idle
        )

        self.mouse_listener.start()
        self.key_listener.start()

        # Thread untuk memantau waktu idle
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def stop(self):
        self.is_running = False
        if self.mouse_listener:
            self.mouse_listener.stop()
        if self.key_listener:
            self.key_listener.stop()

    def _reset_idle(self, *args, **kwargs):
        with self.lock:
            if self.is_idle:
                self.is_idle = False
                print(f"[IDLE] Back to active at {datetime.now().isoformat()}")
            self.last_activity_time = time.time()

    def _monitor_loop(self):
        while self.is_running:
            current_time = time.time()
            idle_duration = current_time - self.last_activity_time

            if idle_duration >= self.idle_threshold and not self.is_idle:
                with self.lock:
                    self.is_idle = True
                    event = {
                        "timestamp": datetime.now().isoformat(),
                        "event": "idle_detected",
                        "duration_threshold": self.idle_threshold
                    }
                    self.idle_events.append(event)
                    print(f"[IDLE] Idle detected at {event['timestamp']}")
                    
                    if self.on_idle_callback:
                        # Jalankan callback di thread terpisah agar tidak memblokir monitor
                        threading.Thread(target=self.on_idle_callback, args=(event,), daemon=True).start()

            time.sleep(1)

    def get_new_events(self):
        with self.lock:
            events = self.idle_events.copy()
            self.idle_events = []
            return events
