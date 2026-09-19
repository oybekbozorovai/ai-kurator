"""Sertifikat berish oqimi: menyu/eslatma tugmasi + ism kiritish FSM.

Qoidalar:
- O'quvchi botga qo'shilgandan CERT_OPEN_AFTER_DAYS kun o'tgach ochiladi.
- Sertifikat CERT_MAX_ISSUES martagacha beriladi (ismdagi xatoni to'g'rilash uchun);
  har qayta olishda eski sertifikat bekor bo'lib, yangi ID bilan beriladi.
"""
import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from config import CERT_MAX_ISSUES, CERT_OPEN_AFTER_DAYS, COURSE_NAME
from keyboards import home_kb
from services.auth import _user_row, is_admin, is_user_approved
from services.certificate import clean_name, generate_cert_id, render_certificate
from services.cert_store import (
    cert_days_left,
    cert_issue_count,
    cert_window_open,
    get_certificate,
    mark_cert_issued,
    save_certificate,
)

logger = logging.getLogger(__name__)
router = Router(name="certificate")
router.message.filter(F.chat.type == ChatType.PRIVATE)


class CertStates(StatesGroup):
    waiting_name = State()


NOT_OPEN_TEXT = (
    "🏆 Sertifikat hali ochilmagan.\n\n"
    "Sertifikat botga qo'shilganingizdan {days} kun o'tgach ochiladi. "
    "Sizga yana {left} kun qoldi.\n\n"
    "Vaqti kelganda bot o'zi eslatma yuboradi va shu tugma orqali "
    "sertifikatingizni olasiz."
)

EXHAUSTED_TEXT = (
    "Siz sertifikatni {max} marta oldingiz — boshqa qayta berilmaydi.\n\n"
    "Amaldagi sertifikatingiz: #{cert_id} ({name})."
)

ASK_NAME_FIRST_TEXT = (
    "Sertifikatga yoziladigan to'liq Ism Familiyangizni yuboring.\n\n"
    "⚠️ Diqqat bilan, xatosiz yozing. Sertifikat jami {max} marta beriladi — "
    "xato bo'lsa, keyin to'g'rilab qayta olishingiz mumkin."
)

ASK_NAME_AGAIN_TEXT = (
    "Sizda allaqachon sertifikat bor: #{cert_id} ({name}).\n\n"
    "Ismda xato bo'lgan bo'lsa, to'g'rilangan to'liq Ism Familiyangizni yuboring — "
    "eski sertifikat bekor bo'lib, yangi ID bilan beriladi.\n"
    "Qolgan urinishlar: {left} ta.\n\n"
    "O'zgartirmoqchi bo'lmasangiz, «Bosh menyu» tugmasini bosing."
)


def _is_command(message: Message) -> bool:
    return bool(message.text) and message.text.startswith("/")


async def _begin_cert(callback: CallbackQuery, state: FSMContext) -> None:
    uid = callback.from_user.id
    admin = is_admin(uid)
    if not (admin or is_user_approved(uid)):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return

    row = _user_row(uid) or {}
    cohort_id = row.get("cohort_id")

    if admin:
        # Adminlar sinash uchun har doim ola oladi (muddat/limit shart emas, bazaga yozilmaydi)
        await state.set_state(CertStates.waiting_name)
        await state.update_data(cohort_id=str(cohort_id) if cohort_id else "admin-test", is_test=True)
        await callback.message.answer(
            "🧪 Admin sinovi. Sertifikatga yoziladigan to'liq Ism Familiyani yuboring:",
            reply_markup=home_kb(),
        )
        await callback.answer()
        return

    if not cohort_id:
        await callback.answer("Patok ma'lumoti topilmadi.", show_alert=True)
        return
    cohort_id = str(cohort_id)

    if not cert_window_open(row, CERT_OPEN_AFTER_DAYS):
        await callback.answer()
        await callback.message.answer(
            NOT_OPEN_TEXT.format(days=CERT_OPEN_AFTER_DAYS,
                                 left=cert_days_left(row, CERT_OPEN_AFTER_DAYS)),
            reply_markup=home_kb(),
        )
        return

    issued = cert_issue_count(uid, cohort_id)
    existing = get_certificate(uid, cohort_id) if issued else None
    if issued >= CERT_MAX_ISSUES:
        await callback.answer()
        await callback.message.answer(
            EXHAUSTED_TEXT.format(
                max=CERT_MAX_ISSUES,
                cert_id=(existing or {}).get("id", "?"),
                name=(existing or {}).get("full_name", "?"),
            ),
            reply_markup=home_kb(),
        )
        return

    await state.set_state(CertStates.waiting_name)
    await state.update_data(cohort_id=cohort_id, is_test=False)
    if issued and existing:
        text = ASK_NAME_AGAIN_TEXT.format(
            cert_id=existing.get("id", "?"),
            name=existing.get("full_name", "?"),
            left=CERT_MAX_ISSUES - issued,
        )
    else:
        text = ASK_NAME_FIRST_TEXT.format(max=CERT_MAX_ISSUES)
    await callback.message.answer(text, reply_markup=home_kb())
    await callback.answer()


@router.callback_query(F.data.in_({"cert:start", "menu:cert"}))
async def cert_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Bosh menyudagi 'Sertifikat olish' yoki scheduler eslatmasidagi tugma."""
    await _begin_cert(callback, state)


@router.message(CertStates.waiting_name, F.text, ~F.text.startswith("/"))
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

    # Limit qayta tekshiriladi (tugma bosilgandan keyin vaqt o'tgan bo'lishi mumkin)
    if not is_test and cert_issue_count(uid, cohort_id) >= CERT_MAX_ISSUES:
        await state.clear()
        existing = get_certificate(uid, cohort_id) or {}
        await message.answer(EXHAUSTED_TEXT.format(
            max=CERT_MAX_ISSUES, cert_id=existing.get("id", "?"),
            name=existing.get("full_name", "?"),
        ), reply_markup=home_kb())
        return

    cert_id = generate_cert_id()
    try:
        png_bytes = render_certificate(full_name, cert_id)
    except Exception as e:
        logger.error("Sertifikat render xato: %s", e)
        await message.answer("Sertifikat yaratishda xatolik. Iltimos, qayta urinib ko'ring.")
        return

    caption = f"Tabriklaymiz, {full_name}!\nSertifikat ID: {cert_id}"
    if is_test:
        await message.answer_photo(
            BufferedInputFile(png_bytes, filename=f"certificate_{cert_id}.png"),
            caption="🧪 (Admin sinovi)\n" + caption,
        )
        await state.clear()
        logger.info("Sertifikat (admin sinovi): user=%s cert=%s name=%s", uid, cert_id, full_name)
        return

    # Avval bazaga yozamiz — yozilmasa o'quvchiga rasm ham ketmaydi (mos kelmasligi oldini oladi)
    try:
        old_id = save_certificate(cert_id, uid, full_name, cohort_id, COURSE_NAME)
    except Exception as e:
        logger.error("Sertifikat bazaga yozilmadi (user=%s): %s", uid, e)
        await message.answer("Sertifikatni saqlashda xatolik. Iltimos, birozdan so'ng qayta urinib ko'ring.")
        return
    issued = mark_cert_issued(uid, cohort_id, cert_id)
    left = CERT_MAX_ISSUES - issued

    if old_id:
        caption += f"\n\nEski sertifikat (#{old_id}) bekor qilindi."
    if left > 0:
        caption += (
            f"\n\nIsmda xato bo'lsa, «🏆 Sertifikat olish» tugmasi orqali to'g'rilab "
            f"qayta olishingiz mumkin (qolgan urinishlar: {left} ta)."
        )
    else:
        caption += f"\n\nBu sizning oxirgi ({CERT_MAX_ISSUES}-) sertifikatingiz — endi qayta berilmaydi."

    await message.answer_photo(
        BufferedInputFile(png_bytes, filename=f"certificate_{cert_id}.png"),
        caption=caption,
    )
    try:
        from services.cert_storage import delete_cert_png, upload_cert_png
        upload_cert_png(cert_id, png_bytes)  # saytdagi gallereya uchun (non-fatal)
        if old_id:
            delete_cert_png(old_id)
    except Exception as e:
        logger.warning("Sert rasm storage yuklash o'tkazildi: %s", e)
    await state.clear()
    logger.info("Sertifikat berildi: user=%s cert=%s name=%s urinish=%d/%d old=%s",
                uid, cert_id, full_name, issued, CERT_MAX_ISSUES, old_id)


@router.message(CertStates.waiting_name, ~F.text.startswith("/"))
async def cert_waiting_other(message: Message) -> None:
    """Matn bo'lmagan xabar — qayta so'raymiz. Buyruqlar (/start ...) o'tkazib yuboriladi,
    ularni private router ishlaydi (holatni o'zi tozalaydi)."""
    await message.answer(
        "Iltimos, to'liq Ism Familiyangizni matn sifatida yuboring:",
        reply_markup=home_kb(),
    )
