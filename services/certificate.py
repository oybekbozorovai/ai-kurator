"""Sertifikat render, ID generatsiya va ism tozalash."""
import io
import re
import secrets
import string

from PIL import Image, ImageDraw, ImageFont
import qrcode

from config import CERT_FONT, CERT_TEMPLATE, CERT_VERIFY_BASE_URL

# Kirill → lotin (o'zbek)
_TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd',
    'е': 'e', 'ё': 'yo', 'ж': 'j', 'з': 'z', 'и': 'i',
    'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n',
    'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't',
    'у': 'u', 'ф': 'f', 'х': 'x', 'ц': 'ts', 'ч': 'ch',
    'ш': 'sh', 'щ': 'sh', 'ъ': "'", 'ы': 'i', 'ь': "'",
    'э': 'e', 'ю': 'yu', 'я': 'ya',
    'ў': "o'", 'ғ': "g'", 'қ': 'q', 'ҳ': 'h',
}


def _cyrillic_to_latin(text: str) -> str:
    result = []
    for ch in text:
        lower_ch = ch.lower()
        if lower_ch in _TRANSLIT:
            rep = _TRANSLIT[lower_ch]
            if ch.isupper() and rep:
                rep = rep[0].upper() + rep[1:]
            result.append(rep)
        else:
            result.append(ch)
    return ''.join(result)


def clean_name(raw: str) -> str:
    """Ismni tozalaydi: kirill→lotin, bosh harf, raqam/emoji olib tashlaydi."""
    if not raw:
        raise ValueError("Ism bo'sh")
    text = raw.strip()
    text = _cyrillic_to_latin(text)
    # Faqat lotin harflari, apostrof, tire va probel
    text = re.sub(r"[^a-zA-Z\s'\-]", "", text)
    text = ' '.join(text.split())
    # Harfsiz qismlarni tozalab, minimal uzunlikni tekshirish
    letters_only = re.sub(r"[^a-zA-Z]", "", text)
    if len(letters_only) < 2:
        raise ValueError("Ism juda qisqa yoki noto'g'ri")
    return text.title()


# Chalkash O, 0, I, 1 chiqarib tashlangan alfavit
_ID_CHARS = ''.join(
    ch for ch in string.ascii_uppercase + string.digits
    if ch not in 'O0I1'
)


def generate_cert_id() -> str:
    """8 belgili URL-safe katta harf+raqam ID."""
    return ''.join(secrets.choice(_ID_CHARS) for _ in range(8))


def _fit_font(name: str, maxw: int = 1040, start: int = 160, lo: int = 95):
    """Font o'lchamini topadi: getbbox kengligi <= maxw bo'lguncha 3px kamaytiradi."""
    for size in range(start, lo - 1, -3):
        font = ImageFont.truetype(str(CERT_FONT), size)
        bbox = font.getbbox(name, anchor='ls')
        if bbox[2] - bbox[0] <= maxw:
            return font, bbox
    font = ImageFont.truetype(str(CERT_FONT), lo)
    return font, font.getbbox(name, anchor='ls')


def _rounded_card(size: tuple, rad: int = 24) -> Image.Image:
    """Oq yumaloq burchakli karta (RGBA, mask sifatida ishlatiladi)."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([(0, 0), (size[0] - 1, size[1] - 1)], radius=rad,
                        fill=(255, 255, 255, 255))
    return img


def render_certificate(full_name: str, cert_id: str) -> bytes:
    """Sertifikat rasmini PNG bytes sifatida qaytaradi."""
    img = Image.open(CERT_TEMPLATE).convert("RGB")
    d = ImageDraw.Draw(img)

    f, bb = _fit_font(full_name)
    w = bb[2] - bb[0]
    x = 717 - w // 2 - bb[0]
    d.text((x, 620), full_name, font=f, fill=(80, 112, 240), anchor="ls")

    qr = qrcode.QRCode(box_size=6, border=1)
    qr.add_data(CERT_VERIFY_BASE_URL + cert_id)
    qr.make(fit=True)
    q_img = qr.make_image(fill_color=(20, 20, 20), back_color="white")
    # PilImage wrapper → PIL Image
    if not isinstance(q_img, Image.Image):
        q_img = q_img.get_image()
    q = q_img.convert("RGB").resize((150, 150))

    card = _rounded_card((182, 182))
    cx, cy = 55, img.height - 182 - 45
    img.paste(card, (cx, cy), card)
    img.paste(q, (cx + 16, cy + 16))

    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
