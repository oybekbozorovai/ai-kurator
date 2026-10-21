"""Eng oxirgi router: hech qaysi handler ushlamagan callback tugmalarga javob beradi.

Eski xabardagi tugma (holat allaqachon tozalangan) bosilsa, Telegram'da "soat" aylanib qolmasin."""
import logging

from aiogram import Router
from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)
router = Router(name="fallback")


@router.callback_query()
async def unhandled_callback(callback: CallbackQuery) -> None:
    logger.info("Eskirgan tugma: %r (user=%s)", callback.data, callback.from_user.id)
    try:
        await callback.answer("Bu tugma eskirgan. 🏠 Bosh menyu orqali qaytadan boshlang.", show_alert=False)
    except Exception:  # noqa: BLE001
        pass
