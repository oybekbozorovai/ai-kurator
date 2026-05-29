"""Foydalanuvchi autentifikatsiyasi: telefon ro'yxati + tasdiqlangan foydalanuvchilar.

Muddatli ruxsat (4 oy default) — ekspirit bo'lgach avtomatik chiqarib yuboriladi."""
import logging
import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Tuple

from config import ADMIN_USER_IDS, BASE_DIR, COURSE_ACCESS_MONTHS

logger = logging.getLogger(__name__)

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "auth.db"

_lock = Lock()

DAYS_PER_MONTH = 30


def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS allowed_phones (
                phone TEXT PRIMARY KEY,
                added_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                expires_at INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS approved_users (
                telegram_id INTEGER PRIMARY KEY,
                phone TEXT NOT NULL,
                first_name TEXT,
                username TEXT,
                joined_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                expires_at INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS banned_users (
                telegram_id INTEGER PRIMARY KEY,
                banned_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS kick_log (
                telegram_id INTEGER NOT NULL,
                kicked_at INTEGER NOT NULL,
                reason TEXT,
                PRIMARY KEY (telegram_id, kicked_at)
            );
            CREATE TABLE IF NOT EXISTS assistant_admins (
                telegram_id INTEGER PRIMARY KEY,
                added_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS expiry_warned (
                telegram_id INTEGER PRIMARY KEY,
                warned_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS reminder_optout (
                telegram_id INTEGER PRIMARY KEY,
                disabled_at INTEGER NOT NULL
            );
        """)
        # Migratsiya: agar eski DB'da expires_at ustuni yo'q bo'lsa qo'shamiz
        for table in ("allowed_phones", "approved_users"):
            cols = [row[1] for row in c.execute(f"PRAGMA table_info({table})").fetchall()]
            if "expires_at" not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN expires_at INTEGER NOT NULL DEFAULT 0")
                logger.info("Migratsiya: %s ga expires_at qo'shildi", table)


_init_db()


def normalize_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if not digits:
        return ""
    if len(digits) == 9 and digits[0] == "9":
        return "998" + digits
    return digits


def _months_to_timestamp(months: int) -> int:
    """N oy keyingi unix timestamp. months=0 → 0 (cheksiz)."""
    if months <= 0:
        return 0
    return int(time.time()) + months * DAYS_PER_MONTH * 24 * 3600


def add_allowed_phones(phones: List[str], months: int = COURSE_ACCESS_MONTHS) -> int:
    """Telefon ro'yxatiga muddatli ruxsat bilan qo'shadi.
    months=0 → cheksiz, months=4 → 4 oy."""
    expires_at = _months_to_timestamp(months)
    now = int(time.time())
    added = 0
    with _lock, sqlite3.connect(DB_PATH) as c:
        for p in phones:
            n = normalize_phone(p)
            if not n:
                continue
            cur = c.execute(
                "INSERT OR REPLACE INTO allowed_phones (phone, added_at, expires_at) "
                "VALUES (?, ?, ?)",
                (n, now, expires_at),
            )
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
    now = int(time.time())
    with _lock, sqlite3.connect(DB_PATH) as c:
        row = c.execute(
            "SELECT expires_at FROM allowed_phones WHERE phone = ?",
            (n,),
        ).fetchone()
        if not row:
            return False
        expires_at = row[0]
        # 0 = cheksiz; aks holda muddat tekshiriladi
        if expires_at == 0:
            return True
        return now < expires_at


def approve_user(telegram_id: int, phone: str, first_name: str = "", username: str = "") -> None:
    n = normalize_phone(phone)
    now = int(time.time())
    with _lock, sqlite3.connect(DB_PATH) as c:
        # Telefon ro'yxatidan expires_at ni olamiz
        row = c.execute(
            "SELECT expires_at FROM allowed_phones WHERE phone = ?",
            (n,),
        ).fetchone()
        expires_at = row[0] if row else 0
        c.execute(
            """INSERT OR REPLACE INTO approved_users
               (telegram_id, phone, first_name, username, joined_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (telegram_id, n, first_name, username, now, expires_at),
        )
        c.execute("DELETE FROM banned_users WHERE telegram_id = ?", (telegram_id,))
        c.execute("DELETE FROM expiry_warned WHERE telegram_id = ?", (telegram_id,))


def free_phone(phone: str) -> List[int]:
    """Shu raqam bilan ro'yxatdan o'tgan akkount(lar)ni approved_users'dan o'chiradi.
    Raqam ruxsat ro'yxatida (allowed_phones) qoladi — boshqa akkount qayta ro'yxatdan o'ta oladi.
    O'chirilgan telegram_id'lar ro'yxatini qaytaradi."""
    n = normalize_phone(phone)
    if not n:
        return []
    with _lock, sqlite3.connect(DB_PATH) as c:
        rows = c.execute(
            "SELECT telegram_id FROM approved_users WHERE phone = ?", (n,)
        ).fetchall()
        ids = [r[0] for r in rows]
        if ids:
            c.execute("DELETE FROM approved_users WHERE phone = ?", (n,))
        return ids


def get_phone_owner(phone: str) -> Optional[int]:
    """Shu raqam bilan ro'yxatdan o'tgan telegram_id ni qaytaradi (bo'lmasa None)."""
    n = normalize_phone(phone)
    if not n:
        return None
    with _lock, sqlite3.connect(DB_PATH) as c:
        row = c.execute(
            "SELECT telegram_id FROM approved_users WHERE phone = ?",
            (n,),
        ).fetchone()
        return row[0] if row else None


def is_user_approved(telegram_id: int) -> bool:
    """Foydalanuvchi tasdiqlangan VA muddati o'tmagan."""
    now = int(time.time())
    with _lock, sqlite3.connect(DB_PATH) as c:
        if c.execute("SELECT 1 FROM banned_users WHERE telegram_id = ?", (telegram_id,)).fetchone():
            return False
        row = c.execute(
            "SELECT expires_at FROM approved_users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if not row:
            return False
        expires_at = row[0]
        if expires_at == 0:
            return True
        return now < expires_at


def get_user_info(telegram_id: int) -> Optional[Dict]:
    with _lock, sqlite3.connect(DB_PATH) as c:
        row = c.execute(
            "SELECT telegram_id, phone, first_name, username, joined_at, expires_at "
            "FROM approved_users WHERE telegram_id = ?",
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
            "expires_at": row[5],
        }


def list_approved_users(limit: int = 200) -> List[Tuple]:
    with _lock, sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT telegram_id, phone, first_name, username, joined_at, expires_at "
            "FROM approved_users ORDER BY joined_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


def list_allowed_phones(limit: int = 500) -> List[Tuple]:
    """Ruxsat ro'yxatidagi raqamlar va ular ro'yxatdan o'tganmi.
    Qaytaradi: (phone, expires_at, is_registered) — is_registered=1 agar o'sha raqam approved_users'da bo'lsa."""
    with _lock, sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT a.phone, a.expires_at, "
            "       EXISTS(SELECT 1 FROM approved_users u WHERE u.phone = a.phone) "
            "FROM allowed_phones a ORDER BY a.added_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


def list_all_user_ids() -> List[int]:
    """Barcha tasdiqlangan o'quvchilarning telegram_id'lari (e'lon yuborish uchun)."""
    with _lock, sqlite3.connect(DB_PATH) as c:
        return [r[0] for r in c.execute("SELECT telegram_id FROM approved_users").fetchall()]


def disable_reminders(telegram_id: int) -> None:
    """O'quvchi kunlik eslatmalarni o'chiradi."""
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT OR IGNORE INTO reminder_optout (telegram_id, disabled_at) VALUES (?, ?)",
            (telegram_id, int(time.time())),
        )


def enable_reminders(telegram_id: int) -> None:
    """O'quvchi kunlik eslatmalarni qayta yoqadi."""
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute("DELETE FROM reminder_optout WHERE telegram_id = ?", (telegram_id,))


def list_reminder_user_ids() -> List[int]:
    """Kunlik eslatma yoqilgan o'quvchilar (o'chirmaganlar)."""
    with _lock, sqlite3.connect(DB_PATH) as c:
        return [
            r[0] for r in c.execute(
                "SELECT telegram_id FROM approved_users "
                "WHERE telegram_id NOT IN (SELECT telegram_id FROM reminder_optout)"
            ).fetchall()
        ]


def get_setting(key: str) -> Optional[str]:
    """Botning kichik holatini o'qiydi (masalan oxirgi kunlik eslatma sanasi)."""
    with _lock, sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None


def set_setting(key: str, value: str) -> None:
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)", (key, value)
        )


def get_users_to_warn(days: int = 3) -> List[Tuple]:
    """Muddati `days` kun ichida tugaydigan, lekin hali ogohlantirilmaganlar."""
    now = int(time.time())
    deadline = now + days * 24 * 3600
    with _lock, sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT telegram_id, phone, first_name, expires_at FROM approved_users "
            "WHERE expires_at > ? AND expires_at < ? "
            "AND telegram_id NOT IN (SELECT telegram_id FROM expiry_warned)",
            (now, deadline),
        ).fetchall()


def mark_warned(telegram_id: int) -> None:
    """Ogohlantirilgan deb belgilaydi — qayta ogohlantirmaslik uchun."""
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT OR IGNORE INTO expiry_warned (telegram_id, warned_at) VALUES (?, ?)",
            (telegram_id, int(time.time())),
        )


def get_expired_users() -> List[Tuple]:
    """Muddati o'tgan tasdiqlangan foydalanuvchilar."""
    now = int(time.time())
    with _lock, sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT telegram_id, phone, first_name, expires_at "
            "FROM approved_users WHERE expires_at > 0 AND expires_at < ?",
            (now,),
        ).fetchall()


def get_expiring_soon(days: int = 7) -> List[Tuple]:
    """Yaqin N kun ichida muddati tugaydiganlar."""
    now = int(time.time())
    soon = now + days * 24 * 3600
    with _lock, sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT telegram_id, phone, first_name, expires_at "
            "FROM approved_users WHERE expires_at > ? AND expires_at < ? "
            "ORDER BY expires_at ASC",
            (now, soon),
        ).fetchall()


def mark_user_kicked(telegram_id: int, reason: str = "expired") -> None:
    """Foydalanuvchini approved_users'dan o'chiradi va kick_log'ga yozadi."""
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT OR IGNORE INTO kick_log (telegram_id, kicked_at, reason) VALUES (?, ?, ?)",
            (telegram_id, int(time.time()), reason),
        )
        c.execute("DELETE FROM approved_users WHERE telegram_id = ?", (telegram_id,))
        c.execute("DELETE FROM expiry_warned WHERE telegram_id = ?", (telegram_id,))


def ban_user(telegram_id: int) -> None:
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute("INSERT OR REPLACE INTO banned_users (telegram_id) VALUES (?)", (telegram_id,))
        c.execute("DELETE FROM approved_users WHERE telegram_id = ?", (telegram_id,))


def unban_user(telegram_id: int) -> None:
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute("DELETE FROM banned_users WHERE telegram_id = ?", (telegram_id,))


# ============================================================
# Yordamchi adminlar (asosiy admin DB orqali boshqaradi)
# ============================================================

def add_assistant_admin(telegram_id: int) -> bool:
    """Yordamchi admin qo'shadi. Yangi qo'shilsa True, allaqachon bor bo'lsa False."""
    with _lock, sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO assistant_admins (telegram_id) VALUES (?)",
            (telegram_id,),
        )
        return cur.rowcount > 0


def remove_assistant_admin(telegram_id: int) -> bool:
    with _lock, sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            "DELETE FROM assistant_admins WHERE telegram_id = ?", (telegram_id,)
        )
        return cur.rowcount > 0


def is_assistant_admin(telegram_id: int) -> bool:
    with _lock, sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT 1 FROM assistant_admins WHERE telegram_id = ?", (telegram_id,)
        ).fetchone() is not None


def list_assistant_admins() -> List[int]:
    with _lock, sqlite3.connect(DB_PATH) as c:
        return [
            r[0] for r in c.execute(
                "SELECT telegram_id FROM assistant_admins ORDER BY added_at"
            ).fetchall()
        ]


def is_admin(telegram_id: int) -> bool:
    """Asosiy admin (ADMIN_USER_IDS) yoki yordamchi admin (DB)."""
    return telegram_id in ADMIN_USER_IDS or is_assistant_admin(telegram_id)


def stats() -> Dict[str, int]:
    with _lock, sqlite3.connect(DB_PATH) as c:
        now = int(time.time())
        return {
            "allowed_phones": c.execute("SELECT COUNT(*) FROM allowed_phones").fetchone()[0],
            "approved_users": c.execute("SELECT COUNT(*) FROM approved_users").fetchone()[0],
            "banned_users": c.execute("SELECT COUNT(*) FROM banned_users").fetchone()[0],
            "expired_pending_kick": c.execute(
                "SELECT COUNT(*) FROM approved_users "
                "WHERE expires_at > 0 AND expires_at < ?", (now,)
            ).fetchone()[0],
            "kick_log": c.execute("SELECT COUNT(*) FROM kick_log").fetchone()[0],
        }
