import asyncio
import logging
from typing import Tuple

from aiogram import Router, F
from aiogram.enums import ChatType, ChatAction
from aiogram.types import Message

from config import COURSE_GROUP_ID, KICK_CHAT_IDS
from handlers.utils import split_for_telegram
from services.auth import is_admin, is_user_approved
from services.gemini import ask_tutor
from services.limiter import cache_answer, check_rate_limit, get_cached_answer
from services.rag import format_context, retrieve

logger = logging.getLogger(__name__)
router = Router(name="group")
router.message.filter(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))

# Bot faqat shu guruh(lar)da javob beradi. Bo'sh bo'lsa — istalgan guruhda,
# lekin baribir faqat tasdiqlangan o'quvchilarga (quyidagi auth tekshiruvi).
_ALLOWED_GROUP_IDS = set(KICK_CHAT_IDS) | ({COURSE_GROUP_ID} if COURSE_GROUP_ID else set())


async def _user_allowed(user_id: int) -> bool:
    """Tasdiqlangan o'quvchi yoki admin (bloklovchi Supabase chaqiruvi thread'da)."""
    return await asyncio.to_thread(lambda: is_admin(user_id) or is_user_approved(user_id))


def _is_addressed_to_bot(message: Message, bot_username: str) -> Tuple[bool, str]:
    """Guruhda botga murojaat qilinganligini tekshiradi."""
    text = message.text or message.caption or ""
    if not text:
        return False, ""

    if message.reply_to_message and message.reply_to_message.from_user \
            and message.reply_to_message.from_user.username == bot_username:
        return True, text.strip()

    mention = f"@{bot_username}"
    if mention.lower() in text.lower():
        cleaned = text.replace(mention, "").replace(mention.lower(), "").strip()
        return True, _strip_command(cleaned)

    for cmd in ("/savol", "/ask"):
        if text.startswith(cmd):
            return True, text[len(cmd):].strip()

    return False, ""


def _strip_command(text: str) -> str:
    """Matn boshidagi /savol yoki /ask ni olib tashlaydi —
    savol vektoriga buyruq so'zi qo'shilib ketmasin."""
    t = text.strip()
    for cmd in ("/savol", "/ask"):
        if t.lower().startswith(cmd):
            return t[len(cmd):].lstrip("@ ").strip()
    return t


@router.message(F.text | F.caption)
async def handle_group_message(message: Message) -> None:
    # bot.me() — keshlanadi (get_me har xabarda API chaqirmaydi)
    me = await message.bot.me()
    addressed, question = _is_addressed_to_bot(message, me.username or "")
    if not addressed or not question:
        return

    # Faqat kurs guruh(lar)ida ishlaydi (agar sozlangan bo'lsa)
    if _ALLOWED_GROUP_IDS and message.chat.id not in _ALLOWED_GROUP_IDS:
        return

    user_id = message.from_user.id

    # Faqat tasdiqlangan o'quvchi yoki admin — bepul API suiiste'molini oldini oladi
    if not await _user_allowed(user_id):
        await message.reply(
            "⛔ Bu AI yordamchi faqat kurs o'quvchilari uchun.\n"
            "Botga shaxsiy yozib /start bosing va ro'yxatdan o'ting."
        )
        return

    # Kesh urishi limitni yemasligi uchun avval keshni tekshiramiz
    cached = get_cached_answer(question)
    if cached:
        parts = split_for_telegram(cached)
        await message.reply(parts[0])
        for p in parts[1:]:
            await message.answer(p)
        return

    allowed, _ = check_rate_limit(user_id)
    if not allowed:
        await message.reply("⏳ Limitni to'ldirgansiz, biroz keyin urinib ko'ring.")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    try:
        hits = await retrieve(question)
        context = format_context(hits)
        answer = await ask_tutor(question, context)
    except Exception:
        logger.exception("Guruh savol-javob xatosi")
        await message.reply(
            "😔 Kechirasiz, hozir javob berib bo'lmadi. Biroz kuting va qayta urinib ko'ring."
        )
        return

    if not answer.startswith("⚠️"):
        cache_answer(question, answer)

    parts = split_for_telegram(answer)
    await message.reply(parts[0])
    for p in parts[1:]:
        await message.answer(p)
