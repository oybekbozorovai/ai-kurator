"""Sertifikat berish oqimi: cert:start callback + ism kiritish FSM."""
import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from config import COURSE_NAME
from services.auth import _user_row, is_admin, is_user_approved
from services.certificate import clean_name, generate_cert_id, render_certificate
from services.cert_store import get_certificate, has_certificate, save_certificate

logger = logging.getLogger(__name__)
router = Router(name="certificate")
router.message.filter(F.chat.type == ChatType.PRIVATE)


class CertStates(StatesGroup):
    waiting_name = State()


@router.callback_query(F.data == "cert:start")
async def cert_start(callback: CallbackQuery, state: FSMContext) -> None:
    uid = callback.from_user.id
    if not (is_admin(uid) or is_user_approved(uid)):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return

    row = _user_row(uid)
    cohort_id = row.get("cohort_id") if row else None
    if not cohort_id:
        await callback.answer("Patok ma'lumoti topilmadi.", show_alert=True)
        return

    await state.set_state(CertStates.waiting_name)
    await state.update_data(cohort_id=str(cohort_id))
    await callback.message.answer(
        "Sertifikatingizga yoziladigan to'liq Ism Familiyangizni yuboring:"
    )
    await callback.answer()


@router.message(CertStates.waiting_name, F.text & ~F.text.startswith("/"))
async def cert_receive_name(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    data = await state.get_data()
    cohort_id = data.get("cohort_id")
    if not cohort_id:
        await state.clear()
        await message.answer("Xatolik yuz berdi. Iltimos, qayta /start bosing.")
        return

    try:
        full_name = clean_name(message.text)
    except ValueError:
        await message.answer(
            "Ism noto'g'ri kiritildi. Iltimos, lotin yoki kirill harflarida "
            "to'liq Ism Familiyangizni yuboring:"
        )
        return

    if has_certificate(uid, cohort_id):
        await state.clear()
        existing = get_certificate(uid, cohort_id)
        if existing:
            await message.answer(f"Sizda allaqachon sertifikat bor (#{existing['id']}).")
        else:
            await message.answer("Sizda allaqachon sertifikat bor.")
        return

    cert_id = generate_cert_id()
    try:
        png_bytes = render_certificate(full_name, cert_id)
    except Exception as e:
        logger.error("Sertifikat render xato: %s", e)
        await message.answer("Sertifikat yaratishda xatolik. Iltimos, qayta urinib ko'ring.")
        return

    file = BufferedInputFile(png_bytes, filename=f"certificate_{cert_id}.png")
    await message.answer_photo(
        file,
        caption=f"Tabriklaymiz, {full_name}!\nSertifikat ID: {cert_id}",
    )
    save_certificate(cert_id, uid, full_name, cohort_id, COURSE_NAME)
    await state.clear()
    logger.info("Sertifikat berildi: user=%s cert=%s name=%s", uid, cert_id, full_name)


@router.message(CertStates.waiting_name)
async def cert_waiting_other(message: Message) -> None:
    await message.answer("Iltimos, to'liq Ism Familiyangizni matn sifatida yuboring:")
