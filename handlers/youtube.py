"""YouTube xizmatlari — Kanal SEO, Video SEO, Avatar, Banner, Thumbnail + Tarix.

Barcha handlerlar FSM holat bilan filtrlangan — shuning uchun mentor botning
matn ushlovchi handlerlariga halaqit bermaydi (youtube router private'dan oldin
ulanadi: bot.py ga qarang).
"""

import asyncio
import html
import io
import logging

from aiogram import F, Router
from aiogram.enums import ChatType, ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from config import DAILY_IMAGE_LIMIT, DAILY_TEXT_LIMIT, FLUX_MODEL, FLUX_REDUX_MODEL
from handlers.utils import split_for_telegram
from keyboards import (
    MENU_TEXT,
    history_kb,
    home_kb,
    main_menu_kb,
    thumb_color_kb,
    thumb_position_kb,
    thumb_skip_kb,
    video_seo_menu_kb,
)
from video_seo_presets import VIDEO_SEO_PRESETS, format_preset
from services.auth import is_admin, is_user_approved
from services.gemini import (
    analyze_channel,
    generate_banner_imagen,
    generate_channel_seo,
    generate_image_prompt,
    generate_video_seo,
)
from services.youtube_api import fetch_channel_analysis, is_configured as yt_api_ready
from services.history import count_today, get_history, get_item, log_generation
from services import usage
from services.image_service import add_text_to_thumbnail, resize_image, overlay_banner_frame, add_banner_text
from services.replicate_service import generate_image, generate_img2img, generate_banner_image

logger = logging.getLogger(__name__)
router = Router(name="youtube")
router.message.filter(F.chat.type == ChatType.PRIVATE)


# --- FSM holatlari ---
class YT(StatesGroup):
    channel_analysis = State()  # kanal analizi — kanal linki kutilmoqda
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
    """O'quvchi tasdiqlangan yoki admin (asosiy/yordamchi)."""
    return is_admin(user_id) or is_user_approved(user_id)


def _check_limit(user_id: int, kind: str):
    """Kunlik limit tekshiruvi. (ruxsat_bormi, qolgan_son) qaytaradi."""
    if is_admin(user_id):
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


def _seo_copyable_html(name: str, titles: list, description: str, tags: list) -> str:
    """Video SEO natijasi — nom/opisaniye/teglar alohida HTML bloklar ichida.
    Telegram'da har blokni bosganda matn nusxalanadi. parse_mode='HTML' bilan yuboriladi."""
    parts = [f"🎬 {html.escape(name)}\n"]

    titles = titles or []
    if titles:
        if len(titles) == 1:
            parts.append("📌 Video nomi (bosib nusxalang):")
        else:
            parts.append("📌 Video nomi variantlari (har birini bosib nusxalang):")
        for t in titles:
            parts.append(f"<code>{html.escape(str(t))}</code>")

    if description:
        parts.append("\n📝 Opisaniye (bosib nusxalang):")
        parts.append(f"<pre>{html.escape(str(description))}</pre>")

    if tags:
        parts.append("\n🏷 Teglar (bosib nusxalang):")
        tag_str = ", ".join(str(t) for t in tags)
        parts.append(f"<pre>{html.escape(tag_str)}</pre>")

    return "\n".join(parts) + GUIDE["video_seo"]


async def _send_seo_html(message: Message, text: str) -> None:
    """Nusxalanadigan SEO natijasini HTML rejimda yuboradi, 🏠 tugma bilan."""
    await message.answer(text, parse_mode="HTML", reply_markup=home_kb())


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


GUIDE_TEXT = (
    "ℹ️ Botdan foydalanish qo'llanmasi\n\n"
    "Har bir tugma nima uchun:\n\n"
    "🎓 Kurs bo'yicha savol — dars, vazifa yoki kurs mavzusi bo'yicha savolingizga "
    "AI darrov javob beradi.\n\n"
    "🔍 Kanal analizi — YouTube kanal havolasini yuborsangiz, uning strategiyasi va "
    "kuchli/zaif tomonlarini tahlil qiladi.\n\n"
    "📺 Kanal SEO — kanalingiz mavzusini yozsangiz, kanal nomi, tavsif va kalit so'zlar "
    "bo'yicha tavsiya beradi.\n\n"
    "🎬 Video SEO — video uchun sarlavha, tavsif va teglar yozib beradi "
    "(tayyor yo'nalishlar yoki o'zingiznikini).\n\n"
    "🖼 Avatar yaratish — kanal uchun profil rasm (avatar) chizadi.\n\n"
    "🎨 Banner yaratish — kanal shapkasi (banner) chizadi.\n\n"
    "🌅 Thumbnail yaratish — video uchun cover rasm. Mavzu yozasiz yoki namuna rasm "
    "yuborasiz.\n\n"
    "📂 Mening ishlarim — ilgari yaratgan ishlaringiz tarixi.\n\n"
    "🛠 Texnik yordam — bot, mini-app yoki login ishlamasa, muammoni shu yerga yozing "
    "(skrinshot bilan).\n\n"
    "Qaytish uchun 🏠 Bosh menyu tugmasini bosing."
)


@router.callback_query(F.data == "menu:guide")
async def show_guide(callback: CallbackQuery, state: FSMContext) -> None:
    """Botdan foydalanish qo'llanmasi — har bir tugma vazifasi."""
    await state.clear()
    try:
        await callback.message.edit_text(GUIDE_TEXT, reply_markup=home_kb())
    except Exception:
        await callback.message.answer(GUIDE_TEXT, reply_markup=home_kb())
    await callback.answer()


# ============================================================
# Kanal analizi (YouTube Data API)
# ============================================================

@router.callback_query(F.data == "menu:channel_analysis")
async def analysis_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    if not yt_api_ready():
        await callback.answer(
            "🔍 Kanal analizi tez orada ishga tushadi. Biroz kuting!",
            show_alert=True,
        )
        return
    ok, _ = _check_limit(callback.from_user.id, "text")
    if not ok:
        await _deny_limit(callback, "text")
        return
    await state.set_state(YT.channel_analysis)
    await callback.message.edit_text(
        "🔍 Kanal analizi\n\n"
        "Tahlil qilmoqchi bo'lgan YouTube kanal havolasini yuboring.\n"
        "Masalan:\n"
        "• https://youtube.com/@MrBeast\n"
        "• @MrBeast\n"
        "• kanal ID (UC...)\n\n"
        "Bot kanalning strategiyasini, yuklash jadvalini, sarlavhalari, "
        "teglari va oblojkalarini tahlil qilib beradi.",
        reply_markup=home_kb(),
    )
    await callback.answer()


@router.message(YT.channel_analysis, F.text & ~F.text.startswith("/"))
async def analysis_process(message: Message, state: FSMContext) -> None:
    link = message.text.strip()
    waiting = await message.answer("🔍 Kanal tahlil qilinmoqda... (10-30 soniya)")
    try:
        data = await asyncio.to_thread(fetch_channel_analysis, link)
    except Exception:
        logger.exception("Kanal analizi — fetch xatosi")
        data = None

    if not data:
        await waiting.edit_text(
            "❌ Bu kanalni topa olmadim.\n\n"
            "Havolani tekshiring (to'liq link yoki @handle bo'lsin) va qayta "
            "urinib ko'ring.",
            reply_markup=home_kb(),
        )
        return

    if not data.get("videos"):
        await waiting.edit_text(
            "⚠️ Kanal topildi, lekin tahlil uchun ochiq videolar topilmadi.\n"
            "Boshqa kanal bilan urinib ko'ring.",
            reply_markup=home_kb(),
        )
        return

    try:
        await waiting.edit_text("🧠 Strategiya tayyorlanmoqda...")
        analysis = await analyze_channel(data, telegram_id=message.from_user.id)
    except Exception:
        logger.exception("Kanal analizi — Gemini xatosi")
        await waiting.edit_text(ERROR_TEXT, reply_markup=home_kb())
        return

    header = (
        f"🔍 {data.get('title', 'Kanal')} — tahlil\n"
        f"👥 Obunachilar: {data.get('subscribers', 0):,}\n"
        f"🎬 Videolar: {data.get('video_count', 0):,}\n"
        f"👁 Umumiy ko'rishlar: {data.get('views', 0):,}\n"
        f"{'─' * 20}\n\n"
    )
    result = header + analysis

    log_generation(message.from_user.id, "channel_analysis", "text",
                   label=data.get("title", link)[:40],
                   result_type="text", result_text=result)
    await state.clear()
    await waiting.delete()
    await _send_text_result(message, result)


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
        "Masalan: BeamNG Drive avariya videolari, mashina simulyatori",
        reply_markup=home_kb(),
    )
    await callback.answer()


@router.message(YT.channel, F.text & ~F.text.startswith("/"))
async def channel_process(message: Message, state: FSMContext) -> None:
    niche = message.text.strip()
    waiting = await message.answer("⏳ Kanal SEO tayyorlanmoqda...")
    try:
        data = await generate_channel_seo(niche, telegram_id=message.from_user.id)
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
    """Video SEO — tayyor yo'nalishlar menyusini ko'rsatadi."""
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "🎬 Video SEO\n\n"
        "Tayyor yo'nalishlardan birini tanlang yoki o'z mavzungiz uchun "
        "AI'ga SEO qildiring 👇",
        reply_markup=video_seo_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("vseo:") & ~(F.data == "vseo:custom"))
async def video_preset(callback: CallbackQuery, state: FSMContext) -> None:
    """Tayyor yo'nalishni ko'rsatadi — AI ishlatilmaydi, limit yo'q."""
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    key = callback.data.split(":", 1)[1]
    preset = VIDEO_SEO_PRESETS.get(key)
    if not preset:
        await callback.answer("Yo'nalish topilmadi.", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    plain = format_preset(preset) + GUIDE["video_seo"]  # tarix uchun toza matn
    log_generation(callback.from_user.id, "video_seo", "text",
                   label=preset["name"], result_type="text", result_text=plain)
    try:
        await callback.message.delete()
    except Exception:
        pass
    html_result = _seo_copyable_html(
        preset["name"], preset.get("titles", []),
        preset.get("description", ""), preset.get("tags", []),
    )
    await _send_seo_html(callback.message, html_result)


@router.callback_query(F.data == "vseo:custom")
async def video_custom(callback: CallbackQuery, state: FSMContext) -> None:
    """O'z yo'nalishi — mavzu so'raydi, AI generatsiya qiladi."""
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    ok, _ = _check_limit(callback.from_user.id, "text")
    if not ok:
        await _deny_limit(callback, "text")
        return
    await state.set_state(YT.video)
    await callback.message.edit_text(
        "🎬 Video SEO — o'z yo'nalishingiz\n\n"
        "Video mavzusi nima? Qisqa tasvirlab bering.\n"
        "Masalan: BeamNG Drive da yuqori tezlikdagi avariyalar",
        reply_markup=home_kb(),
    )
    await callback.answer()


@router.message(YT.video, F.text & ~F.text.startswith("/"))
async def video_process(message: Message, state: FSMContext) -> None:
    topic = message.text.strip()
    waiting = await message.answer("⏳ Video SEO tayyorlanmoqda...")
    try:
        data = await generate_video_seo(topic, telegram_id=message.from_user.id)
    except Exception:
        logger.exception("Video SEO xatosi")
        await waiting.edit_text(ERROR_TEXT, reply_markup=home_kb())
        return

    titles = data.get("titles", [])
    description = data.get("description", "")
    tags = data.get("tags", [])

    # Tarix uchun toza matn
    lines = ["🎬 Video SEO tayyor!\n", "📌 Video nomi variantlari:"]
    for i, title in enumerate(titles, 1):
        lines.append(f"{i}. {title}")
    lines.append("\n📝 Video opisaniyesi:")
    lines.append(str(description))
    lines.append("\n🏷 Teglar:")
    lines.append(", ".join(str(t) for t in tags))
    plain = "\n".join(lines) + GUIDE["video_seo"]

    log_generation(message.from_user.id, "video_seo", "text",
                   label=topic, result_type="text", result_text=plain)
    await state.clear()
    await waiting.delete()
    html_result = _seo_copyable_html(topic, titles, description, tags)
    await _send_seo_html(message, html_result)


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
        "Masalan: BeamNG Drive o'yin kanali, dramatik, qizil-qora ranglarda",
        reply_markup=home_kb(),
    )
    await callback.answer()


@router.message(YT.avatar, F.text & ~F.text.startswith("/"))
async def avatar_process(message: Message, state: FSMContext) -> None:
    description = message.text.strip()
    waiting = await message.answer("⏳ Rasm uchun prompt tayyorlanmoqda...")
    try:
        prompt = await generate_image_prompt(
            description, kind="avatar", telegram_id=message.from_user.id)
        await waiting.edit_text("🎨 Avatar chizilmoqda... (30-60 soniya)")
        image = await generate_image(prompt, aspect_ratio="1:1")
        image = resize_image(image, 1024, 1024)
        usage.record(message.from_user.id, "avatar", kind="image", model=FLUX_MODEL)
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

_BANNER_TEMPLATE = (
    "A vibrant 3D-rendered YouTube gaming channel banner. "
    "A detailed sports car being transported on a flatbed tow truck along a winding country road, "
    "surrounded by rolling green hills, scattered potholes, and a bright blue sky with soft clouds. "
    "Dynamic dust clouds, motion blur, and flying debris convey high-speed movement and physics-driven energy. "
    "Cinematic lighting, ultra-sharp details, colorful and energetic atmosphere. "
    "CRITICAL COMPOSITION: all main subjects must be in the CENTER HORIZONTAL BAND (middle third of height). "
    "Top ~38% and bottom ~38%: background/sky/environment only. "
    "Optimized for 2560x1440 pixels, important content centered within safe area 1546x423 pixels. "
    "NO text, NO words, NO letters in the image."
)


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
        "Kanal nomingizni yozing — banner tayyor bo'ladi.\n\n"
        "Masalan: Oybek BeamNG",
        reply_markup=home_kb(),
    )
    await callback.answer()


@router.message(YT.banner, F.text & ~F.text.startswith("/"))
async def banner_process(message: Message, state: FSMContext) -> None:
    channel_name = message.text.strip()
    waiting = await message.answer("🎨 Banner chizilmoqda... (30-60 soniya)")
    try:
        image = await generate_banner_imagen(_BANNER_TEMPLATE)
        image = resize_image(image, 2560, 1440)
        image = add_banner_text(image, channel_name)
        usage.record(message.from_user.id, "banner", kind="image", model=FLUX_MODEL)
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
        "1️⃣ Ikki yo'l bor:\n"
        "• Video mavzusini YOZING — bot rasm chizadi\n"
        "• YOKI namuna RASM yuboring — bot unga o'xshash rasm chizadi\n\n"
        "Mavzu yozing yoki namuna rasm yuboring 👇",
        reply_markup=home_kb(),
    )
    await callback.answer()


_TEXT_STEP = (
    "2️⃣ Thumbnail ustiga qanday matn yozilsin?\n"
    "Masalan: RASMDAGI MATN\n\n"
    "Matn kerak bo'lmasa — pastdagi tugmani bosing."
)


@router.message(YT.thumb_topic, F.text & ~F.text.startswith("/"))
async def thumb_get_topic(message: Message, state: FSMContext) -> None:
    """O'quvchi mavzu yozdi — AI rasm chizadi."""
    await state.update_data(topic=message.text.strip(), mode="ai")
    await state.set_state(YT.thumb_text)
    await message.answer(_TEXT_STEP, reply_markup=thumb_skip_kb())


@router.message(YT.thumb_topic, F.photo)
async def thumb_get_photo(message: Message, state: FSMContext) -> None:
    """O'quvchi namuna rasm yubordi — unga o'xshash rasm chizamiz."""
    file_id = message.photo[-1].file_id  # eng katta o'lchamdagisi
    await state.update_data(mode="upload", photo_file_id=file_id, topic="O'z rasmi")
    await state.set_state(YT.thumb_text)
    await message.answer(
        "✅ Namuna rasm qabul qilindi — unga o'xshash rasm chizaman.\n\n" + _TEXT_STEP,
        reply_markup=thumb_skip_kb(),
    )


@router.message(YT.thumb_text, F.text & ~F.text.startswith("/"))
async def thumb_get_text(message: Message, state: FSMContext) -> None:
    await state.update_data(overlay=message.text.strip())
    await state.set_state(YT.thumb_position)
    await message.answer(
        "3️⃣ Matn rasmda qayerda joylashsin?",
        reply_markup=thumb_position_kb(),
    )


@router.callback_query(YT.thumb_text, F.data == "thumb:notext")
async def thumb_skip_text(callback: CallbackQuery, state: FSMContext) -> None:
    """Matnsiz — to'g'ridan-to'g'ri rasm yaratiladi."""
    await state.update_data(overlay="")
    await callback.answer()
    await _make_thumbnail(callback, state)


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
    await state.update_data(color=callback.data.split(":")[-1])
    await callback.answer()
    await _make_thumbnail(callback, state)


async def _make_thumbnail(callback: CallbackQuery, state: FSMContext) -> None:
    """Thumbnail rasmini yaratadi/oladi, matn bo'lsa qo'shadi va yuboradi."""
    data = await state.get_data()
    topic = data.get("topic", "")
    mode = data.get("mode", "ai")
    photo_file_id = data.get("photo_file_id")
    overlay = data.get("overlay", "")
    position = data.get("position", "bottom")
    color = data.get("color", "yellow")

    await callback.message.edit_text("🎨 Thumbnail yaratilmoqda...")
    try:
        if mode == "upload" and photo_file_id:
            # Namuna rasm — yuklab olib, ~70% o'xshash yangi rasm chizamiz
            buf = io.BytesIO()
            await callback.bot.download(photo_file_id, destination=buf)
            image = await generate_img2img(buf.getvalue())
            usage.record(callback.from_user.id, "thumbnail", kind="image",
                         model=FLUX_REDUX_MODEL)
        else:
            # AI rasm chizadi
            prompt = await generate_image_prompt(
                topic, kind="thumbnail", telegram_id=callback.from_user.id)
            image = await generate_image(prompt, aspect_ratio="16:9")
            usage.record(callback.from_user.id, "thumbnail", kind="image",
                         model=FLUX_MODEL)
        image = resize_image(image, 1280, 720)
        if overlay:  # matn faqat kerak bo'lsa qo'shiladi
            image = add_text_to_thumbnail(image, overlay, position, color)
    except Exception:
        logger.exception("Thumbnail yaratish xatosi")
        await callback.message.edit_text(ERROR_TEXT, reply_markup=home_kb())
        return

    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass

    caption = "✅ Thumbnail tayyor! (1280x720)"
    if overlay:
        caption += f"\nMatn: «{overlay}»"
    caption += GUIDE["thumbnail"]
    sent = await callback.message.answer_document(
        BufferedInputFile(image, filename="thumbnail.png"),
        caption=caption,
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
