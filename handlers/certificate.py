"""Sertifikat berish oqimi: cert:start callback + ism kiritish FSM."""
import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from config import CERT_GRANDFATHER_BEFORE, CERT_PROMPT_DAYS, COURSE_NAME
from services.auth import _user_row, is_admin, is_user_approved
from services.certificate import clean_name, generate_cert_id, render_certificate
from services.cert_store import (
    cert_window_open,
    get_certificate,
    has_certificate,
    is_grandfathered,
    save_certificate,
)

logger = logging.getLogger(__name__)
router = Router(name="certificate")
router.message.filter(F.chat.type == ChatType.PRIVATE)


class CertStates(StatesGroup):
    waiting_name = State()


NOT_OPEN_TEXT = (
    "🏆 Sertifikat hali ochilmagan.\n\n"
    "Sertifikat kurs tugashiga {days} kun qolganda ochiladi. "
    "Shu vaqt kelganda bu tugma orqali sertifikatingizni olishingiz mumkin."
)


async def _begin_cert(callback: CallbackQuery, state: FSMContext, check_window: bool) -> None:
    """Sertifikat oqimini boshlaydi. check_window=True bo'lsa, muddat oynasini tekshiradi
    (menyu tugmasi uchun); scheduler tugmasi allaqachon tekshirilgan."""
    uid = callback.from_user.id
    admin = is_admin(uid)
    if not (admin or is_user_approved(uid)):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return

    row = _user_row(uid) or {}
    cohort_id = row.get("cohort_id")
    is_test = False

    if admin:
        # Adminlar sinash uchun har doim ola oladi (patok/muddat shart emas)
        cohort_id = str(cohort_id) if cohort_id else "admin-test"
        is_test = True
    elif is_grandfathered(row, CERT_GRANDFATHER_BEFORE):
        # Eski o'quvchi — patok/muddatdan qat'i nazar darhol oladi
        cohort_id = str(cohort_id) if cohort_id else "early"
    else:
        # Oddiy o'quvchi — patok kerak + oxirgi 10 kun oynasi
        if not cohort_id:
            await callback.answer("Patok ma'lumoti topilmadi.", show_alert=True)
            return
        if check_window and not cert_window_open(row.get("cohort"), CERT_PROMPT_DAYS):
            await callback.answer()
            await callback.message.answer(NOT_OPEN_TEXT.format(days=CERT_PROMPT_DAYS))
            return
        cohort_id = str(cohort_id)

    await state.set_state(CertStates.waiting_name)
    await state.update_data(cohort_id=cohort_id, is_test=is_test)
    await callback.message.answer(
        "Sertifikatingizga yoziladigan to'liq Ism Familiyangizni yuboring:"
    )
    await callback.answer()


@router.callback_query(F.data == "cert:start")
async def cert_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Scheduler yuborgan taklif tugmasi — muddat allaqachon tekshirilgan."""
    await _begin_cert(callback, state, check_window=False)


@router.callback_query(F.data == "menu:cert")
async def cert_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Bosh menyudagi 'Sertifikat olish' tugmasi — muddat oynasini tekshiradi."""
    await _begin_cert(callback, state, check_window=True)


@router.message(CertStates.waiting_name, F.text & ~F.text.startswith("/"))
async def cert_receive_name(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    data = await state.get_data()
    cohort_id = data.get("cohort_id")
    is_test = data.get("is_test", False)
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

    # Admin sinovida takror tekshiruv o'tkazib yuboriladi (qayta chizib ko'rish mumkin)
    if not is_test and has_certificate(uid, cohort_id):
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
    caption = f"Tabriklaymiz, {full_name}!\nSertifikat ID: {cert_id}"
    if is_test:
        caption = "🧪 (Admin sinovi)\n" + caption
    await message.answer_photo(file, caption=caption)
    if not is_test:  # admin sinovini bazaga yozmaymiz
        save_certificate(cert_id, uid, full_name, cohort_id, COURSE_NAME)
    await state.clear()
    logger.info("Sertifikat berildi: user=%s cert=%s name=%s test=%s",
                uid, cert_id, full_name, is_test)


@router.message(CertStates.waiting_name)
async def cert_waiting_other(message: Message) -> None:
    await message.answer("Iltimos, to'liq Ism Familiyangizni matn sifatida yuboring:")
