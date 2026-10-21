"""Adminlarga texnik ogohlantirish (kvota tugadi va h.k.) — takrorlanmasligi uchun cooldown bilan.

bot.py ishga tushganda set_bot(bot) chaqiradi; xizmatlar alert_admins(...) ni chaqiradi.
Bot hali o'rnatilmagan bo'lsa (masalan skriptdan) — jimgina o'tkazib yuboradi."""
import logging
import time
from typing import Dict, Optional

from config import ADMIN_USER_IDS

logger = logging.getLogger(__name__)

_bot = None
_last_sent: Dict[str, float] = {}


def set_bot(bot) -> None:
    global _bot
    _bot = bot


async def alert_admins(key: str, text: str, cooldown: int = 3600) -> bool:
    """`key` bo'yicha `cooldown` soniyada ko'pi bilan bir marta barcha adminlarga yuboradi."""
    now = time.time()
    if _bot is None or now - _last_sent.get(key, 0.0) < cooldown:
        return False
    _last_sent[key] = now
    from handlers.utils import safe_send  # lokal import — handlers ↔ services aylanma importdan saqlanish
    sent = False
    for admin_id in sorted(ADMIN_USER_IDS):
        try:
            sent = await safe_send(_bot, admin_id, text) or sent
        except Exception as e:  # noqa: BLE001
            logger.warning("Admin ogohlantirishi yuborilmadi (%s): %s", admin_id, e)
    return sent
