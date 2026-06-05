"""Foydalanuvchi autentifikatsiyasi — Supabase asosiy manba (A.Y.P.I Platforma bilan umumiy baza).

Drop-in: funksiya imzolari o'zgarmagan. Ruxsat/roster/muddat — Supabase'dan:
  - allowed_contacts : ruxsat etilgan raqamlar (patok bo'yicha)
  - users            : ro'yxatdan o'tgan o'quvchilar (phone, cohort_id)
  - cohorts          : patok (ends_at, is_active) → muddat shu yerda

Bot-faqat operatsion holat (ban, kick log, eslatma, sozlamalar) kichik lokal
`data/botstate.db` (sqlite) da saqlanadi — Supabase sxemasini o'zgartirmaslik uchun.

Telefon E.164 formatda: "+998XXXXXXXXX" (Supabase kanonik). normalize_phone
A.Y.P.I normalizePhone bilan bir xil natija beradi.
"""
import logging
import sqlite3
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, List, Optional, Tuple

from config import ADMIN_USER_IDS, BASE_DIR
from services import supabase_db as sb

logger = logging.getLogger(__name__)

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "botstate.db"

_lock = Lock()

# Supabase'da admin hisoblangan rollar (is_admin shu rollarni ham qabul qiladi)
ADMIN_ROLES = {"owner", "admin"}


# ============================================================
# Lokal bot holati (faqat shu bot uchun — Supabase'da emas)
# ============================================================

def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.executescript("""
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


_init_db()


# ============================================================
# Telefon normalizatsiyasi (A.Y.P.I normalizePhone bilan bir xil)
# ============================================================

def normalize_phone(phone: str) -> str:
    """E.164 ga keltiradi: "+998XXXXXXXXX". Tushunarsiz bo'lsa "" qaytaradi."""
    if not phone:
        return ""
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if not digits:
        return ""
    n = len(digits)
    if n == 9 and digits[0] == "9":
        return "+998" + digits
    if n == 12 and digits.startswith("998"):
        return "+" + digits
    if n == 13 and digits.startswith("9998"):  # ikki marta 9 yozib yuborilgan
        return "+" + digits[1:]
    if 10 <= n <= 15:
        return "+" + digits
    return ""


def _strip_plus(phone: Optional[str]) -> str:
    """Admin/scheduler matnlari `+{phone}` ko'rinishida chop etadi —
    qaytariladigan tuple'larda '+' ni olib tashlaymiz (ikki marta '+' bo'lmasin)."""
    if not phone:
        return ""
    return phone[1:] if phone.startswith("+") else phone


# ============================================================
# Vaqt / sana yordamchilari
# ============================================================

def _now() -> int:
    return int(time.time())


def _date_to_ts(date_str: Optional[str]) -> int:
    """cohorts.ends_at ("2026-09-01" yoki ISO) → unix timestamp (kun oxiri, UTC).
    Bo'sh/yo'q bo'lsa 0 (cheksiz)."""
    if not date_str:
        return 0
    s = str(date_str).strip()
    try:
        if "T" in s:
            s = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
        else:
            dt = datetime.fromisoformat(s + "T23:59:59+00:00")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        logger.warning("ends_at parse bo'lmadi: %r", date_str)
        return 0


def _cohort_expiry(cohort: Optional[dict]) -> Tuple[int, bool]:
    """Embedded cohort dict'idan (expires_ts, is_active) qaytaradi.
    cohort yo'q → (0, True) = cheksiz/faol (admin/owner kabi)."""
    if not cohort:
        return 0, True
    return _date_to_ts(cohort.get("ends_at")), bool(cohort.get("is_active", True))


# ============================================================
# Ruxsat ro'yxati (allowed_contacts) — Supabase
# ============================================================

def is_phone_allowed(phone: str) -> bool:
    """Raqam patokka biriktirilgan VA patok faol/muddati o'tmaganmi."""
    n = normalize_phone(phone)
    if not n:
        return False
    rows = sb.select(
        "allowed_contacts",
        f"phone=eq.{sb.quote(n)}&select=cohort_id,cohort:cohorts(ends_at,is_active)",
    )
    if not rows:
        return False
    now = _now()
    for r in rows:
        exp, active = _cohort_expiry(r.get("cohort"))
        if r.get("cohort_id") is None:  # patoksiz ruxsat — cheksiz
            return True
        if active and (exp == 0 or now < exp):
            return True
    return False


def get_phone_owner(phone: str) -> Optional[int]:
    """Shu raqam bilan ro'yxatdan o'tgan telegram_id (bo'lmasa None)."""
    n = normalize_phone(phone)
    if not n:
        return None
    rows = sb.select(
        "users",
        f"phone=eq.{sb.quote(n)}&telegram_id=not.is.null&select=telegram_id&limit=1",
    )
    if rows and rows[0].get("telegram_id") is not None:
        return int(rows[0]["telegram_id"])
    return None


def _allowed_contact(n: str) -> Optional[dict]:
    rows = sb.select(
        "allowed_contacts",
        f"phone=eq.{sb.quote(n)}&select=cohort_id,role&limit=1",
    )
    return rows[0] if rows else None


def approve_user(telegram_id: int, phone: str, first_name: str = "", username: str = "") -> bool:
    """O'quvchini Supabase users'ga bog'laydi (phone + patok). Yangi/yangilangan → True."""
    n = normalize_phone(phone)
    if not n:
        return False
    contact = _allowed_contact(n)
    cohort_id = contact.get("cohort_id") if contact else None
    role = (contact.get("role") if contact else None) or "student"

    existing = sb.select(
        "users", f"telegram_id=eq.{telegram_id}&select=id,role&limit=1"
    )
    patch = {
        "phone": n,
        "cohort_id": cohort_id,
        "first_name": first_name or None,
        "username": username or None,
        "last_seen_at": _iso_now(),
    }
    try:
        if existing:
            sb.update("users", f"telegram_id=eq.{telegram_id}", patch)
        else:
            row = dict(patch)
            row["telegram_id"] = telegram_id
            row["role"] = role
            sb.insert("users", row)
    except sb.SupabaseError as e:
        logger.error("approve_user Supabase xato: %s", e)
        return False

    # allowed_contacts.used_at ni belgilaymiz (statistika uchun)
    try:
        sb.update(
            "allowed_contacts",
            f"phone=eq.{sb.quote(n)}&used_at=is.null",
            {"used_at": _iso_now()},
            prefer="return=minimal",
        )
    except sb.SupabaseError:
        pass

    # Lokal ban/ogohlantirishni tozalaymiz
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute("DELETE FROM banned_users WHERE telegram_id = ?", (telegram_id,))
        c.execute("DELETE FROM expiry_warned WHERE telegram_id = ?", (telegram_id,))
    return True


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Tasdiqlangan foydalanuvchilar (users) — Supabase
# ============================================================

def _is_banned(telegram_id: int) -> bool:
    with _lock, sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT 1 FROM banned_users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone() is not None


def _user_row(telegram_id: int) -> Optional[dict]:
    rows = sb.select(
        "users",
        f"telegram_id=eq.{telegram_id}"
        "&select=telegram_id,phone,first_name,username,role,created_at,cohort_id,"
        "cohort:cohorts!users_cohort_id_fkey(ends_at,is_active)&limit=1",
    )
    return rows[0] if rows else None


def is_user_approved(telegram_id: int) -> bool:
    """Ro'yxatdan o'tgan (phone bor), banlanmagan, patok faol va muddati o'tmagan."""
    if _is_banned(telegram_id):
        return False
    row = _user_row(telegram_id)
    if not row or not row.get("phone"):
        return False
    role = (row.get("role") or "").lower()
    if role in ADMIN_ROLES:
        return True
    exp, active = _cohort_expiry(row.get("cohort"))
    if row.get("cohort_id") is None:
        return True
    if not active:
        return False
    return exp == 0 or _now() < exp


def get_user_info(telegram_id: int) -> Optional[Dict]:
    row = _user_row(telegram_id)
    if not row:
        return None
    exp, _ = _cohort_expiry(row.get("cohort"))
    return {
        "telegram_id": int(row["telegram_id"]),
        "phone": _strip_plus(row.get("phone")),
        "first_name": row.get("first_name"),
        "username": row.get("username"),
        "joined_at": _date_to_ts(row.get("created_at")),
        "expires_at": exp,
    }


def free_phone(phone: str) -> List[int]:
    """Shu raqam bilan bog'langan akkount(lar)dan phone'ni uzadi (users.phone=null) —
    raqam allowed_contacts'da qoladi, boshqa akkount qayta ro'yxatdan o'ta oladi.
    Uzilgan telegram_id'lar ro'yxatini qaytaradi."""
    n = normalize_phone(phone)
    if not n:
        return []
    rows = sb.select(
        "users", f"phone=eq.{sb.quote(n)}&select=telegram_id"
    )
    ids = [int(r["telegram_id"]) for r in rows if r.get("telegram_id") is not None]
    if ids:
        try:
            sb.update(
                "users", f"phone=eq.{sb.quote(n)}",
                {"phone": None}, prefer="return=minimal",
            )
        except sb.SupabaseError as e:
            logger.error("free_phone xato: %s", e)
            return []
    # raqamni qayta ishlatish mumkin bo'lishi uchun used_at ni qaytaramiz
    try:
        sb.update(
            "allowed_contacts", f"phone=eq.{sb.quote(n)}",
            {"used_at": None}, prefer="return=minimal",
        )
    except sb.SupabaseError:
        pass
    return ids


def _registered_users() -> List[dict]:
    """phone va telegram_id biriktirilgan userlar (botga kirganlar, patok bilan)."""
    return sb.select(
        "users",
        "phone=not.is.null&telegram_id=not.is.null"
        "&select=telegram_id,phone,first_name,username,role,created_at,cohort_id,"
        "cohort:cohorts!users_cohort_id_fkey(ends_at,is_active)&order=created_at.desc",
    )


def list_approved_users(limit: int = 200) -> List[Tuple]:
    out: List[Tuple] = []
    for r in _registered_users()[:limit]:
        exp, _ = _cohort_expiry(r.get("cohort"))
        out.append((
            int(r["telegram_id"]),
            _strip_plus(r.get("phone")),
            r.get("first_name"),
            r.get("username"),
            _date_to_ts(r.get("created_at")),
            exp,
        ))
    return out


def list_allowed_phones(limit: int = 500) -> List[Tuple]:
    """(phone, expires_at, is_registered). is_registered=1 agar used_at to'ldirilgan bo'lsa."""
    rows = sb.select(
        "allowed_contacts",
        "select=phone,used_at,cohort:cohorts(ends_at)&order=invited_at.desc"
        f"&limit={int(limit)}",
    )
    out: List[Tuple] = []
    for r in rows:
        exp = _date_to_ts((r.get("cohort") or {}).get("ends_at"))
        out.append((_strip_plus(r.get("phone")), exp, 1 if r.get("used_at") else 0))
    return out


def list_all_user_ids() -> List[int]:
    """Ro'yxatdan o'tgan (phone bor) o'quvchilarning telegram_id'lari (e'lon uchun)."""
    rows = sb.select("users", "phone=not.is.null&telegram_id=not.is.null&select=telegram_id")
    return [int(r["telegram_id"]) for r in rows if r.get("telegram_id") is not None]


# ============================================================
# Muddat / kick — Supabase o'qish + lokal log
# ============================================================

def get_expired_users() -> List[Tuple]:
    """Patok muddati o'tgan ro'yxatdan o'tgan foydalanuvchilar."""
    now = _now()
    out: List[Tuple] = []
    for r in _registered_users():
        if (r.get("role") or "").lower() in ADMIN_ROLES or r.get("cohort_id") is None:
            continue
        exp, _active = _cohort_expiry(r.get("cohort"))
        if exp > 0 and exp < now:
            out.append((int(r["telegram_id"]), _strip_plus(r.get("phone")),
                        r.get("first_name"), exp))
    return out


def get_expiring_soon(days: int = 7) -> List[Tuple]:
    """Yaqin N kun ichida muddati tugaydiganlar (expires_at bo'yicha o'sib)."""
    now = _now()
    soon = now + days * 24 * 3600
    out: List[Tuple] = []
    for r in _registered_users():
        if (r.get("role") or "").lower() in ADMIN_ROLES or r.get("cohort_id") is None:
            continue
        exp, _active = _cohort_expiry(r.get("cohort"))
        if now < exp < soon:
            out.append((int(r["telegram_id"]), _strip_plus(r.get("phone")),
                        r.get("first_name"), exp))
    out.sort(key=lambda t: t[3])
    return out


def get_users_to_warn(days: int = 3) -> List[Tuple]:
    """Muddati `days` kun ichida tugaydigan, lekin hali ogohlantirilmaganlar."""
    now = _now()
    deadline = now + days * 24 * 3600
    with _lock, sqlite3.connect(DB_PATH) as c:
        warned = {r[0] for r in c.execute("SELECT telegram_id FROM expiry_warned").fetchall()}
    out: List[Tuple] = []
    for r in _registered_users():
        tid = int(r["telegram_id"])
        if tid in warned or (r.get("role") or "").lower() in ADMIN_ROLES:
            continue
        if r.get("cohort_id") is None:
            continue
        exp, _active = _cohort_expiry(r.get("cohort"))
        if now < exp < deadline:
            out.append((tid, _strip_plus(r.get("phone")), r.get("first_name"), exp))
    return out


def mark_warned(telegram_id: int) -> None:
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT OR IGNORE INTO expiry_warned (telegram_id, warned_at) VALUES (?, ?)",
            (telegram_id, _now()),
        )


def mark_user_kicked(telegram_id: int, reason: str = "expired") -> None:
    """Bot ruxsatini olib tashlaydi (users.phone=null) va lokal kick_log'ga yozadi."""
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT OR IGNORE INTO kick_log (telegram_id, kicked_at, reason) VALUES (?, ?, ?)",
            (telegram_id, _now(), reason),
        )
        c.execute("DELETE FROM expiry_warned WHERE telegram_id = ?", (telegram_id,))
    try:
        sb.update(
            "users", f"telegram_id=eq.{telegram_id}",
            {"phone": None}, prefer="return=minimal",
        )
    except sb.SupabaseError as e:
        logger.error("mark_user_kicked xato: %s", e)


# ============================================================
# Ban (lokal — faqat shu bot)
# ============================================================

def ban_user(telegram_id: int) -> None:
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute("INSERT OR REPLACE INTO banned_users (telegram_id) VALUES (?)", (telegram_id,))
    try:
        sb.update(
            "users", f"telegram_id=eq.{telegram_id}",
            {"phone": None}, prefer="return=minimal",
        )
    except sb.SupabaseError as e:
        logger.error("ban_user xato: %s", e)


def unban_user(telegram_id: int) -> None:
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute("DELETE FROM banned_users WHERE telegram_id = ?", (telegram_id,))


# ============================================================
# Eslatma sozlamalari (lokal)
# ============================================================

def disable_reminders(telegram_id: int) -> None:
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT OR IGNORE INTO reminder_optout (telegram_id, disabled_at) VALUES (?, ?)",
            (telegram_id, _now()),
        )


def enable_reminders(telegram_id: int) -> None:
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute("DELETE FROM reminder_optout WHERE telegram_id = ?", (telegram_id,))


def list_reminder_user_ids() -> List[int]:
    """Kunlik eslatma yoqilgan o'quvchilar (o'chirmaganlar)."""
    with _lock, sqlite3.connect(DB_PATH) as c:
        optout = {r[0] for r in c.execute("SELECT telegram_id FROM reminder_optout").fetchall()}
    return [tid for tid in list_all_user_ids() if tid not in optout]


def get_setting(key: str) -> Optional[str]:
    with _lock, sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None


def set_setting(key: str, value: str) -> None:
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)", (key, value))


# ============================================================
# Yordamchi adminlar (lokal) + asosiy admin tekshiruvi
# ============================================================

def add_assistant_admin(telegram_id: int) -> bool:
    with _lock, sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO assistant_admins (telegram_id) VALUES (?)", (telegram_id,)
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


def _has_admin_role(telegram_id: int) -> bool:
    rows = sb.select(
        "users", f"telegram_id=eq.{telegram_id}&select=role&limit=1"
    )
    return bool(rows) and (rows[0].get("role") or "").lower() in ADMIN_ROLES


def is_admin(telegram_id: int) -> bool:
    """Asosiy admin (ADMIN_USER_IDS), Supabase owner/admin roli yoki lokal yordamchi admin."""
    if telegram_id in ADMIN_USER_IDS:
        return True
    if is_assistant_admin(telegram_id):
        return True
    return _has_admin_role(telegram_id)


# ============================================================
# Statistika
# ============================================================

def stats() -> Dict[str, int]:
    now = _now()
    allowed = sb.select("allowed_contacts", "select=phone")
    registered = _registered_users()
    expired_pending = 0
    for r in registered:
        if (r.get("role") or "").lower() in ADMIN_ROLES or r.get("cohort_id") is None:
            continue
        exp, _active = _cohort_expiry(r.get("cohort"))
        if exp > 0 and exp < now:
            expired_pending += 1
    with _lock, sqlite3.connect(DB_PATH) as c:
        banned = c.execute("SELECT COUNT(*) FROM banned_users").fetchone()[0]
        kicks = c.execute("SELECT COUNT(*) FROM kick_log").fetchone()[0]
    return {
        "allowed_phones": len(allowed),
        "approved_users": len(registered),
        "banned_users": banned,
        "expired_pending_kick": expired_pending,
        "kick_log": kicks,
    }
