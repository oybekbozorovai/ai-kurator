"""YouTube xizmatlari — Kanal SEO, Video SEO, Avatar, Banner, Thumbnail + Tarix.

Barcha handlerlar FSM holat bilan filtrlangan — shuning uchun mentor botning
matn ushlovchi handlerlariga halaqit bermaydi (youtube router private'dan oldin
ulanadi: bot.py ga qarang).
"""

import logging

from aiogram import F, Router
from aiogram.enums import ChatType, ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from config import ADMIN_USER_IDS, DAILY_IMAGE_LIMIT, DAILY_TEXT_LIMIT
from handlers.utils import split_for_telegram
from keyboards import (
    MENU_TEXT,
    history_kb,
    home_kb,
    main_menu_kb,
    thumb_color_kb,
    thumb_position_kb,
)
from services.auth import is_user_approved
from services.gemini import (
    generate_channel_seo,
    generate_image_prompt,
    generate_video_seo,
)
from services.history import count_today, get_history, get_item, log_generation
from services.image_service import add_text_to_thumbnail, resize_image
from services.replicate_service import generate_image

logger = logging.getLogger(__name__)
router = Router(name="youtube")
router.message.filter(F.chat.type == ChatType.PRIVATE)


# --- FSM holatlari ---
class YT(StatesGroup):
    channel = State()         # kanal SEO — mavzu kutilmoqda
    video = State()           # video SEO — mavzu kutilmoqda
    avatar = State()          # avatar — tavsif kutilmoqda
    banner = State()          # banner — ma'lumot kutilmoqda
    thumb_topic = State()     # thumbnail — video mavzusi
    thumb_text = State()      # thumbnail — ustki matn
    thumb_position = State()  # thumbnail — matn joylashuvi
    thumb_color = State()     # thumbnail — matn rangi


ERROR_TEXT = (
    "😔 Kechirasiz, biroz kuting va qayta urinib ko'ring.\n"
    "Muammo takrorlansa — keyinroq harakat qiling."
)

# Har bir natija ostiga qo'shiladigan qadamba-qadam qo'llanma
GUIDE = {
    "channel_seo": (
        "\n\n📍 Qayerga qo'yiladi:\n"
        "YouTube Studio → Sozlash (Customization) → Asosiy ma'lumot:\n"
        "• Nomni — kanal nomiga\n"
        "• Tavsifni — kanal tavsifi maydoniga\n"
        "• Kalit so'zlarni — Sozlamalar → Kanal → Kalit so'zlar"
    ),
    "video_seo": (
        "\n\n📍 Qayerga qo'yiladi:\n"
        "YouTube Studio → Kontent → videoni oching → Tafsilotlar:\n"
        "• Nom va tavsifni tegishli maydonlarga\n"
        "• Teglarni — 'Ko'proq' bo'limidagi 'Teglar' ga"
    ),
    "avatar": "\n\n📍 Qo'yish: Studio → Sozlash → Brending → Rasm (Picture)",
    "banner": "\n\n📍 Qo'yish: Studio → Sozlash → Brending → Banner rasm",
    "thumbnail": "\n\n📍 Qo'yish: Studio → Kontent → video → Thumbnail → 'Faylni yuklash'",
}


# ============================================================
# Yordamchi funksiyalar
# ============================================================

def _is_allowed(user_id: int) -> bool:
    """O'quvchi tasdiqlangan yoki admin."""
    return user_id in ADMIN_USER_IDS or is_user_approved(user_id)


def _check_limit(user_id: int, kind: str):
    """Kunlik limit tekshiruvi. (ruxsat_bormi, qolgan_son) qaytaradi."""
    if user_id in ADMIN_USER_IDS:
        return True, -1  # adminlarga limit yo'q
    limit = DAILY_IMAGE_LIMIT if kind == "image" else DAILY_TEXT_LIMIT
    used = count_today(user_id, kind)
    return used < limit, limit - used


async def _send_text_result(message: Message, text: str) -> None:
    """Uzun matnli natijani bo'lib yuboradi, oxiriga 🏠 tugma qo'yadi."""
    parts = split_for_telegram(text)
    for i, part in enumerate(parts):
        await message.answer(
            part, reply_markup=home_kb() if i == len(parts) - 1 else None
        )


async def _deny_limit(callback: CallbackQuery, kind: str) -> None:
    """Limit tugaganini bildiradi."""
    limit = DAILY_IMAGE_LIMIT if kind == "image" else DAILY_TEXT_LIMIT
    word = "rasm" if kind == "image" else "SEO"
    await callback.answer(
        f"Bugungi limit tugadi (kuniga {limit} ta {word}). Ertaga urinib ko'ring.",
        show_alert=True,
    )


# ============================================================
# Navigatsiya
# ============================================================

@router.callback_query(F.data == "nav:home")
async def go_home(callback: CallbackQuery, state: FSMContext) -> None:
    """Bosh menyuga qaytaradi, FSM holatini tozalaydi."""
    await state.clear()
    try:
        await callback.message.edit_text(MENU_TEXT, reply_markup=main_menu_kb())
    except Exception:
        # Rasm xabarini tahrirlab bo'lmaydi — yangi xabar yuboramiz
        await callback.message.answer(MENU_TEXT, reply_markup=main_menu_kb())
    await callback.answer()


# ============================================================
# Kanal SEO
# ============================================================

@router.callback_query(F.data == "menu:channel_seo")
async def channel_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    ok, _ = _check_limit(callback.from_user.id, "text")
    if not ok:
        await _deny_limit(callback, "text")
        return
    await state.set_state(YT.channel)
    await callback.message.edit_text(
        "📺 Kanal SEO\n\n"
        "Kanalingiz qaysi mavzuda?\n"
        "Masalan: oshxona retsepti, IT ta'lim, sport, sayohat",
        reply_markup=home_kb(),
    )
    await callback.answer()


@router.message(YT.channel, F.text & ~F.text.startswith("/"))
async def channel_process(message: Message, state: FSMContext) -> None:
    niche = message.text.strip()
    waiting = await message.answer("⏳ Kanal SEO tayyorlanmoqda...")
    try:
        data = await generate_channel_seo(niche)
    except Exception:
        logger.exception("Kanal SEO xatosi")
        await waiting.edit_text(ERROR_TEXT, reply_markup=home_kb())
        return

    names = data.get("names", [])
    description = data.get("description", "")
    keywords = data.get("keywords", [])

    lines = ["📺 Kanal SEO tayyor!\n", "📌 Kanal nomi variantlari:"]
    for i, name in enumerate(names, 1):
        lines.append(f"{i}. {name}")
    lines.append("\n📝 Kanal tavsifi:")
    lines.append(str(description))
    lines.append("\n🔑 Kalit so'zlar:")
    lines.append(", ".join(str(k) for k in keywords))
    result = "\n".join(lines) + GUIDE["channel_seo"]

    log_generation(message.from_user.id, "channel_seo", "text",
                   label=niche, result_type="text", result_text=result)
    await state.clear()
    await waiting.delete()
    await _send_text_result(message, result)


# ============================================================
# Video SEO
# ============================================================

@router.callback_query(F.data == "menu:video_seo")
async def video_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    ok, _ = _check_limit(callback.from_user.id, "text")
    if not ok:
        await _deny_limit(callback, "text")
        return
    await state.set_state(YT.video)
    await callback.message.edit_text(
        "🎬 Video SEO\n\n"
        "Video mavzusi nima? Qisqa tasvirlab bering.\n"
        "Masalan: yangi telefon ko'rib chiqish va taqqoslash",
        reply_markup=home_kb(),
    )
    await callback.answer()


@router.message(YT.video, F.text & ~F.text.startswith("/"))
async def video_process(message: Message, state: FSMContext) -> None:
    topic = message.text.strip()
    waiting = await message.answer("⏳ Video SEO tayyorlanmoqda...")
    try:
        data = await generate_video_seo(topic)
    except Exception:
        logger.exception("Video SEO xatosi")
        await waiting.edit_text(ERROR_TEXT, reply_markup=home_kb())
        return

    titles = data.get("titles", [])
    description = data.get("description", "")
    tags = data.get("tags", [])

    lines = ["🎬 Video SEO tayyor!\n", "📌 Video nomi variantlari:"]
    for i, title in enumerate(titles, 1):
        lines.append(f"{i}. {title}")
    lines.append("\n📝 Video opisaniyesi:")
    lines.append(str(description))
    lines.append("\n🏷 Teglar:")
    lines.append(", ".join(str(t) for t in tags))
    result = "\n".join(lines) + GUIDE["video_seo"]

    log_generation(message.from_user.id, "video_seo", "text",
                   label=topic, result_type="text", result_text=result)
    await state.clear()
    await waiting.delete()
    await _send_text_result(message, result)


# ============================================================
# Avatar
# ============================================================

@router.callback_query(F.data == "menu:avatar")
async def avatar_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    ok, _ = _check_limit(callback.from_user.id, "image")
    if not ok:
        await _deny_limit(callback, "image")
        return
    await state.set_state(YT.avatar)
    await callback.message.edit_text(
        "🖼 Avatar yaratish\n\n"
        "Kanalingiz nima haqida? Qanday uslub kerak?\n"
        "Masalan: IT ta'lim kanali, minimalistik, ko'k ranglarda",
        reply_markup=home_kb(),
    )
    await callback.answer()


@router.message(YT.avatar, F.text & ~F.text.startswith("/"))
async def avatar_process(message: Message, state: FSMContext) -> None:
    description = message.text.strip()
    waiting = await message.answer("⏳ Rasm uchun prompt tayyorlanmoqda...")
    try:
        prompt = await generate_image_prompt(description, kind="avatar")
        await waiting.edit_text("🎨 Avatar chizilmoqda... (30-60 soniya)")
        image = await generate_image(prompt, aspect_ratio="1:1")
        image = resize_image(image, 1024, 1024)
    except Exception:
        logger.exception("Avatar yaratish xatosi")
        await waiting.edit_text(ERROR_TEXT, reply_markup=home_kb())
        return

    await state.clear()
    await waiting.delete()
    sent = await message.answer_document(
        BufferedInputFile(image, filename="avatar.png"),
        caption="✅ Avataringiz tayyor! (1024x1024)" + GUIDE["avatar"],
        reply_markup=home_kb(),
    )
    log_generation(message.from_user.id, "avatar", "image",
                   label=f"Avatar — {description[:30]}",
                   result_type="file", file_id=sent.document.file_id)


# ============================================================
# Banner
# ============================================================

@router.callback_query(F.data == "menu:banner")
async def banner_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    ok, _ = _check_limit(callback.from_user.id, "image")
    if not ok:
        await _deny_limit(callback, "image")
        return
    await state.set_state(YT.banner)
    await callback.message.edit_text(
        "🎨 Banner yaratish\n\n"
        "Kanal nomi va mavzusini yozing.\n"
        "Masalan: \"IT Akademiya\" — dasturlash kanali",
        reply_markup=home_kb(),
    )
    await callback.answer()


@router.message(YT.banner, F.text & ~F.text.startswith("/"))
async def banner_process(message: Message, state: FSMContext) -> None:
    info = message.text.strip()
    waiting = await message.answer("⏳ Rasm uchun prompt tayyorlanmoqda...")
    try:
        prompt = await generate_image_prompt(info, kind="banner")
        await waiting.edit_text("🎨 Banner chizilmoqda... (30-60 soniya)")
        image = await generate_image(prompt, aspect_ratio="16:9")
        image = resize_image(image, 2560, 1440)
    except Exception:
        logger.exception("Banner yaratish xatosi")
        await waiting.edit_text(ERROR_TEXT, reply_markup=home_kb())
        return

    await state.clear()
    await waiting.delete()
    sent = await message.answer_document(
        BufferedInputFile(image, filename="banner.png"),
        caption="✅ Banneringiz tayyor! (2560x1440)" + GUIDE["banner"],
        reply_markup=home_kb(),
    )
    log_generation(message.from_user.id, "banner", "image",
                   label=f"Banner — {info[:30]}",
                   result_type="file", file_id=sent.document.file_id)


# ============================================================
# Thumbnail (ko'p bosqichli)
# ============================================================

@router.callback_query(F.data == "menu:thumbnail")
async def thumb_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    ok, _ = _check_limit(callback.from_user.id, "image")
    if not ok:
        await _deny_limit(callback, "image")
        return
    await state.set_state(YT.thumb_topic)
    await callback.message.edit_text(
        "🌅 Thumbnail yaratish\n\n"
        "1️⃣ Video nima haqida? Qisqa tasvirlab bering.\n"
        "Masalan: internetdan pul topish bo'yicha qo'llanma",
        reply_markup=home_kb(),
    )
    await callback.answer()


@router.message(YT.thumb_topic, F.text & ~F.text.startswith("/"))
async def thumb_get_topic(message: Message, state: FSMContext) -> None:
    await state.update_data(topic=message.text.strip())
    await state.set_state(YT.thumb_text)
    await message.answer(
        "2️⃣ Thumbnail ustiga qanday matn yozilsin?\n"
        "Masalan: 1000$ TOPDIM",
        reply_markup=home_kb(),
    )


@router.message(YT.thumb_text, F.text & ~F.text.startswith("/"))
async def thumb_get_text(message: Message, state: FSMContext) -> None:
    await state.update_data(overlay=message.text.strip())
    await state.set_state(YT.thumb_position)
    await message.answer(
        "3️⃣ Matn rasmda qayerda joylashsin?",
        reply_markup=thumb_position_kb(),
    )


@router.callback_query(YT.thumb_position, F.data.startswith("thumb:pos:"))
async def thumb_get_position(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(position=callback.data.split(":")[-1])
    await state.set_state(YT.thumb_color)
    await callback.message.edit_text(
        "4️⃣ Matn rangi qanday bo'lsin?",
        reply_markup=thumb_color_kb(),
    )
    await callback.answer()


@router.callback_query(YT.thumb_color, F.data.startswith("thumb:color:"))
async def thumb_generate(callback: CallbackQuery, state: FSMContext) -> None:
    color = callback.data.split(":")[-1]
    data = await state.get_data()
    topic = data.get("topic", "")
    overlay = data.get("overlay", "")
    position = data.get("position", "bottom")

    await callback.answer()
    await callback.message.edit_text("🎨 Thumbnail yaratilmoqda... (30-60 soniya)")

    try:
        prompt = await generate_image_prompt(topic, kind="thumbnail")
        image = await generate_image(prompt, aspect_ratio="16:9")
        image = resize_image(image, 1280, 720)
        final = add_text_to_thumbnail(image, overlay, position, color)
    except Exception:
        logger.exception("Thumbnail yaratish xatosi")
        await callback.message.edit_text(ERROR_TEXT, reply_markup=home_kb())
        return

    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass

    sent = await callback.message.answer_document(
        BufferedInputFile(final, filename="thumbnail.png"),
        caption=f"✅ Thumbnail tayyor! (1280x720)\nMatn: «{overlay}»" + GUIDE["thumbnail"],
        reply_markup=home_kb(),
    )
    log_generation(callback.from_user.id, "thumbnail", "image",
                   label=f"Thumbnail — {topic[:30]}",
                   result_type="file", file_id=sent.document.file_id)


# ============================================================
# Mening ishlarim (tarix)
# ============================================================

@router.callback_query(F.data == "menu:history")
async def history_show(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    await state.clear()
    items = get_history(callback.from_user.id, limit=10)
    if not items:
        await callback.message.edit_text(
            "📂 Mening ishlarim\n\nHozircha hech narsa yaratmagansiz.",
            reply_markup=home_kb(),
        )
    else:
        await callback.message.edit_text(
            "📂 Mening ishlarim\n\n"
            "Oxirgi ishlaringiz — ko'rish uchun bosing 👇",
            reply_markup=history_kb(items),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("hist:"))
async def history_open(callback: CallbackQuery) -> None:
    """Tarixdagi bitta ishni qayta yuboradi."""
    item_id = int(callback.data.split(":")[-1])
    item = get_item(item_id)
    await callback.answer()

    # item: (id, telegram_id, service, kind, label, result_type, result_text, file_id)
    if not item or item[1] != callback.from_user.id:
        await callback.message.answer("⚠️ Bu ish topilmadi.", reply_markup=home_kb())
        return

    result_type, result_text, file_id = item[5], item[6], item[7]
    if result_type == "file" and file_id:
        await callback.message.answer_document(
            file_id, caption="📂 Saqlangan ish", reply_markup=home_kb()
        )
    else:
        await _send_text_result(callback.message, result_text or "(bo'sh)")
