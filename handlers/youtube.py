"""YouTube xizmatlari — Kanal SEO, Video SEO, Avatar, Banner, Thumbnail + Tarix.

Barcha handlerlar FSM holat bilan filtrlangan — shuning uchun mentor botning
matn ushlovchi handlerlariga halaqit bermaydi (youtube router private'dan oldin
ulanadi: bot.py ga qarang).
"""

import asyncio
import html
import io
import logging
import re
from datetime import datetime

from aiogram import F, Router
from aiogram.enums import ChatType, ChatAction
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import DAILY_IMAGE_LIMIT, DAILY_TEXT_LIMIT, FLUX_MODEL, NANO_BANANA_MODEL
from handlers.utils import split_for_telegram
from keyboards import (
    MENU_TEXT,
    history_kb,
    home_kb,
    main_menu_kb,
    thumb_color_kb,
    thumb_download_again_kb,
    thumb_position_kb,
    thumb_result_kb,
    video_seo_menu_kb,
)
from video_seo_presets import VIDEO_SEO_PRESETS, format_preset
from services.auth import is_admin, is_user_approved
from services.gemini import (
    analyze_competitors,
    compare_niches,
    describe_thumbnails,
    generate_banner_imagen,
    generate_channel_seo,
    generate_image_prompt,
    generate_video_seo,
)
from services.youtube_api import (
    fetch_video_info,
    is_configured as yt_api_ready,
)
from services import niche_store as ns
from services import watch_store as ws
from services.competitor import MIN_CHANNELS, cross_patterns, evaluate_niche, parse_channel_refs
from services.competitor_run import (
    MAX_CHANNELS,
    baseline_snapshots,
    build_sheet,
    collect_channels,
    find_suggestions,
)
from services.report_pdf import build_competitor_report, build_niche_comparison
from services.thumbnail import extract_video_id, fetch_best_thumbnail
from services.history import count_today, get_history, get_item, log_generation
from services import usage
from services.image_service import add_text_to_thumbnail, cover_resize, resize_image, overlay_banner_frame, add_banner_text
from services.replicate_service import (
    ThumbModelError,
    generate_banner_image,
    generate_image,
    generate_thumbnail_nano,
)

logger = logging.getLogger(__name__)
router = Router(name="youtube")
router.message.filter(F.chat.type == ChatType.PRIVATE)


# --- FSM holatlari ---
class YT(StatesGroup):
    channel_analysis = State()  # kanal analizi — kanal linki kutilmoqda
    channel = State()         # kanal SEO — mavzu kutilmoqda
    video = State()           # video SEO — mavzu kutilmoqda
    avatar = State()          # avatar — yo'nalish tanlash
    avatar_name = State()     # avatar — kanal nomi kutilmoqda
    banner = State()          # banner — yo'nalish tanlash
    banner_name = State()     # banner — kanal nomi kutilmoqda
    thumb_prompt = State()    # thumbnail — tavsif (prompt) yoki namuna rasm kutilmoqda
    thumb_edit = State()      # thumbnail — tayyor rasmga o'zgartirish ko'rsatmasi kutilmoqda
    thumb_text = State()      # thumbnail — ustki matn
    thumb_position = State()  # thumbnail — matn joylashuvi
    thumb_color = State()     # thumbnail — matn rangi
    thumb_download = State()  # video oblojkasini yuklab olish — video linki kutilmoqda


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
# Yo'nalishlar (niche) — har biri uchun banner va avatar promptlari
# ============================================================
# "{name}" o'rniga o'quvchining kanal nomi qo'yiladi.
# "custom" — o'quvchi o'z tavsifini yozadi.

# {name} — o'quvchining kanal nomi, generate vaqtida almashtiriladi
_NICHES: dict[str, dict] = {
    "beamng": {
        "label": "BeamNG Flatbed",
        "avatar": (
            "A professional YouTube gaming channel avatar, 800x800 pixels, 1:1 ratio. "
            "A 3D sports car loaded on a flatbed tow truck with dust and motion effects. "
            "Electric blue and orange neon colors, glowing edges, dark background with subtle tech pattern. "
            'Bold text "{name}". '
            "Centered circular composition, esports logo style, sharp and eye-catching. 4K quality."
        ),
        "banner": (
            "photorealistic 8K RAW DSLR photo, sports car on flatbed tow truck, "
            "country highway with green hills, flying dust, dramatic storm clouds, "
            "golden hour sunlight, motion blur, cinematic wide shot, "
            "shallow depth of field, sharp subject, no text, no watermark"
        ),
    },
    "stickman": {
        "label": "Stickman Dismounting",
        "avatar": (
            "A fun YouTube gaming channel avatar, 800x800 pixels, 1:1 ratio. "
            "A 3D ragdoll stickman character mid-fall with motion and impact effects. "
            "Bright energetic colors — yellow, red, and blue. "
            "Dynamic dark background with light streaks. "
            'Bold text "{name}". '
            "Centered circular composition, playful esports logo style, sharp and eye-catching. 4K quality."
        ),
        "banner": (
            "hyperrealistic 3D render 8K, ragdoll character flying through air after massive crash, "
            "explosion sparks and debris, motion blur, bright energy particles, "
            "dark dramatic background, cinematic lighting, no text, no watermark"
        ),
    },
    "wrongeyes": {
        "label": "Wrong Eyes",
        "avatar": (
            "A bold YouTube channel avatar, 800x800 pixels, 1:1 ratio. "
            "A stylized pair of cartoon eyes looking in odd directions, fun and quirky style. "
            "Vibrant contrasting colors, glowing outline, clean dark background. "
            'Bold text "{name}". '
            "Centered circular composition, modern logo style, sharp and eye-catching. 4K quality."
        ),
        "banner": (
            "photorealistic 3D render 8K, two giant realistic human eyeballs floating in space, "
            "pupils pointing in opposite silly directions, glossy texture, "
            "vivid colorful abstract background with sparkles, dramatic studio lighting, "
            "no text, no watermark"
        ),
    },
    "asmr_baby": {
        "label": "ASMR BABY",
        "avatar": (
            "A soft cute YouTube ASMR channel avatar, 800x800 pixels, 1:1 ratio. "
            "A gentle baby-themed icon with a glowing microphone and soft sound waves. "
            "Pastel colors — soft pink, baby blue, cream. Dreamy soft lighting, subtle glow. "
            'Bold soft text "{name}". '
            "Centered circular composition, clean and adorable. 4K quality."
        ),
        "banner": (
            "professional product photography 8K, ASMR microphone surrounded by soft plush baby toys, "
            "pastel bokeh background, soft pink and baby blue tones, "
            "shallow depth of field, warm cozy studio lighting, no text, no watermark"
        ),
    },
    "asmr_chupa": {
        "label": "ASMR Chupa-Chups",
        "avatar": (
            "A colorful YouTube ASMR channel avatar, 800x800 pixels, 1:1 ratio. "
            "A glossy swirl lollipop with a small microphone and soft sound waves. "
            "Vibrant candy colors — pink, red, yellow swirl. Soft glow, clean playful background. "
            'Bold text "{name}". '
            "Centered circular composition, sweet and eye-catching. 4K quality."
        ),
        "banner": (
            "macro photography 8K, glossy colorful swirl lollipops and ASMR microphone, "
            "candy bokeh background with sparkles, vivid pink red yellow colors, "
            "shallow depth of field, glossy reflections, studio lighting, no text, no watermark"
        ),
    },
    "asmr_mms": {
        "label": "ASMR MMS",
        "avatar": (
            "A colorful YouTube ASMR channel avatar, 800x800 pixels, 1:1 ratio. "
            "Glossy colorful candy buttons/chocolates with a small microphone and soft sound waves. "
            "Vibrant rainbow candy colors, soft glow, clean background. "
            'Bold text "{name}". '
            "Centered circular composition, fun and eye-catching. 4K quality."
        ),
        "banner": (
            "macro photography 8K, colorful M&M candies scattered with ASMR microphone, "
            "rainbow bokeh background, glossy candy reflections, "
            "shallow depth of field, bright vivid colors, studio lighting, no text, no watermark"
        ),
    },
    "roblox": {
        "label": "Roblox",
        "avatar": (
            "A fun YouTube gaming channel avatar, 800x800 pixels, 1:1 ratio. "
            "A blocky 3D voxel character head in playful style, bright primary colors, "
            "glowing edges, clean dark background with blocky pattern. "
            'Bold text "{name}". '
            "Centered circular composition, esports logo style, sharp and eye-catching. 4K quality."
        ),
        "banner": (
            "high quality 3D render 8K, colorful blocky voxel game characters in adventure world, "
            "floating cubes, glowing particle effects, vivid primary colors, "
            "cinematic dramatic lighting, ultra-sharp detail, no text, no watermark"
        ),
    },
    "custom": {
        "label": "🖊 O'z yo'nalishim (AI)",
        "banner": None,
        "avatar": None,
    },
}


def _niche_kb(kind: str) -> "InlineKeyboardMarkup":
    """Yo'nalish tanlash klaviaturasi. kind='banner' yoki 'avatar'."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    buttons = []
    for key, niche in _NICHES.items():
        buttons.append([InlineKeyboardButton(
            text=niche["label"],
            callback_data=f"niche:{kind}:{key}",
        )])
    buttons.append([InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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


_SEO_BLOCK_LIMIT = 3800  # bitta xabar chegarasi (4096'dan xavfsiz pastda)


def _split_escaped(text: str, limit: int) -> list:
    """Matnni HTML-escape qilib, escape'dan KEYINGI uzunligi `limit`dan oshmaydigan
    bo'laklarga bo'ladi (avval qator, kerak bo'lsa belgi bo'yicha).
    Entity (&amp;) ichida hech qachon bo'linmaydi. Qaytgan bo'laklar allaqachon escape qilingan."""
    out: list = []
    cur = ""
    for line in str(text).split("\n"):
        esc = html.escape(line)
        if len(esc) > limit:
            # Juda uzun qator — belgi-belgilab (escape qilingan holda) bo'lamiz
            if cur:
                out.append(cur)
                cur = ""
            buf = ""
            for ch in line:
                e = html.escape(ch)
                if len(buf) + len(e) > limit:
                    out.append(buf)
                    buf = ""
                buf += e
            cur = buf
            continue
        if cur and len(cur) + 1 + len(esc) > limit:
            out.append(cur)
            cur = esc
        else:
            cur = f"{cur}\n{esc}" if cur else esc
    if cur:
        out.append(cur)
    return out or [""]


def _seo_blocks(name: str, titles: list, description: str, tags: list) -> list:
    """Video SEO natijasini nusxalanadigan HTML bloklar RO'YXATI qilib qaytaradi.
    Har blok alohida xabar — hech biri 4096 chegaradan oshmaydi, tag ichida bo'linmaydi."""
    blocks = []

    head = [f"🎬 {html.escape(name)}\n"]
    titles = titles or []
    if titles:
        head.append(
            "📌 Video nomi (bosib nusxalang):" if len(titles) == 1
            else "📌 Video nomi variantlari (har birini bosib nusxalang):"
        )
        for t in titles:
            head.append(f"<code>{html.escape(str(t))}</code>")
    blocks.append("\n".join(head))

    if description:
        chunks = _split_escaped(str(description), _SEO_BLOCK_LIMIT - 60)
        for i, chunk in enumerate(chunks):
            label = "📝 Opisaniye (bosib nusxalang):" if i == 0 else "📝 Opisaniye (davomi):"
            blocks.append(f"{label}\n<pre>{chunk}</pre>")

    if tags:
        tag_str = ", ".join(str(t) for t in tags)
        for i, chunk in enumerate(_split_escaped(tag_str, _SEO_BLOCK_LIMIT - 60)):
            label = "🏷 Teglar (bosib nusxalang):" if i == 0 else "🏷 Teglar (davomi):"
            blocks.append(f"{label}\n<pre>{chunk}</pre>")

    # Qo'llanmani oxirgi blokka qo'shamiz (agar sig'sa)
    if blocks and len(blocks[-1]) + len(GUIDE["video_seo"]) < _SEO_BLOCK_LIMIT:
        blocks[-1] += GUIDE["video_seo"]
    else:
        blocks.append(GUIDE["video_seo"].strip())
    return blocks


async def _send_seo_html(message: Message, blocks: list, last_kb=None) -> None:
    """Nusxalanadigan SEO bloklarini ketma-ket yuboradi; oxirgisiga tugma (default 🏠)."""
    last_kb = last_kb or home_kb()
    for i, block in enumerate(blocks):
        last = i == len(blocks) - 1
        try:
            await message.answer(
                block, parse_mode="HTML",
                reply_markup=last_kb if last else None,
            )
        except Exception:
            logger.exception("SEO blok yuborilmadi (uzunlik=%d)", len(block))
            # HTML muammosi bo'lsa — oddiy matn sifatida urinib ko'ramiz
            plain = block.replace("<code>", "").replace("</code>", "") \
                         .replace("<pre>", "").replace("</pre>", "")
            import html as _h
            plain = _h.unescape(plain)
            await message.answer(
                plain, reply_markup=last_kb if last else None
            )


async def _deny_limit(callback: CallbackQuery, kind: str) -> None:
    """Limit tugaganini bildiradi."""
    limit = DAILY_IMAGE_LIMIT if kind == "image" else DAILY_TEXT_LIMIT
    word = "rasm" if kind == "image" else "SEO"
    await callback.answer(
        f"Bugungi limit tugadi (kuniga {limit} ta {word}). Ertaga urinib ko'ring.",
        show_alert=True,
    )


async def _gate_generation(message: Message, state: FSMContext, kind: str) -> bool:
    """Generatsiya oldidan haqiqiy tekshiruv (matn/rasm bosqichida).
    Eski tugma yoki muddat o'tgan/banlangan foydalanuvchi suiiste'molini to'xtatadi.
    Xato bo'lsa holatni tozalab, sababni yozadi va False qaytaradi."""
    if not _is_allowed(message.from_user.id):
        await state.clear()
        await message.answer("⛔ Avval /start bosib ro'yxatdan o'ting yoki ruxsatingizni tekshiring.")
        return False
    ok, _ = _check_limit(message.from_user.id, kind)
    if not ok:
        await state.clear()
        limit = DAILY_IMAGE_LIMIT if kind == "image" else DAILY_TEXT_LIMIT
        word = "rasm" if kind == "image" else "SEO"
        await message.answer(
            f"Bugungi limit tugadi (kuniga {limit} ta {word}). Ertaga urinib ko'ring.",
            reply_markup=home_kb(),
        )
        return False
    return True


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
    "🔍 Raqobatchi kanallar analizi — bitta yo'nalishdan 10 ta kanal (5 eski + 5 yangi) "
    "havolasini yuborasiz: bot faolligini, strategiyasini (vaqt, davomiylik, nom/teg/opisaniye, "
    "oblojka) tahlil qilib PDF hisobot beradi va 7 kun har kuni kuzatuv xabarini yuboradi.\n\n"
    "📺 Kanal SEO — kanalingiz mavzusini yozsangiz, kanal nomi, tavsif va kalit so'zlar "
    "bo'yicha tavsiya beradi.\n\n"
    "🎬 Video SEO — video uchun sarlavha, tavsif va teglar yozib beradi "
    "(tayyor yo'nalishlar yoki o'zingiznikini).\n\n"
    "🖼 Avatar yaratish — kanal uchun profil rasm (avatar) chizadi.\n\n"
    "🎨 Banner yaratish — kanal shapkasi (banner) chizadi.\n\n"
    "🌅 Video ustiga rasm yaratish — qanday oblojka kerakligini yozasiz (yoki o'z "
    "rasmingizni yuborasiz), AI chizadi. Keyin «O'zgartirish» bilan ko'rsatma yozib "
    "xohlagancha to'g'rilatasiz, «Ustiga aniq matn yozish» bilan matn qo'shasiz.\n\n"
    "📥 Video ma'lumotlarini olish — YouTube video havolasini yuborsangiz, "
    "videoning nomi, opisaniyesi va teglarini nusxalash uchun chiqaradi hamda "
    "ustidagi rasmni (oblojkani) eng sifatli variantda fayl qilib beradi.\n\n"
    "📂 Mening ishlarim — ilgari yaratgan ishlaringiz tarixi.\n\n"
    "🛠 Texnik nosozlik (bot/mini-app/login ishlamasa) — /yordam buyrug'ini yozing.\n\n"
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

_ANALYSIS_START_TEXT = (
    "🔍 Raqobatchi kanallar analizi\n\n"
    "Bitta yo'nalishdan KAMIDA 10 ta kanal havolasini yuboring — bitta xabarda, "
    "har biri yangi qatorda:\n"
    "• 5 tasi ESKI — 2–3 yil oldin ochilgan, katta kanallar\n"
    "• 5 tasi YANGI — oxirgi 1–3 oyda ochilgan kanallar\n\n"
    "Nega shunday? 10 ta kanal topilmasa — bu yo'nalishda ishlamagan ma'qul. "
    "Faqat eskilar bo'lsa — yo'nalish hozir trendda emas. Faqat yangilar bo'lsa — shubhali.\n\n"
    "Qabul qilinadi: kanal havolasi, @nom yoki kanalning istalgan video havolasi.\n"
    "Birinchi qatorga yo'nalish NOMINI yozing (masalan: «Oshxona» yoki «Avto obzor») — "
    "keyin yo'nalishlarni taqqoslashda shu nom ko'rinadi.\n\n"
    "Bot nima qiladi:\n"
    "1) kanallar faolmi (haftasiga 3+ video) — tekshiradi;\n"
    "2) strategiyani tahlil qiladi: chiqarish soni va vaqti, davomiylik, nom/teg/opisaniye, "
    "oblojkalar, 48 soatlik va oxirgi 7 video ko'rishlari;\n"
    "3) chiroyli PDF hisobot beradi;\n"
    "4) 7 kun davomida har kuni ertalab shu kanallar bo'yicha ma'lumot yuborib boradi.\n\n"
    "Havolalarni yuboring 👇"
)


@router.callback_query(F.data == "menu:channel_analysis")
async def analysis_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    if not yt_api_ready():
        await callback.answer("🔍 Kanal analizi tez orada ishga tushadi. Biroz kuting!", show_alert=True)
        return
    ok, _ = _check_limit(callback.from_user.id, "text")
    if not ok:
        await _deny_limit(callback, "text")
        return
    await state.clear()
    await state.set_state(YT.channel_analysis)
    kb = _analysis_start_kb(ns.count_analyses(callback.from_user.id))
    try:
        await callback.message.edit_text(_ANALYSIS_START_TEXT, reply_markup=kb)
    except Exception:
        await callback.message.answer(_ANALYSIS_START_TEXT, reply_markup=kb)
    await callback.answer()


def _analysis_start_kb(saved: int) -> InlineKeyboardMarkup:
    rows = []
    if saved >= 1:
        rows.append([InlineKeyboardButton(
            text=f"📊 Yo'nalishlarni taqqoslash ({saved} ta saqlangan)", callback_data="niche:compare")])
    rows.append([InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _niche_name_from_text(text: str, refs: list) -> str:
    """Xabardagi havolasiz birinchi qisqa qator — yo'nalish nomi (ixtiyoriy)."""
    ref_keys = [r.lower() for r in refs]
    for line in (text or "").split("\n"):
        ln = line.strip().strip("-–•:").strip()
        if not ln or len(ln) > 40:
            continue
        low = ln.lower()
        if "youtube" in low or "youtu.be" in low or low.startswith("@") or low.startswith("uc"):
            continue
        if any(k in low for k in ref_keys):
            continue
        ln = re.sub(r"^(yo'nalish|nisha|niche|yonalish)\s*[:\-]\s*", "", ln, flags=re.I).strip()
        if 2 <= len(ln) <= 40:
            return ln
    return ""


@router.message(YT.channel_analysis, F.text & ~F.text.startswith("/"))
async def analysis_process(message: Message, state: FSMContext) -> None:
    refs = parse_channel_refs(message.text)
    if len(refs) < MIN_CHANNELS:
        await message.answer(
            f"Xabaringizda {len(refs)} ta kanal havolasi topildi, kamida {MIN_CHANNELS} ta kerak "
            "(5 eski + 5 yangi).\n\n"
            "Metodika: 10 ta kanal topilmasa — bu yo'nalishda ishlamagan ma'qul. "
            "Yana kanallar topib, hammasini bitta xabarda (har biri yangi qatorda) yuboring.",
            reply_markup=home_kb(),
        )
        return
    if not await _gate_generation(message, state, "text"):
        return
    uid = message.from_user.id
    niche_name = _niche_name_from_text(message.text, refs)
    note = f" (faqat birinchi {MAX_CHANNELS} tasi olinadi)" if len(refs) > MAX_CHANNELS else ""
    waiting = await message.answer(f"🔍 1/4 — {len(refs)} ta kanal tekshirilmoqda{note}... (30–60 soniya)")

    try:
        channels, raw, unresolved = await asyncio.to_thread(collect_channels, refs)
        if not channels:
            await waiting.edit_text(
                "❌ Hech bir kanal topilmadi. Havolalarni tekshirib qayta yuboring.",
                reply_markup=home_kb(),
            )
            return
        niche_eval = evaluate_niche(channels)
        patterns = cross_patterns(channels)

        await waiting.edit_text(f"🔍 2/4 — {len(channels)} ta kanal topildi. Oblojkalar va strategiya tahlil qilinmoqda...")
        sheet, labels = await asyncio.to_thread(build_sheet, channels)
        facts = {
            "niche_eval": niche_eval, "patterns": patterns,
            "channels": [{k: c[k] for k in (
                "title", "category", "age_label", "subscribers", "uploads_per_week", "active",
                "dur_avg_min", "shorts_share", "last7_avg", "views_48h", "top_hours", "top_weekdays",
                "time_consistency", "title_len", "title_numbers", "title_caps", "sample_titles",
                "tags_avg", "top_tags", "desc_len", "engagement", "outliers", "best_video")} for c in channels],
        }
        thumbs_task = describe_thumbnails(sheet, labels, telegram_id=uid) if sheet else asyncio.sleep(0, result="")
        thumbs_text, ai_text = await asyncio.gather(thumbs_task, analyze_competitors(facts, telegram_id=uid),
                                                    return_exceptions=True)
        if isinstance(thumbs_text, BaseException):
            logger.warning("Oblojka tahlili xatosi: %s", thumbs_text)
            thumbs_text = ""
        if isinstance(ai_text, BaseException):
            logger.exception("Raqobatchi AI tahlili xatosi", exc_info=ai_text)
            ai_text = "AI tahlil matni olinmadi. Yuqoridagi raqamlar va jadvallar to'g'ri."
        facts["thumbnails_review"] = thumbs_text[:1500]

        await waiting.edit_text("🔍 3/4 — Yo'nalishdagi boshqa kanallar qidirilmoqda...")
        niche_query, suggestions = await asyncio.to_thread(find_suggestions, channels)

        await waiting.edit_text("🔍 4/4 — PDF hisobot tayyorlanmoqda...")
        student = message.from_user.full_name or "O'quvchi"
        niche_label = niche_name or niche_query or channels[0]["title"]
        pdf = await asyncio.to_thread(
            build_competitor_report, student, channels, niche_eval, patterns, ai_text, thumbs_text,
            sheet, suggestions, unresolved, niche_label,
        )
    except Exception:
        logger.exception("Raqobatchi analizi xatosi")
        await waiting.edit_text(ERROR_TEXT, reply_markup=home_kb())
        return

    # Qisqa xulosa + PDF
    summary = (
        f"📌 Yo'nalish: {niche_label}\n"
        f"{niche_eval['emoji']} Xulosa: {niche_eval['label']} — {niche_eval['score']}/100\n\n"
        f"Kanallar: {niche_eval['count']} ta (eski {niche_eval['old']}, yangi {niche_eval['new']}, o'rta {niche_eval['mid']})\n"
        f"Faol (haftasiga 3+ video): {niche_eval['active']} ta ({niche_eval['active_share']}%)\n"
        f"O'rtacha video/hafta: {patterns.get('uploads_per_week_avg', 0)}, davomiylik ~{patterns.get('dur_avg_all', 0)} daq, "
        f"Shorts ~{patterns.get('shorts_avg', 0)}%\n"
        f"Chiqarish soatlari (Toshkent): " + ", ".join(f"{h:02d}:00" for h in patterns.get("top_hours", [])[:3]) + "\n"
    )
    for wline in niche_eval["warnings"][:3]:
        summary += f"⚠️ {wline}\n"
    for rline in niche_eval["reasons"][:2]:
        summary += f"✅ {rline}\n"
    if unresolved:
        summary += f"\nTopilmadi: {', '.join(unresolved[:5])}"
    summary += ("\n\nTo'liq tahlil — PDF hisobotda 👇\n"
                "🤖 Oybek Bozorov AI yordamchisi analiz qildi. AI adashishi mumkin — 100% ishonmang.")
    await message.answer(summary)
    fname = f"raqobat_analiz_{datetime.now().strftime('%Y%m%d')}.pdf"
    sent = await message.answer_document(BufferedInputFile(pdf, filename=fname),
                                         caption="📘 Raqobatchi kanallar analizi (PDF)")
    log_generation(uid, "channel_analysis", "text", label=f"Raqobat — {niche_label}"[:40],
                   result_type="file", file_id=sent.document.file_id)
    try:
        ns.save_analysis(uid, niche_label, niche_query, niche_eval, patterns, channels)
    except Exception:
        logger.exception("Yo'nalish tahlilini saqlashda xato (user=%s)", uid)
    saved = ns.count_analyses(uid)

    # 7 kunlik kuzatuvni boshlaymiz (baseline snapshot bugun)
    try:
        ws.start_watch(uid, niche_label,
                       [{"channel_id": c["channel_id"], "title": c["title"], "url": c["url"]} for c in channels])
        await asyncio.to_thread(baseline_snapshots, raw)
        rows = [[InlineKeyboardButton(text="⏹ Kuzatuvni to'xtatish", callback_data="watch:stop")]]
        if saved >= 2:
            rows.append([InlineKeyboardButton(text=f"📊 Yo'nalishlarni taqqoslash ({saved} ta)",
                                              callback_data="niche:compare")])
        rows.append([InlineKeyboardButton(text="🔍 Yana bir yo'nalish", callback_data="menu:channel_analysis"),
                     InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="nav:home")])
        compare_hint = ("\n\n📊 Boshqa yo'nalishni ham tahlil qilsangiz, «Yo'nalishlarni taqqoslash» "
                        "tugmasi qaysi nishaga kirish yaxshiligini jadval qilib beradi." if saved < 2 else
                        f"\n\n📊 Sizda {saved} ta yo'nalish tahlili saqlangan — taqqoslash mumkin.")
        await message.answer(
            f"👀 7 kunlik kuzatuv boshlandi: {len(channels)} ta kanal.\n"
            "Har kuni ertalab 09:00 da shu kanallar bo'yicha ma'lumot yuboraman: yangi videolar va "
            "chiqqan vaqti, nom/teg/opisaniye, 48 soatlik ko'rishlar, obunachi o'sishi va AI izohi. "
            "7-kuni yakuniy PDF hisobot keladi." + compare_hint,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception:
        logger.exception("Kuzatuvni boshlashda xato (user=%s)", uid)

    await state.clear()
    try:
        await waiting.delete()
    except Exception:
        pass


@router.callback_query(F.data == "watch:stop")
async def watch_stop(callback: CallbackQuery) -> None:
    had = ws.stop_watch(callback.from_user.id)
    await callback.answer("Kuzatuv to'xtatildi." if had else "Faol kuzatuv yo'q.", show_alert=True)


@router.callback_query(F.data == "niche:compare")
async def niche_compare(callback: CallbackQuery, state: FSMContext) -> None:
    """Saqlangan yo'nalish tahlillarini yonma-yon taqqoslaydi: reyting + PDF."""
    uid = callback.from_user.id
    if not _is_allowed(uid):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    niches = ns.list_analyses(uid)
    if len(niches) < 2:
        await callback.answer(
            f"Taqqoslash uchun kamida 2 ta yo'nalish tahlili kerak (hozir {len(niches)} ta). "
            "Boshqa yo'nalishni ham tahlil qiling.", show_alert=True)
        return
    ok, _ = _check_limit(uid, "text")
    if not ok:
        await _deny_limit(callback, "text")
        return
    await callback.answer()
    await state.clear()
    waiting = await callback.message.answer(f"📊 {len(niches)} ta yo'nalish taqqoslanmoqda...")
    try:
        ranked = ns.rank_niches(niches)
        facts = {"ranked": [{k: n.get(k) for k in (
            "rank", "name", "score", "label", "why", "count", "old", "new", "mid", "active_share",
            "new_avg_views", "old_avg_views", "avg_subs", "uploads_per_week_avg", "dur_avg_all",
            "shorts_avg", "engagement_avg", "top_hours", "reasons", "warnings")} for n in ranked]}
        try:
            ai_text = await compare_niches(facts, telegram_id=uid)
        except Exception as e:
            logger.warning("Taqqoslash AI matni yo'q: %s", e)
            ai_text = "AI tavsiya matni olinmadi. Jadval va reyting to'g'ri."
        student = callback.from_user.full_name or "O'quvchi"
        pdf = await asyncio.to_thread(build_niche_comparison, student, ranked, ai_text)
    except Exception:
        logger.exception("Yo'nalish taqqoslash xatosi (user=%s)", uid)
        await waiting.edit_text(ERROR_TEXT, reply_markup=home_kb())
        return

    lines = [f"📊 Qaysi yo'nalishga kirsam? ({len(ranked)} ta taqqoslandi)\n"]
    for n in ranked:
        emoji = "🟢" if n["score"] >= 70 else ("🟡" if n["score"] >= 45 else "🔴")
        lines.append(f"{n['rank']}. {emoji} {n['name']} — {n['score']}/100 ({n['label']})\n"
                     f"    {n['why']}; yangi kanal o'rt. {n.get('new_avg_views', 0):,} ko'rish, faol {n.get('active_share', 0)}%")
    lines.append("\nTo'liq jadval va AI tavsiyasi — PDF'da 👇\n"
                 "🤖 Oybek Bozorov AI yordamchisi analiz qildi. AI adashishi mumkin — 100% ishonmang.")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Yana bir yo'nalish tahlili", callback_data="menu:channel_analysis")],
        [InlineKeyboardButton(text="🗑 Ro'yxatni tozalash", callback_data="niche:clear"),
         InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="nav:home")],
    ])
    await waiting.delete()
    await callback.message.answer("\n".join(lines))
    sent = await callback.message.answer_document(
        BufferedInputFile(pdf, filename=f"yonalishlar_taqqoslash_{datetime.now().strftime('%Y%m%d')}.pdf"),
        caption="📘 Yo'nalishlarni taqqoslash (PDF)", reply_markup=kb,
    )
    log_generation(uid, "channel_analysis", "text", label=f"Taqqoslash — {len(ranked)} yo'nalish",
                   result_type="file", file_id=sent.document.file_id)


@router.callback_query(F.data == "niche:clear")
async def niche_clear(callback: CallbackQuery) -> None:
    n = ns.clear_analyses(callback.from_user.id)
    await callback.answer(f"{n} ta yo'nalish tahlili o'chirildi." if n else "Ro'yxat bo'sh.", show_alert=True)


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
    # kind="preset" — tarixda qoladi, lekin kunlik "text" limitini yemaydi (AI ishlatilmaydi)
    log_generation(callback.from_user.id, "video_seo", "preset",
                   label=preset["name"], result_type="text", result_text=plain)
    try:
        await callback.message.delete()
    except Exception:
        pass
    blocks = _seo_blocks(
        preset["name"], preset.get("titles", []),
        preset.get("description", ""), preset.get("tags", []),
    )
    await _send_seo_html(callback.message, blocks)


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
    blocks = _seo_blocks(topic, titles, description, tags)
    await _send_seo_html(message, blocks)


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
        "🖼 Avatar yaratish\n\nYo'nalishingizni tanlang:",
        reply_markup=_niche_kb("avatar"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("niche:"))
async def niche_select(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    ok, _ = _check_limit(callback.from_user.id, "image")
    if not ok:
        await _deny_limit(callback, "image")
        return
    _, kind, niche_key = callback.data.split(":", 2)
    niche = _NICHES.get(niche_key)
    if not niche:
        await callback.answer("Noma'lum yo'nalish", show_alert=True)
        return
    await state.update_data(niche_key=niche_key, kind=kind)
    if niche_key == "custom":
        # O'z tavsifini yozadi
        next_state = YT.avatar_name if kind == "avatar" else YT.banner_name
        await state.set_state(next_state)
        await callback.message.edit_text(
            f"{'🖼 Avatar' if kind == 'avatar' else '🎨 Banner'} yaratish\n\n"
            "Kanalingiz nima haqida? Qanday uslub kerak?\n"
            "Batafsil o'zbek yoki ingliz tilida yozing:",
            reply_markup=home_kb(),
        )
    else:
        next_state = YT.avatar_name if kind == "avatar" else YT.banner_name
        await state.set_state(next_state)
        await callback.message.edit_text(
            f"{'🖼 Avatar' if kind == 'avatar' else '🎨 Banner'} — {niche['label']}\n\n"
            "Kanal nomingizni yozing:",
            reply_markup=home_kb(),
        )
    await callback.answer()


import re as _re

_TEXT_PATTERN = _re.compile(
    r'Bold\s+(?:modern\s+)?(?:soft\s+)?(?:elegant\s+)?(?:3D\s+)?text\s+"[^"]*"[^.]*\.\s*',
    _re.IGNORECASE,
)


def _no_text_prompt(prompt: str) -> str:
    """Promptdan matn ko'rsatmasini olib tashlaydi — PIL text ishlatadi."""
    return _TEXT_PATTERN.sub("", prompt).strip()


async def _generate_banner(prompt: str) -> bytes:
    """Flux Schnell (Replicate) orqali banner yaratadi; xato bo'lsa Ideogramga fallback."""
    try:
        return await generate_image(prompt, aspect_ratio="16:9")
    except Exception as e:
        logger.warning("Flux banner xatosi, Ideogramga o'tilmoqda: %s", e)
        return await generate_banner_image(prompt)


async def _generate_niche_image(niche_key: str, kind: str,
                                channel_name: str) -> bytes:
    """Niche promptiga {name} o'rniga kanal nomini qo'yib rasm yaratadi.
    Banner uchun: Imagen 3 fon chizadi, PIL matnni aniq markazga yozadi.
    """
    niche = _NICHES[niche_key]
    if niche_key == "custom":
        if kind == "avatar":
            prompt = await generate_image_prompt(channel_name, kind="avatar")
            return resize_image(await generate_image(prompt, aspect_ratio="1:1"), 1024, 1024)
        else:
            prompt = await generate_image_prompt(channel_name, kind="banner")
            bg = resize_image(await _generate_banner(prompt), 2560, 1440)
            return add_banner_text(bg, channel_name)

    template = niche[kind]
    if kind == "avatar":
        prompt = template.replace("{name}", channel_name)
        image = await generate_image(prompt, aspect_ratio="1:1")
        return resize_image(image, 1024, 1024)
    else:
        # Matnni promptdan olib tashlaymiz — PIL aniq markazga yozadi
        prompt = _no_text_prompt(template.replace("{name}", channel_name))
        bg = resize_image(await _generate_banner(prompt), 2560, 1440)
        return add_banner_text(bg, channel_name)


@router.message(YT.avatar_name, F.text & ~F.text.startswith("/"))
async def avatar_name_process(message: Message, state: FSMContext) -> None:
    if not await _gate_generation(message, state, "image"):
        return
    data = await state.get_data()
    niche_key = data.get("niche_key", "custom")
    channel_name = message.text.strip()
    await state.clear()  # ikki marta yubormaslik uchun holatni darhol tozalaymiz
    waiting = await message.answer("🎨 Avatar chizilmoqda... (30-60 soniya)")
    try:
        image = await _generate_niche_image(niche_key, "avatar", channel_name)
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
                   label=f"Avatar — {channel_name[:30]}",
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
        "🎨 Banner yaratish\n\nYo'nalishingizni tanlang:",
        reply_markup=_niche_kb("banner"),
    )
    await callback.answer()


@router.message(YT.banner_name, F.text & ~F.text.startswith("/"))
async def banner_name_process(message: Message, state: FSMContext) -> None:
    if not await _gate_generation(message, state, "image"):
        return
    data = await state.get_data()
    niche_key = data.get("niche_key", "custom")
    channel_name = message.text.strip()
    await state.clear()  # ikki marta yubormaslik uchun holatni darhol tozalaymiz
    waiting = await message.answer("🎨 Banner chizilmoqda... (30-60 soniya)")
    try:
        image = await _generate_niche_image(niche_key, "banner", channel_name)
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
                   label=f"Banner — {channel_name[:30]}",
                   result_type="file", file_id=sent.document.file_id)


# ============================================================
# Thumbnail — prompt asosida yaratish, ko'rsatma bilan tahrirlash, aniq matn qo'shish
# ============================================================

_THUMB_START_TEXT = (
    "🌅 Video ustiga rasm yaratish\n\n"
    "Qanday rasm kerakligini yozib yuboring — AI shu tavsif bo'yicha chizadi.\n"
    "Yaxshi tavsifda: mavzu, rasmda nima bo'lsin, kayfiyat va ranglar, "
    "ustiga yoziladigan matn.\n\n"
    "Masalan:\n"
    "«Telefon ustida o'sayotgan grafik, to'q ko'k fon, ustida katta sariq matn: "
    "100 MING OBUNACHI»\n\n"
    "💡 O'z rasmingizni (masalan, yuzingizni) ham yuborishingiz mumkin — izohida "
    "nima qilishni yozing, AI shu rasm asosida oblojka yasaydi.\n\n"
    "Natija chiqqach, «O'zgartirish» tugmasi bilan xohlagancha to'g'rilatasiz."
)

_THUMB_EDIT_TEXT = (
    "✏️ Nimani o'zgartiray? Yozing.\n\n"
    "Masalan: «fonni qizil qil», «matnni kattalashtir», «chap tomonga raketa qo'sh», "
    "«matnni YANGI VIDEO ga almashtir», «odamni o'ngga sur»."
)


@router.callback_query(F.data == "menu:thumbnail")
async def thumb_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    ok, _ = _check_limit(callback.from_user.id, "image")
    if not ok:
        await _deny_limit(callback, "image")
        return
    await state.clear()
    await state.set_state(YT.thumb_prompt)
    try:
        await callback.message.edit_text(_THUMB_START_TEXT, reply_markup=home_kb())
    except Exception:
        # Natija (fayl) xabaridan kelgan bo'lsa — tahrirlab bo'lmaydi, yangi xabar
        await callback.message.answer(_THUMB_START_TEXT, reply_markup=home_kb())
    await callback.answer()


@router.message(YT.thumb_prompt, F.photo)
async def thumb_prompt_photo(message: Message, state: FSMContext) -> None:
    """O'quvchi o'z rasmini yubordi: izoh bo'lsa darhol yaratamiz, bo'lmasa so'raymiz."""
    file_id = message.photo[-1].file_id  # eng katta o'lchamdagisi
    caption = (message.caption or "").strip()
    if caption:
        await _thumb_generate(message, state, prompt=caption, ref_file_id=file_id)
        return
    await state.update_data(ref_file_id=file_id)
    await message.answer(
        "✅ Rasm qabul qilindi. Endi bu rasm bilan nima qilishni yozing.\n\n"
        "Masalan: «meni YouTube oblojkasiga aylantir, orqa fon — zamonaviy studiya, "
        "ustida matn: YANGI VIDEO»",
        reply_markup=home_kb(),
    )


@router.message(YT.thumb_prompt, F.text & ~F.text.startswith("/"))
async def thumb_prompt_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await _thumb_generate(message, state, prompt=message.text.strip(),
                          ref_file_id=data.get("ref_file_id"))


@router.callback_query(F.data == "thumb:edit")
async def thumb_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Natija ostidagi «O'zgartirish» — ko'rsatma so'raymiz."""
    data = await state.get_data()
    if not data.get("last_file_id"):
        await callback.answer("Avval rasm yarating (🔄 Yangi rasm).", show_alert=True)
        return
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    ok, _ = _check_limit(callback.from_user.id, "image")
    if not ok:
        await _deny_limit(callback, "image")
        return
    await state.set_state(YT.thumb_edit)
    await callback.message.answer(_THUMB_EDIT_TEXT, reply_markup=home_kb())
    await callback.answer()


@router.message(YT.thumb_edit, F.text & ~F.text.startswith("/"))
async def thumb_edit_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    last = data.get("last_file_id")
    if not last:
        await state.clear()
        await message.answer("Avval rasm yarating.", reply_markup=home_kb())
        return
    await _thumb_generate(message, state, prompt=message.text.strip(),
                          ref_file_id=last, is_edit=True)


async def _thumb_generate(message: Message, state: FSMContext, prompt: str,
                          ref_file_id=None, is_edit: bool = False) -> None:
    """Nano Banana orqali oblojka yaratadi (yoki tahrirlaydi) va natijani yuboradi."""
    uid = message.from_user.id
    if not await _gate_generation(message, state, "image"):
        return
    if len(prompt) < 3:
        await message.answer("Tavsif juda qisqa. Qanday rasm kerakligini batafsilroq yozing.")
        return

    waiting = await message.answer(
        "🎨 Rasm o'zgartirilmoqda... (10-30 soniya)" if is_edit
        else "🎨 Rasm yaratilmoqda... (10-30 soniya)"
    )
    await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)

    ref_bytes = None
    if ref_file_id:
        try:
            buf = io.BytesIO()
            await message.bot.download(ref_file_id, destination=buf)
            ref_bytes = buf.getvalue()
        except Exception:
            logger.exception("Thumbnail: rasmni yuklab olib bo'lmadi (%s)", ref_file_id)
            await waiting.edit_text(
                "❌ Rasmni olib bo'lmadi. Iltimos, rasmni qayta yuboring.",
                reply_markup=home_kb(),
            )
            return

    try:
        try:
            image = await generate_thumbnail_nano(prompt, ref_bytes, edit=is_edit)
        except ThumbModelError as first_err:
            # Model o'zbekcha tavsifni tushunmadi/rad etdi — inglizcha promptga o'girib qayta
            logger.warning("Nano Banana rad etdi (%s), inglizcha prompt bilan qayta", first_err)
            en_prompt = await generate_image_prompt(prompt, kind="thumbnail", telegram_id=uid)
            image = await generate_thumbnail_nano(en_prompt, ref_bytes, edit=is_edit)
    except Exception:
        logger.exception("Thumbnail yaratish xatosi (edit=%s)", is_edit)
        await waiting.edit_text(ERROR_TEXT, reply_markup=home_kb())
        return

    usage.record(uid, "thumbnail", kind="image", model=NANO_BANANA_MODEL)
    try:
        await waiting.delete()
    except Exception:
        pass
    label = ("O'zgartirish — " if is_edit else "") + prompt[:30]
    await _send_thumb_result(message, state, image, label=label)


async def _send_thumb_result(message: Message, state: FSMContext, image: bytes,
                             label: str, note: str = "", log: bool = True) -> None:
    """1280x720 ga keltiradi, fayl qilib yuboradi, tarixga yozadi va keyingi qadam
    tugmalarini ko'rsatadi. Rasm file_id'si state'da saqlanadi (o'zgartirish uchun)."""
    image = cover_resize(image, 1280, 720)
    caption = "✅ Oblojka tayyor! (1280x720)"
    if note:
        caption += f"\n{note}"
    caption += (
        "\n\nYoqmadimi? «O'zgartirish» tugmasini bosib, nimani o'zgartirishni yozing."
        + GUIDE["thumbnail"]
    )
    sent = await message.answer_document(
        BufferedInputFile(image, filename="thumbnail.png"),
        caption=caption[:1024],
        reply_markup=thumb_result_kb(),
    )
    if log:
        log_generation(message.from_user.id, "thumbnail", "image",
                       label=f"Thumbnail — {label}",
                       result_type="file", file_id=sent.document.file_id)
    # Holat: kutish yo'q (matn yozsa savol-javobga ketadi), lekin rasm ma'lumoti saqlanadi
    await state.set_state(None)
    await state.update_data(last_file_id=sent.document.file_id, ref_file_id=None, base_file_id=None)


# --- Ustiga aniq matn yozish (AI'siz, PIL) ---

@router.callback_query(F.data == "thumb:addtext")
async def thumb_addtext_start(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("last_file_id"):
        await callback.answer("Avval rasm yarating (🔄 Yangi rasm).", show_alert=True)
        return
    await state.update_data(base_file_id=data["last_file_id"])
    await state.set_state(YT.thumb_text)
    await callback.message.answer(
        "🔤 Rasm ustiga qanday matn yozilsin? Aynan shu ko'rinishda yoziladi.\n"
        "Masalan: 100 MING OBUNACHI",
        reply_markup=home_kb(),
    )
    await callback.answer()


@router.message(YT.thumb_text, F.text & ~F.text.startswith("/"))
async def thumb_get_text(message: Message, state: FSMContext) -> None:
    await state.update_data(overlay=message.text.strip())
    await state.set_state(YT.thumb_position)
    await message.answer(
        "Matn rasmda qayerda joylashsin?",
        reply_markup=thumb_position_kb(),
    )


@router.callback_query(YT.thumb_position, F.data.startswith("thumb:pos:"))
async def thumb_get_position(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(position=callback.data.split(":")[-1])
    await state.set_state(YT.thumb_color)
    await callback.message.edit_text(
        "Matn rangi qanday bo'lsin?",
        reply_markup=thumb_color_kb(),
    )
    await callback.answer()


@router.callback_query(YT.thumb_color, F.data.startswith("thumb:color:"))
async def thumb_overlay_text(callback: CallbackQuery, state: FSMContext) -> None:
    """Tayyor oblojka ustiga matnni PIL bilan yozadi (AI ishlatilmaydi, limitga kirmaydi)."""
    data = await state.get_data()
    base_file_id = data.get("base_file_id") or data.get("last_file_id")
    overlay = data.get("overlay", "")
    position = data.get("position", "bottom")
    color = callback.data.split(":")[-1]
    await callback.answer()
    if not base_file_id or not overlay:
        await state.clear()
        await callback.message.edit_text("Xatolik yuz berdi. Iltimos, qayta boshlang.",
                                         reply_markup=home_kb())
        return
    await callback.message.edit_text("🔤 Matn qo'shilmoqda...")
    try:
        buf = io.BytesIO()
        await callback.bot.download(base_file_id, destination=buf)
        image = add_text_to_thumbnail(resize_image(buf.getvalue(), 1280, 720),
                                      overlay, position, color)
    except Exception:
        logger.exception("Thumbnail matn qo'shish xatosi")
        await state.set_state(None)
        await callback.message.edit_text(ERROR_TEXT, reply_markup=home_kb())
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _send_thumb_result(callback.message, state, image,
                             label=f"Matn: {overlay[:30]}", note=f"Matn: «{overlay}»", log=False)


# ============================================================
# Mening ishlarim (tarix)
# ============================================================

# ============================================================
# Video oblojkasini (thumbnail) yuklab olish
# ============================================================

@router.callback_query(F.data == "menu:thumb_download")
async def thumb_download_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
    await state.set_state(YT.thumb_download)
    text = (
        "📥 Video ma'lumotlarini olish\n\n"
        "YouTube video havolasini yuboring. Masalan:\n"
        "• https://youtu.be/dQw4w9WgXcQ\n"
        "• https://www.youtube.com/watch?v=dQw4w9WgXcQ\n"
        "• https://youtube.com/shorts/...\n\n"
        "Bot videoning nomi, opisaniyesi va teglarini nusxalash uchun chiqarib "
        "beradi, shuningdek video ustidagi rasmni (oblojkani) YouTube'da mavjud "
        "bo'lgan eng sifatli variantda fayl ko'rinishida yuboradi."
    )
    try:
        await callback.message.edit_text(text, reply_markup=home_kb())
    except Exception:
        await callback.message.answer(text, reply_markup=home_kb())
    await callback.answer()


def _video_info_blocks(info: dict) -> list:
    """Videoning haqiqiy nomi, opisaniyesi va teglarini nusxalanadigan HTML bloklar qilib qaytaradi."""
    title = info.get("title") or "(nomsiz)"
    head = ["🎬 Video nomi (bosib nusxalang):", f"<code>{html.escape(title)}</code>"]
    meta = []
    if info.get("channel_title"):
        meta.append(f"📺 {html.escape(info['channel_title'])}")
    if info.get("published_at"):
        meta.append(f"📅 {info['published_at']}")
    if info.get("views"):
        meta.append(f"👁 {info['views']:,}")
    if info.get("likes"):
        meta.append(f"👍 {info['likes']:,}")
    if meta:
        head.append("")
        head.append(" · ".join(meta))
    blocks = ["\n".join(head)]

    desc = (info.get("description") or "").strip()
    if desc:
        for i, chunk in enumerate(_split_escaped(desc, _SEO_BLOCK_LIMIT - 60)):
            label = "📝 Opisaniye (bosib nusxalang):" if i == 0 else "📝 Opisaniye (davomi):"
            blocks.append(f"{label}\n<pre>{chunk}</pre>")
    else:
        blocks.append("📝 Opisaniye: bu videoda yozilmagan.")

    tags = info.get("tags") or []
    if tags:
        tag_str = ", ".join(tags)
        for i, chunk in enumerate(_split_escaped(tag_str, _SEO_BLOCK_LIMIT - 60)):
            label = f"🏷 Teglar — {len(tags)} ta (bosib nusxalang):" if i == 0 else "🏷 Teglar (davomi):"
            blocks.append(f"{label}\n<pre>{chunk}</pre>")
    else:
        blocks.append("🏷 Teglar: bu videoda teglar qo'yilmagan.")
    return blocks


@router.message(YT.thumb_download, F.text & ~F.text.startswith("/"))
async def thumb_download_process(message: Message, state: FSMContext) -> None:
    video_id = extract_video_id(message.text)
    if not video_id:
        await message.answer(
            "❌ Bu YouTube video havolasiga o'xshamadi.\n\n"
            "To'liq havolani yuboring (youtube.com/watch?v=..., youtu.be/... "
            "yoki youtube.com/shorts/...).",
            reply_markup=home_kb(),
        )
        return

    waiting = await message.answer("📥 Rasm va ma'lumotlar olinmoqda...")
    # Rasm (i.ytimg) va ma'lumot (Data API) parallel olinadi; API kalit bo'lmasa faqat rasm
    thumb_task = asyncio.to_thread(fetch_best_thumbnail, video_id)
    if yt_api_ready():
        info_task = asyncio.to_thread(fetch_video_info, video_id)
    else:
        info_task = asyncio.sleep(0, result=None)
    thumb, info = await asyncio.gather(thumb_task, info_task, return_exceptions=True)
    if isinstance(thumb, BaseException):
        logger.exception("Thumbnail yuklab olish xatosi (%s)", video_id, exc_info=thumb)
        thumb = None
    if isinstance(info, BaseException):
        logger.exception("Video ma'lumot xatosi (%s)", video_id, exc_info=info)
        info = None

    if not thumb and not info:
        await waiting.edit_text(
            "❌ Bu video topilmadi.\n\n"
            "Video o'chirilgan, yopiq (private) yoki havola noto'g'ri bo'lishi mumkin. "
            "Boshqa havola bilan urinib ko'ring.",
            reply_markup=home_kb(),
        )
        return

    if thumb:
        w, h = thumb["width"], thumb["height"]
        title = (info or {}).get("title") or thumb.get("title")
        caption = f"🖼 {title}\n" if title else ""
        caption += f"📐 {w}×{h} — {thumb['quality']} sifat\n"
        if w < 1280:
            caption += "ℹ️ YouTube bu video uchun bundan kattaroq rasm saqlamagan.\n"
        caption += "\nFayl ko'rinishida yuborildi — sifati siqilmagan."
        await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_DOCUMENT)
        await message.answer_document(
            BufferedInputFile(thumb["data"], filename=f"{video_id}_{w}x{h}.jpg"),
            caption=caption[:1024],
            reply_markup=None if info else thumb_download_again_kb(),
        )
    else:
        await message.answer("⚠️ Rasm topilmadi, lekin video ma'lumotlari quyida.")

    if info:
        await _send_seo_html(message, _video_info_blocks(info), last_kb=thumb_download_again_kb())

    await waiting.delete()
    await state.clear()
    logger.info("Video yuklab berildi: user=%s video=%s rasm=%s info=%s teglar=%d",
                message.from_user.id, video_id,
                f"{thumb['width']}x{thumb['height']}" if thumb else "-",
                bool(info), len((info or {}).get("tags") or []))


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
    if not _is_allowed(callback.from_user.id):
        await callback.answer("Avval /start bosib ro'yxatdan o'ting.", show_alert=True)
        return
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


# ============================================================
# Holat tuzoqlaridan saqlash — noto'g'ri kirish turida bot jim qolmasin
# (Bu handlerlar oxirida — faqat yuqoridagi aniq handlerlar ishlamasa fire bo'ladi)
# ============================================================

@router.message(StateFilter(
    YT.channel, YT.video, YT.channel_analysis,
    YT.avatar_name, YT.banner_name, YT.thumb_prompt, YT.thumb_edit, YT.thumb_text, YT.thumb_download,
))
async def _yt_expect_text(message: Message) -> None:
    """Matn kutilgan bosqichda rasm/stiker yuborilsa — jim qolmasdan yo'naltiradi."""
    await message.answer(
        "✍️ Iltimos, so'ralgan ma'lumotni MATN ko'rinishida yozing.\n"
        "Bekor qilish uchun 🏠 Bosh menyu tugmasini bosing.",
        reply_markup=home_kb(),
    )


@router.message(StateFilter(YT.thumb_position, YT.thumb_color))
async def _yt_expect_button(message: Message) -> None:
    """Tugma kutilgan bosqichda matn yozilsa — holatni saqlab, tugmani eslatadi."""
    await message.answer(
        "👆 Iltimos, yuqoridagi tugmalardan birini bosing "
        "(yoki 🏠 Bosh menyu bilan bekor qiling)."
    )
