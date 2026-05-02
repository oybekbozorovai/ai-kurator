import logging
from typing import Tuple

from aiogram import Router, F
from aiogram.enums import ChatType, ChatAction
from aiogram.types import Message

from handlers.utils import split_for_telegram
from services.gemini import ask_tutor
from services.limiter import cache_answer, check_rate_limit, get_cached_answer
from services.rag import format_context, retrieve

logger = logging.getLogger(__name__)
router = Router(name="group")
router.message.filter(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))


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
        return True, cleaned

    for cmd in ("/savol", "/ask"):
        if text.startswith(cmd):
            return True, text[len(cmd):].strip()

    return False, ""


@router.message(F.text | F.caption)
async def handle_group_message(message: Message) -> None:
    me = await message.bot.get_me()
    addressed, question = _is_addressed_to_bot(message, me.username or "")
    if not addressed or not question:
        return

    user_id = message.from_user.id
    allowed, _ = check_rate_limit(user_id)
    if not allowed:
        await message.reply("⏳ Limitni to'ldirgansiz, biroz keyin urinib ko'ring.")
        return

    cached = get_cached_answer(question)
    if cached:
        parts = split_for_telegram(cached)
        await message.reply(parts[0])
        for p in parts[1:]:
            await message.answer(p)
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    hits = await retrieve(question)
    context = format_context(hits)
    answer = await ask_tutor(question, context)

    if not answer.startswith("⚠️"):
        cache_answer(question, answer)

    parts = split_for_telegram(answer)
    await message.reply(parts[0])
    for p in parts[1:]:
        await message.answer(p)
