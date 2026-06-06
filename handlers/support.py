"""Texnik yordam oqimi: menu:support / /yordam → muammo + skrinshot qabul qilish."""
import logging
from typing import Optional

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from keyboards import home_kb
from services.auth import _user_row
from services.support_store import create_ticket

logger = logging.getLogger(__name__)
router = Router(name="support")
router.message.filter(F.chat.type == ChatType.PRIVATE)


class SupportStates(StatesGroup):
    waiting_report = State()


PROMPT = (
    "🛠 Texnik yordam\n\n"
    "Bu bo'lim faqat texnik nosozliklar uchun (bot, mini-app, login, vazifa yuborish ishlamasa).\n"
    "Kurs mavzusi bo'yicha savollaringizni 🎓 «Kurs bo'yicha savol» tugmasiga yozing — u yerda darrov javob olasiz.\n\n"
    "Qanday muammoga duch keldingiz? Iltimos, batafsil yozib bering: "
    "qachon, qayerda (bot, mini-app, vazifa yuborish) va qanday xatolik chiqyapti.\n\n"
    "Skrinshot bo'lsa — rasmni shu yerga yuboring (izoh bilan birga ham bo'ladi).\n\n"
    "Bekor qilish uchun 🏠 Bosh menyu tugmasini bosing."
)

CONFIRM = (
    "✅ Murojaatingiz qabul qilindi.\n\n"
    "Tez orada muammoni ko'rib chiqib, shu yerda javob beramiz. Rahmat!"
)

ERROR = (
    "Kechirasiz, murojaatni saqlashda texnik xatolik yuz berdi. "
    "Iltimos, birozdan keyin qayta urinib ko'ring."
)


async def _enter(message: Message, state: FSMContext) -> None:
    await state.set_state(SupportStates.waiting_report)
    await message.answer(PROMPT, reply_markup=home_kb())


@router.callback_query(F.data == "menu:support")
async def support_start(callback: CallbackQuery, state: FSMContext) -> None:
    await _enter(callback.message, state)
    await callback.answer()


@router.message(Command("yordam"))
async def support_cmd(message: Message, state: FSMContext) -> None:
    await _enter(message, state)


async def _save_and_confirm(
    message: Message, state: FSMContext, text: str, file_id: Optional[str]
) -> None:
    uid = message.from_user.id
    row = _user_row(uid) or {}
    full_name = (message.from_user.full_name or row.get("first_name") or "").strip() or None
    username = message.from_user.username
    cohort_id = row.get("cohort_id")
    ticket = create_ticket(uid, username, full_name, cohort_id, text or None, file_id)
    await state.clear()
    if ticket:
        logger.info("Support ticket #%s yaratildi (user=%s)", ticket.get("id"), uid)
        await message.answer(CONFIRM, reply_markup=home_kb())
    else:
        await message.answer(ERROR, reply_markup=home_kb())


@router.message(SupportStates.waiting_report, F.photo)
async def support_photo(message: Message, state: FSMContext) -> None:
    file_id = message.photo[-1].file_id
    text = (message.caption or "").strip()
    await _save_and_confirm(message, state, text, file_id)


@router.message(SupportStates.waiting_report, F.text & ~F.text.startswith("/"))
async def support_text(message: Message, state: FSMContext) -> None:
    await _save_and_confirm(message, state, message.text.strip(), None)


@router.message(SupportStates.waiting_report)
async def support_other(message: Message) -> None:
    await message.answer(
        "Iltimos, muammoni matn sifatida yozing yoki skrinshot (rasm) yuboring."
    )
