"""Replicate API — Flux modeli orqali rasm yaratish (YouTube xizmatlari uchun)."""

import asyncio
import io
import logging
import urllib.request

import replicate

import time

from config import (
    FLUX_DEV_MODEL,
    FLUX_MODEL,
    FLUX_REDUX_MODEL,
    IDEOGRAM_MODEL,
    NANO_BANANA_MODEL,
    REPLICATE_API_TOKEN,
)

logger = logging.getLogger(__name__)


def _run_sync(prompt: str, aspect_ratio: str) -> bytes:
    """Replicate'ni sinxron chaqiradi va tayyor rasm baytlarini qaytaradi.
    Alohida ipda (thread) ishlatiladi — botni bloklamaslik uchun.
    """
    client = replicate.Client(api_token=REPLICATE_API_TOKEN)
    output = client.run(
        FLUX_MODEL,
        input={
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "output_format": "png",
            "num_outputs": 1,
            "go_fast": True,
        },
    )

    # Natija ro'yxat ko'rinishida keladi — birinchisini olamiz
    item = output[0] if isinstance(output, list) else output

    # replicate >= 1.0 da FileOutput obyekti — .read() bor
    if hasattr(item, "read"):
        return item.read()

    # Aks holda bu URL satr — yuklab olamiz
    with urllib.request.urlopen(str(item)) as resp:
        return resp.read()


def _run_ideogram_sync(prompt: str) -> bytes:
    """Ideogram v2 Turbo — banner uchun (2560x1440, 16:9). Sinxron."""
    client = replicate.Client(api_token=REPLICATE_API_TOKEN)
    output = client.run(
        IDEOGRAM_MODEL,
        input={
            "prompt": prompt,
            "aspect_ratio": "16:9",
            "style_type": "Design",
            "negative_prompt": "text, watermark, words, letters, blurry, low quality",
            "magic_prompt_option": "Off",
        },
    )
    item = output[0] if isinstance(output, list) else output
    if hasattr(item, "read"):
        return item.read()
    with urllib.request.urlopen(str(item)) as resp:
        return resp.read()


async def generate_banner_image(prompt: str) -> bytes:
    """Ideogram v2 Turbo orqali YouTube banner yaratadi (async).
    Kompozitsiya sifati Flux'dan yuqori — safe zone uchun mos.
    """
    if not REPLICATE_API_TOKEN:
        raise RuntimeError("REPLICATE_API_TOKEN sozlanmagan (.env faylga qo'shing)")
    logger.info("Ideogram banner so'rovi")
    return await asyncio.to_thread(_run_ideogram_sync, prompt)


async def generate_image(prompt: str, aspect_ratio: str = "1:1") -> bytes:
    """Flux orqali rasm yaratadi (async). Rasm baytlarini qaytaradi.

    aspect_ratio: '1:1' (avatar), '16:9' (banner, thumbnail).
    """
    if not REPLICATE_API_TOKEN:
        raise RuntimeError("REPLICATE_API_TOKEN sozlanmagan (.env faylga qo'shing)")
    logger.info("Replicate rasm so'rovi: aspect=%s", aspect_ratio)
    return await asyncio.to_thread(_run_sync, prompt, aspect_ratio)


def _run_redux_sync(image_bytes: bytes, aspect_ratio: str) -> bytes:
    """Flux Redux'ni sinxron chaqiradi — namuna rasmdan o'xshash rasm yaratadi."""
    client = replicate.Client(api_token=REPLICATE_API_TOKEN)
    bio = io.BytesIO(image_bytes)
    bio.name = "reference.jpg"  # mimetype aniqlash uchun
    output = client.run(
        FLUX_REDUX_MODEL,
        input={
            "redux_image": bio,
            "aspect_ratio": aspect_ratio,
            "num_outputs": 1,
            "output_format": "png",
        },
    )
    item = output[0] if isinstance(output, list) else output
    if hasattr(item, "read"):
        return item.read()
    with urllib.request.urlopen(str(item)) as resp:
        return resp.read()


async def generate_variation(image_bytes: bytes,
                             aspect_ratio: str = "16:9") -> bytes:
    """Namuna rasmga o'xshash yangi rasm yaratadi (image-to-image, async)."""
    if not REPLICATE_API_TOKEN:
        raise RuntimeError("REPLICATE_API_TOKEN sozlanmagan (.env faylga qo'shing)")
    logger.info("Replicate redux so'rovi: aspect=%s", aspect_ratio)
    return await asyncio.to_thread(_run_redux_sync, image_bytes, aspect_ratio)


# Sifatni oshiradigan umumiy prompt (img2img uchun)
_IMG2IMG_PROMPT = (
    "professional YouTube thumbnail, high quality, sharp focus, "
    "detailed, vibrant colors, dramatic lighting"
)


def _run_img2img_sync(image_bytes: bytes, prompt_strength: float) -> bytes:
    """Flux Dev'ni img2img rejimida chaqiradi — namuna rasmga o'xshash rasm.
    prompt_strength kichik bo'lsa — natija namunaga ko'proq o'xshaydi.
    """
    client = replicate.Client(api_token=REPLICATE_API_TOKEN)
    bio = io.BytesIO(image_bytes)
    bio.name = "input.jpg"  # mimetype aniqlash uchun
    output = client.run(
        FLUX_DEV_MODEL,
        input={
            "prompt": _IMG2IMG_PROMPT,
            "image": bio,
            "prompt_strength": prompt_strength,
            "num_outputs": 1,
            "output_format": "png",
        },
    )
    item = output[0] if isinstance(output, list) else output
    if hasattr(item, "read"):
        return item.read()
    with urllib.request.urlopen(str(item)) as resp:
        return resp.read()


async def generate_img2img(image_bytes: bytes,
                           prompt_strength: float = 0.3) -> bytes:
    """Namuna rasmga ~70% o'xshash yangi rasm yaratadi (img2img, async).

    prompt_strength=0.3 -> taxminan 70% o'xshash. Kattalashtirilsa ko'proq o'zgaradi.
    """
    if not REPLICATE_API_TOKEN:
        raise RuntimeError("REPLICATE_API_TOKEN sozlanmagan (.env faylga qo'shing)")
    logger.info("Replicate img2img so'rovi: strength=%s", prompt_strength)
    return await asyncio.to_thread(_run_img2img_sync, image_bytes, prompt_strength)


# ============================================================
# Nano Banana — thumbnail: prompt'dan yaratish / rasmni ko'rsatma bilan tahrirlash
# ============================================================

class ThumbModelError(RuntimeError):
    """Model rasm yarata olmadi (prompt tushunilmadi yoki xavfsizlik filtri)."""


_THUMB_CREATE_PREFIX = (
    "Create a YouTube video thumbnail (16:9): eye-catching, vivid colors, high contrast, "
    "sharp focus, professional composition, no watermark, no logo. "
    "If the request contains text to put on the image, render it exactly as written, "
    "big and bold with a contrasting outline. Request: "
)
_THUMB_REFERENCE_PREFIX = (
    "Create a YouTube video thumbnail (16:9) using the person/object from the provided image "
    "(keep their face and identity recognizable). Eye-catching, vivid colors, high contrast, "
    "professional composition, no watermark. If the request contains text to put on the image, "
    "render it exactly as written, big and bold. Request: "
)
_THUMB_EDIT_PREFIX = (
    "Edit the provided YouTube thumbnail image. Apply ONLY the requested change and keep "
    "everything else exactly the same (composition, people, style, text unless asked). "
    "If new text is requested, render it exactly as written. Change: "
)


def _read_output(output) -> bytes:
    item = output[0] if isinstance(output, list) else output
    if hasattr(item, "read"):
        return item.read()
    with urllib.request.urlopen(str(item)) as resp:
        return resp.read()


def _run_nano_sync(prompt: str, reference_bytes) -> bytes:
    """Nano Banana'ni sinxron chaqiradi. Kam kredit rejimida (6 so'rov/daqiqa) 429 kelsa
    12 soniya kutib 2 martagacha qayta uradi."""
    client = replicate.Client(api_token=REPLICATE_API_TOKEN)
    for attempt in range(3):
        inp = {"prompt": prompt, "aspect_ratio": "16:9", "output_format": "png"}
        if reference_bytes:
            bio = io.BytesIO(reference_bytes)
            bio.name = "reference.png"
            inp["image_input"] = [bio]
        try:
            return _read_output(client.run(NANO_BANANA_MODEL, input=inp))
        except replicate.exceptions.ModelError as e:
            raise ThumbModelError(str(e)) from e
        except replicate.exceptions.ReplicateError as e:
            msg = str(e).lower()
            if attempt < 2 and ("429" in msg or "throttled" in msg):
                logger.warning("Replicate throttle (kam kredit) — 12s kutib qayta: %s", attempt + 1)
                time.sleep(12)
                continue
            raise
    raise RuntimeError("Replicate: qayta urinishlar tugadi")


async def generate_thumbnail_nano(prompt: str, reference_bytes=None, edit: bool = False) -> bytes:
    """Thumbnail: matn prompt'dan yaratadi; reference_bytes berilsa — o'sha rasm asosida
    (edit=True: tayyor oblojkani ko'rsatma bo'yicha o'zgartirish; edit=False: o'quvchi
    yuborgan rasmdan oblojka yasash). PNG baytlarini qaytaradi."""
    if not REPLICATE_API_TOKEN:
        raise RuntimeError("REPLICATE_API_TOKEN sozlanmagan (.env faylga qo'shing)")
    if reference_bytes:
        prefix = _THUMB_EDIT_PREFIX if edit else _THUMB_REFERENCE_PREFIX
    else:
        prefix = _THUMB_CREATE_PREFIX
    logger.info("Nano Banana so'rovi: edit=%s ref=%s", edit, bool(reference_bytes))
    return await asyncio.to_thread(_run_nano_sync, prefix + prompt.strip(), reference_bytes)
