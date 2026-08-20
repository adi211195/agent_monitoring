import os
import sqlite3
import shutil
import tempfile
import time
import json
from datetime import datetime, timedelta
from typing import List, Dict
from urllib.parse import urlparse
from app_paths import get_app_data_path


class BrowsingHistoryTracker:
    def __init__(self, days_limit=7):
        self.days_limit = days_limit
        self.config_path = get_app_data_path("history_config.json")
        self.last_sent_time = self._load_last_sent_time()

    def _load_last_sent_time(self):
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "r") as f:
                    config = json.load(f)
                    return config.get("last_sent_time")
        except Exception as e:
            print(f"Error loading history config: {e}")
        return None

    def _save_last_sent_time(self, timestamp):
        try:
            with open(self.config_path, "w") as f:
                json.dump({"last_sent_time": timestamp}, f)
        except Exception as e:
            print(f"Error saving history config: {e}")

    def mark_as_sent(self, last_timestamp=None):
        """Update last_sent_time ke waktu sekarang atau ke timestamp data terbaru"""
        new_time = last_timestamp if last_timestamp else datetime.now().isoformat()
        self.last_sent_time = new_time
        self._save_last_sent_time(new_time)
        print(f" History marked as sent up to: {new_time}")

    # =========================
    # UTIL
    # =========================
    def _copy_db(self, db_path):
        temp_path = os.path.join(
            tempfile.gettempdir(),
            f"history_{os.getpid()}_{int(time.time())}.db"
        )

        for _ in range(3):
            try:
                shutil.copy2(db_path, temp_path)
                return temp_path
            except Exception:
                time.sleep(1)

        return None

    def _extract_domain(self, url):
        try:
            return urlparse(url).netloc
        except:
            return url

    def _normalize_time(self, dt: datetime):
        """Bulatkan ke detik → cegah duplikat beda milidetik"""
        return dt.replace(microsecond=0) if dt else None

    def _remove_duplicates(self, history):
        unique = {}

        for item in history:
            key = f"{item['url']}_{item['timestamp']}"
            unique[key] = item

        return list(unique.values())

    def _filter_new_data(self, history):
        if not self.last_sent_time:
            return history

        new_data = [
            h for h in history
            if h["timestamp"] and h["timestamp"] > self.last_sent_time
        ]

        return new_data

    # =========================
    # CHROME / EDGE
    # =========================
    def _get_chromium_history(self, base_path, browser_name):
        history = []

        if not os.path.exists(base_path):
            return history

        cutoff = datetime.now() - timedelta(days=self.days_limit)
        cutoff_micro = int((cutoff - datetime(1601, 1, 1)).total_seconds() * 1_000_000)

        for profile in os.listdir(base_path):
            history_path = os.path.join(base_path, profile, "History")

            if not os.path.exists(history_path):
                continue

            temp_db = self._copy_db(history_path)
            if not temp_db:
                continue

            try:
                conn = sqlite3.connect(temp_db)
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT url, title, visit_count, last_visit_time
                    FROM urls
                    WHERE last_visit_time > ?
                    ORDER BY last_visit_time DESC
                    LIMIT 500
                """, (cutoff_micro,))

                for url, title, visit_count, ts in cursor.fetchall():
                    if ts:
                        timestamp = datetime(1601, 1, 1) + timedelta(microseconds=ts)
                        timestamp = self._normalize_time(timestamp)
                    else:
                        timestamp = None

                    history.append({
                        "browser": browser_name,
                        "url": url,
                        "title": title or "",
                        "visit_count": visit_count or 1,
                        "timestamp": timestamp.isoformat() if timestamp else None,
                        "domain": self._extract_domain(url)
                    })

                conn.close()
            except Exception as e:
                print(f"{browser_name} error:", e)

            try:
                os.remove(temp_db)
            except:
                pass

        return history

    def get_chrome_history(self):
        base = os.path.expanduser("~")
        path = os.path.join(base, "AppData", "Local", "Google", "Chrome", "User Data")
        return self._get_chromium_history(path, "Chrome")

    def get_edge_history(self):
        base = os.path.expanduser("~")
        path = os.path.join(base, "AppData", "Local", "Microsoft", "Edge", "User Data")
        return self._get_chromium_history(path, "Edge")

    # =========================
    # FIREFOX
    # =========================
    def get_firefox_history(self):
        history = []

        base = os.path.expanduser("~")
        profiles_path = os.path.join(base, "AppData", "Roaming", "Mozilla", "Firefox", "Profiles")

        if not os.path.exists(profiles_path):
            return history

        cutoff = datetime.now() - timedelta(days=self.days_limit)
        cutoff_micro = int(cutoff.timestamp() * 1_000_000)

        for profile in os.listdir(profiles_path):
            db_path = os.path.join(profiles_path, profile, "places.sqlite")

            if not os.path.exists(db_path):
                continue

            temp_db = self._copy_db(db_path)
            if not temp_db:
                continue

            try:
                conn = sqlite3.connect(temp_db)
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT p.url, p.title, p.visit_count, h.visit_date
                    FROM moz_places p
                    JOIN moz_historyvisits h ON p.id = h.place_id
                    WHERE h.visit_date > ?
                    ORDER BY h.visit_date DESC
                    LIMIT 500
                """, (cutoff_micro,))

                for url, title, visit_count, visit_date in cursor.fetchall():
                    if visit_date:
                        timestamp = datetime.fromtimestamp(visit_date / 1_000_000)
                        timestamp = self._normalize_time(timestamp)
                    else:
                        timestamp = None

                    history.append({
                        "browser": "Firefox",
                        "url": url,
                        "title": title or "",
                        "visit_count": visit_count or 1,
                        "timestamp": timestamp.isoformat() if timestamp else None,
                        "domain": self._extract_domain(url)
                    })

                conn.close()
            except Exception as e:
                print("Firefox error:", e)

            try:
                os.remove(temp_db)
            except:
                pass

        return history

    # =========================
    # MAIN
    # =========================
    def get_all_history(self):
        all_data = []

        print(" Chrome...")
        all_data.extend(self.get_chrome_history())

        print(" Edge...")
        all_data.extend(self.get_edge_history())

        print(" Firefox...")
        all_data.extend(self.get_firefox_history())

        # sort terbaru
        all_data.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

        #  FIX 1: hapus duplikat
        all_data = self._remove_duplicates(all_data)

        return all_data[:500]

    def get_new_history(self):
        data = self.get_all_history()

        #  FIX 2: hanya kirim data baru
        data = self._filter_new_data(data)

        return data