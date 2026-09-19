"""Sertifikat ombori — Supabase (asosiy) + lokal holat (SQLite, botstate.db).

Qoidalar:
- Sertifikat o'quvchi botga (raqami bilan) qo'shilgandan CERT_OPEN_AFTER_DAYS kun
  o'tgach ochiladi (users.created_at bo'yicha).
- Bitta o'quvchi sertifikatni CERT_MAX_ISSUES martagacha oladi (ismdagi xatoni
  to'g'rilash uchun). Har qayta olishda Supabase'dagi yozuv YANGILANADI (yangi ID,
  yangi ism) — eski ID bekor bo'ladi. Urinishlar soni lokal SQLite'da saqlanadi.
"""
import logging
import sqlite3
from typing import List, Optional, Tuple

from services import supabase_db as sb
from services.auth import (
    ADMIN_ROLES,
    DB_PATH,
    _cohort_expiry,
    _date_to_ts,
    _is_banned,
    _lock,
    _now,
    _registered_users,
)

logger = logging.getLogger(__name__)

DAY = 24 * 3600
# Avtomatik eslatma yuborilmaydigan rollar (tugma orqali baribir ola oladilar)
PROMPT_SKIP_ROLES = ADMIN_ROLES | {"curator"}


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
        c.execute("""
            CREATE TABLE IF NOT EXISTS cert_issued (
                telegram_id  INTEGER NOT NULL,
                cohort_id    TEXT    NOT NULL,
                issues       INTEGER NOT NULL DEFAULT 0,
                last_cert_id TEXT,
                updated_at   INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, cohort_id)
            )
        """)


_init_cert_db()


# ============================================================
# Lokal holat (sqlite): taklif yuborilgani + urinishlar soni
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


def _local_issue_count(telegram_id: int, cohort_id: str) -> int:
    with _lock, sqlite3.connect(DB_PATH) as c:
        row = c.execute(
            "SELECT issues FROM cert_issued WHERE telegram_id=? AND cohort_id=?",
            (telegram_id, cohort_id),
        ).fetchone()
    return int(row[0]) if row else 0


def cert_issue_count(telegram_id: int, cohort_id: str) -> int:
    """O'quvchi shu patokda sertifikatni necha marta olgan.

    Lokal hisoblagich yo'q, lekin Supabase'da yozuv bor bo'lsa (bu qoidadan oldin
    berilgan sertifikatlar) — 1 deb hisoblanadi."""
    local = _local_issue_count(telegram_id, cohort_id)
    if local > 0:
        return local
    return 1 if has_certificate(telegram_id, cohort_id) else 0


def mark_cert_issued(telegram_id: int, cohort_id: str, cert_id: str) -> int:
    """Urinishlar sonini 1 ga oshiradi va yangi sonni qaytaradi."""
    current = cert_issue_count(telegram_id, cohort_id)
    new_count = current + 1
    with _lock, sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT INTO cert_issued (telegram_id, cohort_id, issues, last_cert_id, updated_at)"
            " VALUES (?,?,?,?,?)"
            " ON CONFLICT(telegram_id, cohort_id) DO UPDATE SET"
            " issues=excluded.issues, last_cert_id=excluded.last_cert_id,"
            " updated_at=excluded.updated_at",
            (telegram_id, cohort_id, new_count, cert_id, _now()),
        )
    return new_count


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
) -> Optional[str]:
    """Sertifikatni Supabase'ga yozadi.

    O'quvchida shu patok uchun sertifikat allaqachon bo'lsa (UNIQUE telegram_id+cohort_id),
    eski yozuv yangi ID va ism bilan ALMASHTIRILADI — eski ID tekshiruv saytida
    bekor bo'ladi. Almashtirilgan eski ID qaytariladi (yo'q bo'lsa None)."""
    row = {
        "id": cert_id,
        "telegram_id": telegram_id,
        "full_name": full_name,
        "cohort_id": cohort_id,
        "course": course,
    }
    try:
        sb.insert("certificates", row, prefer="return=minimal")
        return None
    except sb.SupabaseError as e:
        err = str(e)
        if not ("23505" in err or "duplicate" in err.lower() or "unique" in err.lower()):
            raise

    existing = get_certificate(telegram_id, cohort_id)
    old_id = existing.get("id") if existing else None
    sb.update(
        "certificates",
        f"telegram_id=eq.{telegram_id}&cohort_id=eq.{sb.quote(cohort_id)}",
        {
            "id": cert_id,
            "full_name": full_name,
            "course": course,
            "issued_at": _iso_now(),
        },
        prefer="return=minimal",
    )
    logger.info(
        "Sertifikat qayta berildi (almashtirildi): tg=%s cohort=%s %s -> %s",
        telegram_id, cohort_id, old_id, cert_id,
    )
    return old_id


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Sertifikat oynasi: botga qo'shilgandan `days` kun o'tgach
# ============================================================

def cert_open_at(row: Optional[dict], days: int) -> int:
    """Sertifikat ochiladigan vaqt (unix ts).

    created_at yo'q/o'qilmasa — XAVFSIZ tomonga: hozir qo'shilgan deb hisoblanadi
    (ya'ni hali ochilmagan). Aks holda barcha yangi o'quvchilarga sertifikat ochilib ketardi."""
    created = _date_to_ts((row or {}).get("created_at"))
    if created <= 0:
        logger.warning("created_at o'qilmadi (tg=%s) — sertifikat oynasi yopiq deb olinadi",
                       (row or {}).get("telegram_id"))
        created = _now()
    return created + days * DAY


def cert_window_open(row: Optional[dict], days: int) -> bool:
    """O'quvchi botga qo'shilganidan `days` kun o'tganmi."""
    return _now() >= cert_open_at(row, days)


def cert_days_left(row: Optional[dict], days: int) -> int:
    """Sertifikat ochilishiga necha kun qoldi (0 = ochiq)."""
    remaining = cert_open_at(row, days) - _now()
    if remaining <= 0:
        return 0
    return max(1, -(-remaining // DAY))  # yuqoriga yaxlitlash


# ============================================================
# Eslatma yuboriladigan o'quvchilar (scheduler)
# ============================================================

def _cert_holders() -> set:
    """Supabase'da sertifikati bor (telegram_id, cohort_id) juftliklari — bitta so'rovda."""
    rows = sb.select("certificates", "select=telegram_id,cohort_id")
    return {(int(r["telegram_id"]), str(r["cohort_id"])) for r in rows}


def get_users_for_certificate(days: int) -> List[Tuple]:
    """Botga qo'shilganiga `days` kun to'lgan, hali sertifikat olmagan va eslatma
    yuborilmagan FAOL o'quvchilar (patoki tugamagan, banlanmagan).

    Returns: [(telegram_id, phone, first_name, cohort_id), ...]
    """
    now = _now()
    holders = None  # kerak bo'lgandagina yuklanadi
    out: List[Tuple] = []
    for r in _registered_users():
        tid = int(r["telegram_id"])
        if (r.get("role") or "").lower() in PROMPT_SKIP_ROLES:
            continue
        cohort_id = r.get("cohort_id")
        if cohort_id is None:
            continue
        if not cert_window_open(r, days):
            continue
        exp, active = _cohort_expiry(r.get("cohort"))
        if not active or (exp and now >= exp):
            continue  # patoki tugagan — botga kira olmaydi, eslatma bema'ni
        cohort_str = str(cohort_id)
        if _was_cert_prompted(tid, cohort_str):
            continue
        if _is_banned(tid):
            continue
        if holders is None:
            holders = _cert_holders()
        if (tid, cohort_str) in holders or _local_issue_count(tid, cohort_str) > 0:
            # Sertifikati bor — eslatma kerak emas; keyingi soatlarda qayta tekshirmaslik uchun
            mark_cert_prompted(tid, cohort_str)
            continue
        out.append((tid, r.get("phone"), r.get("first_name"), cohort_id))
    return out
