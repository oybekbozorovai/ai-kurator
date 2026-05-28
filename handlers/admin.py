"""Admin uchun buyruqlar — telefon ro'yxati, statistika, muddatlar va auto-kick boshqaruvi."""
import io
import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from config import ADMIN_USER_IDS, COURSE_ACCESS_MONTHS, KICK_CHAT_IDS
from services.auth import (
    add_allowed_phones,
    ban_user,
    free_phone,
    get_expiring_soon,
    list_allowed_phones,
    list_approved_users,
    remove_allowed_phone,
    stats,
    unban_user,
)
from services.scheduler import kick_expired_once

logger = logging.getLogger(__name__)
router = Router(name="admin")


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


router.message.filter(F.from_user.id.in_(ADMIN_USER_IDS) | (F.chat.type == ChatType.PRIVATE))


HELP_TEXT = (
    "🔐 Admin buyruqlari:\n\n"
    "Statistika va ko'rish:\n"
    "/admin_stats — umumiy statistika\n"
    "/list_users — ro'yxatdan o'tgan (kontakt ulashgan) talabalar\n"
    "/list_phones — ruxsat ro'yxatiga qo'shilgan raqamlar\n"
    "/list_expiring — yaqin 7 kun ichida muddati tugaydiganlar\n\n"
    "Telefon ro'yxati:\n"
    f"/add_phone +998901234567 [oy] — bitta raqam (default: {COURSE_ACCESS_MONTHS} oy, 0 = cheksiz)\n"
    "/remove_phone +998901234567 — raqamni o'chirish\n"
    "Fayl yuklash: txt/csv faylni caption bilan yuboring:\n"
    f"  /upload_phones — default {COURSE_ACCESS_MONTHS} oy\n"
    "  /upload_phones 6 — 6 oy\n"
    "  /upload_phones 0 — cheksiz\n\n"
    "Boshqaruv:\n"
    "/ban_user 123456789 — ban\n"
    "/unban_user 123456789 — banni olib tashlash\n"
    "/free_phone +998901234567 — raqamni bo'shatish (boshqa akkount qayta kira oladi)\n"
    "/kick_now — muddati o'tganlarni darhol chiqarish (odatda avtomat)\n\n"
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
        f"⚙️ Default muddat: {COURSE_ACCESS_MONTHS} oy\n"
        f"🚪 Kick chatlar: {kick_chats}"
    )


def _parse_months(arg: str, default: int = COURSE_ACCESS_MONTHS) -> int:
    """Captiondan/argumentdan oy sonini olish. Bo'sh yoki noto'g'ri → default."""
    if not arg:
        return default
    arg = arg.strip()
    if arg.isdigit():
        return int(arg)
    return default


@router.message(Command("add_phone"))
async def cmd_add_phone(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(
            f"Ishlatish: /add_phone +998901234567 [oy]\n"
            f"Default: {COURSE_ACCESS_MONTHS} oy. 0 = cheksiz."
        )
        return
    phone = parts[1]
    months = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else COURSE_ACCESS_MONTHS
    n = add_allowed_phones([phone], months=months)
    expiry_text = f"{months} oy ruxsat" if months > 0 else "cheksiz ruxsat"
    await message.answer(f"✅ Qo'shildi: {n} ta yangi raqam ({expiry_text}).")


@router.message(Command("remove_phone"))
async def cmd_remove_phone(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Ishlatish: /remove_phone +998901234567")
        return
    ok = remove_allowed_phone(parts[1])
    await message.answer("✅ O'chirildi." if ok else "ℹ️ Bunday raqam topilmadi.")


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


@router.message(F.document, F.caption.regexp(r"^/upload_phones\b"))
async def handle_phone_file(message: Message, bot: Bot) -> None:
    if not _is_admin(message.from_user.id):
        return
    doc = message.document
    if not doc:
        return
    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        await message.answer("❌ Fayl 5 MB dan kichik bo'lsin.")
        return

    # Captiondan oy sonini olish: /upload_phones 4
    caption = message.caption or ""
    m = re.match(r"^/upload_phones\s*(\d*)\s*", caption)
    months_str = m.group(1) if m else ""
    months = int(months_str) if months_str else COURSE_ACCESS_MONTHS

    file = await bot.get_file(doc.file_id)
    buf = io.BytesIO()
    await bot.download_file(file.file_path, destination=buf)
    text = buf.getvalue().decode("utf-8", errors="ignore")

    phones = [
        line.strip() for line in text.replace(";", "\n").replace(",", "\n").splitlines()
        if line.strip() and not line.strip().lower().startswith("phone")
    ]
    if not phones:
        await message.answer("❌ Faylda raqam topilmadi.")
        return

    n = add_allowed_phones(phones, months=months)
    expiry_text = f"{months} oy ruxsat" if months > 0 else "cheksiz ruxsat"
    await message.answer(
        f"✅ {n} ta yangi raqam qo'shildi (jami yuklangani: {len(phones)} ta).\n"
        f"⏱ Muddat: {expiry_text}\n"
        f"Takroriy raqamlar avtomatik o'tkazib yuborildi."
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
            "/add_phone +998901234567 bilan raqam qo'shing."
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
