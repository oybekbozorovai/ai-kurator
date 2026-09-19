"""YouTube video oblojkasini (thumbnail) eng sifatli variantda yuklab olish.

API kalit kerak emas: rasm i.ytimg.com'dan, video nomi oEmbed'dan olinadi.
Sifat tartibi: maxresdefault (1280×720) → hq720 → sddefault (640×480) →
hqdefault (480×360) → mqdefault (320×180) → default (120×90).
"""
import io
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

TIMEOUT = 15
_UA = {"User-Agent": "Mozilla/5.0 (compatible; AI-Kurator-bot/1.0)"}

# Sifat bo'yicha kamayib boruvchi tartib: (fayl nomi, o'quvchiga ko'rsatiladigan nomi)
_QUALITIES = (
    ("maxresdefault.jpg", "maksimal"),
    ("hq720.jpg", "HD 720p"),
    ("sddefault.jpg", "SD"),
    ("hqdefault.jpg", "yuqori"),
    ("mqdefault.jpg", "o'rta"),
    ("default.jpg", "past"),
)

_ID_PATTERNS = (
    r"(?:youtube\.com|youtube-nocookie\.com)/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/|v/)([A-Za-z0-9_-]{11})",
    r"youtu\.be/([A-Za-z0-9_-]{11})",
)


def extract_video_id(text: str) -> Optional[str]:
    """Matndan YouTube video ID'sini topadi (watch, youtu.be, shorts, embed, live yoki
    to'g'ridan-to'g'ri 11 belgili ID). Topilmasa None."""
    s = (text or "").strip()
    if not s:
        return None
    for pat in _ID_PATTERNS:
        m = re.search(pat, s, re.I)
        if m:
            return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    return None


def _fetch(url: str) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code != 404:
            logger.warning("Thumbnail HTTP %s: %s", e.code, url)
        return None
    except Exception as e:
        logger.warning("Thumbnail so'rov xatosi (%s): %s", url, e)
        return None


def _video_title(video_id: str) -> Optional[str]:
    """oEmbed orqali video nomi (kalit kerak emas). Xatoda None."""
    watch = urllib.parse.quote(f"https://www.youtube.com/watch?v={video_id}", safe="")
    raw = _fetch(f"https://www.youtube.com/oembed?url={watch}&format=json")
    if not raw:
        return None
    try:
        return (json.loads(raw.decode("utf-8")).get("title") or "").strip() or None
    except Exception:
        return None


def fetch_best_thumbnail(video_id: str) -> Optional[dict]:
    """Eng sifatli mavjud oblojkani qaytaradi:
    {"video_id", "data" (JPEG bytes), "width", "height", "quality", "title"}.
    Hech qaysi variant topilmasa None (video yo'q/yopiq)."""
    for filename, label in _QUALITIES:
        data = _fetch(f"https://i.ytimg.com/vi/{video_id}/{filename}")
        if not data or len(data) < 1000:
            continue
        try:
            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size
        except Exception:
            continue
        if width < 200:
            continue  # YouTube'ning kulrang "mavjud emas" plakati (120×90)
        return {
            "video_id": video_id,
            "data": data,
            "width": width,
            "height": height,
            "quality": label,
            "title": _video_title(video_id),
        }
    return None
