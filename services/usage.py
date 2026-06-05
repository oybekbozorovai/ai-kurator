"""AI iste'mol (token/xarajat) hisobi — Supabase `usage_events` jadvaliga yozadi.

Poydevor: Feature 2 (bot ichida admin /usage) va Feature 3 (A.Y.P.I dashboard).

MUHIM: `record()` HECH QACHON xato ko'tarmaydi — agar jadval hali yaratilmagan
bo'lsa yoki tarmoq uzilsa, faqat debug log yoziladi, foydalanuvchi oqimi buzilmaydi.
Jadval: migrations/usage_events.sql (Supabase SQL editor'da bir marta ishga tushiriladi).
"""

import logging

from services import supabase_db as sb

logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# Narx modeli — TAXMINIY (1M token uchun USD). Kerak bo'lsa moslang.
# Gemini 2.5 Flash: input ~$0.30, output ~$2.50 / 1M token.
# ------------------------------------------------------------
_TEXT_PRICING = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-1.5-flash": (0.075, 0.30),
}
_DEFAULT_TEXT_PRICE = (0.30, 2.50)

# Rasm narxi (bitta rasm uchun USD) — Replicate flux-schnell ~ $0.003.
_IMAGE_PRICING = {
    "flux-schnell": 0.003,
    "flux-dev": 0.025,
    "flux-redux-schnell": 0.003,
}
_DEFAULT_IMAGE_PRICE = 0.003


def _short_model(model: str) -> str:
    """'black-forest-labs/flux-schnell' -> 'flux-schnell'."""
    return (model or "").split("/")[-1].strip()


def estimate_text_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    inp, out = _TEXT_PRICING.get(_short_model(model), _DEFAULT_TEXT_PRICE)
    return round(prompt_tokens / 1_000_000 * inp + completion_tokens / 1_000_000 * out, 6)


def estimate_image_cost(model: str, count: int = 1) -> float:
    price = _IMAGE_PRICING.get(_short_model(model), _DEFAULT_IMAGE_PRICE)
    return round(price * count, 6)


def record(telegram_id, service: str, kind: str = "text", model: str = "",
           prompt_tokens: int = 0, completion_tokens: int = 0,
           cost_usd=None) -> None:
    """Bitta iste'mol yozuvini Supabase'ga yozadi (best-effort, xato ko'tarmaydi)."""
    total = (prompt_tokens or 0) + (completion_tokens or 0)
    if cost_usd is None:
        if kind == "image":
            cost_usd = estimate_image_cost(model)
        else:
            cost_usd = estimate_text_cost(model, prompt_tokens or 0, completion_tokens or 0)
    row = {
        "telegram_id": telegram_id,
        "service": service,
        "kind": kind,
        "model": _short_model(model) or None,
        "prompt_tokens": prompt_tokens or 0,
        "completion_tokens": completion_tokens or 0,
        "total_tokens": total,
        "cost_usd": cost_usd,
    }
    try:
        sb.request("POST", "usage_events", body=row, prefer="return=minimal")
    except Exception as e:  # noqa: BLE001 — hisob foydalanuvchi oqimini buzmasligi shart
        logger.debug("usage.record o'tkazib yuborildi (%s): %s", service, e)


def record_from_gemini(response, telegram_id, service: str, model: str) -> None:
    """Gemini javobining usage_metadata'sidan token sonini olib yozadi."""
    pt = ct = 0
    try:
        um = getattr(response, "usage_metadata", None)
        if um is not None:
            pt = int(getattr(um, "prompt_token_count", 0) or 0)
            ct = int(getattr(um, "candidates_token_count", 0) or 0)
    except Exception:  # noqa: BLE001
        pass
    record(telegram_id, service, kind="text", model=model,
           prompt_tokens=pt, completion_tokens=ct)


# ------------------------------------------------------------
# O'qish — RPC agregatsiya (admin /usage va dashboard uchun).
# Jadval/RPC bo'lmasa bo'sh natija qaytaradi (xato ko'tarmaydi).
# ------------------------------------------------------------

def _rpc(fn: str, params: dict):
    try:
        return sb.request("POST", f"rpc/{fn}", body=params) or []
    except Exception as e:  # noqa: BLE001
        logger.warning("usage RPC %s xato: %s", fn, e)
        return []


def summary(days: int = 30) -> dict:
    rows = _rpc("usage_summary", {"days": days})
    if rows:
        return rows[0]
    return {"total_events": 0, "active_users": 0, "total_tokens": 0, "total_cost": 0}


def daily(days: int = 14) -> list:
    return _rpc("usage_daily", {"days": days})


def top_users(days: int = 30, lim: int = 15) -> list:
    return _rpc("usage_top_users", {"days": days, "lim": lim})


def by_service(days: int = 30) -> list:
    return _rpc("usage_by_service", {"days": days})


def user_detail(telegram_id: int, days: int = 90) -> list:
    return _rpc("usage_user_detail", {"p_telegram_id": telegram_id, "days": days})
