"""7 kunlik kuzatuv: kunlik snapshot, kunlik farqlar, xabar matni va yakuniy hisobot ma'lumotlari."""
import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

from services import watch_store as ws
from services.competitor import TASHKENT, _fmt, _ts
from services.competitor_run import compact_videos
from services.youtube_api import fetch_channels_batch, fetch_recent_videos_full

logger = logging.getLogger(__name__)


def today_key() -> str:
    return datetime.now(TASHKENT).strftime("%Y-%m-%d")


def take_snapshots(channel_ids: List[str], day: str) -> Dict[str, dict]:
    """Bugun hali olinmagan kanallar uchun snapshot oladi (kanal statistikasi batch, videolar alohida)."""
    need = [c for c in channel_ids if not ws.get_snapshot(c, day)]
    if need:
        profiles = fetch_channels_batch(need)
        for cid, p in profiles.items():
            try:
                vids = fetch_recent_videos_full(p.get("uploads_playlist"), max_results=15)
            except Exception as e:  # noqa: BLE001
                logger.warning("Snapshot video xatosi (%s): %s", cid, e)
                vids = []
            ws.save_snapshot(cid, day, p.get("subscribers", 0), p.get("views", 0),
                             p.get("video_count", 0), compact_videos(vids))
    return {c: ws.get_snapshot(c, day) for c in channel_ids}


def _hm(iso: str) -> str:
    d = _ts(iso)
    return d.astimezone(TASHKENT).strftime("%H:%M") if d else "?"


def build_digest(watch: dict, snaps: Dict[str, dict], day: str) -> Tuple[dict, str]:
    """Bitta o'quvchi uchun kunlik ma'lumot: (AI uchun faktlar, tayyor matn)."""
    now = datetime.now(timezone.utc)
    day_no = watch["digests_sent"] + 1
    lines = [f"📊 Raqobatchilar kuzatuvi — {day_no}-kun / {watch['days']}",
             f"Yo'nalish: {watch['niche'] or '—'}", ""]
    facts_channels = []
    best = None  # (views_24h_gain, title, channel)
    total_new = 0
    for ch in watch["channels"]:
        cid = ch["channel_id"]
        snap = snaps.get(cid)
        if not snap:
            lines.append(f"▫️ {ch['title']} — ma'lumot olinmadi")
            continue
        prev = ws.previous_snapshot(cid, day)
        subs_delta = (snap["subs"] - prev["subs"]) if prev else None
        prev_views = {v["id"]: v["views"] for v in (prev["videos"] if prev else [])}
        vids = snap["videos"]
        new24 = [v for v in vids if _ts(v["published_at"]) and (now - _ts(v["published_at"])).total_seconds() <= 24 * 3600]
        last48 = [v for v in vids if _ts(v["published_at"]) and (now - _ts(v["published_at"])).total_seconds() <= 48 * 3600]
        views48 = sum(v["views"] for v in last48)
        gains = [(v["views"] - prev_views.get(v["id"], v["views"]), v) for v in vids if v["id"] in prev_views]
        gains.sort(key=lambda g: g[0], reverse=True)
        median = statistics.median([v["views"] for v in vids]) if vids else 0
        hot = [v for v in last48 if median and v["views"] >= 2 * median]
        total_new += len(new24)

        subs_txt = f"👥 {_fmt(snap['subs'])}" + (f" ({'+' if subs_delta >= 0 else ''}{subs_delta:,} kecha)" if subs_delta is not None else "")
        lines.append(f"▫️ {ch['title']} — {subs_txt}")
        if new24:
            lines.append(f"   🎬 Yangi video: {len(new24)} ta (" + ", ".join(_hm(v['published_at']) for v in new24) + ")")
            for v in new24[:3]:
                lines.append(f"   • «{v['title'][:70]}» — {_fmt(v['views'])} ko'rish, {v['duration_min']} daq, {v['tags']} teg, opisaniye {v['desc_len']} belgi")
        else:
            lines.append("   🎬 Oxirgi 24 soatda yangi video yo'q")
        if last48:
            lines.append(f"   👁 48 soatlik ko'rishlar: {_fmt(views48)} ({len(last48)} ta video)")
        if gains and gains[0][0] > 0:
            g, v = gains[0]
            lines.append(f"   📈 Kecha eng ko'p o'sgan: «{v['title'][:50]}» +{_fmt(g)}")
            if best is None or g > best[0]:
                best = (g, v["title"], ch["title"])
        if hot:
            lines.append("   🔥 Trend signal: «" + hot[0]["title"][:50] + "» — kanal o'rtachasidan 2× ko'p")
        lines.append("")
        facts_channels.append({
            "title": ch["title"], "subs": snap["subs"], "subs_delta": subs_delta,
            "new_videos_24h": [{"title": v["title"], "time": _hm(v["published_at"]), "views": v["views"],
                                "duration_min": v["duration_min"], "tags": v["tag_list"][:6],
                                "desc_len": v["desc_len"]} for v in new24[:3]],
            "views_48h": views48, "videos_48h": len(last48),
            "top_gain_24h": ({"title": gains[0][1]["title"], "gain": gains[0][0]} if gains and gains[0][0] > 0 else None),
            "hot": [v["title"] for v in hot[:2]],
        })
    if best:
        lines.append(f"🏆 Kun g'olibi: {best[2]} — «{best[1][:50]}» (+{_fmt(best[0])} ko'rish)")
    lines.append(f"🎬 Jami yangi videolar (24 soat): {total_new} ta")
    facts = {"day": day_no, "of_days": watch["days"], "niche": watch["niche"], "channels": facts_channels,
             "total_new_videos_24h": total_new}
    return facts, "\n".join(lines)


def weekly_summary(watch: dict) -> Tuple[List[List[str]], List[List[str]], dict]:
    """7 kun yakuni: kanallar jadvali, kunlar jadvali, AI uchun faktlar."""
    start_day = datetime.fromtimestamp(watch["started_at"], TASHKENT).strftime("%Y-%m-%d")
    start_dt = datetime.fromtimestamp(watch["started_at"], timezone.utc)
    channel_rows, facts = [], {"niche": watch["niche"], "channels": []}
    per_day: Dict[str, dict] = {}
    for ch in watch["channels"]:
        snaps = ws.snapshots_since(ch["channel_id"], start_day)
        if not snaps:
            continue
        first, last = snaps[0], snaps[-1]
        new_vids = [v for v in last["videos"] if _ts(v["published_at"]) and _ts(v["published_at"]) >= start_dt]
        views_sum = sum(v["views"] for v in new_vids)
        delta = last["subs"] - first["subs"]
        channel_rows.append([ch["title"][:30], f"{_fmt(first['subs'])} → {_fmt(last['subs'])}",
                             f"{'+' if delta >= 0 else ''}{delta:,}", f"{len(new_vids)}", _fmt(views_sum)])
        facts["channels"].append({"title": ch["title"], "subs_delta": delta, "new_videos": len(new_vids),
                                  "views_new_videos": views_sum,
                                  "best": (max(new_vids, key=lambda v: v["views"])["title"] if new_vids else None)})
        for i in range(1, len(snaps)):
            d = snaps[i]["day"]
            pd = per_day.setdefault(d, {"new": 0, "top": None, "subs": 0})
            pd["subs"] += snaps[i]["subs"] - snaps[i - 1]["subs"]
            prev_ids = {v["id"] for v in snaps[i - 1]["videos"]}
            fresh = [v for v in snaps[i]["videos"] if v["id"] not in prev_ids]
            pd["new"] += len(fresh)
            for v in fresh:
                if pd["top"] is None or v["views"] > pd["top"][1]:
                    pd["top"] = (f"{ch['title'][:18]}: {v['title'][:40]}", v["views"])
    day_rows = [[d, f"{pd['new']}", (f"{pd['top'][0]} ({_fmt(pd['top'][1])})" if pd["top"] else "—"),
                 f"{'+' if pd['subs'] >= 0 else ''}{pd['subs']:,}"] for d, pd in sorted(per_day.items())]
    return channel_rows, day_rows, facts
