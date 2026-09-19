"""Yo'nalish (nisha) tahlillari ombori — "qaysi nishaga kirsam" taqqoslash uchun (SQLite, yt.db)."""
import json
import sqlite3
import threading
import time
from typing import List, Optional

from config import YT_DB_PATH

_lock = threading.Lock()
MAX_PER_USER = 10  # o'quvchi uchun saqlanadigan oxirgi tahlillar


def _init() -> None:
    with sqlite3.connect(YT_DB_PATH) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS niche_analysis (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id  INTEGER NOT NULL,
                name         TEXT    NOT NULL,
                keywords     TEXT,
                created_at   INTEGER NOT NULL,
                score        INTEGER NOT NULL,
                label        TEXT,
                data_json    TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_niche_user ON niche_analysis(telegram_id, created_at)")


_init()


def save_analysis(telegram_id: int, name: str, keywords: str, niche_eval: dict, patterns: dict,
                  channels: List[dict]) -> int:
    """Tahlil xulosasini saqlaydi (jadval uchun kerakli raqamlar). Bir xil nom bo'lsa — almashtiradi."""
    data = {
        "count": niche_eval.get("count", 0), "old": niche_eval.get("old", 0),
        "new": niche_eval.get("new", 0), "mid": niche_eval.get("mid", 0),
        "active_share": niche_eval.get("active_share", 0),
        "new_avg_views": niche_eval.get("new_avg_views", 0),
        "old_avg_views": niche_eval.get("old_avg_views", 0),
        "reasons": niche_eval.get("reasons", []), "warnings": niche_eval.get("warnings", []),
        "uploads_per_week_avg": patterns.get("uploads_per_week_avg", 0),
        "dur_avg_all": patterns.get("dur_avg_all", 0), "shorts_avg": patterns.get("shorts_avg", 0),
        "engagement_avg": patterns.get("engagement_avg", 0), "top_hours": patterns.get("top_hours", [])[:3],
        "tag_overlap": patterns.get("tag_overlap", 0), "time_similar": patterns.get("time_similar", False),
        "dur_similar": patterns.get("dur_similar", False),
        "channels": [c.get("title", "")[:30] for c in channels][:15],
        "avg_subs": (round(sum(c.get("subscribers", 0) for c in channels) / len(channels)) if channels else 0),
    }
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        c.execute("DELETE FROM niche_analysis WHERE telegram_id=? AND lower(name)=lower(?)", (telegram_id, name))
        cur = c.execute(
            "INSERT INTO niche_analysis (telegram_id, name, keywords, created_at, score, label, data_json)"
            " VALUES (?,?,?,?,?,?,?)",
            (telegram_id, name[:60], (keywords or "")[:120], int(time.time()),
             int(niche_eval.get("score", 0)), niche_eval.get("label", ""), json.dumps(data, ensure_ascii=False)),
        )
        new_id = cur.lastrowid
        # eng eskilarini o'chiramiz
        c.execute(
            "DELETE FROM niche_analysis WHERE telegram_id=? AND id NOT IN ("
            " SELECT id FROM niche_analysis WHERE telegram_id=? ORDER BY created_at DESC LIMIT ?)",
            (telegram_id, telegram_id, MAX_PER_USER),
        )
    return int(new_id)


def list_analyses(telegram_id: int, limit: int = MAX_PER_USER) -> List[dict]:
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        rows = c.execute(
            "SELECT id, name, keywords, created_at, score, label, data_json FROM niche_analysis"
            " WHERE telegram_id=? ORDER BY created_at DESC LIMIT ?", (telegram_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = json.loads(r[6] or "{}")
        d.update({"id": r[0], "name": r[1], "keywords": r[2], "created_at": r[3], "score": r[4], "label": r[5]})
        out.append(d)
    return out


def count_analyses(telegram_id: int) -> int:
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        r = c.execute("SELECT COUNT(*) FROM niche_analysis WHERE telegram_id=?", (telegram_id,)).fetchone()
    return int(r[0]) if r else 0


def clear_analyses(telegram_id: int) -> int:
    with _lock, sqlite3.connect(YT_DB_PATH) as c:
        cur = c.execute("DELETE FROM niche_analysis WHERE telegram_id=?", (telegram_id,))
    return cur.rowcount


def rank_niches(niches: List[dict]) -> List[dict]:
    """Deterministik reyting: ball → yangi kanallar o'sishi → faollik. Har biriga sabab qo'shiladi."""
    ranked = sorted(niches, key=lambda n: (n["score"], n.get("new_avg_views", 0), n.get("active_share", 0)),
                    reverse=True)
    if not ranked:
        return []
    best_new = max(n.get("new_avg_views", 0) for n in ranked)
    best_act = max(n.get("active_share", 0) for n in ranked)
    least_comp = min((n.get("old_avg_views", 0) or 10 ** 12) for n in ranked)
    for i, n in enumerate(ranked, 1):
        why = []
        if i == 1:
            why.append("eng yuqori ball")
        if n.get("new_avg_views", 0) and n["new_avg_views"] == best_new:
            why.append("yangi kanallar eng tez o'sadi")
        if n.get("active_share", 0) == best_act and best_act:
            why.append("eng faol yo'nalish")
        if n.get("old_avg_views", 0) and n["old_avg_views"] == least_comp:
            why.append("eski kanallar eng kam ko'rish oladi — raqobat yumshoqroq")
        if n.get("new", 0) == 0:
            why.append("yangi kanal yo'q (trendda emas)")
        if n.get("old", 0) == 0:
            why.append("eski kanal yo'q (shubhali)")
        n["rank"] = i
        n["why"] = ", ".join(why) if why else "o'rtacha ko'rsatkichlar"
    return ranked
