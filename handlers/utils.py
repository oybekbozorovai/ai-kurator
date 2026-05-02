"""Yordamchi funksiyalar — xabarlarni Telegram cheklovlariga moslashtirish."""
from typing import List

TELEGRAM_MAX_LENGTH = 4000  # 4096 - xavfsizlik chegarasi


def split_for_telegram(text: str, limit: int = TELEGRAM_MAX_LENGTH) -> List[str]:
    """Uzun xabarni paragraflar bo'yicha bir nechta xabarga bo'ladi."""
    if len(text) <= limit:
        return [text]

    parts: List[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 <= limit:
            current = f"{current}\n\n{paragraph}" if current else paragraph
        else:
            if current:
                parts.append(current)
            if len(paragraph) <= limit:
                current = paragraph
            else:
                # juda uzun paragrafni so'z bo'yicha bo'lamiz
                while len(paragraph) > limit:
                    cut = paragraph.rfind(" ", 0, limit)
                    if cut < 0:
                        cut = limit
                    parts.append(paragraph[:cut])
                    paragraph = paragraph[cut:].lstrip()
                current = paragraph
    if current:
        parts.append(current)
    return parts
