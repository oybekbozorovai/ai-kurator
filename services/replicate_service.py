"""Replicate API — Flux modeli orqali rasm yaratish (YouTube xizmatlari uchun)."""

import asyncio
import io
import logging
import urllib.request

import replicate

from config import FLUX_MODEL, FLUX_REDUX_MODEL, REPLICATE_API_TOKEN

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
