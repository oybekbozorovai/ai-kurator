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
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

from handlers.utils import split_for_telegram
from keyboards import MENU_TEXT, home_kb, main_menu_kb
from services.auth import (
    approve_user,
    disable_reminders,
    enable_reminders,
    get_phone_owner,
    is_admin,
    is_phone_allowed,
    is_user_approved,
    normalize_phone,
)
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
    "Bosh menyuda xizmatni tanlang:\n"
    "🎓 Kurs bo'yicha savol — savollaringizga javob\n"
    "📺 Kanal SEO, 🎬 Video SEO — matn tayyorlash\n"
    "🖼 Avatar, 🎨 Banner, 🌅 Thumbnail — rasm tayyorlash\n"
    "📂 Mening ishlarim — oldingi natijalaringiz"
)

REGISTRATION_TEXT = (
    "👋 Salom!\n\n"
    "Bu bot — onlayn kursning rasmiy AI yordamchisi. "
    "Faqat kurs talabalari foydalana oladi.\n\n"
    "📱 Kursga yozilgan telefon raqamingizni yozib yuboring.\n"
    "Masalan: +998901234567 yoki 901234567\n\n"
    "ℹ️ Telegramdagi raqamingiz boshqacha bo'lsa ham — kursga yozilgan raqamni yozing."
)

NOT_ALLOWED_TEXT = (
    "❌ Bu raqam kurs ro'yxatida topilmadi.\n\n"
    "• Raqamni xato yozgan bo'lsangiz — qaytadan to'g'ri yozing (masalan: +998901234567 yoki 901234567).\n"
    "• Kursni xarid qilganingizga ishonchingiz komil bo'lsa — ustozga murojaat qiling."
)

ALREADY_USED_TEXT = (
    "⛔ Bu telefon raqami allaqachon boshqa Telegram akkaunti bilan ro'yxatdan o'tgan.\n\n"
    "Har bir raqam faqat bitta akkaunt bilan ishlatiladi. "
    "Agar bu sizning raqamingiz bo'lsa-yu, kira olmayotgan bo'lsangiz — ustozga murojaat qiling."
)

ROADMAP_TEXT = (
    "🗺 Kurs yo'l xaritasi\n\n"
    "1️⃣ Modullarni tartib bilan ko'ring (1-moduldan boshlang)\n"
    "2️⃣ Har darsdan keyin topshiriqni bajaring\n"
    "3️⃣ Savol bo'lsa — 🎓 «Kurs bo'yicha savol»\n\n"
    "📌 Har kuni kamida 1 dars + 1 amaliyot bo'lishi kerak"
)


def _is_allowed(user_id: int) -> bool:
    return is_admin(user_id) or is_user_approved(user_id)


async def _request_phone(message: Message, state: FSMContext) -> None:
    await state.set_state(AuthStates.waiting_for_phone)
    await message.answer(REGISTRATION_TEXT, reply_markup=ReplyKeyboardRemove())


async def _approve_and_welcome(message: Message, state: FSMContext, phone: str) -> None:
    """Raqam ruxsat ro'yxatida topilganda — tasdiqlaydi va menyuni ochadi."""
    ok = approve_user(
        telegram_id=message.from_user.id,
        phone=phone,
        first_name=message.from_user.first_name or "",
        username=message.from_user.username or "",
    )
    if not ok:
        await message.answer(
            "⚠️ Texnik nosozlik tufayli ro'yxatdan o'tkaza olmadim. "
            "Birozdan so'ng qayta urinib ko'ring yoki qabul bo'limiga murojaat qiling."
        )
        logger.error("approve_user muvaffaqiyatsiz: %s (id=%s)", phone, message.from_user.id)
        return
    await state.clear()
    await message.answer(
        f"✅ Tasdiqlandi! Xush kelibsiz, {message.from_user.first_name or 'talaba'}.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(ROADMAP_TEXT)
    await _show_menu(message)
    logger.info("Yangi talaba tasdiqlandi: %s (id=%s)", phone, message.from_user.id)


async def _process_phone(message: Message, state: FSMContext, raw_phone: str) -> None:
    """Raqamni tekshiradi: ruxsat ro'yxatida bormi + boshqa akkount band qilmaganmi."""
    phone = normalize_phone(raw_phone)

    if not is_phone_allowed(phone):
        await message.answer(NOT_ALLOWED_TEXT)
        logger.info("Ruxsat etilmagan raqam: %s (id=%s)", phone, message.from_user.id)
        return

    owner = get_phone_owner(phone)
    if owner is not None and owner != message.from_user.id:
        await message.answer(ALREADY_USED_TEXT)
        logger.info("Band raqam: %s (egasi id=%s), urindi id=%s",
                    phone, owner, message.from_user.id)
        return

    await _approve_and_welcome(message, state, phone)


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


@router.message(AuthStates.waiting_for_phone, F.text & ~F.text.startswith("/"))
async def receive_typed_phone(message: Message, state: FSMContext) -> None:
    """O'quvchi kursga yozilgan raqamini yozadi (Telegram raqamidan farq qilsa ham)."""
    phone = normalize_phone(message.text)
    if len(phone) < 9:
        await message.answer(
            "⚠️ Telefon raqamini to'liq yozing.\nMasalan: +998901234567 yoki 901234567"
        )
        return  # holatni saqlab qolamiz — qaytadan urinib ko'rsin

    await _process_phone(message, state, phone)


@router.message(AuthStates.waiting_for_phone, F.contact)
async def receive_contact(message: Message, state: FSMContext) -> None:
    """O'quvchi kontaktni ulashsa ham qabul qilamiz (ixtiyoriy)."""
    contact = message.contact
    if not contact:
        return
    await _process_phone(message, state, contact.phone_number)


@router.message(AuthStates.waiting_for_phone)
async def waiting_for_phone_other(message: Message) -> None:
    await message.answer(
        "⚠️ Iltimos, kursga yozilgan telefon raqamingizni yozing.\nMasalan: +998901234567"
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
# Kunlik eslatmani yoqish/o'chirish
# ============================================================

def _reminders_kb(enabled: bool) -> InlineKeyboardMarkup:
    if enabled:
        btn = InlineKeyboardButton(text="🔕 Eslatmalarni o'chirish", callback_data="reminders:off")
    else:
        btn = InlineKeyboardButton(text="🔔 Eslatmalarni qayta yoqish", callback_data="reminders:on")
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


@router.callback_query(F.data == "reminders:off")
async def cb_reminders_off(callback: CallbackQuery) -> None:
    disable_reminders(callback.from_user.id)
    try:
        await callback.message.edit_reply_markup(reply_markup=_reminders_kb(enabled=False))
    except Exception:
        pass
    await callback.answer("🔕 Kunlik eslatmalar o'chirildi.", show_alert=True)


@router.callback_query(F.data == "reminders:on")
async def cb_reminders_on(callback: CallbackQuery) -> None:
    enable_reminders(callback.from_user.id)
    try:
        await callback.message.edit_reply_markup(reply_markup=_reminders_kb(enabled=True))
    except Exception:
        pass
    await callback.answer("🔔 Kunlik eslatmalar qayta yoqildi.", show_alert=True)


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
