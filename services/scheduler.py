"""Muddati o'tgan talabalarni avtomat chiqarib yuborish.

Bot soatda bir marta tekshiradi va konfiguratsiya qilingan kanal/guruhlardan chiqarib tashlaydi."""
import asyncio
import logging
from datetime import datetime
from typing import List

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from config import KICK_CHAT_IDS
from services.auth import get_expired_users, mark_user_kicked

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 60 * 60  # har 1 soatda
NOTIFY_BEFORE_KICK_DAYS = 0  # 0 = darhol chiqar (kerak bo'lsa keyinroq qo'shaman)


EXPIRY_MESSAGE = (
    "📢 Salom!\n\n"
    "Sizning kursdagi {months} oylik ruxsat muddatingiz tugadi. "
    "Shu sababli sizni kurs guruhi va kanalidan chiqarildik.\n\n"
    "Agar kursni davom ettirmoqchi bo'lsangiz yoki yangi patokga yozilmoqchi bo'lsangiz, "
    "ustozga murojaat qiling."
)


async def kick_expired_loop(bot: Bot) -> None:
    """Cheksiz tsikl — har soatda muddati o'tganlarni chiqaradi."""
    while True:
        try:
            await kick_expired_once(bot)
        except Exception as e:
            logger.exception("Expiry tekshirish xatolik: %s", e)
        await asyncio.sleep(CHECK_INTERVAL)


async def kick_expired_once(bot: Bot) -> int:
    """Muddati o'tganlarni darhol chiqaradi. Chiqarilgan talabalar sonini qaytaradi."""
    expired = get_expired_users()
    if not expired:
        return 0

    if not KICK_CHAT_IDS:
        logger.warning(
            "%d ta talaba muddati o'tgan, lekin KICK_CHAT_IDS sozlanmagan — chiqarib bo'lmaydi",
            len(expired),
        )
        return 0

    logger.info("%d ta talaba muddati o'tgan, chiqarish boshlanadi", len(expired))
    kicked_count = 0

    for telegram_id, phone, first_name, expires_at in expired:
        success = await _kick_from_all_chats(bot, telegram_id, first_name)
        if success:
            await _notify_user(bot, telegram_id)
            mark_user_kicked(telegram_id, reason="expired")
            kicked_count += 1
        else:
            # Chiqarib bo'lmagan bo'lsa ham DB'dan o'chirib qo'yamiz
            mark_user_kicked(telegram_id, reason="expired_kick_failed")

    return kicked_count


async def _kick_from_all_chats(bot: Bot, user_id: int, first_name: str) -> bool:
    """Barcha konfiguratsiya qilingan chatlardan chiqaradi. Hech bo'lmasa bittada muvaffaqiyatli bo'lsa True."""
    success_anywhere = False
    for chat_id in KICK_CHAT_IDS:
        try:
            await bot.ban_chat_member(chat_id, user_id)
            await asyncio.sleep(1)
            try:
                await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
            except Exception:
                pass
            success_anywhere = True
            logger.info("Chiqarildi: %s (id=%s) chat=%s", first_name or "?", user_id, chat_id)
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            # Talaba allaqachon chatdan chiqib ketgan yoki bot admin emas
            logger.info("Chiqarib bo'lmadi (chat=%s, user=%s): %s", chat_id, user_id, e)
        except Exception as e:
            logger.warning("Kutilmagan xato (chat=%s, user=%s): %s", chat_id, user_id, e)
    return success_anywhere


async def _notify_user(bot: Bot, telegram_id: int) -> None:
    """Talabaga shaxsiy chatda muddat tugagani haqida xabar yuboradi."""
    from config import COURSE_ACCESS_MONTHS
    try:
        await bot.send_message(
            telegram_id,
            EXPIRY_MESSAGE.format(months=COURSE_ACCESS_MONTHS),
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        # Talaba botni bloklagan bo'lishi mumkin — bu normal
        pass
    except Exception as e:
        logger.warning("Notification yuborib bo'lmadi (user=%s): %s", telegram_id, e)
