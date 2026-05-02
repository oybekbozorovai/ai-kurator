import logging

from aiogram import Router, F
from aiogram.enums import ChatType, ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from services.gemini import ask_tutor
from services.limiter import cache_answer, check_rate_limit, get_cached_answer
from services.rag import format_context, retrieve

logger = logging.getLogger(__name__)
router = Router(name="private")
router.message.filter(F.chat.type == ChatType.PRIVATE)


WELCOME = (
    "👋 Salom! Men — kursingizning AI kuratorisiz.\n\n"
    "Men sizga quyidagilarda yordam beraman:\n"
    "• Kurs bo'yicha savollarga javob berish — shunchaki savolingizni yozing\n"
    "• Uy vazifalarini tekshirish — /uy_vazifa buyrug'idan foydalaning\n\n"
    "Boshlash uchun savolingizni yozing yoki /yordam ni bosing."
)

HELP = (
    "📚 Buyruqlar:\n"
    "/start — botni ishga tushirish\n"
    "/yordam — bu xabar\n"
    "/uy_vazifa — uy vazifani tekshirish (matn yoki rasm yuboring)\n\n"
    "Kurs bo'yicha savollar uchun shunchaki xabar yozing."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(WELCOME)


@router.message(Command("yordam", "help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(F.text & ~F.text.startswith("/"))
async def handle_question(message: Message) -> None:
    if not message.text:
        return
    user_id = message.from_user.id

    allowed, remaining = check_rate_limit(user_id)
    if not allowed:
        await message.answer(
            "⏳ Soatiga ruxsat etilgan savollar limitini to'ldirgansiz. "
            "Iltimos, biroz keyin urinib ko'ring."
        )
        return

    cached = get_cached_answer(message.text)
    if cached:
        logger.info("Keshdan javob: user=%s", user_id)
        await message.answer(cached + "\n\n_♻️ keshlangan javob_")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    hits = await retrieve(message.text)
    context = format_context(hits)
    answer = await ask_tutor(message.text, context)

    if not answer.startswith("⚠️"):
        cache_answer(message.text, answer)

    await message.answer(answer)
