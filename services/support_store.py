"""Texnik yordam ticketlari — Supabase (support_tickets jadvali)."""
import logging
from typing import Optional

from services import supabase_db as sb

logger = logging.getLogger(__name__)


def create_ticket(
    telegram_id: int,
    username: Optional[str],
    full_name: Optional[str],
    cohort_id: Optional[str],
    message: Optional[str],
    screenshot_file_id: Optional[str],
) -> Optional[dict]:
    """Yangi ticket yaratadi. Muvaffaqiyatda ticket dict, xatoda None."""
    row = {
        "telegram_id": telegram_id,
        "username": username,
        "full_name": full_name,
        "cohort_id": cohort_id,
        "message": message,
        "screenshot_file_id": screenshot_file_id,
        "status": "new",
    }
    try:
        res = sb.insert("support_tickets", row)
        return res[0] if res else None
    except sb.SupabaseError as e:
        logger.error("Ticket yaratib bo'lmadi (user=%s): %s", telegram_id, e)
        return None


def get_unnotified_resolved() -> list:
    """Hal qilingan, lekin o'quvchiga hali xabar berilmagan ticketlar."""
    return sb.select(
        "support_tickets",
        "status=eq.resolved&notified_student=eq.false"
        "&select=id,telegram_id,resolution_text",
    )


def mark_notified(ticket_id: int) -> None:
    """O'quvchiga 'hal bo'ldi' xabari yuborilganini belgilaydi."""
    try:
        sb.update(
            "support_tickets",
            f"id=eq.{ticket_id}",
            {"notified_student": True},
            prefer="return=minimal",
        )
    except sb.SupabaseError as e:
        logger.error("mark_notified xato (ticket=%s): %s", ticket_id, e)
