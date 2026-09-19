"""Raqobatchi analizi — tarmoq bilan ishlaydigan bosqichlar (thread'da chaqiriladi)."""
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

from services import watch_store as ws
from services.competitor import (
    TASHKENT,
    _age_label,
    _ts,
    analyze_channel,
    build_contact_sheet,
    niche_keywords,
)
from services.report_pdf import FONT_BOLD
from services.youtube_api import (
    fetch_channel_profile,
    fetch_recent_videos_full,
    search_niche_channels,
)

logger = logging.getLogger(__name__)
MAX_CHANNELS = 15  # kvota himoyasi


def collect_channels(refs: List[str]) -> Tuple[List[dict], List[dict], List[str]]:
    """Har havola uchun profil + oxirgi 50 video (parallel). Qaytadi:
    (metrics ro'yxati, raw [{profile, videos}], topilmagan havolalar)."""
    refs = refs[:MAX_CHANNELS]
    now = datetime.now(timezone.utc)

    def one(ref):
        try:
            p = fetch_channel_profile(ref)
            if not p:
                return ref, None, []
            return ref, p, fetch_recent_videos_full(p.get("uploads_playlist"))
        except Exception as e:  # noqa: BLE001
            logger.warning("Kanal olishda xato (%s): %s", ref, e)
            return ref, None, []

    metrics, raw, unresolved, seen = [], [], [], set()
    with ThreadPoolExecutor(max_workers=5) as ex:
        for ref, p, vids in ex.map(one, refs):
            if not p:
                unresolved.append(ref)
                continue
            if p["channel_id"] in seen:
                continue  # bir kanalning ikki xil havolasi
            seen.add(p["channel_id"])
            raw.append({"profile": p, "videos": vids})
            metrics.append(analyze_channel(p, vids, now))
    return metrics, raw, unresolved


def _get(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.read()
    except Exception:
        return b""


def build_sheet(channels: List[dict]) -> Tuple[bytes, List[str]]:
    """Har kanalning oxirgi 3 oblojkasidan jadval (PNG) va qator nomlari."""
    cat = {"old": "eski", "new": "yangi", "mid": "o'rta"}
    rows, labels = [], []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for c in channels:
            imgs = [i for i in ex.map(_get, c.get("thumbnails", [])[:3]) if i]
            rows.append((f"{c['title']}\n{cat[c['category']]} · {c['age_label']}", imgs))
            labels.append(c["title"])
    if not rows:
        return b"", labels
    return build_contact_sheet(rows, str(FONT_BOLD)), labels


def find_suggestions(channels: List[dict]) -> Tuple[str, List[dict]]:
    """Yo'nalish kalit so'zlari bo'yicha YouTube qidiruvi (100 birlik) — boshqa kanallar."""
    query = niche_keywords(channels)
    if not query:
        return "", []
    since = (datetime.now(timezone.utc) - timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ")
    exclude = {c["channel_id"] for c in channels}
    try:
        found = search_niche_channels(query, exclude, since)
    except Exception as e:  # noqa: BLE001
        logger.warning("Qidiruv xatosi: %s", e)
        found = []
    now = datetime.now(timezone.utc)
    out = []
    for p in found:
        created = _ts(p.get("created_at"))
        p["age_label"] = _age_label((now - created).days) if created else ""
        if p.get("subscribers", 0) >= 1000:
            out.append(p)
    return query, out[:12]


def baseline_snapshots(raw: List[dict]) -> None:
    """Analiz kuni uchun boshlang'ich snapshot (ertangi kunlik farq shundan hisoblanadi)."""
    day = datetime.now(TASHKENT).strftime("%Y-%m-%d")
    for item in raw:
        p, vids = item["profile"], item["videos"]
        ws.save_snapshot(
            p["channel_id"], day, p.get("subscribers", 0), p.get("views", 0), p.get("video_count", 0),
            compact_videos(vids),
        )


def compact_videos(vids: List[dict], limit: int = 15) -> List[dict]:
    return [{
        "id": v.get("video_id"), "title": v.get("title", ""), "published_at": v.get("published_at", ""),
        "views": v.get("views", 0), "likes": v.get("likes", 0),
        "duration_min": round((v.get("duration_sec") or 0) / 60, 1),
        "tags": len(v.get("tags") or []), "tag_list": (v.get("tags") or [])[:8],
        "desc_len": len(v.get("description") or ""), "url": v.get("url", ""),
    } for v in vids[:limit]]
