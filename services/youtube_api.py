"""YouTube Data API v3 — kanalning OCHIQ ma'lumotlarini tortadi (API key, OAuth emas).

Kanal analizi uchun: profil statistikasi + oxirgi videolar (sana, nom, tavsif,
teglar, ko'rishlar, thumbnail). Daromad bu yerda YO'Q (faqat OAuth+Analytics bilan).

A.Y.P.I Platforma lib/youtube.ts mantig'i Python'ga ko'chirildi.
YOUTUBE_API_KEY o'rnatilmagan bo'lsa — None qaytaradi (xizmat "tez orada" rejimida).
"""

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from config import YOUTUBE_API_KEY

logger = logging.getLogger(__name__)

API = "https://www.googleapis.com/youtube/v3"
TIMEOUT = 15
RECENT_VIDEOS = 15  # tahlil uchun olinadigan oxirgi videolar soni


def is_configured() -> bool:
    return bool(YOUTUBE_API_KEY)


def _get(path: str, params: dict) -> Optional[dict]:
    """API'ga GET so'rov. Xatoda None qaytaradi (loglaydi)."""
    params = {**params, "key": YOUTUBE_API_KEY}
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:300]
        logger.warning("YouTube API HTTP %s (%s): %s", e.code, path, body)
        return None
    except Exception:
        logger.exception("YouTube API so'rov xatosi: %s", path)
        return None


def _parse_input(raw: str) -> tuple:
    """Kanal linki/handle/ID'ni (kind, value) ga aylantiradi.
    kind: 'id' | 'handle' | 'username' | 'search'
    """
    s = (raw or "").strip()
    url_match = re.search(r"youtube\.com/(.+)$", s, re.I)
    path = url_match.group(1) if url_match else s

    m = re.search(r"channel/(UC[\w-]{20,})", path, re.I)
    if m:
        return ("id", m.group(1))
    if re.fullmatch(r"UC[\w-]{20,}", s):
        return ("id", s)
    m = re.search(r"@([\w.\-]+)", path)
    if m:
        return ("handle", "@" + m.group(1))
    if s.startswith("@"):
        return ("handle", s)
    m = re.search(r"user/([\w.\-]+)", path, re.I)
    if m:
        return ("username", m.group(1))
    m = re.search(r"c/([\w.\-]+)", path, re.I)
    if m:
        return ("search", m.group(1))
    return ("search", re.sub(r"^https?://", "", s))


def _search_channel_id(query: str) -> Optional[str]:
    """Qidiruv orqali channelId topadi (100 quota birlik — faqat zarur bo'lsa)."""
    data = _get("search", {
        "part": "snippet", "type": "channel", "maxResults": 1, "q": query,
    })
    items = (data or {}).get("items") or []
    if not items:
        return None
    return items[0].get("snippet", {}).get("channelId")


def _resolve_channel(raw: str) -> Optional[dict]:
    """Kanalni topadi: snippet+statistics+contentDetails (uploads playlisti).
    Qaytadi: channels.list item dict yoki None.
    """
    kind, value = _parse_input(raw)
    part = "snippet,statistics,contentDetails"

    def by(param, val):
        d = _get("channels", {"part": part, param: val})
        items = (d or {}).get("items") or []
        return items[0] if items else None

    if kind == "id":
        return by("id", value)
    if kind == "handle":
        return by("forHandle", value)
    if kind == "username":
        item = by("forUsername", value)
        if item:
            return item
        cid = _search_channel_id(value)
        return by("id", cid) if cid else None
    cid = _search_channel_id(value)
    return by("id", cid) if cid else None


def _recent_videos(uploads_playlist_id: str) -> list:
    """Uploads playlistidan oxirgi videolarni to'liq ma'lumoti bilan qaytaradi."""
    pl = _get("playlistItems", {
        "part": "contentDetails", "playlistId": uploads_playlist_id,
        "maxResults": RECENT_VIDEOS,
    })
    items = (pl or {}).get("items") or []
    video_ids = [
        it.get("contentDetails", {}).get("videoId")
        for it in items
        if it.get("contentDetails", {}).get("videoId")
    ]
    if not video_ids:
        return []

    vd = _get("videos", {
        "part": "snippet,statistics", "id": ",".join(video_ids),
    })
    out = []
    for v in (vd or {}).get("items") or []:
        sn = v.get("snippet", {})
        st = v.get("statistics", {})
        thumbs = sn.get("thumbnails", {})
        thumb = (thumbs.get("maxres") or thumbs.get("high")
                 or thumbs.get("medium") or thumbs.get("default") or {})
        out.append({
            "title": sn.get("title", ""),
            "description": sn.get("description", ""),
            "tags": sn.get("tags", []),
            "published_at": sn.get("publishedAt", ""),
            "views": int(st.get("viewCount", 0) or 0),
            "likes": int(st.get("likeCount", 0) or 0),
            "comments": int(st.get("commentCount", 0) or 0),
            "thumbnail": thumb.get("url"),
        })
    return out


def fetch_channel_analysis(raw_input: str) -> Optional[dict]:
    """Kanalni to'liq tahlil uchun tayyorlaydi.

    Qaytadi: {
        "channel_id", "title", "description", "subscribers", "views",
        "video_count", "thumbnail", "videos": [ {video dict}, ... ]
    }  yoki None (kanal topilmadi / API o'chiq).
    """
    if not is_configured():
        return None
    item = _resolve_channel(raw_input)
    if not item:
        return None

    sn = item.get("snippet", {})
    st = item.get("statistics", {})
    uploads = (item.get("contentDetails", {})
               .get("relatedPlaylists", {}).get("uploads"))
    videos = _recent_videos(uploads) if uploads else []

    thumbs = sn.get("thumbnails", {})
    thumb = (thumbs.get("high") or thumbs.get("medium")
             or thumbs.get("default") or {})

    return {
        "channel_id": item.get("id", ""),
        "title": sn.get("title", ""),
        "description": sn.get("description", ""),
        "subscribers": int(st.get("subscriberCount", 0) or 0),
        "views": int(st.get("viewCount", 0) or 0),
        "video_count": int(st.get("videoCount", 0) or 0),
        "thumbnail": thumb.get("url"),
        "videos": videos,
    }


def fetch_video_info(video_id: str) -> Optional[dict]:
    """Bitta videoning ochiq ma'lumotlari: nomi, tavsifi, teglari, kanal, sana, statistika.
    API kalit yo'q yoki video topilmasa None."""
    if not is_configured():
        return None
    data = _get("videos", {"part": "snippet,statistics", "id": video_id})
    items = (data or {}).get("items") or []
    if not items:
        return None
    sn = items[0].get("snippet") or {}
    st = items[0].get("statistics") or {}

    def _int(v) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "video_id": video_id,
        "title": (sn.get("title") or "").strip(),
        "description": (sn.get("description") or "").strip(),
        "tags": [str(t) for t in (sn.get("tags") or [])],
        "channel_title": (sn.get("channelTitle") or "").strip(),
        "published_at": (sn.get("publishedAt") or "")[:10],
        "views": _int(st.get("viewCount")),
        "likes": _int(st.get("likeCount")),
        "comments": _int(st.get("commentCount")),
    }


# ============================================================
# Raqobatchi kanallar analizi uchun kengaytirilgan ma'lumot
# ============================================================

ANALYSIS_VIDEOS = 50  # oxirgi 50 ta video (faol kanalda ~4 hafta)

_DUR_RE = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_duration(iso: str) -> int:
    """ISO 8601 davomiylik (PT1H2M3S) -> soniya."""
    m = _DUR_RE.fullmatch(iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _channel_from_video(video_id: str) -> Optional[str]:
    """Video linki berilsa — uning kanal ID'sini qaytaradi (1 birlik)."""
    d = _get("videos", {"part": "snippet", "id": video_id})
    items = (d or {}).get("items") or []
    return items[0].get("snippet", {}).get("channelId") if items else None


def fetch_channel_profile(raw_input: str) -> Optional[dict]:
    """Kanal profili (ochilgan sana, statistika, uploads playlist). Video linki ham qabul qilinadi."""
    if not is_configured():
        return None
    from services.thumbnail import extract_video_id
    item = None
    vid = extract_video_id(raw_input) if re.search(r"watch\?|youtu\.be/|/shorts/", raw_input, re.I) else None
    if vid:
        cid = _channel_from_video(vid)
        if cid:
            d = _get("channels", {"part": "snippet,statistics,contentDetails", "id": cid})
            items = (d or {}).get("items") or []
            item = items[0] if items else None
    if not item:
        item = _resolve_channel(raw_input)
    if not item:
        return None
    sn = item.get("snippet", {}) or {}
    st = item.get("statistics", {}) or {}
    thumbs = sn.get("thumbnails", {}) or {}
    thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {})
    custom = sn.get("customUrl") or ""
    cid = item.get("id", "")
    return {
        "channel_id": cid,
        "title": sn.get("title", ""),
        "handle": custom,
        "url": f"https://www.youtube.com/{custom}" if custom.startswith("@") else f"https://www.youtube.com/channel/{cid}",
        "created_at": sn.get("publishedAt", ""),
        "country": sn.get("country", ""),
        "description": sn.get("description", ""),
        "subscribers": int(st.get("subscriberCount", 0) or 0),
        "views": int(st.get("viewCount", 0) or 0),
        "video_count": int(st.get("videoCount", 0) or 0),
        "thumbnail": thumb.get("url"),
        "uploads_playlist": (item.get("contentDetails", {}) or {}).get("relatedPlaylists", {}).get("uploads"),
    }


def _videos_details(video_ids: list) -> list:
    """videos.list — 50 tagacha ID bitta so'rovda: snippet+statistics+contentDetails."""
    out = []
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        vd = _get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(chunk)})
        for v in (vd or {}).get("items") or []:
            sn = v.get("snippet", {}) or {}
            st = v.get("statistics", {}) or {}
            cd = v.get("contentDetails", {}) or {}
            thumbs = sn.get("thumbnails", {}) or {}
            thumb = (thumbs.get("medium") or thumbs.get("high") or thumbs.get("default") or {})
            dur = parse_duration(cd.get("duration", ""))
            out.append({
                "video_id": v.get("id", ""),
                "url": f"https://www.youtube.com/watch?v={v.get('id', '')}",
                "channel_id": sn.get("channelId", ""),
                "title": sn.get("title", ""),
                "description": sn.get("description", ""),
                "tags": [str(t) for t in (sn.get("tags") or [])],
                "published_at": sn.get("publishedAt", ""),
                "duration_sec": dur,
                "is_short": 0 < dur <= 60,
                "views": int(st.get("viewCount", 0) or 0),
                "likes": int(st.get("likeCount", 0) or 0),
                "comments": int(st.get("commentCount", 0) or 0),
                "thumbnail": thumb.get("url"),
            })
    return out


def fetch_recent_videos_full(uploads_playlist_id: str, max_results: int = ANALYSIS_VIDEOS) -> list:
    """Oxirgi `max_results` video (davomiylik bilan), yangidan eskiga."""
    if not uploads_playlist_id:
        return []
    pl = _get("playlistItems", {
        "part": "contentDetails", "playlistId": uploads_playlist_id,
        "maxResults": min(50, max_results),
    })
    ids = [it.get("contentDetails", {}).get("videoId")
           for it in (pl or {}).get("items") or []
           if it.get("contentDetails", {}).get("videoId")]
    videos = _videos_details(ids)
    videos.sort(key=lambda v: v.get("published_at", ""), reverse=True)
    return videos


def fetch_channels_batch(channel_ids: list) -> dict:
    """Bir nechta kanal statistikasi bitta so'rovda (50 tagacha). {channel_id: profile}."""
    out = {}
    ids = [c for c in channel_ids if c]
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        d = _get("channels", {"part": "snippet,statistics,contentDetails", "id": ",".join(chunk)})
        for item in (d or {}).get("items") or []:
            sn = item.get("snippet", {}) or {}
            st = item.get("statistics", {}) or {}
            custom = sn.get("customUrl") or ""
            cid = item.get("id", "")
            out[cid] = {
                "channel_id": cid,
                "title": sn.get("title", ""),
                "handle": custom,
                "url": f"https://www.youtube.com/{custom}" if custom.startswith("@") else f"https://www.youtube.com/channel/{cid}",
                "created_at": sn.get("publishedAt", ""),
                "subscribers": int(st.get("subscriberCount", 0) or 0),
                "views": int(st.get("viewCount", 0) or 0),
                "video_count": int(st.get("videoCount", 0) or 0),
                "uploads_playlist": (item.get("contentDetails", {}) or {}).get("relatedPlaylists", {}).get("uploads"),
            }
    return out


def search_niche_channels(query: str, exclude_ids: set, published_after_iso: str, max_results: int = 25) -> list:
    """Yo'nalish bo'yicha oxirgi paytda ko'p ko'rilgan videolarning kanallarini topadi
    (search.list = 100 birlik, faqat bir marta). Qaytadi: profil ro'yxati (obunachi bo'yicha)."""
    if not query.strip():
        return []
    d = _get("search", {
        "part": "snippet", "type": "video", "order": "viewCount",
        "publishedAfter": published_after_iso, "q": query, "maxResults": max_results,
        "relevanceLanguage": "uz",
    })
    cids = []
    for it in (d or {}).get("items") or []:
        cid = it.get("snippet", {}).get("channelId")
        if cid and cid not in exclude_ids and cid not in cids:
            cids.append(cid)
    profiles = fetch_channels_batch(cids[:20])
    return sorted(profiles.values(), key=lambda p: p.get("subscribers", 0), reverse=True)
