"""Pillow (PIL) yordamida rasmlarni tahrirlash — thumbnail ustiga matn yozish."""

import io
import logging
import os

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Shrift qidiriladigan joylar — iOS uslubiga yaqin zamonaviy sans-serif
_FONT_PATHS = [
    os.path.join(os.path.dirname(__file__), "..", "assets", "font.ttf"),
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",  # Railway/Ubuntu
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",          # Linux fallback
    "/System/Library/Fonts/HelveticaNeue.ttc",                       # macOS (iOS uslubi)
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",             # macOS fallback
    "/Library/Fonts/Arial Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
]

# YouTube banner safe area konstantalar (2560x1440)
_BANNER_W, _BANNER_H = 2560, 1440
_SAFE_X, _SAFE_Y = 640, 545       # safe area boshi
_SAFE_W, _SAFE_H = 1280, 350      # safe area o'lchami

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


_BANNER_FRAME_PATH = os.path.join(os.path.dirname(__file__), "..", "assets", "banner_frame.png")


def resize_image(image_bytes: bytes, width: int, height: int) -> bytes:
    """Rasmni berilgan o'lchamga keltiradi (PNG qaytaradi)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = img.resize((width, height), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def add_banner_text(image_bytes: bytes, text: str) -> bytes:
    """Banner rasmiga safe zone to'liq to'ldirib matn yozadi.

    Safe area: 1280x350 — matn shu zonani to'liq to'ldiradi.
    Font haqiqiy pixel balandligiga qarab avtomatik katta bo'ladi.
    Ko'p so'zli nomlar qatorlarga bo'linadi, har qator max kattalikda.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = img.resize((_BANNER_W, _BANNER_H), Image.LANCZOS)
    draw = ImageDraw.Draw(img)

    text_upper = text.upper()

    # To'liq safe area — hech qanday padding yo'q
    max_w = _SAFE_W   # 1280px
    max_h = _SAFE_H   # 350px

    # Eng katta mos fontni top — haqiqiy pixel balandligini o'lchaymiz
    font_size = 24
    chosen_lines = [text_upper]
    line_h_px = 100

    for fs in range(350, 23, -4):
        fnt = _load_font(fs)
        lines = _wrap_text(draw, text_upper, fnt, max_w)

        # Haqiqiy balandlik (formula emas, actual pixel)
        bbox = draw.textbbox((0, 0), "Ag", font=fnt)
        lh = bbox[3] - bbox[1]
        total_h = lh * len(lines)

        max_lw = max(draw.textlength(ln, font=fnt) for ln in lines)
        if max_lw <= max_w and total_h <= max_h:
            font_size = fs
            chosen_lines = lines
            line_h_px = lh
            break

    font = _load_font(font_size)
    total_h = line_h_px * len(chosen_lines)

    # Safe area markazida joylashtir
    cx = _SAFE_X + _SAFE_W // 2
    cy = _SAFE_Y + _SAFE_H // 2
    y_start = cy - total_h // 2

    stroke = max(6, font_size // 14)
    shadow_offset = max(5, font_size // 16)

    for i, line in enumerate(chosen_lines):
        lw = int(draw.textlength(line, font=font))
        x = cx - lw // 2
        y = y_start + i * line_h_px
        draw.text((x + shadow_offset, y + shadow_offset), line,
                  font=font, fill=(0, 0, 0, 220))
        draw.text((x, y), line, font=font,
                  fill="#FFFFFF", stroke_width=stroke, stroke_fill=(0, 0, 0))

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def overlay_banner_frame(image_bytes: bytes) -> bytes:
    """Banner rasmi (2560x1440) ustiga qurilma safe-zone ramkasini qo'yadi."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    img = img.resize((2560, 1440), Image.LANCZOS)
    if os.path.exists(_BANNER_FRAME_PATH):
        frame = Image.open(_BANNER_FRAME_PATH).convert("RGBA")
        frame = frame.resize((2560, 1440), Image.LANCZOS)
        img.paste(frame, (0, 0), mask=frame)
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG")
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
