"""Admin uchun buyruqlar — statistika, muddatlar, ban va auto-kick boshqaruvi.

Eslatma: ruxsat etilgan raqamlar A.Y.P.I dashboard orqali patokka qo'shiladi
(allowed_contacts). Bot ichida raqam qo'shish/o'chirish buyruqlari yo'q."""
import asyncio
import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import ADMIN_USER_IDS, KICK_CHAT_IDS
from services.auth import (
    add_assistant_admin,
    ban_user,
    free_phone,
    get_expiring_soon,
    is_admin as auth_is_admin,
    list_allowed_phones,
    list_all_user_ids,
    list_approved_users,
    list_assistant_admins,
    remove_assistant_admin,
    stats,
    unban_user,
)
from services.scheduler import kick_expired_once

logger = logging.getLogger(__name__)
router = Router(name="admin")


class BroadcastStates(StatesGroup):
    waiting_message = State()
    confirm = State()


def _is_super_admin(user_id: int) -> bool:
    """Asosiy admin — ADMIN_USER_IDS (Railway) dagi. Faqat ular admin qo'sha/o'chira oladi."""
    return user_id in ADMIN_USER_IDS


def _is_admin(user_id: int) -> bool:
    """Asosiy yoki yordamchi admin — odatiy admin buyruqlarini ishlata oladi."""
    return auth_is_admin(user_id)


router.message.filter(F.from_user.id.in_(ADMIN_USER_IDS) | (F.chat.type == ChatType.PRIVATE))


HELP_TEXT = (
    "🔐 Admin buyruqlari:\n\n"
    "Statistika va ko'rish:\n"
    "/admin_stats — umumiy statistika\n"
    "/list_users — ro'yxatdan o'tgan (kontakt ulashgan) talabalar\n"
    "/list_phones — patokka biriktirilgan ruxsat raqamlari\n"
    "/list_expiring — yaqin 7 kun ichida muddati tugaydiganlar\n\n"
    "ℹ️ Raqamlar A.Y.P.I dashboard orqali patokka qo'shiladi.\n\n"
    "Boshqaruv:\n"
    "/broadcast — barcha o'quvchilarga e'lon yuborish\n"
    "/ban_user 123456789 — ban\n"
    "/unban_user 123456789 — banni olib tashlash\n"
    "/free_phone +998901234567 — raqamni bo'shatish (boshqa akkount qayta kira oladi)\n"
    "/kick_now — muddati o'tganlarni darhol chiqarish (odatda avtomat)\n\n"
    "Adminlar (faqat asosiy admin):\n"
    "/add_admin 123456789 — yordamchi admin qo'shish\n"
    "/remove_admin 123456789 — yordamchi adminni o'chirish\n"
    "/list_admins — adminlar ro'yxati\n\n"
    "Yordamchi:\n"
    "/chat_id — joriy chat ID'ini ko'rsatadi (KICK_CHAT_IDS uchun)\n"
    "/myid — o'z ID va admin holatingiz\n"
)


@router.message(Command("admin_help"))
async def cmd_admin_help(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    await message.answer(HELP_TEXT)


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    """Har kim ishlatadi — o'z ID'sini va admin holatini ko'rsatadi (diagnostika)."""
    uid = message.from_user.id
    admin_status = "HA ✅" if uid in ADMIN_USER_IDS else "YO'Q ❌"
    admins = ", ".join(str(x) for x in sorted(ADMIN_USER_IDS)) or "(bo'sh)"
    await message.answer(
        f"🆔 Sizning Telegram ID: {uid}\n"
        f"👑 Admin: {admin_status}\n"
        f"📋 Botdagi adminlar ro'yxati: {admins}"
    )


@router.message(Command("chat_id"))
async def cmd_chat_id(message: Message) -> None:
    """Har qanday chatda ishlaydi — chat ID'ini ko'rsatadi."""
    if not _is_admin(message.from_user.id):
        return
    await message.reply(
        f"Joriy chat ID: {message.chat.id}\n"
        f"Chat turi: {message.chat.type}\n"
        f"Chat nomi: {message.chat.title or message.chat.full_name or '-'}"
    )


@router.message(Command("admin_stats"))
async def cmd_admin_stats(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    s = stats()
    kick_chats = ", ".join(str(x) for x in KICK_CHAT_IDS) or "(sozlanmagan)"
    await message.answer(
        "📊 Statistika:\n"
        f"• Ruxsat etilgan raqamlar: {s['allowed_phones']}\n"
        f"• Tasdiqlangan talabalar: {s['approved_users']}\n"
        f"• Banlangan: {s['banned_users']}\n"
        f"• Muddati o'tgan (chiqarish kutilmoqda): {s['expired_pending_kick']}\n"
        f"• Tarixiy chiqarilganlar: {s['kick_log']}\n\n"
        f"🚪 Kick chatlar: {kick_chats}"
    )


@router.message(Command("free_phone"))
async def cmd_free_phone(message: Message) -> None:
    """Raqamni ro'yxatdan o'tgan akkountdan ajratadi — boshqa akkount qayta kira oladi.
    Raqamning o'zi ruxsat ro'yxatida qoladi."""
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Ishlatish: /free_phone +998901234567")
        return
    freed = free_phone(parts[1])
    if freed:
        ids = ", ".join(str(i) for i in freed)
        await message.answer(
            f"✅ Raqam bo'shatildi: {parts[1]}\n"
            f"O'chirilgan akkount(lar): {ids}\n\n"
            f"Endi bu raqam bilan boshqa Telegram akkaunti qayta ro'yxatdan o'ta oladi.\n"
            f"(Raqam ruxsat ro'yxatida qoldi — to'liq o'chirish uchun /remove_phone.)"
        )
    else:
        await message.answer(
            f"ℹ️ Bu raqam bilan hech kim ro'yxatdan o'tmagan: {parts[1]}\n"
            f"(Ruxsat ro'yxatidan butunlay o'chirish uchun /remove_phone ishlating.)"
        )


@router.message(Command("list_users"))
async def cmd_list_users(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    users = list_approved_users(limit=100)
    if not users:
        await message.answer("Hech qanday talaba ro'yxatdan o'tmagan.")
        return

    lines = [f"👥 Tasdiqlangan talabalar ({len(users)} ta):\n"]
    for tid, phone, first_name, username, joined_at, expires_at in users[:50]:
        u = f"@{username}" if username else "—"
        name = first_name or "(ismsiz)"
        if expires_at == 0:
            exp = "cheksiz"
        else:
            exp_dt = datetime.fromtimestamp(expires_at)
            exp = exp_dt.strftime("%Y-%m-%d")
        lines.append(f"• {name} ({u}) — +{phone} — id={tid} — ⏱{exp}")
    if len(users) > 50:
        lines.append(f"\n... va yana {len(users) - 50} ta")
    await message.answer("\n".join(lines))


@router.message(Command("list_phones"))
async def cmd_list_phones(message: Message) -> None:
    """Ruxsat ro'yxatiga qo'shilgan raqamlarni ko'rsatadi (ro'yxatdan o'tgan/o'tmaganini belgilaydi)."""
    if not _is_admin(message.from_user.id):
        return
    phones = list_allowed_phones(limit=500)
    if not phones:
        await message.answer(
            "Ruxsat ro'yxati bo'sh.\n"
            "Raqamlarni A.Y.P.I dashboard orqali patokka qo'shing."
        )
        return

    registered = sum(1 for _, _, is_reg in phones if is_reg)
    lines = [
        f"📋 Ruxsat ro'yxatidagi raqamlar: {len(phones)} ta\n"
        f"(✅ ro'yxatdan o'tgan: {registered} ta, ⏳ hali kutilmoqda: {len(phones) - registered} ta)\n"
    ]
    for phone, expires_at, is_reg in phones[:100]:
        if expires_at == 0:
            exp = "cheksiz"
        else:
            exp = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d")
        mark = "✅" if is_reg else "⏳"
        lines.append(f"{mark} +{phone} — ⏱{exp}")
    if len(phones) > 100:
        lines.append(f"\n... va yana {len(phones) - 100} ta")
    await message.answer("\n".join(lines))


@router.message(Command("list_expiring"))
async def cmd_list_expiring(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    soon = get_expiring_soon(days=7)
    if not soon:
        await message.answer("Yaqin 7 kun ichida hech kim muddati tugamaydi.")
        return

    lines = [f"⚠️ Yaqin 7 kun ichida muddati tugaydiganlar ({len(soon)} ta):\n"]
    for tid, phone, first_name, expires_at in soon:
        exp_dt = datetime.fromtimestamp(expires_at)
        days_left = (exp_dt - datetime.now()).days
        lines.append(
            f"• {first_name or '?'} — +{phone} — id={tid} — "
            f"{exp_dt.strftime('%Y-%m-%d')} ({days_left} kun qoldi)"
        )
    await message.answer("\n".join(lines))


@router.message(Command("kick_now"))
async def cmd_kick_now(message: Message, bot: Bot) -> None:
    if not _is_admin(message.from_user.id):
        return
    await message.answer("🔍 Muddati o'tganlarni tekshirayapman...")
    n = await kick_expired_once(bot)
    await message.answer(f"✅ Tugadi: {n} ta talaba chiqarildi.")


@router.message(Command("ban_user"))
async def cmd_ban(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Ishlatish: /ban_user 123456789")
        return
    ban_user(int(parts[1]))
    await message.answer(f"🚫 Banlandi: {parts[1]}")


@router.message(Command("unban_user"))
async def cmd_unban(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Ishlatish: /unban_user 123456789")
        return
    unban_user(int(parts[1]))
    await message.answer(f"✅ Ban olib tashlandi: {parts[1]}")


# ============================================================
# Yordamchi adminlarni boshqarish — FAQAT asosiy admin
# ============================================================

@router.message(Command("add_admin"))
async def cmd_add_admin(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    if not _is_super_admin(message.from_user.id):
        await message.answer("⛔ Faqat asosiy admin yangi admin qo'sha oladi.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer(
            "Ishlatish: /add_admin 123456789\n"
            "(Yordamchining Telegram ID'si — u botga /myid yozsa ko'rinadi.)"
        )
        return
    new_id = int(parts[1])
    if new_id in ADMIN_USER_IDS:
        await message.answer("ℹ️ Bu foydalanuvchi allaqachon asosiy admin.")
        return
    added = add_assistant_admin(new_id)
    if added:
        await message.answer(
            f"✅ Yordamchi admin qo'shildi: {new_id}\n"
            f"Endi u o'quvchi qo'sha/o'chira oladi (lekin admin qo'sha olmaydi)."
        )
    else:
        await message.answer(f"ℹ️ {new_id} allaqachon yordamchi admin.")


@router.message(Command("remove_admin"))
async def cmd_remove_admin(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    if not _is_super_admin(message.from_user.id):
        await message.answer("⛔ Faqat asosiy admin adminni o'chira oladi.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Ishlatish: /remove_admin 123456789")
        return
    ok = remove_assistant_admin(int(parts[1]))
    await message.answer(
        "✅ Yordamchi admin o'chirildi." if ok else "ℹ️ Bunday yordamchi admin topilmadi."
    )


@router.message(Command("list_admins"))
async def cmd_list_admins(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    supers = ", ".join(str(x) for x in sorted(ADMIN_USER_IDS)) or "(yo'q)"
    assistants = list_assistant_admins()
    a_str = ", ".join(str(x) for x in assistants) if assistants else "(yo'q)"
    await message.answer(
        f"👑 Asosiy admin(lar): {supers}\n"
        f"🤝 Yordamchi adminlar: {a_str}"
    )


# ============================================================
# E'lon yuborish (broadcast) — barcha o'quvchilarga
# ============================================================

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    await state.set_state(BroadcastStates.waiting_message)
    await message.answer(
        "📢 Barcha o'quvchilarga yuboriladigan xabarni yozing.\n"
        "Bekor qilish: /cancel"
    )


@router.message(BroadcastStates.waiting_message, Command("cancel"))
async def broadcast_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ E'lon bekor qilindi.")


@router.message(BroadcastStates.waiting_message, F.text)
async def broadcast_preview(message: Message, state: FSMContext) -> None:
    await state.update_data(text=message.text)
    await state.set_state(BroadcastStates.confirm)
    count = len(list_all_user_ids())
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Ha, yuborish", callback_data="bcast:yes"),
        InlineKeyboardButton(text="❌ Bekor", callback_data="bcast:no"),
    ]])
    await message.answer(
        f"Quyidagi xabar {count} ta o'quvchiga yuboriladi:\n\n"
        f"———\n{message.text}\n———\n\n"
        f"Tasdiqlaysizmi?",
        reply_markup=kb,
    )


@router.callback_query(BroadcastStates.confirm, F.data == "bcast:no")
async def broadcast_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ E'lon bekor qilindi.")
    await callback.answer()


@router.callback_query(BroadcastStates.confirm, F.data == "bcast:yes")
async def broadcast_send(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    text = data.get("text", "")
    await state.clear()
    await callback.answer()
    if not text:
        await callback.message.edit_text("⚠️ Xabar bo'sh, bekor qilindi.")
        return
    await callback.message.edit_text("📤 Yuborilmoqda...")

    ids = list_all_user_ids()
    sent = 0
    failed = 0
    for uid in ids:
        try:
            await bot.send_message(uid, text)
            sent += 1
        except (TelegramBadRequest, TelegramForbiddenError):
            failed += 1  # bloklagan yoki akkount o'chgan
        except Exception as e:
            failed += 1
            logger.warning("E'lon yuborilmadi (user=%s): %s", uid, e)
        await asyncio.sleep(0.05)  # Telegram limitlariga moslashish

    await callback.message.answer(
        f"✅ E'lon yuborildi: {sent} ta\n"
        f"❌ Yetib bormadi: {failed} ta (bloklagan/o'chirilgan)"
    )
