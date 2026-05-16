"""Shaxsiy chat — /start, ro'yxatdan o'tish (telefon), bosh menyu va savol-javob.

Bot menyuli ishlaydi: o'quvchi tugma tanlaydi → o'sha xizmat ishlaydi.
Savol-javob ham tugma orqali ('🎓 Kurs bo'yicha savol') — QAStates holatida.
"""

import logging

from aiogram import F, Router
from aiogram.enums import ChatType, ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from config import ADMIN_USER_IDS
from handlers.utils import split_for_telegram
from keyboards import MENU_TEXT, home_kb, main_menu_kb
from services.auth import approve_user, is_phone_allowed, is_user_approved
from services.gemini import ask_tutor
from services.limiter import cache_answer, check_rate_limit, get_cached_answer
from services.rag import format_context, retrieve

logger = logging.getLogger(__name__)
router = Router(name="private")
router.message.filter(F.chat.type == ChatType.PRIVATE)


class AuthStates(StatesGroup):
    waiting_for_phone = State()


class QAStates(StatesGroup):
    active = State()  # savol-javob rejimi yoqilgan


WELCOME = "👋 Salom! Men — kursingizning AI yordamchisiman."

HELP = (
    "📚 Bot menyu orqali ishlaydi.\n\n"
    "/start — botni ishga tushirish\n"
    "/menu — bosh menyuni ochish\n"
    "/yordam — bu xabar\n\n"
    "Bosh menyuда xizmatni tanlang:\n"
    "🎓 Kurs bo'yicha savol — savollaringizga javob\n"
    "📺 Kanal SEO, 🎬 Video SEO — matn tayyorlash\n"
    "🖼 Avatar, 🎨 Banner, 🌅 Thumbnail — rasm tayyorlash\n"
    "📂 Mening ishlarim — oldingi natijalaringiz"
)

REGISTRATION_TEXT = (
    "👋 Salom!\n\n"
    "Bu bot — onlayn kursning rasmiy AI yordamchisi. "
    "Faqat kurs talabalari foydalana oladi.\n\n"
    "📱 Telefon raqamingizni jo'nating, biz sizning kursdaligingizni tekshiramiz.\n\n"
    "Pastdagi tugmani bosing:"
)

NOT_ALLOWED_TEXT = (
    "❌ Sizning telefon raqamingiz kurs ro'yxatida topilmadi.\n\n"
    "Agar siz haqiqatdan ham kursni xarid qilgan bo'lsangiz, ustozga murojaat qiling. "
    "Iltimos, telefon raqamingizni va to'liq ismingizni bering."
)


def _is_allowed(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS or is_user_approved(user_id)


def _phone_keyboard() -> ReplyKeyboardMarkup:
    button = KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)
    return ReplyKeyboardMarkup(
        keyboard=[[button]], resize_keyboard=True, one_time_keyboard=True
    )


async def _request_phone(message: Message, state: FSMContext) -> None:
    await state.set_state(AuthStates.waiting_for_phone)
    await message.answer(REGISTRATION_TEXT, reply_markup=_phone_keyboard())


async def _show_menu(message: Message) -> None:
    """Bosh menyuni ko'rsatadi."""
    await message.answer(MENU_TEXT, reply_markup=main_menu_kb())


# ============================================================
# /start va ro'yxatdan o'tish
# ============================================================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id

    if _is_allowed(user_id):
        await message.answer(WELCOME, reply_markup=ReplyKeyboardRemove())
        await _show_menu(message)
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
            f"✅ Tasdiqlandi! Xush kelibsiz, {message.from_user.first_name or 'talaba'}.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await _show_menu(message)
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


# ============================================================
# Buyruqlar
# ============================================================

@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    if not _is_allowed(message.from_user.id):
        await message.answer("⛔ Avval /start bosib ro'yxatdan o'ting.")
        return
    await state.clear()
    await _show_menu(message)


@router.message(Command("yordam", "help"))
async def cmd_help(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        await message.answer("⛔ Avval /start bosib ro'yxatdan o'ting.")
        return
    await message.answer(HELP)


# ============================================================
# Savol-javob rejimi (🎓 tugma orqali)
# ============================================================

@router.callback_query(F.data == "menu:qa")
async def qa_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    await state.set_state(QAStates.active)
    await callback.message.edit_text(
        "🎓 Kurs bo'yicha savol\n\n"
        "Savolingizni yozing — kurs materiallari asosida javob beraman.\n"
        "Tugatish uchun 🏠 Bosh menyu tugmasini bosing.",
        reply_markup=home_kb(),
    )
    await callback.answer()


@router.message(QAStates.active, F.text & ~F.text.startswith("/"))
async def qa_answer(message: Message) -> None:
    """Savol-javob rejimida har bir matnli xabarga javob beradi."""
    user_id = message.from_user.id

    allowed, _ = check_rate_limit(user_id)
    if not allowed:
        await message.answer(
            "⏳ Soatiga ruxsat etilgan savollar limitini to'ldirgansiz. "
            "Iltimos, biroz keyin urinib ko'ring.",
            reply_markup=home_kb(),
        )
        return

    cached = get_cached_answer(message.text)
    if cached:
        logger.info("Keshdan javob: user=%s", user_id)
        parts = split_for_telegram(cached)
        for i, part in enumerate(parts):
            await message.answer(
                part, reply_markup=home_kb() if i == len(parts) - 1 else None
            )
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    hits = await retrieve(message.text)
    context = format_context(hits)
    answer = await ask_tutor(message.text, context)

    if not answer.startswith("⚠️"):
        cache_answer(message.text, answer)

    parts = split_for_telegram(answer)
    for i, part in enumerate(parts):
        await message.answer(
            part, reply_markup=home_kb() if i == len(parts) - 1 else None
        )


# ============================================================
# Boshqa matn — menyuni eslatadi
# ============================================================

@router.message(F.text)
async def fallback(message: Message, state: FSMContext) -> None:
    """Holatsiz matn — o'quvchini menyuga yo'naltiradi."""
    if not _is_allowed(message.from_user.id):
        await message.answer("⛔ Botdan foydalanish uchun /start bosing.")
        return
    await state.clear()
    await message.answer(
        "👇 Iltimos, menyudan kerakli xizmatni tanlang.",
        reply_markup=main_menu_kb(),
    )
