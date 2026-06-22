"""Broadcast xabarlarini saqlash va o'chirish — message_id lar Supabase'da."""
import logging
from datetime import datetime, timezone

from services.supabase_db import delete, insert, select

logger = logging.getLogger(__name__)

TABLE = "broadcast_messages"


def save_message(telegram_id: int, message_id: int, broadcast_type: str) -> None:
    try:
        insert(TABLE, {
            "telegram_id": telegram_id,
            "message_id": message_id,
            "broadcast_type": broadcast_type,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.warning("broadcast_messages saqlashda xato: %s", e)


def get_messages(broadcast_type: str) -> list[dict]:
    """broadcast_type bo'yicha barcha saqlangan xabarlarni qaytaradi."""
    try:
        return select(TABLE, f"broadcast_type=eq.{broadcast_type}&select=telegram_id,message_id")
    except Exception as e:
        logger.warning("broadcast_messages o'qishda xato: %s", e)
        return []


def delete_messages(broadcast_type: str) -> int:
    """broadcast_type bo'yicha DB dan yozuvlarni o'chiradi. O'chirilgan sonini qaytaradi."""
    try:
        rows = delete(TABLE, f"broadcast_type=eq.{broadcast_type}")
        return len(rows) if isinstance(rows, list) else 0
    except Exception as e:
        logger.warning("broadcast_messages o'chirishda xato: %s", e)
        return 0


def list_broadcasts() -> list[dict]:
    """Barcha broadcast turlarini va sonini qaytaradi."""
    try:
        return select(TABLE, "select=broadcast_type,sent_at&order=sent_at.desc&limit=20")
    except Exception as e:
        logger.warning("broadcast_messages ro'yxatda xato: %s", e)
        return []
