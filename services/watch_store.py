"""7 kunlik raqobatchi kuzatuvi — lokal SQLite (yt.db, Railway volume'da saqlanadi).

- channel_watch: o'quvchi -> kuzatilayotgan kanallar (bitta faol kuzatuv).
- watch_meta:    o'quvchi -> yo'nalish, boshlanish, nechta kunlik hisobot yuborilgan.
- channel_snapshot: kanal -> kun -> statistika + oxirgi videolar (kunlik farqlarni hisoblash uchun).
"""
import json
import sqlite3
import threading
import time
from typing import Dict, List, Optional

from config import YT_DB_PATH

_lock = threading.Lock()
WATCH_DAYS = 7


def _init() -> None:
    with sqlite3.connect(YT_DB_PATH) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS watch_meta (
                telegram_id     INTEGER PRIMARY KEY,
                niche           TEXT,
                started_at      INTEGER NOT NULL,
                days            INTEGER NOT NULL DEFAULT 7,
                digests_sent    INTEGER NOT NULL DEFAULT 0,
                last_digest_day TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS channel_watch (
                telegram_id INTEGER NOT NULL,
                channel_id  TEXT    NOT NULL,
                title       TEXT,
                url         TEXT,
                PRIMARY KEY (telegram_id, channel_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS channel_snapshot (
                channel_id  TEXT    NOT NULL,
                day         TEXT    NOT NULL,
                taken_at    INTEGER NOT NULL,
                subs        INTEGER NOT NULL DEFAULT 0,
                views       INTEGER NOT NULL DEFAULT 0,
                video_count INTEGER NOT NULL DEFAULT 0,
                videos_json TEXT,
                PRIMARY KEY (channel_id, day)
            )
        """)


_init()


def start_watch(telegram_id: int, niche: str, channels: List[dict], days: int = WATCH_DAYS) -> None:
    """Yangi kuzatuv boshlaydi (eskisini almashtiradi)."""
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        c.execute("DELETE FROM channel_watch WHERE telegram_id=?", (telegram_id,))
        c.execute("DELETE FROM watch_meta WHERE telegram_id=?", (telegram_id,))
        c.execute(
            "INSERT INTO watch_meta (telegram_id, niche, started_at, days, digests_sent, last_digest_day)"
            " VALUES (?,?,?,?,0,NULL)",
            (telegram_id, niche[:120], int(time.time()), days),
        )
        c.executemany(
            "INSERT OR IGNORE INTO channel_watch (telegram_id, channel_id, title, url) VALUES (?,?,?,?)",
            [(telegram_id, ch["channel_id"], ch.get("title", "")[:100], ch.get("url", "")) for ch in channels],
        )


def stop_watch(telegram_id: int) -> bool:
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        had = c.execute("SELECT 1 FROM watch_meta WHERE telegram_id=?", (telegram_id,)).fetchone()
        c.execute("DELETE FROM channel_watch WHERE telegram_id=?", (telegram_id,))
        c.execute("DELETE FROM watch_meta WHERE telegram_id=?", (telegram_id,))
    return bool(had)


def get_watch(telegram_id: int) -> Optional[dict]:
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        m = c.execute(
            "SELECT niche, started_at, days, digests_sent, last_digest_day FROM watch_meta WHERE telegram_id=?",
            (telegram_id,),
        ).fetchone()
        if not m:
            return None
        chans = c.execute(
            "SELECT channel_id, title, url FROM channel_watch WHERE telegram_id=?", (telegram_id,)
        ).fetchall()
    return {
        "telegram_id": telegram_id, "niche": m[0], "started_at": m[1], "days": m[2],
        "digests_sent": m[3], "last_digest_day": m[4],
        "channels": [{"channel_id": r[0], "title": r[1], "url": r[2]} for r in chans],
    }


def active_watches() -> List[dict]:
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        ids = [r[0] for r in c.execute("SELECT telegram_id FROM watch_meta").fetchall()]
    return [w for w in (get_watch(t) for t in ids) if w]


def all_watched_channel_ids() -> List[str]:
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        return [r[0] for r in c.execute("SELECT DISTINCT channel_id FROM channel_watch").fetchall()]


def save_snapshot(channel_id: str, day: str, subs: int, views: int, video_count: int, videos: list) -> None:
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        c.execute(
            "INSERT OR REPLACE INTO channel_snapshot (channel_id, day, taken_at, subs, views, video_count, videos_json)"
            " VALUES (?,?,?,?,?,?,?)",
            (channel_id, day, int(time.time()), subs, views, video_count, json.dumps(videos, ensure_ascii=False)),
        )


def _row_to_snap(r) -> dict:
    return {"day": r[0], "taken_at": r[1], "subs": r[2], "views": r[3], "video_count": r[4],
            "videos": json.loads(r[5] or "[]")}


def get_snapshot(channel_id: str, day: str) -> Optional[dict]:
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        r = c.execute(
            "SELECT day, taken_at, subs, views, video_count, videos_json FROM channel_snapshot"
            " WHERE channel_id=? AND day=?", (channel_id, day),
        ).fetchone()
    return _row_to_snap(r) if r else None


def previous_snapshot(channel_id: str, before_day: str) -> Optional[dict]:
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        r = c.execute(
            "SELECT day, taken_at, subs, views, video_count, videos_json FROM channel_snapshot"
            " WHERE channel_id=? AND day<? ORDER BY day DESC LIMIT 1", (channel_id, before_day),
        ).fetchone()
    return _row_to_snap(r) if r else None


def snapshots_since(channel_id: str, since_day: str) -> List[dict]:
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        rows = c.execute(
            "SELECT day, taken_at, subs, views, video_count, videos_json FROM channel_snapshot"
            " WHERE channel_id=? AND day>=? ORDER BY day ASC", (channel_id, since_day),
        ).fetchall()
    return [_row_to_snap(r) for r in rows]


def mark_digest_sent(telegram_id: int, day: str) -> int:
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        c.execute(
            "UPDATE watch_meta SET digests_sent = digests_sent + 1, last_digest_day=? WHERE telegram_id=?",
            (day, telegram_id),
        )
        r = c.execute("SELECT digests_sent FROM watch_meta WHERE telegram_id=?", (telegram_id,)).fetchone()
    return int(r[0]) if r else 0


def cleanup_snapshots(older_than_days: int = 30) -> None:
    cutoff = int(time.time()) - older_than_days * 86400
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        c.execute("DELETE FROM channel_snapshot WHERE taken_at < ?", (cutoff,))
