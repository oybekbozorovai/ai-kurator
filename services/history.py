"""YouTube xizmatlari tarixi va kunlik limitlar (SQLite).

Har bir o'quvchining yaratgan ishlari saqlanadi — matn yoki Telegram file_id.
file_id ishlatilgani uchun rasmlar Railway redeploy'da yo'qolmaydi.
"""

import logging
import sqlite3
import time
from datetime import datetime
from threading import Lock

from config import YT_DB_PATH

logger = logging.getLogger(__name__)

YT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_lock = Lock()


def _init_db() -> None:
    with sqlite3.connect(YT_DB_PATH) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS generations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                service     TEXT NOT NULL,   -- channel_seo, video_seo, avatar, banner, thumbnail
                kind        TEXT NOT NULL,   -- 'text' yoki 'image' (limit uchun)
                label       TEXT,            -- tarix ro'yxatida ko'rsatiladigan nom
                result_type TEXT NOT NULL,   -- 'text' yoki 'file'
                result_text TEXT,            -- matnli natija / izoh
                file_id     TEXT,            -- rasm uchun Telegram file_id
                created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_gen_user "
            "ON generations(telegram_id, created_at)"
        )


_init_db()


def log_generation(telegram_id: int, service: str, kind: str, label: str,
                   result_type: str, result_text: str = None,
                   file_id: str = None) -> None:
    """Yaratilgan ishni tarixga yozadi."""
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        c.execute(
            "INSERT INTO generations "
            "(telegram_id, service, kind, label, result_type, result_text, file_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (telegram_id, service, kind, label, result_type,
             result_text, file_id, int(time.time())),
        )


def count_today(telegram_id: int, kind: str) -> int:
    """Bugun (yarim tundan beri) shu turdagi nechta ish yaratilgan."""
    start_of_day = int(
        datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        row = c.execute(
            "SELECT COUNT(*) FROM generations "
            "WHERE telegram_id = ? AND kind = ? AND created_at >= ?",
            (telegram_id, kind, start_of_day),
        ).fetchone()
        return row[0] if row else 0


def get_history(telegram_id: int, limit: int = 10) -> list:
    """O'quvchining oxirgi ishlarini qaytaradi: [(id, service, label, created_at), ...]."""
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        return c.execute(
            "SELECT id, service, label, created_at FROM generations "
            "WHERE telegram_id = ? ORDER BY id DESC LIMIT ?",
            (telegram_id, limit),
        ).fetchall()


def get_item(item_id: int):
    """Bitta ishni to'liq qaytaradi yoki None.
    Qaytadi: (id, telegram_id, service, kind, label, result_type, result_text, file_id)
    """
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        return c.execute(
            "SELECT id, telegram_id, service, kind, label, result_type, result_text, file_id "
            "FROM generations WHERE id = ?",
            (item_id,),
        ).fetchone()
