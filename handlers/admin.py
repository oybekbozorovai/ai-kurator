"""Admin uchun buyruqlar — telefon ro'yxati va statistika boshqaruvi."""
import io
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from config import ADMIN_USER_IDS
from services.auth import (
    add_allowed_phones,
    ban_user,
    list_approved_users,
    remove_allowed_phone,
    stats,
    unban_user,
)

logger = logging.getLogger(__name__)
router = Router(name="admin")
router.message.filter(F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(ADMIN_USER_IDS))


HELP_TEXT = (
    "🔐 Admin buyruqlar:\n\n"
    "/admin_help — bu xabar\n"
    "/admin_stats — statistika\n"
    "/add_phone +998901234567 — bitta raqam qo'shish\n"
    "/remove_phone +998901234567 — raqamni o'chirish\n"
    "/list_users — ro'yxatdan o'tgan talabalar (oxirgi 50 ta)\n"
    "/ban_user 123456789 — telegram_id orqali ban\n"
    "/unban_user 123456789 — banni olib tashlash\n\n"
    "📂 Ko'p raqamni qo'shish: txt yoki csv faylni yuklang va caption sifatida /upload_phones yozing.\n"
    "Har qatorda bitta raqam (masalan: +998901234567)."
)


@router.message(Command("admin_help"))
async def cmd_admin_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("admin_stats"))
async def cmd_admin_stats(message: Message) -> None:
    s = stats()
    await message.answer(
        "📊 Statistika:\n"
        f"• Ruxsat etilgan raqamlar: {s['allowed_phones']}\n"
        f"• Ro'yxatdan o'tgan talabalar: {s['approved_users']}\n"
        f"• Banlangan: {s['banned_users']}"
    )


@router.message(Command("add_phone"))
async def cmd_add_phone(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Ishlatish: /add_phone +998901234567")
        return
    n = add_allowed_phones([parts[1]])
    await message.answer(f"✅ Qo'shildi: {n} ta yangi raqam (yoki avval bor edi).")


@router.message(Command("remove_phone"))
async def cmd_remove_phone(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Ishlatish: /remove_phone +998901234567")
        return
    ok = remove_allowed_phone(parts[1])
    await message.answer("✅ O'chirildi." if ok else "ℹ️ Bunday raqam topilmadi.")


@router.message(F.document, F.caption.contains("/upload_phones"))
async def handle_phone_file(message: Message, bot: Bot) -> None:
    doc = message.document
    if not doc:
        return
    if doc.file_size and doc.file_size > 5 * 1024 * 1024:
        await message.answer("❌ Fayl 5 MB dan kichik bo'lsin.")
        return

    file = await bot.get_file(doc.file_id)
    buf = io.BytesIO()
    await bot.download_file(file.file_path, destination=buf)
    text = buf.getvalue().decode("utf-8", errors="ignore")

    phones = [line.strip() for line in text.replace(";", "\n").replace(",", "\n").splitlines() if line.strip()]
    if not phones:
        await message.answer("❌ Faylda raqam topilmadi.")
        return

    n = add_allowed_phones(phones)
    await message.answer(
        f"✅ {n} ta yangi raqam qo'shildi (jami yuklangani: {len(phones)} ta).\n"
        f"Takroriy raqamlar avtomatik o'tkazib yuborildi."
    )


@router.message(Command("list_users"))
async def cmd_list_users(message: Message) -> None:
    users = list_approved_users(limit=100)
    if not users:
        await message.answer("Hech qanday talaba ro'yxatdan o'tmagan.")
        return

    lines = [f"👥 Ro'yxatdan o'tganlar ({len(users)} ta):\n"]
    for tid, phone, first_name, username, _ in users[:50]:
        u = f"@{username}" if username else "—"
        name = first_name or "(ismsiz)"
        lines.append(f"• {name} ({u}) — +{phone} — id={tid}")
    if len(users) > 50:
        lines.append(f"\n... va yana {len(users) - 50} ta")
    await message.answer("\n".join(lines))


@router.message(Command("ban_user"))
async def cmd_ban(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Ishlatish: /ban_user 123456789")
        return
    ban_user(int(parts[1]))
    await message.answer(f"🚫 Banlandi: {parts[1]}")


@router.message(Command("unban_user"))
async def cmd_unban(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Ishlatish: /unban_user 123456789")
        return
    unban_user(int(parts[1]))
    await message.answer(f"✅ Ban olib tashlandi: {parts[1]}")
