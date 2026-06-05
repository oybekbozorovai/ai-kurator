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
