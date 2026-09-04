"""
file_manager.py - Agent file manager.
Browse, tree view, recursive search (via scandir depth-limited), upload/download.
"""
import os, base64, threading, string, logging
from datetime import datetime
from pathlib import Path

logger   = logging.getLogger(__name__)
CHUNK_SZ = 32 * 1024
MAX_FILE = 512 * 1024 * 1024

SKIP_DIRS = {
    '__pycache__', 'node_modules', '.git', '.svn',
    '$RECYCLE.BIN', 'System Volume Information',
    'WindowsApps', 'MicrosoftEdgeBackups', 'Recovery',
}


def _fmt_mod(ts: float) -> str:
    dt  = datetime.fromtimestamp(ts)
    now = datetime.now()
    return dt.strftime('%b %d') if dt.year == now.year else str(dt.year)


class FileManager:
    def __init__(self, data_sender, log_callback=None):
        self._ds      = data_sender
        self._log     = log_callback or logger.info
        self._uploads = {}

    # ── Drives ───────────────────────────────────────────────────
    def _all_drives(self) -> list:
        drives = []
        for L in string.ascii_uppercase:
            p = f"{L}:\\"
            if os.path.exists(p):
                drives.append({
                    'name': f"{L}:", 'is_dir': True, 'size': 0,
                    'ext': '', 'modified': '',
                    'has_children': True,
                    'full_path': p,
                })
        return drives

    # ── Browse ────────────────────────────────────────────────────
    def browse(self, path: str, search: str = None):
        threading.Thread(target=self._browse, args=(path, search), daemon=True).start()

    def _browse(self, path: str, search: str):
        # ── Root = list all drives ──────────────────────────────
        if not path or path.strip() in ('', 'root', '/'):
            if search and len(search) >= 3:
                # Search across ALL drives
                results = []
                for d in self._all_drives():
                    dp = Path(d['full_path'])
                    self._log(f"[FM] Searching '{search}' in {dp}...")
                    self._scan(dp, search.lower(), results, 0, 4, 300)
                    if len(results) >= 300:
                        break
                self._ds.send_file_listing(path='root', items=results)
                self._log(f"[FM] Search '{search}' across all drives → {len(results)} results")
            else:
                self._ds.send_file_listing(path='root', items=self._all_drives())
            return

        # ── Regular path ────────────────────────────────────────
        p = Path(path)
        if not p.exists() or not p.is_dir():
            self._ds.send_file_error(f"Direktori tidak ditemukan: {path}")
            return

        if search and len(search) >= 3:
            # Recursive search within current path
            results = []
            self._scan(p, search.lower(), results, 0, 4, 300)
            self._ds.send_file_listing(path=str(p), items=results)
            self._log(f"[FM] Search '{search}' in {path} → {len(results)} results")
            return

        # Normal directory listing
        try:
            entries = list(p.iterdir())
        except PermissionError:
            self._ds.send_file_error(f"Akses ditolak: {path}")
            return

        items = []
        for e in entries:
            try:
                st     = e.stat()
                is_dir = e.is_dir()
                ext    = '' if is_dir else (e.suffix.lstrip('.').upper() or 'File')
                has_ch = False
                if is_dir:
                    try:
                        has_ch = any(x.is_dir() for x in e.iterdir())
                    except Exception:
                        pass
                items.append({
                    'name': e.name, 'is_dir': is_dir,
                    'size': 0 if is_dir else st.st_size,
                    'ext': ext, 'modified': _fmt_mod(st.st_mtime),
                    'has_children': has_ch,
                })
            except Exception:
                continue

        self._ds.send_file_listing(path=str(p), items=items)
        self._log(f"[FM] Listed {len(items)} items in {path}")

    # ── Recursive search ─────────────────────────────────────────
    def _scan(self, directory: Path, query: str, results: list,
              depth: int, max_depth: int, max_results: int):
        if len(results) >= max_results:
            return
        try:
            with os.scandir(str(directory)) as sc:
                entries = list(sc)
        except Exception:
            return

        for e in entries:
            if len(results) >= max_results:
                break
            try:
                name = e.name
                if name.startswith('.') or name in SKIP_DIRS:
                    continue
                is_dir = e.is_dir(follow_symlinks=False)
                st     = e.stat()
                full   = e.path

                if query in name.lower():
                    ext = '' if is_dir else Path(name).suffix.lstrip('.').upper() or 'File'
                    has_ch = False
                    if is_dir:
                        try:
                            has_ch = any(x.is_dir() for x in Path(full).iterdir())
                        except Exception:
                            pass
                    results.append({
                        'name'        : name,
                        'display_name': name,
                        'is_dir'      : is_dir,
                        'size'        : 0 if is_dir else st.st_size,
                        'ext'         : ext,
                        'modified'    : _fmt_mod(st.st_mtime),
                        'has_children': has_ch,
                        'full_path'   : full,
                        'parent_path' : str(Path(full).parent),
                    })

                if is_dir and depth < max_depth:
                    self._scan(Path(full), query, results, depth + 1, max_depth, max_results)
            except Exception:
                continue

    # ── Download ─────────────────────────────────────────────────
    def download(self, path: str, transfer_id: str):
        threading.Thread(target=self._dl, args=(path, transfer_id), daemon=True).start()

    def _dl(self, path: str, transfer_id: str):
        try:
            p = Path(path)
            if not p.exists() or not p.is_file():
                self._ds.send_file_error(f"File tidak ditemukan: {path}")
                return
            size  = p.stat().st_size
            if size > MAX_FILE:
                self._ds.send_file_error("File terlalu besar (maks 512MB)")
                return
            total = max(1, -(-size // CHUNK_SZ))
            self._log(f"[FM] Downloading {path} ({size}B, {total} chunks)")
            import time
            with open(p, 'rb') as f:
                for i in range(total):
                    chunk = f.read(CHUNK_SZ)
                    self._ds.send_file_chunk(
                        transfer_id=transfer_id, filename=p.name,
                        chunk_index=i, total_chunks=total,
                        data=base64.b64encode(chunk).decode('ascii'),
                        is_last=(i == total - 1),
                    )
                    time.sleep(0.02)
            self._log(f"[FM] Download done: {path}")
        except Exception as e:
            self._log(f"[FM] DL error: {e}")
            self._ds.send_file_error(str(e))

    # ── Upload ────────────────────────────────────────────────────
    def receive_chunk(self, transfer_id, filename, dest_path,
                      chunk_index, total_chunks, data):
        if transfer_id not in self._uploads:
            self._uploads[transfer_id] = {
                'chunks': {}, 'total': total_chunks,
                'filename': filename, 'dest': dest_path
            }
        tr = self._uploads[transfer_id]
        tr['chunks'][int(chunk_index)] = data
        self._log(f"[FM] Upload chunk {int(chunk_index)+1}/{total_chunks} - {filename}")
        if len(tr['chunks']) >= int(total_chunks):
            threading.Thread(target=self._assemble, args=(transfer_id,), daemon=True).start()

    def _assemble(self, transfer_id):
        tr = self._uploads.pop(transfer_id, None)
        if not tr:
            return
        try:
            dest   = Path(tr['dest']) / tr['filename']
            chunks = [tr['chunks'][i] for i in sorted(tr['chunks'])]
            with open(dest, 'wb') as f:
                f.write(base64.b64decode(''.join(chunks)))
            self._log(f"[FM] Upload done: {dest}")
            self._ds.send_file_upload_done(
                transfer_id=transfer_id, filename=tr['filename'], success=True)
        except Exception as e:
            self._log(f"[FM] Assemble error: {e}")
            self._ds.send_file_upload_done(
                transfer_id=transfer_id, filename=tr.get('filename', ''),
                success=False, error=str(e))
