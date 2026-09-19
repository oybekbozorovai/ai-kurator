"""Sertifikat PNG'ini Supabase Storage'ga yuklash (saytdagi gallereya uchun).

Bucket: certificates (public). Rasm: {cert_id}.png
Sayt gallereyasi rasmni deterministik URL orqali oladi:
  {SUPABASE_URL}/storage/v1/object/public/certificates/{cert_id}.png
Yuklash xatosi sertifikat berishni TO'XTATMAYDI (non-fatal).
"""
import json
import logging
import urllib.error
import urllib.request

from config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL

logger = logging.getLogger(__name__)

_BUCKET = "certificates"
_bucket_ready = False


def _req(url: str, data: bytes, content_type: str, extra: dict | None = None):
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": content_type,
    }
    if extra:
        headers.update(extra)
    return urllib.request.Request(url, data=data, headers=headers, method="POST")


def _ensure_bucket() -> None:
    """Bucket yo'q bo'lsa yaratadi (public). 400/409 = allaqachon bor."""
    global _bucket_ready
    if _bucket_ready:
        return
    body = json.dumps({"id": _BUCKET, "name": _BUCKET, "public": True}).encode()
    try:
        with urllib.request.urlopen(
            _req(f"{SUPABASE_URL}/storage/v1/bucket", body, "application/json"), timeout=15
        ):
            pass
        _bucket_ready = True
    except urllib.error.HTTPError as e:
        if e.code in (400, 409):  # allaqachon bor
            _bucket_ready = True
        else:
            logger.warning("Bucket yaratish xato: %s %s", e.code, e.read()[:150])
    except Exception as e:
        logger.warning("Bucket yaratish xato: %s", e)


def upload_cert_png(cert_id: str, png_bytes: bytes) -> bool:
    """PNG'ni storage'ga yuklaydi. Muvaffaqiyat=True. Xato bo'lsa False (jim)."""
    try:
        _ensure_bucket()
        with urllib.request.urlopen(
            _req(
                f"{SUPABASE_URL}/storage/v1/object/{_BUCKET}/{cert_id}.png",
                png_bytes, "image/png", {"x-upsert": "true"},
            ),
            timeout=30,
        ) as r:
            if r.status in (200, 201):
                return True
    except urllib.error.HTTPError as e:
        logger.warning("Sert rasm yuklash xato (%s): %s %s", cert_id, e.code, e.read()[:150])
    except Exception as e:
        logger.warning("Sert rasm yuklash xato (%s): %s", cert_id, e)
    return False


def delete_cert_png(cert_id: str) -> bool:
    """Bekor qilingan sertifikat rasmini storage'dan o'chiradi (non-fatal)."""
    try:
        req = urllib.request.Request(
            f"{SUPABASE_URL}/storage/v1/object/{_BUCKET}/{cert_id}.png",
            headers={
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            },
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status in (200, 204)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            logger.warning("Sert rasm o'chirish xato (%s): %s %s", cert_id, e.code, e.read()[:150])
    except Exception as e:
        logger.warning("Sert rasm o'chirish xato (%s): %s", cert_id, e)
    return False
