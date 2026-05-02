import asyncio
import logging
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
