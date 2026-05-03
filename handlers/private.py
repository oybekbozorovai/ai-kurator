import logging

from aiogram import Router, F
from aiogram.enums import ChatType, ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from config import ADMIN_USER_IDS
from handlers.utils import split_for_telegram
from services.auth import approve_user, is_phone_allowed, is_user_approved
from services.gemini import ask_tutor
from services.limiter import cache_answer, check_rate_limit, get_cached_answer
from services.rag import format_context, retrieve

logger = logging.getLogger(__name__)
router = Router(name="private")
router.message.filter(F.chat.type == ChatType.PRIVATE)


class AuthStates(StatesGroup):
    waiting_for_phone = State()


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
    "/uy_vazifa — uy vazifani tekshirish\n\n"
    "Kurs bo'yicha savollar uchun shunchaki xabar yozing."
)

REGISTRATION_TEXT = (
    "👋 Salom!\n\n"
    "Bu bot — onlayn kursning rasmiy AI kuratori. Faqat kurs talabalari foydalana oladi.\n\n"
    "📱 Telefon raqamingizni jo'nating, biz sizning kursdaligingizni tekshiramiz.\n\n"
    "Pastdagi tugmani bosing:"
)

NOT_ALLOWED_TEXT = (
    "❌ Sizning telefon raqamingiz kurs ro'yxatida topilmadi.\n\n"
    "Agar siz haqiqatdan ham kursni xarid qilgan bo'lsangiz, ustozga murojaat qiling. "
    "Iltimos, telefon raqamingizni va to'liq ismingizni bering."
)


def _phone_keyboard() -> ReplyKeyboardMarkup:
    button = KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)
    return ReplyKeyboardMarkup(
        keyboard=[[button]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def _request_phone(message: Message, state: FSMContext) -> None:
    await state.set_state(AuthStates.waiting_for_phone)
    await message.answer(REGISTRATION_TEXT, reply_markup=_phone_keyboard())


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id

    if user_id in ADMIN_USER_IDS or is_user_approved(user_id):
        await state.clear()
        await message.answer(WELCOME, reply_markup=ReplyKeyboardRemove())
        return

    await _request_phone(message, state)


@router.message(AuthStates.waiting_for_phone, F.contact)
async def receive_contact(message: Message, state: FSMContext) -> None:
    contact = message.contact
    if not contact:
        return
    if contact.user_id != message.from_user.id:
        await message.answer("⚠️ Iltimos, faqat o'zingizning kontaktingizni jo'nating.")
        return

    phone = contact.phone_number
    if is_phone_allowed(phone):
        approve_user(
            telegram_id=message.from_user.id,
            phone=phone,
            first_name=message.from_user.first_name or "",
            username=message.from_user.username or "",
        )
        await state.clear()
        await message.answer(
            f"✅ Tasdiqlandi! Xush kelibsiz, {message.from_user.first_name or 'talaba'}.\n\n" + WELCOME,
            reply_markup=ReplyKeyboardRemove(),
        )
        logger.info("Yangi talaba tasdiqlandi: %s (id=%s)", phone, message.from_user.id)
    else:
        await state.clear()
        await message.answer(NOT_ALLOWED_TEXT, reply_markup=ReplyKeyboardRemove())
        logger.info("Ruxsat etilmagan raqam: %s (id=%s)", phone, message.from_user.id)


@router.message(AuthStates.waiting_for_phone)
async def waiting_for_phone_other(message: Message) -> None:
    await message.answer(
        "⚠️ Iltimos, pastdagi tugma orqali telefon raqamingizni jo'nating.",
        reply_markup=_phone_keyboard(),
    )


@router.message(Command("yordam", "help"))
async def cmd_help(message: Message) -> None:
    if not is_user_approved(message.from_user.id) and message.from_user.id not in ADMIN_USER_IDS:
        await message.answer("⛔ Avval /start bosib ro'yxatdan o'ting.")
        return
    await message.answer(HELP)


@router.message(F.text & ~F.text.startswith("/"))
async def handle_question(message: Message) -> None:
    if not message.text:
        return

    user_id = message.from_user.id

    # Auth gate
    if user_id not in ADMIN_USER_IDS and not is_user_approved(user_id):
        await message.answer(
            "⛔ Botdan foydalanish uchun /start bosib ro'yxatdan o'ting."
        )
        return

    allowed, _ = check_rate_limit(user_id)
    if not allowed:
        await message.answer(
            "⏳ Soatiga ruxsat etilgan savollar limitini to'ldirgansiz. "
            "Iltimos, biroz keyin urinib ko'ring."
        )
        return

    cached = get_cached_answer(message.text)
    if cached:
        logger.info("Keshdan javob: user=%s", user_id)
        for part in split_for_telegram(cached):
            await message.answer(part)
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    hits = await retrieve(message.text)
    context = format_context(hits)
    answer = await ask_tutor(message.text, context)

    if not answer.startswith("⚠️"):
        cache_answer(message.text, answer)

    for part in split_for_telegram(answer):
        await message.answer(part)
