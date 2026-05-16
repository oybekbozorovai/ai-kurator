"""Pillow (PIL) yordamida rasmlarni tahrirlash — thumbnail ustiga matn yozish."""

import io
import logging
import os

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Shrift qidiriladigan joylar (birinchi topilgani ishlatiladi).
# Eng yaxshi natija uchun assets/font.ttf ga qalin (bold) shrift qo'ying.
_FONT_PATHS = [
    os.path.join(os.path.dirname(__file__), "..", "assets", "font.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",   # Linux / Railway
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",       # macOS
    "/Library/Fonts/Arial Bold.ttf",                           # macOS
    "C:\\Windows\\Fonts\\arialbd.ttf",                         # Windows
]

# Matn ranglari
_COLORS = {
    "yellow": "#FFD500",
    "white": "#FFFFFF",
    "red": "#FF2D2D",
    "green": "#22DD55",
}


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Mavjud shriftni yuklaydi. Topilmasa standart shriftga qaytadi."""
    for path in _FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    logger.warning("TTF shrift topilmadi — standart shrift ishlatiladi.")
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str,
               font: ImageFont.FreeTypeFont, max_width: int) -> list:
    """Matnni rasm kengligiga sig'adigan qatorlarga bo'ladi."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def resize_image(image_bytes: bytes, width: int, height: int) -> bytes:
    """Rasmni berilgan o'lchamga keltiradi (PNG qaytaradi)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = img.resize((width, height), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def add_text_to_thumbnail(image_bytes: bytes, text: str,
                          position: str = "bottom",
                          color: str = "yellow") -> bytes:
    """Thumbnail rasmiga (1280x720) ustidan matn qo'shadi.

    - Katta qalin shrift (75px), qora outline (5px)
    - position: 'top' yoki 'bottom'
    - color: 'yellow' | 'white' | 'red' | 'green'
    """
    fill = _COLORS.get(color, _COLORS["yellow"])

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = img.resize((1280, 720), Image.LANCZOS)
    draw = ImageDraw.Draw(img)

    font_size = 75
    font = _load_font(font_size)
    text = text.upper()  # thumbnail matni odatda katta harflarda

    lines = _wrap_text(draw, text, font, max_width=1180)
    line_height = int(font_size * 1.2)
    total_height = line_height * len(lines)

    if position == "top":
        y_start = 40
    else:  # bottom
        y_start = 720 - total_height - 50

    for i, line in enumerate(lines):
        y = y_start + i * line_height
        draw.text(
            (640, y),               # x — markaz (1280 / 2)
            line,
            font=font,
            fill=fill,
            stroke_width=5,         # qora outline
            stroke_fill="black",
            anchor="ma",            # m=markaz, a=yuqori
        )

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
