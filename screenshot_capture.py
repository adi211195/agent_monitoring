import os
import base64
import uuid
from datetime import datetime
from PIL import Image, ImageGrab
from app_paths import get_app_data_path


class ScreenshotCapture:
    def __init__(self, save_dir=None, max_width=1280, max_height=720, quality=50):
        self.save_dir = save_dir or get_app_data_path("screenshots")
        self.max_width = max_width
        self.max_height = max_height
        self.quality = quality
        self._ensure_directory()

    def _ensure_directory(self):
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)

    def _resize_image(self, img):
        if img.width > self.max_width or img.height > self.max_height:
            img.thumbnail((self.max_width, self.max_height), Image.Resampling.LANCZOS)
        return img

    def capture_screen(self):
        try:
            screenshot = ImageGrab.grab()
            return screenshot
        except Exception as e:
            print(f"Error capturing screen: {e}")
            return None

    def capture_and_save(self):
        screenshot = self.capture_screen()
        if screenshot is None:
            return None

        screenshot = self._resize_image(screenshot)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}_{uuid.uuid4().hex[:8]}.jpg"
        filepath = os.path.join(self.save_dir, filename)

        try:
            screenshot.save(filepath, "JPEG", quality=self.quality, optimize=True)
            return {
                "filepath": filepath,
                "filename": filename,
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            print(f"Error saving screenshot: {e}")
            return None

    def capture_as_base64(self):
        screenshot = self.capture_screen()
        if screenshot is None:
            return None

        screenshot = self._resize_image(screenshot)

        try:
            from io import BytesIO
            buffer = BytesIO()
            screenshot.save(buffer, format="JPEG", quality=self.quality, optimize=True)
            img_bytes = buffer.getvalue()
            img_base64 = base64.b64encode(img_bytes).decode("utf-8")
            return {
                "image_base64": img_base64,
                "timestamp": datetime.now().isoformat(),
                "format": "JPEG"
            }
        except Exception as e:
            print(f"Error encoding screenshot: {e}")
            return None

    def capture_and_encode(self):
        result = self.capture_as_base64()
        if result:
            result["filename"] = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.jpg"
        return result

    def delete_screenshot(self, filepath):
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                return True
        except Exception as e:
            print(f"Error deleting screenshot: {e}")
        return False
