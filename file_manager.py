"""
file_manager.py - Agent file manager handler.
"""
import os, base64, threading, logging
from datetime import datetime
from pathlib import Path

logger    = logging.getLogger(__name__)
CHUNK_SZ  = 32 * 1024  # 32KB per chunk (max_message_size sudah 307KB)
MAX_FILE  = 512 * 1024 * 1024


class FileManager:
    def __init__(self, data_sender, log_callback=None):
        self._sender  = data_sender
        self._log     = log_callback or logger.info
        self._uploads = {}

    def browse(self, path: str, search: str = None):
        threading.Thread(target=self._browse, args=(path, search), daemon=True).start()

    def _browse(self, path: str, search: str):
        try:
            p = Path(path)
            if not p.exists() or not p.is_dir():
                self._send_error(f"Direktori tidak ditemukan: {path}")
                return
            try:
                entries = list(p.iterdir())
            except PermissionError:
                self._send_error(f"Akses ditolak: {path}")
                return

            items = []
            for entry in entries:
                try:
                    name   = entry.name
                    # Filter by search term (applies to BOTH files and folders)
                    if search and search.lower() not in name.lower():
                        continue
                    stat   = entry.stat()
                    is_dir = entry.is_dir()
                    ext    = '' if is_dir else (entry.suffix.lstrip('.').upper() or 'File')
                    mod_dt = datetime.fromtimestamp(stat.st_mtime)
                    now    = datetime.now()
                    modified = mod_dt.strftime('%b %d') if mod_dt.year == now.year else str(mod_dt.year)
                    # Cek apakah folder punya subfolder (untuk chevron)
                    has_children = False
                    if is_dir:
                        try:
                            has_children = any(
                                e.is_dir() for e in entry.iterdir()
                            )
                        except (PermissionError, OSError):
                            has_children = False  # Tidak bisa akses = sembunyikan chevron

                    items.append({
                        'name': name, 'is_dir': is_dir,
                        'size': 0 if is_dir else stat.st_size,
                        'ext': ext, 'modified': modified,
                        'has_children': has_children,
                    })
                except Exception:
                    continue

            self._sender.send_file_listing(path=str(p), items=items)
            self._log(f"[FM] Listed {len(items)} items in {path}")
        except Exception as e:
            self._log(f"[FM] Browse error: {e}")
            self._send_error(str(e))

    def download(self, path: str, transfer_id: str):
        threading.Thread(target=self._download, args=(path, transfer_id), daemon=True).start()

    def _download(self, path: str, transfer_id: str):
        try:
            p = Path(path)
            if not p.exists() or not p.is_file():
                self._send_error(f"File tidak ditemukan: {path}")
                return
            size = p.stat().st_size
            if size > MAX_FILE:
                self._send_error(f"File terlalu besar (maks 512MB)")
                return
            total = max(1, -(-size // CHUNK_SZ))
            self._log(f"[FM] Downloading {path} ({size}B, {total} chunks)")
            import time
            with open(p, 'rb') as f:
                for i in range(total):
                    chunk = f.read(CHUNK_SZ)
                    b64   = base64.b64encode(chunk).decode('ascii')
                    self._sender.send_file_chunk(
                        transfer_id=transfer_id, filename=p.name,
                        chunk_index=i, total_chunks=total,
                        data=b64, is_last=(i == total - 1),
                    )
                    time.sleep(0.02)  # 20ms throttle
            self._log(f"[FM] Download done: {path}")
        except Exception as e:
            self._log(f"[FM] Download error: {e}")
            self._send_error(str(e))

    def receive_chunk(self, transfer_id, filename, dest_path, chunk_index, total_chunks, data):
        if transfer_id not in self._uploads:
            self._uploads[transfer_id] = {'chunks': {}, 'total': total_chunks, 'filename': filename, 'dest': dest_path}
        tr = self._uploads[transfer_id]
        tr['chunks'][chunk_index] = data
        self._log(f"[FM] Upload chunk {chunk_index+1}/{total_chunks} - {filename}")
        if len(tr['chunks']) >= total_chunks:
            threading.Thread(target=self._assemble, args=(transfer_id,), daemon=True).start()

    def _assemble(self, transfer_id):
        tr = self._uploads.pop(transfer_id, None)
        if not tr: return
        try:
            dest    = Path(tr['dest']) / tr['filename']
            chunks  = [tr['chunks'][i] for i in sorted(tr['chunks'])]
            binary  = base64.b64decode(''.join(chunks))
            with open(dest, 'wb') as f:
                f.write(binary)
            self._log(f"[FM] Upload done: {dest}")
            self._sender.send_file_upload_done(transfer_id=transfer_id, filename=tr['filename'], success=True)
        except Exception as e:
            self._log(f"[FM] Assemble error: {e}")
            self._sender.send_file_upload_done(transfer_id=transfer_id, filename=tr.get('filename',''), success=False, error=str(e))

    def _send_error(self, msg):
        self._sender.send_file_error(msg)
