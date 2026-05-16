import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

import google.generativeai as genai

from config import GEMINI_API_KEY, GEMINI_MODEL, PROMPTS_DIR

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


async def ask_tutor(question: str, context: str) -> str:
    """Talabaning savoliga, RAG'dan topilgan kontekst asosida javob beradi."""
    prompt = _load_prompt("tutor").replace("{context}", context)
    full_prompt = f"{prompt}\n\nTalaba savoli: {question}"
    return await _generate(full_prompt)


async def grade_homework(
    assignment: str,
    submission: str,
    context: str,
    image_path: Optional[Path] = None,
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
        return await _generate([prompt, image_part])

    return await _generate(prompt)


async def _generate(content) -> str:
    def _call():
        response = _model.generate_content(content)
        text = response.text or ""
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


async def generate_channel_seo(niche: str) -> dict:
    """Kanal SEO: 5 ta nom, tavsif, 15 ta kalit so'z.
    Qaytaradi: {"names": [...], "description": "...", "keywords": [...]}
    """
    prompt = (
        f"Sen YouTube SEO eksperti. Mavzu: {niche}\n\n"
        "Bergin (o'zbek tilida, JSON formatda):\n"
        "- 5 ta jozibali kanal nomi varianti\n"
        "- To'liq kanal tavsifi (200-300 so'z, kalit so'zlar bilan)\n"
        "- 15 ta kalit so'z (keywords)\n\n"
        "Faqat JSON qaytar, boshqa hech narsa yozma.\n"
        'Format: {"names": [...], "description": "...", "keywords": [...]}'
    )
    text = await _generate(prompt)
    if text.startswith("⚠️"):
        raise RuntimeError(text)
    return _extract_json(text)


async def generate_video_seo(topic: str) -> dict:
    """Video SEO: 5 ta nom, opisaniye, 30 ta teg.
    Qaytaradi: {"titles": [...], "description": "...", "tags": [...]}
    """
    prompt = (
        f"YouTube video SEO. Mavzu: {topic}\n\n"
        "Bergin (o'zbek tilida, JSON):\n"
        "- 5 ta clickbait video nomi (60 belgidan kam)\n"
        "- Video opisaniyesi (500-800 so'z, vaqt belgilari bilan, kalit so'zlar)\n"
        "- 30 ta teg (ro'yxat sifatida)\n\n"
        "Faqat JSON qaytar, boshqa hech narsa yozma.\n"
        'Format: {"titles": [...], "description": "...", "tags": [...]}'
    )
    text = await _generate(prompt)
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
        "YouTube channel banner, 16:9, professional, modern, "
        "with a text-free safe zone in the center, visually balanced"
    ),
    "thumbnail": (
        "YouTube video thumbnail, 16:9, dramatic, emotional, "
        "high contrast, bold colors, expressive, leave empty space for text"
    ),
}


async def generate_image_prompt(user_input: str, kind: str) -> str:
    """Foydalanuvchi tavsifidan Flux AI uchun ingliz tilidagi rasm prompti yaratadi.
    kind: 'avatar' | 'banner' | 'thumbnail'
    """
    rules = _IMAGE_RULES.get(kind, _IMAGE_RULES["avatar"])
    prompt = (
        f"Foydalanuvchi tavsifi: {user_input}\n\n"
        f"YouTube {kind} uchun ingliz tilida Flux AI uchun detallashtirilgan "
        f"rasm prompti yoz.\nTalab: {rules}.\n\n"
        "Faqat ingliz tilidagi promptni yoz, boshqa hech narsa yozma."
    )
    text = await _generate(prompt)
    if text.startswith("⚠️"):
        raise RuntimeError(text)
    return text.strip().strip('"')
