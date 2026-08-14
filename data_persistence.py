import json
import os
import threading

class DataPersistence:
    def __init__(self, filename="offline_queue.json"):
        self.filename = filename
        self.lock = threading.Lock()
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not os.path.exists(self.filename):
            with open(self.filename, 'w') as f:
                json.dump([], f)

    def add_to_queue(self, data_type, payload):
        """Simpan data yang gagal dikirim ke file lokal"""
        with self.lock:
            try:
                queue = self._read_queue()
                queue.append({
                    "data_type": data_type,
                    "payload": payload
                })
                self._write_queue(queue)
                return True
            except Exception as e:
                print(f"Error saving to offline queue: {e}")
                return False

    def get_queue(self):
        """Ambil semua data dari antrean"""
        with self.lock:
            return self._read_queue()

    def clear_queue(self):
        """Kosongkan antrean setelah berhasil dikirim semua"""
        with self.lock:
            self._write_queue([])

    def remove_item(self, index):
        """Hapus item spesifik dari antrean"""
        with self.lock:
            queue = self._read_queue()
            if 0 <= index < len(queue):
                queue.pop(index)
                self._write_queue(queue)

    def _read_queue(self):
        try:
            if os.path.exists(self.filename):
                with open(self.filename, 'r') as f:
                    return json.load(f)
        except:
            pass
        return []

    def _write_queue(self, queue):
        try:
            with open(self.filename, 'w') as f:
                json.dump(queue, f)
        except Exception as e:
            print(f"Error writing to queue file: {e}")
