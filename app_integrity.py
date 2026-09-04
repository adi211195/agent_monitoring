"""
app_integrity.py - Application integrity scanner.
Uses publisher, app_id, and install_path for evidence-based detection.
"""
import os, re, threading, logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Known trusted publishers (normalized lowercase) ────────────
TRUSTED_PUBLISHERS = {
    # Microsoft
    'microsoft corporation', 'microsoft', 'cn=microsoft corporation',
    # Google
    'google llc', 'google inc', 'google', 'cn=google llc',
    # Apple
    'apple inc.', 'apple inc', 'apple',
    # Adobe
    'adobe inc.', 'adobe systems', 'adobe',
    # Mozilla
    'mozilla corporation', 'mozilla',
    # NVIDIA / AMD / Intel
    'nvidia corporation', 'advanced micro devices', 'intel corporation',
    # Zoom / Slack / Spotify
    'zoom video communications', 'slack technologies', 'spotify ab',
    # JetBrains / GitHub
    'jetbrains s.r.o.', 'github', 'github, inc.',
    # Canonical / Ubuntu
    'canonical group limited',
    # Oracle / Java
    'oracle america, inc.', 'oracle',
    # Autodesk
    'autodesk, inc.',
    # Valve
    'valve corporation',
    # Other common
    'python software foundation', 'postgresql global development group',
    'realtek', 'qualcomm', 'broadcom',
}

# Known app_id prefixes for trusted apps (AppX packages)
TRUSTED_APPID_PATTERNS = [
    r'^microsoft\.',
    r'^windows\.',
    r'^google\.',
    r'^2fef7e1f\.firefox',  # Firefox AppX
    r'\.spotify',
    r'\.github',
    r'code\.exe',  # VS Code
]

# ── Suspicious file indicators ─────────────────────────────────
STRONG_INDICATORS = [
    ('crack',       60, 'File/folder mengandung "crack" — indikator kuat bajakan'),
    ('keygen',      70, 'Key generator ditemukan — sangat mencurigakan'),
    ('activator',   65, 'Activator ditemukan — indikator bypass lisensi'),
    ('loader',      50, 'Loader mencurigakan ditemukan'),
    ('bypass',      55, 'Bypass tool ditemukan'),
    ('warez',       70, 'Warez indicator ditemukan'),
    ('nulled',      65, 'Nulled version indicator ditemukan'),
    ('pirat',       60, 'Pirate indicator ditemukan'),
]

MEDIUM_INDICATORS = [
    ('patch',       15, 'patch file ditemukan (bisa resmi atau tidak)'),
    ('serial',      20, 'Serial file ditemukan'),
    ('registration',10, 'Registration helper ditemukan'),
    ('license_fix', 30, 'License fix file ditemukan'),
    ('keyfinder',   40, 'Key finder tool ditemukan'),
    ('unlocker',    25, 'Unlocker tool ditemukan'),
]

SUSP_EXTENSIONS = {'.exe', '.dll', '.bat', '.cmd', '.ps1', '.vbs', '.com'}

# Skip these during scan
SKIP_DIRS = {'__pycache__', '.git', 'node_modules', 'temp', 'tmp', 'logs', 'log'}


class AppIntegrity:
    def __init__(self, data_sender, log_callback=None):
        self._ds  = data_sender
        self._log = log_callback or logger.info

    def check_batch(self, apps: list):
        threading.Thread(target=self._batch, args=(apps,), daemon=True).start()

    def check_single(self, app_db_id, app_name: str,
                     install_path=None, app_id=None, publisher=None):
        apps = [{'app_db_id': app_db_id, 'app_name': app_name,
                 'install_path': install_path, 'app_id': app_id,
                 'publisher': publisher}]
        threading.Thread(target=self._batch, args=(apps,), daemon=True).start()

    def _batch(self, apps: list):
        results = []
        for i, app in enumerate(apps):
            try:
                r = self._analyze(app)
                results.append(r)
                self._log(f"[Integrity] {i+1}/{len(apps)} {r['app_name']}: "
                          f"{r['risk_score']}/100 → {r['status']}")
                # Send in small batches
                if len(results) >= 5:
                    self._ds.send_app_integrity_results(results)
                    results = []
            except Exception as e:
                self._log(f"[Integrity] Error: {app.get('app_name')}: {e}")

        if results:
            self._ds.send_app_integrity_results(results)

    def _analyze(self, app: dict) -> dict:
        app_name     = app.get('app_name', '')
        install_path = app.get('install_path') or ''
        app_id       = app.get('app_id') or ''
        publisher    = app.get('publisher') or ''
        app_db_id    = app.get('app_db_id')

        score      = 0
        indicators = []
        evidence   = {'publisher': publisher, 'install_path': install_path}

        # ── 1. Publisher check ──────────────────────────────────
        pub_lower = publisher.lower().strip()
        if pub_lower:
            if any(tp in pub_lower for tp in TRUSTED_PUBLISHERS):
                score = max(0, score - 20)  # Trusted publisher lowers score
                indicators.append({
                    'type'   : 'positive',
                    'detail' : f'Publisher terverifikasi: {publisher}',
                    'file'   : None,
                })
            evidence['publisher_trusted'] = any(tp in pub_lower for tp in TRUSTED_PUBLISHERS)
        else:
            score += 8
            indicators.append({
                'type'   : 'weak',
                'detail' : 'Publisher tidak tersedia',
                'file'   : None,
            })

        # ── 2. App ID / package check ───────────────────────────
        aid_lower = app_id.lower()
        if aid_lower:
            if any(re.search(p, aid_lower) for p in TRUSTED_APPID_PATTERNS):
                score = max(0, score - 15)
                indicators.append({
                    'type'   : 'positive',
                    'detail' : f'App ID menunjukkan paket resmi',
                    'file'   : None,
                })
            # Check suspicious keywords in app_id
            for kw, pts, desc in STRONG_INDICATORS:
                if kw in aid_lower:
                    score += pts
                    indicators.append({'type':'strong','detail':f'App ID: {desc}','file':app_id})
                    break

        # ── 3. Install path scan ────────────────────────────────
        if install_path and os.path.isdir(install_path):
            fs_score, fs_indicators = self._scan_dir(install_path)
            score      += fs_score
            indicators += fs_indicators
        elif not install_path:
            pass  # No path - can't scan, just use other signals

        # ── 4. App name heuristic (lower weight) ────────────────
        name_lower = app_name.lower()
        for kw, pts, desc in STRONG_INDICATORS:
            if kw in name_lower:
                score += pts // 2  # Half weight from name alone
                indicators.append({'type':'medium','detail':f'Nama app mengandung "{kw}"','file':None})
                break

        # ── Final score ─────────────────────────────────────────
        score = max(0, min(score, 100))
        if score >= 60:
            status = 'suspicious'
        elif score >= 30:
            status = 'unknown'
        else:
            status = 'official'

        return {
            'app_db_id'  : app_db_id,
            'app_name'   : app_name,
            'risk_score' : score,
            'status'     : status,
            'indicators' : indicators,
            'publisher'  : publisher,
            'install_path': install_path,
        }

    def _scan_dir(self, path: str, max_depth: int = 2):
        """Scan install directory only (not entire filesystem)."""
        score      = 0
        indicators = []
        root       = Path(path)

        try:
            for item in root.rglob('*'):
                try:
                    depth = len(item.relative_to(root).parts)
                    if depth > max_depth:
                        continue
                except ValueError:
                    continue

                if item.is_dir() and item.name in SKIP_DIRS:
                    continue

                name_lower = item.stem.lower() if item.is_file() else item.name.lower()
                ext        = item.suffix.lower() if item.is_file() else ''

                # Strong indicators
                for kw, pts, desc in STRONG_INDICATORS:
                    if kw in name_lower:
                        actual_pts = pts if ext in SUSP_EXTENSIONS else pts // 2
                        score += actual_pts
                        indicators.append({
                            'type'  : 'strong',
                            'detail': f'{desc}: "{item.name}"',
                            'file'  : str(item),
                        })
                        break
                else:
                    # Medium indicators - only .exe/.dll
                    if ext in {'.exe', '.dll'}:
                        for kw, pts, desc in MEDIUM_INDICATORS:
                            if kw in name_lower:
                                score += pts
                                indicators.append({
                                    'type'  : 'medium',
                                    'detail': f'{desc}: "{item.name}"',
                                    'file'  : str(item),
                                })
                                break

                if score >= 80:
                    break

        except (PermissionError, OSError):
            pass

        return min(score, 80), indicators
