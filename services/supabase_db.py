"""Supabase REST mijozi — yagona auth/roster manbai (A.Y.P.I Platforma bilan umumiy baza).

Tashqi paketsiz (faqat stdlib urllib). Sinxron — auth.py ichidagi qulf bilan ishlaydi.
Xato bo'lsa SupabaseError ko'tariladi; o'qish yordamchilarida default qiymat qaytariladi
va xato log'ga yoziladi (auth gate'lari xato holatida "deny" tomon ishlaydi)."""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL

logger = logging.getLogger(__name__)

TIMEOUT = 15


class SupabaseError(Exception):
    pass


def _headers(prefer: Optional[str] = None) -> dict:
    h = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def quote(value) -> str:
    """PostgREST filtr qiymatini xavfsiz kodlash (masalan +998... → %2B998...)."""
    return urllib.parse.quote(str(value), safe="")


def request(method: str, path: str, query: str = "", body=None, prefer: Optional[str] = None):
    """Xom REST so'rovi. `path` — jadval nomi, `query` — tayyor PostgREST query string."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if query:
        url += "?" + query
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(prefer), method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else []
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")
        raise SupabaseError(f"HTTP {e.code} {method} {path}: {detail}") from e
    except Exception as e:  # noqa: BLE001 — tarmoq/timeout
        raise SupabaseError(f"{method} {path}: {e}") from e


def select(table: str, query: str = "") -> list:
    """SELECT — xato bo'lsa bo'sh ro'yxat qaytaradi (gate'lar deny tomon ishlaydi)."""
    try:
        return request("GET", table, query)
    except SupabaseError as e:
        logger.error("Supabase select xato: %s", e)
        return []


def insert(table: str, row: dict, prefer: str = "return=representation") -> list:
    return request("POST", table, body=row, prefer=prefer)


def update(table: str, query: str, patch: dict, prefer: str = "return=representation") -> list:
    return request("PATCH", table, query=query, body=patch, prefer=prefer)


def delete(table: str, query: str, prefer: str = "return=representation") -> list:
    return request("DELETE", table, query=query, prefer=prefer)
