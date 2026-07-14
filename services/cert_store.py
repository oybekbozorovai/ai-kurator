"""Sertifikat ombori — Supabase (asosiy) + lokal cert_prompted (SQLite, botstate.db)."""
import logging
import sqlite3
from typing import List, Optional, Tuple

from services import supabase_db as sb
from services.auth import (
    ADMIN_ROLES,
    DB_PATH,
    _cohort_expiry,
    _date_to_ts,
    _lock,
    _now,
    _registered_users,
)

logger = logging.getLogger(__name__)


def _init_cert_db() -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS cert_prompted (
                telegram_id INTEGER NOT NULL,
                cohort_id   TEXT    NOT NULL,
                prompted_at INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, cohort_id)
            )
        """)


_init_cert_db()


# ============================================================
# Lokal cert_prompted (sqlite)
# ============================================================

def _was_cert_prompted(telegram_id: int, cohort_id: str) -> bool:
    with _lock, sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT 1 FROM cert_prompted WHERE telegram_id=? AND cohort_id=?",
            (telegram_id, cohort_id),
        ).fetchone() is not None


def mark_cert_prompted(telegram_id: int, cohort_id: str) -> None:
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT OR IGNORE INTO cert_prompted (telegram_id, cohort_id, prompted_at)"
            " VALUES (?,?,?)",
            (telegram_id, cohort_id, _now()),
        )


# ============================================================
# Sertifikat CRUD (Supabase)
# ============================================================

def has_certificate(telegram_id: int, cohort_id: str) -> bool:
    rows = sb.select(
        "certificates",
        f"telegram_id=eq.{telegram_id}"
        f"&cohort_id=eq.{sb.quote(cohort_id)}"
        f"&select=id&limit=1",
    )
    return bool(rows)


def get_certificate(telegram_id: int, cohort_id: str) -> Optional[dict]:
    rows = sb.select(
        "certificates",
        f"telegram_id=eq.{telegram_id}"
        f"&cohort_id=eq.{sb.quote(cohort_id)}"
        f"&limit=1",
    )
    return rows[0] if rows else None


def save_certificate(
    cert_id: str,
    telegram_id: int,
    full_name: str,
    cohort_id: str,
    course: str = "YouTube AI",
) -> None:
    try:
        sb.insert(
            "certificates",
            {
                "id": cert_id,
                "telegram_id": telegram_id,
                "full_name": full_name,
                "cohort_id": cohort_id,
                "course": course,
            },
            prefer="return=minimal",
        )
    except sb.SupabaseError as e:
        err = str(e)
        if "23505" in err or "duplicate" in err.lower() or "unique" in err.lower():
            logger.info(
                "Sertifikat allaqachon mavjud (UNIQUE): tg=%s cohort=%s",
                telegram_id, cohort_id,
            )
        else:
            raise


# ============================================================
# Sertifikat olishga haqli foydalanuvchilar
# ============================================================

def cert_window_open(cohort, days: int = 10) -> bool:
    """Sertifikat tugmasi ochilganmi — patok tugashiga `days` kun yoki undan kam qolgan.
    Muddat belgilanmagan patoklar uchun False."""
    exp, _active = _cohort_expiry(cohort)
    if exp == 0:
        return False
    # Tugashga `days` kun yoki kamroq qolgandan boshlab (tugagach kirish yopiladi)
    return _now() >= exp - days * 24 * 3600


def is_grandfathered(row: dict, before_iso: str) -> bool:
    """Foydalanuvchi `before_iso` sanasidan OLDIN botga qo'shilgan bo'lsa — eski o'quvchi,
    sertifikatni darhol oladi (patok/muddatdan qat'i nazar)."""
    if not before_iso or not row:
        return False
    cutoff = _date_to_ts(before_iso)
    created = _date_to_ts(row.get("created_at"))
    return cutoff > 0 and created > 0 and created < cutoff


def get_users_for_certificate(days: int = 10) -> List[Tuple]:
    """Muddat yaqinlashgan (days kun), sertifikat olmagan, hali taklif qilinmagan.

    Returns: [(telegram_id, phone, first_name, exp, cohort_id), ...]
    """
    now = _now()
    deadline = now + days * 24 * 3600
    out: List[Tuple] = []
    for r in _registered_users():
        tid = int(r["telegram_id"])
        if (r.get("role") or "").lower() in ADMIN_ROLES:
            continue
        cohort_id = r.get("cohort_id")
        if cohort_id is None:
            continue
        exp, _active = _cohort_expiry(r.get("cohort"))
        if exp == 0 or not (now < exp < deadline):
            continue
        cohort_str = str(cohort_id)
        if _was_cert_prompted(tid, cohort_str):
            continue
        if has_certificate(tid, cohort_str):
            continue
        out.append((tid, r.get("phone"), r.get("first_name"), exp, cohort_id))
    return out
