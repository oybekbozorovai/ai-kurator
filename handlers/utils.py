"""Yordamchi funksiyalar — xabarlarni Telegram cheklovlariga moslashtirish."""
import asyncio
import logging
from typing import List

from aiogram import Bot
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramRetryAfter,
)

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LENGTH = 4000  # 4096 - xavfsizlik chegarasi


async def safe_send(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    """Bitta xabarni ommaviy yuborish uchun xavfsiz jo'natadi.
    - 429 (flood/RetryAfter): ko'rsatilgan vaqt kutib, bir marta qayta uradi
    - bloklagan/o'chgan foydalanuvchi: jimgina o'tkazib yuboradi
    True — yetkazildi, False — yetkazilmadi."""
    for attempt in range(2):
        try:
            await bot.send_message(chat_id, text, **kwargs)
            return True
        except TelegramRetryAfter as e:
            wait = getattr(e, "retry_after", 3)
            logger.warning("Flood-limit: %s soniya kutaman (chat=%s)", wait, chat_id)
            await asyncio.sleep(wait + 1)
            continue  # bir marta qayta urinamiz
        except TelegramForbiddenError:
            return False  # foydalanuvchi botni bloklagan / o'chgan
        except Exception as e:  # noqa: BLE001
            logger.warning("Xabar yuborilmadi (chat=%s): %s", chat_id, e)
            return False
    return False


def split_for_telegram(text: str, limit: int = TELEGRAM_MAX_LENGTH) -> List[str]:
    """Uzun xabarni paragraflar bo'yicha bir nechta xabarga bo'ladi."""
    if len(text) <= limit:
        return [text]

    parts: List[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 <= limit:
            current = f"{current}\n\n{paragraph}" if current else paragraph
        else:
            if current:
                parts.append(current)
            if len(paragraph) <= limit:
                current = paragraph
            else:
                # juda uzun paragrafni so'z bo'yicha bo'lamiz
                while len(paragraph) > limit:
                    cut = paragraph.rfind(" ", 0, limit)
                    if cut < 0:
                        cut = limit
                    parts.append(paragraph[:cut])
                    paragraph = paragraph[cut:].lstrip()
                current = paragraph
    if current:
        parts.append(current)
    return parts
