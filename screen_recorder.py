import os
import cv2
import mss
import numpy as np
import threading
import time
import base64
import uuid
from datetime import datetime
from app_paths import get_app_data_path


class ScreenRecorder:
    def __init__(self, save_dir=None, fps=2, max_width=1280, max_height=720):
        self.save_dir = save_dir or get_app_data_path("recordings")
        self.fps = fps
        self.max_width = max_width
        self.max_height = max_height
        self._ensure_directory()

        self.is_recording = False
        self.stop_event = threading.Event()
        self.recording_thread = None
        self.current_recording_path = None
        self.recording_start_time = None
        self.frame_count = 0
        self.is_paused = False

    def _ensure_directory(self):
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)

    def _get_scale_factor(self, width, height):
        if width > self.max_width:
            scale = self.max_width / width
            width = self.max_width
            height = int(height * scale)
        if height > self.max_height:
            scale = self.max_height / height
            height = self.max_height
            width = int(width * scale)
        return width, height

    def start_recording(self):
        if self.is_recording:
            return

        self.stop_event.clear()
        self.is_recording = True
        self.is_paused = False
        self.recording_start_time = datetime.now().isoformat()
        self.frame_count = 0

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Gunakan format .mp4 sebagai standar yang lebih modern
        self.current_recording_path = os.path.join(self.save_dir, f"recording_{timestamp}_{uuid.uuid4().hex[:8]}.mp4")

        self.recording_thread = threading.Thread(target=self._record_loop, daemon=True)
        self.recording_thread.start()

    def stop_recording(self):
        if not self.is_recording:
            return None

        self.is_recording = False
        self.stop_event.set()
        
        # Tunggu thread recording selesai melepaskan VideoWriter
        if self.recording_thread and self.recording_thread.is_alive():
            self.recording_thread.join(timeout=5)

        result = None
        if self.current_recording_path and os.path.exists(self.current_recording_path):
            try:
                # Pastikan file sudah dilepaskan dan memiliki ukuran
                file_size = os.path.getsize(self.current_recording_path)
                if file_size > 0:
                    result = {
                        "filepath": self.current_recording_path,
                        "filename": os.path.basename(self.current_recording_path),
                        "start_time": self.recording_start_time,
                        "end_time": datetime.now().isoformat(),
                        "duration_seconds": self.frame_count / self.fps if self.fps > 0 else 0,
                        "frame_count": self.frame_count,
                        "file_size_kb": file_size / 1024
                    }
            except Exception:
                pass

        self.current_recording_path = None
        self.recording_start_time = None
        self.frame_count = 0

        return result

    def _record_loop(self):
        out = None
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                screen_width = monitor["width"]
                screen_height = monitor["height"]
                target_width, target_height = self._get_scale_factor(screen_width, screen_height)

                # mp4v lebih stabil untuk file .mp4 di Windows
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                out = cv2.VideoWriter(self.current_recording_path, fourcc, self.fps, (target_width, target_height))

                if not out.isOpened():
                    # Fallback ke XVID jika mp4v gagal
                    fourcc = cv2.VideoWriter_fourcc(*'XVID')
                    out = cv2.VideoWriter(self.current_recording_path, fourcc, self.fps, (target_width, target_height))

                if not out.isOpened():
                    self.is_recording = False
                    return

                frame_interval = 1.0 / self.fps
                next_frame_time = time.time()

                while not self.stop_event.is_set():
                    if self.is_paused:
                        self.stop_event.wait(0.1)
                        continue

                    try:
                        sct_img = sct.grab(monitor)
                        if sct_img is None:
                            continue
                            
                        frame = np.array(sct_img)
                        if frame is None or frame.size == 0:
                            continue
                            
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                        if target_width != screen_width or target_height != screen_height:
                            frame = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)

                        if out is not None:
                            out.write(frame)
                            self.frame_count += 1

                        next_frame_time += frame_interval
                        sleep_time = next_frame_time - time.time()
                        if sleep_time > 0:
                            if self.stop_event.wait(sleep_time):
                                break
                        else:
                            next_frame_time = time.time()

                    except Exception:
                        if self.stop_event.wait(0.1):
                            break
        except Exception:
            pass
        finally:
            if out:
                try:
                    out.release()
                except:
                    pass

    def get_recording_as_base64(self, filepath):
        try:
            if not os.path.exists(filepath):
                return None

            # Batasi ukuran (Max 30MB)
            file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
            if file_size_mb > 30:
                return "TOO_LARGE"

            # Jeda untuk memastikan file tertutup sempurna
            time.sleep(1.0)

            with open(filepath, "rb") as f:
                video_bytes = f.read()
            
            if not video_bytes:
                return None
                
            return base64.b64encode(video_bytes).decode("utf-8")
        except Exception:
            return None

    def delete_recording(self, filepath):
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                return True
        except Exception as e:
            print(f"Error deleting recording: {e}")
        return False
