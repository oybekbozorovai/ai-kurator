"""Foydalanuvchi autentifikatsiyasi: telefon ro'yxati + tasdiqlangan foydalanuvchilar.

SQLite asosida (sql3 — Python ichida bor, qo'shimcha kutubxona kerak emas)."""
import logging
import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Tuple

from config import BASE_DIR

logger = logging.getLogger(__name__)

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "auth.db"

_lock = Lock()


def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS allowed_phones (
                phone TEXT PRIMARY KEY,
                added_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS approved_users (
                telegram_id INTEGER PRIMARY KEY,
                phone TEXT NOT NULL,
                first_name TEXT,
                username TEXT,
                joined_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS banned_users (
                telegram_id INTEGER PRIMARY KEY,
                banned_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );
        """)


_init_db()


def normalize_phone(phone: str) -> str:
    """+998901234567, 998901234567, 901234567 → 998901234567."""
    digits = "".join(c for c in phone if c.isdigit())
    if not digits:
        return ""
    # O'zbekiston: 9 raqam → 998 prefiks qo'shamiz
    if len(digits) == 9 and digits[0] == "9":
        return "998" + digits
    return digits


def add_allowed_phones(phones: List[str]) -> int:
    """Telefon ro'yxatiga qo'shadi, qo'shilgan yangi raqamlar sonini qaytaradi."""
    added = 0
    with _lock, sqlite3.connect(DB_PATH) as c:
        for p in phones:
            n = normalize_phone(p)
            if not n:
                continue
            cur = c.execute("INSERT OR IGNORE INTO allowed_phones (phone) VALUES (?)", (n,))
            if cur.rowcount:
                added += 1
    return added


def remove_allowed_phone(phone: str) -> bool:
    n = normalize_phone(phone)
    with _lock, sqlite3.connect(DB_PATH) as c:
        cur = c.execute("DELETE FROM allowed_phones WHERE phone = ?", (n,))
        return cur.rowcount > 0


def is_phone_allowed(phone: str) -> bool:
    n = normalize_phone(phone)
    if not n:
        return False
    with _lock, sqlite3.connect(DB_PATH) as c:
        return c.execute("SELECT 1 FROM allowed_phones WHERE phone = ?", (n,)).fetchone() is not None


def approve_user(telegram_id: int, phone: str, first_name: str = "", username: str = "") -> None:
    n = normalize_phone(phone)
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute(
            """INSERT OR REPLACE INTO approved_users
               (telegram_id, phone, first_name, username, joined_at)
               VALUES (?, ?, ?, ?, ?)""",
            (telegram_id, n, first_name, username, int(time.time())),
        )
        c.execute("DELETE FROM banned_users WHERE telegram_id = ?", (telegram_id,))


def is_user_approved(telegram_id: int) -> bool:
    with _lock, sqlite3.connect(DB_PATH) as c:
        if c.execute("SELECT 1 FROM banned_users WHERE telegram_id = ?", (telegram_id,)).fetchone():
            return False
        return c.execute(
            "SELECT 1 FROM approved_users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone() is not None


def get_user_info(telegram_id: int) -> Optional[Dict]:
    with _lock, sqlite3.connect(DB_PATH) as c:
        row = c.execute(
            "SELECT telegram_id, phone, first_name, username, joined_at FROM approved_users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "telegram_id": row[0],
            "phone": row[1],
            "first_name": row[2],
            "username": row[3],
            "joined_at": row[4],
        }


def list_approved_users(limit: int = 200) -> List[Tuple]:
    with _lock, sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT telegram_id, phone, first_name, username, joined_at "
            "FROM approved_users ORDER BY joined_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


def ban_user(telegram_id: int) -> None:
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute("INSERT OR REPLACE INTO banned_users (telegram_id) VALUES (?)", (telegram_id,))
        c.execute("DELETE FROM approved_users WHERE telegram_id = ?", (telegram_id,))


def unban_user(telegram_id: int) -> None:
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute("DELETE FROM banned_users WHERE telegram_id = ?", (telegram_id,))


def stats() -> Dict[str, int]:
    with _lock, sqlite3.connect(DB_PATH) as c:
        return {
            "allowed_phones": c.execute("SELECT COUNT(*) FROM allowed_phones").fetchone()[0],
            "approved_users": c.execute("SELECT COUNT(*) FROM approved_users").fetchone()[0],
            "banned_users": c.execute("SELECT COUNT(*) FROM banned_users").fetchone()[0],
        }
