"""Raqobatchi kanallar analizi — sof hisob-kitob (LLM'siz).

Metodika (Oybek Bozorov):
1. Kamida 10 ta kanal: 5 tasi eski (>= 2 yil), 5 tasi yangi (<= 3 oy).
   - 10 ta topilmasa — yo'nalishda ishlamagan ma'qul.
   - Faqat eskilar bo'lsa — yo'nalish hozir trendda emas.
   - Faqat yangilar bo'lsa (katta eski kanal yo'q) — monetizatsiyaga ulanishi aniq emas.
2. Faollik: haftasiga kamida 3 ta video. Bo'lmasa — yo'nalishda muammo.
3. Strategiya: kuniga nechta video, davomiylik, 48 soatlik ko'rishlar, oxirgi 7 video,
   nom/teg/opisaniye uslubi, oblojkalar, chiqarish vaqti — o'xshashmi yoki har xilmi.
"""
import io
import re
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont

OLD_CHANNEL_DAYS = 2 * 365        # >= 2 yil -> "eski"
NEW_CHANNEL_DAYS = 90             # <= 90 kun -> "yangi"
MIN_CHANNELS = 10
MIN_OLD = 5
MIN_NEW = 5
ACTIVE_UPLOADS_PER_WEEK = 3.0
TASHKENT = timezone(timedelta(hours=5))
WEEKDAYS = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]

_LINK_RE = re.compile(
    r"(https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/[^\s<>\"']+)"
    r"|(?<![\w.])(@[\w.\-]{3,})"
    r"|\b(UC[\w-]{22})\b",
    re.I,
)


def parse_channel_refs(text: str) -> List[str]:
    """Matndan kanal havolalarini (URL / @handle / UC id) ajratadi, takrorlarsiz."""
    out, seen = [], set()
    for m in _LINK_RE.finditer(text or ""):
        ref = next(g for g in m.groups() if g)
        ref = ref.rstrip(".,;)")
        key = ref.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def _ts(iso: str) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_label(days: int) -> str:
    if days < 31:
        return f"{days} kun"
    months = days // 30
    if months < 12:
        return f"{months} oy"
    years, rem = divmod(months, 12)
    return f"{years} yil" + (f" {rem} oy" if rem else "")


def _fmt(n) -> str:
    """1234567 -> '1.2M', 12345 -> '12.3K'."""
    n = n or 0
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def analyze_channel(profile: dict, videos: list, now: Optional[datetime] = None) -> dict:
    """Bitta kanal uchun barcha ko'rsatkichlar. videos — yangidan eskiga."""
    now = now or datetime.now(timezone.utc)
    created = _ts(profile.get("created_at"))
    age_days = (now - created).days if created else 0
    if age_days >= OLD_CHANNEL_DAYS:
        category = "old"
    elif age_days <= NEW_CHANNEL_DAYS:
        category = "new"
    else:
        category = "mid"

    dated = [(v, _ts(v.get("published_at"))) for v in videos]
    dated = [(v, d) for v, d in dated if d]
    last7d = [v for v, d in dated if (now - d).days < 7]
    last28d = [v for v, d in dated if (now - d).days < 28]
    last48h = [v for v, d in dated if (now - d).total_seconds() <= 48 * 3600]
    uploads_per_week = round(len(last28d) / 4.0, 1)
    # Oxirgi 28 kun ichida 50 ta videodan ko'p chiqargan bo'lsa (ro'yxat to'lgan) — kamida shuncha
    per_day = round(uploads_per_week / 7.0, 2)

    durations = [v["duration_sec"] for v in videos if v.get("duration_sec")]
    long_durs = [d for d in durations if d > 60]
    dur_avg_min = round(statistics.mean(durations) / 60, 1) if durations else 0
    dur_med_min = round(statistics.median(durations) / 60, 1) if durations else 0
    dur_cv = (round(statistics.pstdev(durations) / statistics.mean(durations), 2)
              if len(durations) > 1 and statistics.mean(durations) > 0 else 0)
    shorts_share = round(100 * sum(1 for v in videos if v.get("is_short")) / len(videos)) if videos else 0

    last7_views = [v.get("views", 0) for v in videos[:7]]
    views_all = [v.get("views", 0) for v in videos]
    median_views = statistics.median(views_all) if views_all else 0

    hours = Counter(d.astimezone(TASHKENT).hour for _, d in dated)
    weekdays = Counter(d.astimezone(TASHKENT).weekday() for _, d in dated)
    top_hours = [h for h, _ in hours.most_common(3)]
    consistency = round(100 * sum(c for _, c in hours.most_common(2)) / len(dated)) if dated else 0

    eng = [((v.get("likes", 0) + v.get("comments", 0)) / v["views"]) for v in videos if v.get("views")]
    engagement = round(100 * statistics.mean(eng), 2) if eng else 0

    titles = [v.get("title", "") for v in videos if v.get("title")]
    t_len = round(statistics.mean(len(t) for t in titles)) if titles else 0
    t_num = round(100 * sum(1 for t in titles if re.search(r"\d", t)) / len(titles)) if titles else 0
    t_caps = round(100 * sum(1 for t in titles if sum(c.isupper() for c in t) > 0.5 * max(1, sum(c.isalpha() for c in t))) / len(titles)) if titles else 0
    t_q = round(100 * sum(1 for t in titles if "?" in t) / len(titles)) if titles else 0
    t_emoji = round(100 * sum(1 for t in titles if _EMOJI_RE.search(t)) / len(titles)) if titles else 0

    tag_counts = [len(v.get("tags") or []) for v in videos]
    tags_avg = round(statistics.mean(tag_counts), 1) if tag_counts else 0
    tags_share = round(100 * sum(1 for c in tag_counts if c) / len(tag_counts)) if tag_counts else 0
    all_tags = Counter(t.lower().strip() for v in videos for t in (v.get("tags") or []))
    top_tags = [t for t, _ in all_tags.most_common(12)]

    descs = [v.get("description", "") for v in videos]
    desc_len = round(statistics.mean(len(d) for d in descs)) if descs else 0
    desc_links = round(100 * sum(1 for d in descs if "http" in d) / len(descs)) if descs else 0

    outliers = [v for v in videos if median_views and v.get("views", 0) >= 3 * median_views]
    outliers.sort(key=lambda v: v.get("views", 0), reverse=True)
    best = max(videos, key=lambda v: v.get("views", 0)) if videos else None

    return {
        "channel_id": profile.get("channel_id", ""),
        "title": profile.get("title", ""),
        "url": profile.get("url", ""),
        "handle": profile.get("handle", ""),
        "subscribers": profile.get("subscribers", 0),
        "total_views": profile.get("views", 0),
        "video_count": profile.get("video_count", 0),
        "created_at": (created.date().isoformat() if created else ""),
        "age_days": age_days,
        "age_label": _age_label(age_days),
        "category": category,
        "videos_analyzed": len(videos),
        "uploads_7d": len(last7d),
        "uploads_28d": len(last28d),
        "uploads_per_week": uploads_per_week,
        "uploads_per_day": per_day,
        "active": uploads_per_week >= ACTIVE_UPLOADS_PER_WEEK,
        "last_upload": (dated[0][1].astimezone(TASHKENT).strftime("%Y-%m-%d %H:%M") if dated else ""),
        "days_since_upload": ((now - dated[0][1]).days if dated else None),
        "dur_avg_min": dur_avg_min,
        "dur_med_min": dur_med_min,
        "dur_min_min": round(min(durations) / 60, 1) if durations else 0,
        "dur_max_min": round(max(durations) / 60, 1) if durations else 0,
        "dur_cv": dur_cv,
        "long_avg_min": round(statistics.mean(long_durs) / 60, 1) if long_durs else 0,
        "shorts_share": shorts_share,
        "last7_views": last7_views,
        "last7_avg": round(statistics.mean(last7_views)) if last7_views else 0,
        "median_views": round(median_views),
        "views_48h": sum(v.get("views", 0) for v in last48h),
        "videos_48h": len(last48h),
        "reach_ratio": round(100 * (statistics.mean(last7_views) if last7_views else 0) / profile["subscribers"], 1) if profile.get("subscribers") else 0,
        "top_hours": top_hours,
        "hour_counts": dict(hours),
        "weekday_counts": dict(weekdays),
        "top_weekdays": [WEEKDAYS[d] for d, _ in weekdays.most_common(3)],
        "time_consistency": consistency,
        "engagement": engagement,
        "title_len": t_len,
        "title_numbers": t_num,
        "title_caps": t_caps,
        "title_question": t_q,
        "title_emoji": t_emoji,
        "sample_titles": titles[:6],
        "tags_avg": tags_avg,
        "tags_share": tags_share,
        "top_tags": top_tags,
        "desc_len": desc_len,
        "desc_links": desc_links,
        "sample_description": (descs[0][:400] if descs else ""),
        "outliers": [{"title": v["title"], "views": v["views"], "url": v["url"]} for v in outliers[:3]],
        "best_video": ({"title": best["title"], "views": best["views"], "url": best["url"]} if best else None),
        "thumbnails": [v.get("thumbnail") for v in videos[:3] if v.get("thumbnail")],
        "recent": [
            {"title": v["title"], "views": v["views"], "duration_min": round(v["duration_sec"] / 60, 1),
             "published": (_ts(v["published_at"]).astimezone(TASHKENT).strftime("%d.%m %H:%M") if _ts(v["published_at"]) else ""),
             "tags": len(v.get("tags") or []), "url": v["url"]}
            for v in videos[:7]
        ],
    }


def evaluate_niche(channels: List[dict]) -> dict:
    """Yo'nalish bo'yicha xulosa: aralashma (eski/yangi), faollik, yangi kanallar o'sishi, ball."""
    n = len(channels)
    old = [c for c in channels if c["category"] == "old"]
    new = [c for c in channels if c["category"] == "new"]
    mid = [c for c in channels if c["category"] == "mid"]
    active = [c for c in channels if c["active"]]
    reasons, warnings = [], []
    score = 0

    # 1) Aralashma
    if n < MIN_CHANNELS:
        warnings.append(f"Faqat {n} ta kanal topildi (kamida {MIN_CHANNELS} kerak). Metodika bo'yicha: 10 ta kanal topilmasa — bu yo'nalishda ishlamagan ma'qul.")
    if not new and old:
        mix_verdict = "Faqat eski kanallar bor, yangi (3 oygacha) kanal yo'q — yo'nalish HOZIR trendda emas."
        warnings.append(mix_verdict)
        score += 10
    elif new and not old:
        mix_verdict = "Faqat yangi kanallar bor, 2–3 yillik katta eski kanal yo'q — bu yo'nalish monetizatsiyaga ulanishi aniq bo'lmaydi."
        warnings.append(mix_verdict)
        score += 15
    elif len(old) >= MIN_OLD and len(new) >= MIN_NEW:
        mix_verdict = f"Aralashma to'g'ri: {len(old)} ta eski (2+ yil) va {len(new)} ta yangi (3 oygacha) kanal. Yo'nalish barqaror va hozir ham yangi kirayotganlar bor."
        reasons.append(mix_verdict)
        score += 40
    else:
        mix_verdict = f"Aralashma to'liq emas: {len(old)} ta eski, {len(new)} ta yangi, {len(mid)} ta o'rta yoshdagi kanal (kerak: 5 eski + 5 yangi)."
        warnings.append(mix_verdict)
        score += 25

    # 2) Faollik
    act_share = round(100 * len(active) / n) if n else 0
    if act_share >= 80:
        activity_verdict = f"Kanallarning {act_share}% i faol (haftasiga 3+ video). Yo'nalish jonli."
        reasons.append(activity_verdict)
        score += 30
    elif act_share >= 50:
        activity_verdict = f"Kanallarning faqat {act_share}% i haftasiga 3+ video chiqaradi. O'rtacha faollik — ehtiyot bo'ling."
        warnings.append(activity_verdict)
        score += 15
    else:
        activity_verdict = f"Kanallarning {act_share}% i faol. Ko'pchilik haftasiga 3 tadan kam video chiqaradi — yo'nalishda nimadir muammo bor."
        warnings.append(activity_verdict)
        score += 5

    # 3) Yangi kanallar o'sishi (traction)
    new_avg = round(statistics.mean(c["last7_avg"] for c in new)) if new else 0
    old_avg = round(statistics.mean(c["last7_avg"] for c in old)) if old else 0
    if new:
        if new_avg >= 1000:
            reasons.append(f"Yangi kanallar oxirgi 7 videoda o'rtacha {_fmt(new_avg)} ko'rish olyapti — yangi kirganlar ham o'sa oladi.")
            score += 30
        elif new_avg >= 200:
            warnings.append(f"Yangi kanallar oxirgi 7 videoda o'rtacha {_fmt(new_avg)} ko'rish olyapti — o'sish bor, lekin sekin.")
            score += 15
        else:
            warnings.append(f"Yangi kanallar oxirgi 7 videoda o'rtacha atigi {_fmt(new_avg)} ko'rish olyapti — yangi kirganlarga qiyin.")
            score += 5

    score = max(0, min(100, score))
    if score >= 70:
        label, emoji = "Ishlash mumkin", "🟢"
    elif score >= 45:
        label, emoji = "Ehtiyot bilan", "🟡"
    else:
        label, emoji = "Tavsiya etilmaydi", "🔴"

    return {
        "count": n, "old": len(old), "new": len(new), "mid": len(mid),
        "active": len(active), "active_share": act_share,
        "new_avg_views": new_avg, "old_avg_views": old_avg,
        "mix_verdict": mix_verdict, "activity_verdict": activity_verdict,
        "score": score, "label": label, "emoji": emoji,
        "reasons": reasons, "warnings": warnings,
    }


def cross_patterns(channels: List[dict]) -> dict:
    """Kanallar o'rtasidagi o'xshashlik: davomiylik, vaqt, nom uslubi, teglar, oblojka uchun tayyorgarlik."""
    if not channels:
        return {}
    durs = [c["dur_avg_min"] for c in channels if c["dur_avg_min"]]
    dur_cv = round(statistics.pstdev(durs) / statistics.mean(durs), 2) if len(durs) > 1 and statistics.mean(durs) else 0
    dur_similar = dur_cv < 0.35

    hour_total = Counter()
    for c in channels:
        for h, cnt in c["hour_counts"].items():
            hour_total[int(h)] += cnt
    top_hours = [h for h, _ in hour_total.most_common(4)]
    per_channel_top = [set(c["top_hours"][:2]) for c in channels if c["top_hours"]]
    common_hour = Counter(h for s in per_channel_top for h in s).most_common(1)
    time_similar = bool(common_hour and common_hour[0][1] >= max(2, len(channels) // 2))

    weekday_total = Counter()
    for c in channels:
        for d, cnt in c["weekday_counts"].items():
            weekday_total[int(d)] += cnt
    top_weekdays = [WEEKDAYS[d] for d, _ in weekday_total.most_common(3)]

    t_lens = [c["title_len"] for c in channels if c["title_len"]]
    title_similar = (statistics.pstdev(t_lens) / statistics.mean(t_lens) < 0.3) if len(t_lens) > 1 and statistics.mean(t_lens) else False

    tag_sets = [set(c["top_tags"]) for c in channels if c["top_tags"]]
    overlap = 0.0
    if len(tag_sets) > 1:
        pairs, tot = 0, 0.0
        for i in range(len(tag_sets)):
            for j in range(i + 1, len(tag_sets)):
                u = tag_sets[i] | tag_sets[j]
                if u:
                    tot += len(tag_sets[i] & tag_sets[j]) / len(u)
                    pairs += 1
        overlap = round(100 * tot / pairs) if pairs else 0
    tag_total = Counter()
    for c in channels:
        for t in c["top_tags"]:
            tag_total[t] += 1
    shared_tags = [t for t, n in tag_total.most_common(15) if n >= 2] or [t for t, _ in tag_total.most_common(10)]

    shorts = [c["shorts_share"] for c in channels]
    return {
        "dur_avg_all": round(statistics.mean(durs), 1) if durs else 0,
        "dur_min_all": min(durs) if durs else 0,
        "dur_max_all": max(durs) if durs else 0,
        "dur_similar": dur_similar,
        "shorts_avg": round(statistics.mean(shorts)) if shorts else 0,
        "top_hours": top_hours,
        "top_weekdays": top_weekdays,
        "time_similar": time_similar,
        "uploads_per_week_avg": round(statistics.mean(c["uploads_per_week"] for c in channels), 1),
        "title_len_avg": round(statistics.mean(t_lens)) if t_lens else 0,
        "title_similar": title_similar,
        "title_numbers_avg": round(statistics.mean(c["title_numbers"] for c in channels)),
        "title_caps_avg": round(statistics.mean(c["title_caps"] for c in channels)),
        "tags_share_avg": round(statistics.mean(c["tags_share"] for c in channels)),
        "tags_avg_all": round(statistics.mean(c["tags_avg"] for c in channels), 1),
        "tag_overlap": overlap,
        "shared_tags": shared_tags,
        "desc_len_avg": round(statistics.mean(c["desc_len"] for c in channels)),
        "engagement_avg": round(statistics.mean(c["engagement"] for c in channels), 2),
    }


def niche_keywords(channels: List[dict], limit: int = 4) -> str:
    """Qidiruv uchun yo'nalish kalit so'zlari (eng ko'p uchraydigan teglar; bo'lmasa nomlardan)."""
    tag_total = Counter()
    for c in channels:
        for t in c["top_tags"][:8]:
            if 3 <= len(t) <= 30:
                tag_total[t] += 1
    words = [t for t, _ in tag_total.most_common(limit)]
    if not words:
        w = Counter()
        for c in channels:
            for t in c["sample_titles"]:
                for token in re.findall(r"[\w']{4,}", t.lower()):
                    w[token] += 1
        words = [t for t, _ in w.most_common(limit)]
    return " ".join(words)


# ============================================================
# Oblojka jadvali (contact sheet) — Gemini vision va PDF uchun
# ============================================================

def build_contact_sheet(rows: list, font_path: str, thumb_w: int = 320, thumb_h: int = 180) -> bytes:
    """rows: [(label, [jpeg_bytes, ...]), ...] -> bitta PNG (har qator: nom + 3 ta oblojka)."""
    label_w = 230
    pad = 8
    per_row = 3
    W = label_w + per_row * (thumb_w + pad) + pad
    H = pad + len(rows) * (thumb_h + pad)
    sheet = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype(font_path, 20)
        small = ImageFont.truetype(font_path, 15)
    except OSError:
        font = small = ImageFont.load_default()
    y = pad
    for label, thumbs in rows:
        title, sub = (label.split("\n") + [""])[:2]
        draw.text((pad, y + 8), title[:22], fill=(20, 20, 20), font=font)
        draw.text((pad, y + 40), sub[:30], fill=(110, 110, 110), font=small)
        x = label_w
        for data in thumbs[:per_row]:
            try:
                im = Image.open(io.BytesIO(data)).convert("RGB").resize((thumb_w, thumb_h), Image.LANCZOS)
                sheet.paste(im, (x, y))
            except Exception:
                draw.rectangle((x, y, x + thumb_w, y + thumb_h), outline=(200, 200, 200))
            x += thumb_w + pad
        y += thumb_h + pad
    out = io.BytesIO()
    sheet.save(out, format="JPEG", quality=85, optimize=True)
    return out.getvalue()
