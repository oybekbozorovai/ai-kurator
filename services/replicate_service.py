"""Replicate API — Flux modeli orqali rasm yaratish (YouTube xizmatlari uchun)."""

import asyncio
import logging
import urllib.request

import replicate

from config import FLUX_MODEL, REPLICATE_API_TOKEN

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
