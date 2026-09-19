"""Muddati o'tgan talabalarni avtomat chiqarib yuborish.

Bot soatda bir marta tekshiradi va konfiguratsiya qilingan kanal/guruhlardan chiqarib tashlaydi."""
import asyncio
import logging
import time
from datetime import datetime
from typing import List

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from config import CERT_MAX_ISSUES, CERT_OPEN_AFTER_DAYS, KICK_CHAT_IDS
from handlers.utils import safe_send, split_for_telegram
from services.auth import (
    get_expired_users,
    get_setting,
    get_users_to_warn,
    list_reminder_user_ids,
    mark_user_kicked,
    mark_warned,
    set_setting,
)
from services.cert_store import get_users_for_certificate, mark_cert_prompted
from services import watch_store as ws
from services.gemini import daily_watch_comment
from services.report_pdf import build_watch_report
from services.watch_digest import build_digest, take_snapshots, today_key, weekly_summary
from services.support_store import get_unnotified_resolved, mark_notified

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 60 * 60  # har 1 soatda
SUPPORT_NOTIFY_INTERVAL = 60  # texnik yordam javoblarini har 1 daqiqada yetkazadi
WARN_BEFORE_DAYS = 3  # muddat tugashidan necha kun oldin ogohlantirish

# Kunlik eslatma — har kuni shu soatdan keyin (UTC). 14 UTC = 19:00 Toshkent.
DAILY_REMINDER_HOUR_UTC = 14
DAILY_REMINDER_INTERVAL = 30 * 60  # har 30 daqiqada tekshiradi

DAILY_REMINDER_MESSAGE = (
    "📚 Bugungi cheklist:\n\n"
    "✅ Bugungi video darsni ko'rdingizmi?\n"
    "✅ Dars bo'yicha qaydlar/konspekt qildingizmi?\n"
    "✅ Berilgan topshiriqni bajardingizmi?\n"
    "✅ Amaliyot — video tayyorlash ustida ishladingizmi?\n\n"
    "Har kuni 1 qadam tashlang. Savol bo'lsa, botdan 🎓 «Kurs bo'yicha savol» orqali so'rang."
)


EXPIRY_MESSAGE = (
    "📢 Salom!\n\n"
    "Sizning kursdagi ruxsat muddatingiz tugadi. "
    "Shu sababli sizni kurs guruhi va kanalidan chiqarildik.\n\n"
    "Botda qolish va kursni davom ettirmoqchi bo'lsangiz, yana 3 oylik ruxsatni "
    "sotib olishingiz mumkin (💰 490 000 so'm).\n"
    "Ma'lumot uchun qabul bo'limiga murojaat qiling."
)

WARN_MESSAGE = (
    "⏰ Eslatma!\n\n"
    "Sizning kursdagi ruxsat muddatingiz taxminan {days} kundan so'ng tugaydi.\n\n"
    "Botdan va kurs guruhidan foydalanishni davom ettirmoqchi bo'lsangiz, "
    "yana 3 oylik Botda qolishni sotib olishingiz kerak.\n"
    "💰 Narxi: 490 000 so'm\n\n"
    "Botda qolishni uzaytirish uchun +9985551101031 qabul bo'limiga murojaat qiling."
)


async def kick_expired_loop(bot: Bot) -> None:
    """Cheksiz tsikl — har soatda ogohlantiradi va muddati o'tganlarni chiqaradi."""
    while True:
        try:
            await warn_expiring_soon(bot)
            await kick_expired_once(bot)
        except Exception as e:
            logger.exception("Expiry tekshirish xatolik: %s", e)
        try:
            await prompt_certificates(bot)
        except Exception as e:
            logger.exception("Sertifikat taklifi xatolik: %s", e)
        await asyncio.sleep(CHECK_INTERVAL)


async def warn_expiring_soon(bot: Bot) -> int:
    """Muddati yaqinlashganlarni (default 3 kun) ogohlantiradi.
    Faqat MUVAFFAQIYATLI yuborilганda belgilaydi — vaqtinchalik xatoda keyingi soatda qayta uradi."""
    users = get_users_to_warn(WARN_BEFORE_DAYS)
    if not users:
        return 0
    warned = 0
    for telegram_id, phone, first_name, expires_at in users:
        days_left = max(1, round((expires_at - time.time()) / 86400))
        ok = await safe_send(bot, telegram_id, WARN_MESSAGE.format(days=days_left))
        if ok:
            mark_warned(telegram_id, expires_at)  # shu muddat uchun belgilaymiz
            warned += 1
        # ok=False (vaqtinchalik xato) → belgilamaymiz, keyingi soatda qayta urinadi
        await asyncio.sleep(0.1)
    logger.info("Muddat ogohlantirishi yuborildi: %d ta", warned)
    return warned


async def daily_reminder_loop(bot: Bot) -> None:
    """Har kuni bir marta o'quvchilarga cheklist eslatmasini yuboradi."""
    while True:
        try:
            await maybe_send_daily_reminder(bot)
        except Exception as e:
            logger.exception("Kunlik eslatma xatolik: %s", e)
        await asyncio.sleep(DAILY_REMINDER_INTERVAL)


async def maybe_send_daily_reminder(bot: Bot) -> int:
    """Belgilangan soatdan keyin, kuniga faqat bir marta yuboradi (qayta ishga tushsa ham)."""
    now = datetime.utcnow()
    if now.hour < DAILY_REMINDER_HOUR_UTC:
        return 0
    today = now.strftime("%Y-%m-%d")
    if get_setting("last_daily_reminder") == today:
        return 0  # bugun allaqachon yuborilgan

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔕 Eslatmalarni o'chirish", callback_data="reminders:off")
    ]])
    ids = list_reminder_user_ids()
    sent = 0
    for uid in ids:
        try:
            await bot.send_message(uid, DAILY_REMINDER_MESSAGE, reply_markup=kb)
            sent += 1
        except (TelegramBadRequest, TelegramForbiddenError):
            pass  # bloklagan/o'chgan — normal
        except Exception as e:
            logger.warning("Kunlik eslatma yuborilmadi (user=%s): %s", uid, e)
        await asyncio.sleep(0.05)

    set_setting("last_daily_reminder", today)
    logger.info("Kunlik eslatma yuborildi: %d ta", sent)
    return sent


CERT_PROMPT_MESSAGE = (
    "🏆 Tabriklaymiz, {name}!\n\n"
    "Siz kursda {days} kunni tamomladingiz va endi sertifikat olishingiz mumkin.\n\n"
    "Sertifikat olish uchun quyidagi tugmani bosing va sertifikatga yoziladigan "
    "to'liq Ism Familiyangizni yozib yuboring.\n\n"
    "⚠️ Ism Familiyani diqqat bilan, xatosiz yozing. Sertifikat jami {max} marta "
    "beriladi — xato bo'lsa, {max} martagacha to'g'rilab qayta olishingiz mumkin."
)


async def prompt_certificates(bot: Bot) -> int:
    """Botga qo'shilganiga CERT_OPEN_AFTER_DAYS kun to'lgan o'quvchilarga bir marta
    sertifikat olish eslatmasini yuboradi (Ism Familiya yuborish kerakligi bilan)."""
    users = get_users_for_certificate(CERT_OPEN_AFTER_DAYS)
    if not users:
        return 0
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏆 Sertifikatni olish", callback_data="cert:start")
    ]])
    sent = 0
    for telegram_id, phone, first_name, cohort_id in users:
        text = CERT_PROMPT_MESSAGE.format(
            name=first_name or "Talaba", days=CERT_OPEN_AFTER_DAYS, max=CERT_MAX_ISSUES,
        )
        ok = await safe_send(bot, telegram_id, text, reply_markup=kb)
        if ok:
            sent += 1
        # Bloklagan/o'chgan foydalanuvchiga ham belgilaymiz — har soat qayta urinmaslik uchun
        mark_cert_prompted(telegram_id, str(cohort_id))
        await asyncio.sleep(0.1)
    logger.info("Sertifikat eslatmasi yuborildi: %d ta (jami nomzod: %d)", sent, len(users))
    return sent


SUPPORT_RESOLVED_MESSAGE = (
    "🛠 Texnik yordam\n\n"
    "Murojaatingiz ko'rib chiqildi.\n\n"
    "{resolution}"
)


async def support_notifier_loop(bot: Bot) -> None:
    """Hal qilingan texnik yordam ticketlari bo'yicha o'quvchilarga javob yuboradi."""
    while True:
        try:
            await deliver_resolved_tickets(bot)
        except Exception as e:
            logger.exception("Texnik yordam javobi xatolik: %s", e)
        await asyncio.sleep(SUPPORT_NOTIFY_INTERVAL)


async def deliver_resolved_tickets(bot: Bot) -> int:
    """Hal qilingan, lekin xabar berilmagan ticketlarni o'quvchiga yetkazadi."""
    tickets = get_unnotified_resolved()
    if not tickets:
        return 0
    sent = 0
    for t in tickets:
        telegram_id = t.get("telegram_id")
        resolution = (t.get("resolution_text") or "Muammoyingiz bartaraf etildi.").strip()
        text = SUPPORT_RESOLVED_MESSAGE.format(resolution=resolution)
        try:
            await bot.send_message(telegram_id, text)
            sent += 1
        except (TelegramBadRequest, TelegramForbiddenError):
            pass  # bloklagan/o'chgan — normal
        except Exception as e:
            logger.warning("Texnik yordam javobi yuborilmadi (user=%s): %s", telegram_id, e)
        mark_notified(t.get("id"))  # qayta yubormaslik uchun
        await asyncio.sleep(0.1)
    logger.info("Texnik yordam javobi yuborildi: %d ta", sent)
    return sent


async def kick_expired_once(bot: Bot) -> int:
    """Muddati o'tganlarni darhol chiqaradi. Chiqarilgan talabalar sonini qaytaradi."""
    expired = get_expired_users()
    if not expired:
        return 0

    if not KICK_CHAT_IDS:
        logger.warning(
            "%d ta talaba muddati o'tgan, lekin KICK_CHAT_IDS sozlanmagan — chiqarib bo'lmaydi",
            len(expired),
        )
        return 0

    logger.info("%d ta talaba muddati o'tgan, chiqarish boshlanadi", len(expired))
    kicked_count = 0

    for telegram_id, phone, first_name, expires_at in expired:
        success = await _kick_from_all_chats(bot, telegram_id, first_name)
        if success:
            await _notify_user(bot, telegram_id)
            mark_user_kicked(telegram_id, reason="expired")
            kicked_count += 1
        else:
            # Chiqarib bo'lmagan bo'lsa ham DB'dan o'chirib qo'yamiz
            mark_user_kicked(telegram_id, reason="expired_kick_failed")

    return kicked_count


async def _kick_from_all_chats(bot: Bot, user_id: int, first_name: str) -> bool:
    """Barcha konfiguratsiya qilingan chatlardan chiqaradi. Hech bo'lmasa bittada muvaffaqiyatli bo'lsa True."""
    success_anywhere = False
    for chat_id in KICK_CHAT_IDS:
        try:
            await bot.ban_chat_member(chat_id, user_id)
            await asyncio.sleep(1)
            try:
                await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
            except Exception:
                pass
            success_anywhere = True
            logger.info("Chiqarildi: %s (id=%s) chat=%s", first_name or "?", user_id, chat_id)
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            # Talaba allaqachon chatdan chiqib ketgan yoki bot admin emas
            logger.info("Chiqarib bo'lmadi (chat=%s, user=%s): %s", chat_id, user_id, e)
        except Exception as e:
            logger.warning("Kutilmagan xato (chat=%s, user=%s): %s", chat_id, user_id, e)
    return success_anywhere


async def _notify_user(bot: Bot, telegram_id: int) -> None:
    """Talabaga shaxsiy chatda muddat tugagani haqida xabar yuboradi (429 ishlanadi)."""
    await safe_send(bot, telegram_id, EXPIRY_MESSAGE)


# ============================================================
# Raqobatchi kanallar — 7 kunlik kuzatuv (har kuni ertalab)
# ============================================================

WATCH_DIGEST_HOUR_UTC = 4      # 04:00 UTC = 09:00 Toshkent
WATCH_INTERVAL = 30 * 60       # har 30 daqiqada tekshiradi

WATCH_DISCLAIMER = (
    "\n\n🤖 Oybek Bozorov AI yordamchisi analiz qilib berdi. AI adashishi mumkin — "
    "100% ishonmang, raqamlarni o'zingiz ham tekshiring."
)


def _watch_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⏹ Kuzatuvni to'xtatish", callback_data="watch:stop")
    ]])


async def competitor_watch_loop(bot: Bot) -> None:
    """Har kuni belgilangan soatdan keyin faol kuzatuvlar bo'yicha kunlik xabar yuboradi."""
    while True:
        try:
            await run_watch_digests(bot)
        except Exception as e:
            logger.exception("Kuzatuv xabari xatolik: %s", e)
        await asyncio.sleep(WATCH_INTERVAL)


async def run_watch_digests(bot: Bot) -> int:
    if datetime.utcnow().hour < WATCH_DIGEST_HOUR_UTC:
        return 0
    day = today_key()
    watches = [w for w in ws.active_watches() if w.get("last_digest_day") != day]
    # Analiz qilingan kunning o'zida yubormaymiz (baseline shu kuni olingan)
    from datetime import datetime as _dt
    from services.competitor import TASHKENT
    watches = [w for w in watches
               if _dt.fromtimestamp(w["started_at"], TASHKENT).strftime("%Y-%m-%d") != day]
    if not watches:
        return 0

    all_ids = sorted({c["channel_id"] for w in watches for c in w["channels"]})
    try:
        snaps = await asyncio.to_thread(take_snapshots, all_ids, day)
    except Exception as e:
        logger.exception("Kuzatuv snapshot xatosi: %s", e)
        return 0

    sent = 0
    for w in watches:
        tid = w["telegram_id"]
        try:
            facts, text = build_digest(w, snaps, day)
        except Exception as e:
            logger.exception("Kuzatuv digest xatosi (user=%s): %s", tid, e)
            continue
        comment = ""
        try:
            comment = await daily_watch_comment(facts, telegram_id=tid)
        except Exception as e:
            logger.warning("Kuzatuv AI izohi yo'q (user=%s): %s", tid, e)
        full = text + ("\n\n🤖 AI izohi:\n" + comment if comment else "") + WATCH_DISCLAIMER
        parts = split_for_telegram(full)
        ok = True
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            ok = await safe_send(bot, tid, part, reply_markup=_watch_kb() if last else None) and ok
        n = ws.mark_digest_sent(tid, day)
        sent += 1 if ok else 0
        if n >= w["days"]:
            await _finish_watch(bot, w)
        await asyncio.sleep(0.2)
    logger.info("Kuzatuv xabarlari yuborildi: %d ta", sent)
    return sent


async def _finish_watch(bot: Bot, w: dict) -> None:
    """7-kun: yakuniy PDF hisobot va kuzatuvni yopish."""
    tid = w["telegram_id"]
    try:
        channel_rows, day_rows, facts = weekly_summary(w)
        try:
            ai_text = await daily_watch_comment(facts, telegram_id=tid, weekly=True)
        except Exception:
            ai_text = ""
        pdf = await asyncio.to_thread(build_watch_report, "O'quvchi", w.get("niche") or "", channel_rows, day_rows, ai_text)
        await bot.send_document(
            tid, BufferedInputFile(pdf, filename="kuzatuv_7kun.pdf"),
            caption="📘 7 kunlik kuzatuv yakunlandi — yakuniy hisobot.\n"
                    "Yangi kuzatuv uchun menyudan «Raqobatchi kanallar analizi» ni qayta bosing." + WATCH_DISCLAIMER,
        )
    except Exception as e:
        logger.exception("Yakuniy kuzatuv hisoboti xatosi (user=%s): %s", tid, e)
        await safe_send(bot, tid, "📘 7 kunlik kuzatuv yakunlandi." + WATCH_DISCLAIMER)
    ws.stop_watch(tid)
