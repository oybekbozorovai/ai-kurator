import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

import google.generativeai as genai

from config import GEMINI_API_KEY, GEMINI_MODEL, PROMPTS_DIR
from services import usage

logger = logging.getLogger(__name__)

genai.configure(api_key=GEMINI_API_KEY)

_model = genai.GenerativeModel(
    GEMINI_MODEL,
    generation_config={
        "temperature": 0.3,
        "max_output_tokens": 4096,
    },
)


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


async def ask_tutor(question: str, context: str, telegram_id=None) -> str:
    """Talabaning savoliga, RAG'dan topilgan kontekst asosida javob beradi."""
    prompt = _load_prompt("tutor").replace("{context}", context)
    full_prompt = f"{prompt}\n\nTalaba savoli: {question}"
    return await _generate(full_prompt, telegram_id=telegram_id, service="qa")


async def grade_homework(
    assignment: str,
    submission: str,
    context: str,
    image_path: Optional[Path] = None,
    telegram_id=None,
) -> str:
    """Uy vazifasini tekshirib, baho beradi."""
    prompt = (
        _load_prompt("grader")
        .replace("{context}", context)
        .replace("{assignment}", assignment or "(topshiriq matni berilmagan)")
        .replace("{submission}", submission or "(matn berilmagan)")
    )

    if image_path and image_path.exists():
        image_part = genai.upload_file(str(image_path))
        return await _generate([prompt, image_part], telegram_id=telegram_id, service="grader")

    return await _generate(prompt, telegram_id=telegram_id, service="grader")


async def _generate(content, telegram_id=None, service=None) -> str:
    def _call():
        response = _model.generate_content(content)
        text = response.text or ""
        # Iste'mol (token/xarajat) hisobi — best-effort, oqimni buzmaydi
        if service:
            usage.record_from_gemini(response, telegram_id, service, GEMINI_MODEL)
        # Tugallanish sababini log qilamiz — debug uchun
        try:
            fr = response.candidates[0].finish_reason.name
            if fr not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
                logger.warning("Gemini javobni to'liq tugatmadi: %s (uzunligi: %d)", fr, len(text))
        except Exception:
            pass
        return text or "(Gemini bo'sh javob qaytardi)"

    try:
        return await asyncio.to_thread(_call)
    except Exception as e:
        logger.exception("Gemini API error")
        return f"⚠️ Gemini bilan bog'lanishda xatolik: {e}"


# ============================================================
# YouTube SEO xizmatlari uchun funksiyalar
# ============================================================

def _extract_json(text: str) -> dict:
    """Gemini javobidan JSON qismini ajratib oladi (```json ... ``` ichidan ham)."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Javobdan JSON topilmadi")
    return json.loads(match.group(0))


async def generate_channel_seo(niche: str, telegram_id=None) -> dict:
    """Kanal SEO: 5 ta nom, tavsif, 15 ta kalit so'z.
    Qaytaradi: {"names": [...], "description": "...", "keywords": [...]}
    """
    prompt = (
        f"You are a YouTube SEO expert. Niche: {niche}\n\n"
        "Generate everything in ENGLISH (for an American YouTube audience), "
        "as JSON:\n"
        "- 5 catchy channel name options\n"
        "- a full channel description (200-300 words, keyword-rich)\n"
        "- exactly 10 keywords (only the most important, highest-impact ones)\n\n"
        "Return only JSON, nothing else.\n"
        'Format: {"names": [...], "description": "...", "keywords": [...]}'
    )
    text = await _generate(prompt, telegram_id=telegram_id, service="channel_seo")
    if text.startswith("⚠️"):
        raise RuntimeError(text)
    return _extract_json(text)


async def generate_video_seo(topic: str, telegram_id=None) -> dict:
    """Video SEO: 5 ta nom, opisaniye, 30 ta teg.
    Qaytaradi: {"titles": [...], "description": "...", "tags": [...]}
    """
    prompt = (
        f"YouTube video SEO. Topic: {topic}\n\n"
        "Generate everything in ENGLISH (for an American YouTube audience), "
        "as JSON:\n"
        "- 5 clickbait video titles (under 60 characters)\n"
        "- a SHORT video description (120-180 words total):\n"
        "  * 2-3 engaging intro sentences with keywords\n"
        "  * a timestamps list — each line: time + a SHORT title "
        "(e.g. '0:30 Sports car crash test'), NO long paragraphs\n"
        "  * 3-5 hashtags at the end\n"
        "- exactly 10 tags (only the most relevant, highest-impact ones)\n\n"
        "Return only JSON, nothing else.\n"
        'Format: {"titles": [...], "description": "...", "tags": [...]}'
    )
    text = await _generate(prompt, telegram_id=telegram_id, service="video_seo")
    if text.startswith("⚠️"):
        raise RuntimeError(text)
    return _extract_json(text)


# Har bir rasm turi uchun maxsus talablar (ingliz tilida — Flux uchun)
_IMAGE_RULES = {
    "avatar": (
        "1024x1024, square format, simple, memorable, clean, "
        "solid or transparent background, no text, "
        "works well as a small circular YouTube channel icon"
    ),
    "banner": (
        "photorealistic 8K, wide 16:9 cinematic composition, "
        "professional photography or high-quality 3D render, "
        "no text, no watermark, no logos, "
        "subjects centered in the middle horizontal band of the image"
    ),
    "thumbnail": (
        "YouTube video thumbnail, 16:9, dramatic, emotional, "
        "high contrast, bold colors, expressive, leave empty space for text"
    ),
}


def _digest_channel(data: dict) -> dict:
    """Kanal ma'lumotidan aniq raqamlarni (Python'da) hisoblaydi —
    LLM'ga sanashni ishonib qo'ymaymiz."""
    from collections import Counter
    from datetime import datetime, timezone

    videos = data.get("videos", []) or []
    dates = []
    for v in videos:
        ts = v.get("published_at", "")
        if not ts:
            continue
        try:
            dates.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
        except ValueError:
            pass
    dates.sort(reverse=True)  # eng yangisi birinchi

    # O'rtacha yuklash oralig'i (kun)
    avg_gap = None
    if len(dates) >= 2:
        gaps = [(dates[i] - dates[i + 1]).total_seconds() / 86400.0
                for i in range(len(dates) - 1)]
        if gaps:
            avg_gap = round(sum(gaps) / len(gaps), 1)

    # Hafta kunlari va soatlar (UTC)
    weekdays = ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]
    day_counts = Counter(weekdays[d.weekday()] for d in dates)
    hour_counts = Counter(d.astimezone(timezone.utc).hour for d in dates)

    titles = [v.get("title", "") for v in videos if v.get("title")]
    title_lengths = [len(t) for t in titles]
    avg_title_len = round(sum(title_lengths) / len(title_lengths)) if title_lengths else 0

    all_tags = []
    for v in videos:
        all_tags.extend(t.lower() for t in (v.get("tags") or []))
    top_tags = [t for t, _ in Counter(all_tags).most_common(15)]

    views = [v.get("views", 0) for v in videos]
    avg_views = round(sum(views) / len(views)) if views else 0
    top_videos = sorted(videos, key=lambda v: v.get("views", 0), reverse=True)[:5]

    return {
        "avg_gap_days": avg_gap,
        "uploads_per_week": round(7 / avg_gap, 1) if avg_gap else None,
        "top_days": [d for d, _ in day_counts.most_common(3)],
        "top_hours_utc": [h for h, _ in hour_counts.most_common(3)],
        "avg_title_len": avg_title_len,
        "sample_titles": titles[:10],
        "top_tags": top_tags,
        "uses_tags": bool(top_tags),
        "avg_views": avg_views,
        "top_videos": [
            {"title": v.get("title", ""), "views": v.get("views", 0)}
            for v in top_videos
        ],
        "sample_description": (videos[0].get("description", "")[:600] if videos else ""),
    }


async def analyze_channel(data: dict, telegram_id=None) -> str:
    """YouTube kanal tahlili — o'zbek tilida amaliy strategiya matni.
    data: services.youtube_api.fetch_channel_analysis() natijasi.
    """
    d = _digest_channel(data)
    facts = {
        "kanal_nomi": data.get("title", ""),
        "obunachilar": data.get("subscribers", 0),
        "umumiy_korishlar": data.get("views", 0),
        "videolar_soni": data.get("video_count", 0),
        "tahlildagi_oxirgi_videolar": len(data.get("videos", []) or []),
        "ortacha_yuklash_oraligi_kun": d["avg_gap_days"],
        "haftasiga_video": d["uploads_per_week"],
        "kop_yuklaydigan_kunlar": d["top_days"],
        "kop_yuklaydigan_soatlar_utc": d["top_hours_utc"],
        "ortacha_sarlavha_uzunligi": d["avg_title_len"],
        "ortacha_korishlar": d["avg_views"],
        "teglardan_foydalanadimi": d["uses_tags"],
        "kop_ishlatilgan_teglar": d["top_tags"],
        "sarlavha_namunalari": d["sample_titles"],
        "eng_kop_korilgan_videolar": d["top_videos"],
        "opisaniye_namunasi": d["sample_description"],
    }
    prompt = (
        "Sen tajribali YouTube strateg va SEO mutaxassisisan. Quyida bitta "
        "YouTube kanalning ochiq ma'lumotlari (raqamlar allaqachon hisoblangan) "
        "JSON ko'rinishida berilgan. Shu ma'lumot asosida O'ZBEK TILIDA, aniq va "
        "amaliy tahlil yoz. Raqamlarni o'zing qayta sanama — berilganini ishlat.\n\n"
        f"MA'LUMOT:\n{json.dumps(facts, ensure_ascii=False, indent=2)}\n\n"
        "Quyidagi bo'limlar bilan, qisqa va tushunarli yoz (emoji ishlatma, "
        "Markdown sarlavha # ishlatma, oddiy matn + bo'lim nomlari):\n"
        "1) Umumiy xulosa — kanal qanaqa, qanday holatda.\n"
        "2) Yuklash strategiyasi — qachon va qanchadan video chiqaryapti, "
        "bu yaxshimi, qanday yaxshilash mumkin.\n"
        "3) Sarlavhalar — qanday uslubda yozyapti (uzunlik, hook, clickbait), "
        "namuna asosida 3 ta yaxshilangan sarlavha varianti taklif qil.\n"
        "4) Teglar va opisaniye — to'g'ri ishlatyaptimi, nima yetishmayapti.\n"
        "5) Oblojka (thumbnail) — sarlavhalardan kelib chiqib qanday oblojka "
        "uslubi mos kelishi haqida tavsiya.\n"
        "6) 3 ta aniq keyingi qadam — o'quvchi bugun qila oladigan ish.\n\n"
        "Do'stona, ustozona ohangda yoz. Javob 350 so'zdan oshmasin."
    )
    text = await _generate(prompt, telegram_id=telegram_id, service="channel_analysis")
    if text.startswith("⚠️"):
        raise RuntimeError(text)
    return text.strip()


def _image_prompt_via_claude(user_input: str, kind: str) -> str:
    """Claude Haiku orqali Flux uchun ingliz tilidagi prompt yaratadi."""
    import os
    import anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY sozlanmagan")
    rules = _IMAGE_RULES.get(kind, _IMAGE_RULES["avatar"])
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": (
                f"User description (may be in Uzbek/Russian): {user_input}\n\n"
                f"Write a detailed English image generation prompt for Flux AI for a YouTube {kind}.\n"
                f"Requirements: {rules}.\n\n"
                "Output only the English prompt, nothing else."
            ),
        }],
    )
    return msg.content[0].text.strip().strip('"')


def _generate_banner_imagen_sync(prompt: str) -> bytes:
    """Imagen 3 orqali banner (16:9) sinxron yaratadi. google-genai SDK ishlatiladi."""
    import io as _io
    from google import genai as new_genai
    from google.genai import types as genai_types

    client = new_genai.Client(api_key=GEMINI_API_KEY)
    result = client.models.generate_images(
        model="imagen-3.0-generate-001",
        prompt=prompt,
        config=genai_types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio="16:9",
            output_mime_type="image/png",
        ),
    )
    image_bytes = result.generated_images[0].image.image_bytes
    return image_bytes


async def generate_banner_imagen(prompt: str) -> bytes:
    """Imagen 3 (Gemini API) orqali YouTube banner yaratadi — async."""
    return await asyncio.to_thread(_generate_banner_imagen_sync, prompt)


async def generate_image_prompt(user_input: str, kind: str, telegram_id=None) -> str:
    """Foydalanuvchi tavsifidan Flux AI uchun ingliz tilidagi rasm prompti yaratadi.
    kind: 'avatar' | 'banner' | 'thumbnail'
    Avval Claude Haiku ishlatadi; xato bo'lsa Gemini ga fallback.
    """
    try:
        return await asyncio.to_thread(_image_prompt_via_claude, user_input, kind)
    except Exception as claude_err:
        logger.warning("Claude prompt xatosi (%s), Gemini ga fallback: %s", kind, claude_err)

    rules = _IMAGE_RULES.get(kind, _IMAGE_RULES["avatar"])
    prompt = (
        f"Foydalanuvchi tavsifi: {user_input}\n\n"
        f"YouTube {kind} uchun ingliz tilida Flux AI uchun detallashtirilgan "
        f"rasm prompti yoz.\nTalab: {rules}.\n\n"
        "Faqat ingliz tilidagi promptni yoz, boshqa hech narsa yozma."
    )
    text = await _generate(prompt, telegram_id=telegram_id, service=kind)
    if text.startswith("⚠️"):
        raise RuntimeError(text)
    return text.strip().strip('"')
