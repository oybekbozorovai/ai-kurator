"""PDF hisobotlar (fpdf2, DejaVu Sans — lotin + kirill).

Har sahifa pastida majburiy eslatma: Oybek Bozorov AI yordamchisi analiz qildi,
AI adashishi mumkin — 100% ishonmang.
"""
import io
import logging
import re
from datetime import datetime
from typing import List, Optional

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

from config import BASE_DIR

logging.getLogger("fontTools").setLevel(logging.ERROR)
logging.getLogger("fpdf").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

FONT_REG = BASE_DIR / "assets" / "fonts" / "DejaVuSans.ttf"
FONT_BOLD = BASE_DIR / "assets" / "fonts" / "DejaVuSans-Bold.ttf"

DISCLAIMER = (
    "Bu hisobotni Oybek Bozorov AI yordamchisi analiz qilib berdi. "
    "AI adashishi mumkin — 100% ishonmang, raqamlarni o'zingiz ham tekshiring."
)

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]"
)
_MD_RE = re.compile(r"(\*\*|__|`|#+\s?)")

BLUE = (26, 115, 232)
DARK = (33, 37, 41)
GRAY = (110, 110, 110)
LIGHT = (243, 245, 248)
GREEN = (30, 150, 80)
YELLOW = (220, 160, 0)
RED = (210, 50, 50)


def _clean(text: str) -> str:
    """Emoji va markdown belgilarini olib tashlaydi (shriftda emoji yo'q)."""
    text = _EMOJI_RE.sub("", str(text or ""))
    text = _MD_RE.sub("", text)
    return text.replace("’", "'").strip()


def _fmt(n) -> str:
    n = n or 0
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


class ReportPDF(FPDF):
    def __init__(self, doc_title: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.doc_title = doc_title
        self.add_font("dv", "", str(FONT_REG))
        self.add_font("dv", "B", str(FONT_BOLD))
        self.add_font("dv", "I", str(FONT_REG))
        self.add_font("dv", "BI", str(FONT_BOLD))
        self.set_margins(16, 18, 16)
        self.set_auto_page_break(auto=True, margin=24)

    def _mc(self, w, h, txt, **kw):
        """multi_cell — keyin kursor CHAP chegaraga, keyingi qatorga (fpdf2 default'i o'ngga qoldiradi)."""
        self.set_x(self.l_margin) if kw.pop("reset_x", True) else None
        self.multi_cell(w, h, txt, new_x=XPos.LMARGIN, new_y=YPos.NEXT, **kw)

    # --- har sahifa ---
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("dv", "", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 6, self.doc_title, align="L")
        self.ln(8)
        self.set_text_color(*DARK)

    def footer(self):
        self.set_y(-20)
        self.set_draw_color(220, 220, 220)
        self.line(16, self.get_y(), self.w - 16, self.get_y())
        self.ln(1)
        self.set_font("dv", "", 7.5)
        self.set_text_color(*GRAY)
        self._mc(0, 4, DISCLAIMER, align="C")
        self.set_font("dv", "", 7.5)
        self.cell(0, 4, f"Sahifa {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(*DARK)

    # --- elementlar ---
    def h1(self, text: str):
        self.ln(2)
        self.set_font("dv", "B", 16)
        self.set_text_color(*BLUE)
        self._mc(0, 8, _clean(text))
        self.set_text_color(*DARK)
        self.ln(1)

    def h2(self, text: str):
        self.ln(3)
        self.set_font("dv", "B", 12)
        self.set_text_color(*DARK)
        self._mc(0, 6.5, _clean(text))
        self.ln(1)

    def para(self, text: str, size: float = 10, color=DARK):
        self.set_font("dv", "", size)
        self.set_text_color(*color)
        self._mc(0, 5.2, _clean(text))
        self.set_text_color(*DARK)
        self.ln(1.5)

    def bullet(self, text: str, size: float = 10):
        self.set_font("dv", "", size)
        x = self.l_margin + 3
        self.set_x(x)
        self.cell(4, 5.2, "•")
        self.set_x(x + 4)
        self._mc(self.w - x - 4 - self.r_margin, 5.2, _clean(text), reset_x=False)
        self.ln(0.5)

    def kv(self, key: str, value: str):
        self.set_font("dv", "B", 10)
        self.cell(52, 5.5, _clean(key))
        self.set_font("dv", "", 10)
        self._mc(0, 5.5, _clean(value), reset_x=False)

    def box(self, title: str, lines: List[str], color=BLUE):
        """Rangli chegarali xulosa qutisi."""
        self.ln(2)
        x, y = self.l_margin, self.get_y()
        w = self.w - self.l_margin - self.r_margin
        self.set_fill_color(*LIGHT)
        self.set_draw_color(*color)
        # balandlikni oldindan hisoblash qiyin — avval yozamiz, keyin chegara chizamiz
        start_page = self.page_no()
        self.set_xy(x + 3, y + 3)
        self.set_font("dv", "B", 12)
        self.set_text_color(*color)
        self._mc(w - 6, 6.5, _clean(title), reset_x=False)
        self.set_text_color(*DARK)
        self.set_font("dv", "", 10)
        for ln in lines:
            self.set_x(x + 3)
            self._mc(w - 6, 5.2, _clean(ln), reset_x=False)
        end_y = self.get_y() + 3
        if self.page_no() == start_page:
            self.rect(x, y, w, end_y - y)
        self.set_y(end_y + 2)

    def table(self, headers: List[str], rows: List[List[str]], widths: Optional[List[float]] = None,
              size: float = 8.5):
        self.set_font("dv", "", size)
        with super().table(
            col_widths=widths, text_align="LEFT", line_height=size * 0.55,
            headings_style=FontFace(emphasis="BOLD", fill_color=(232, 238, 247)),
            borders_layout="MINIMAL",
        ) as t:
            hr = t.row()
            for h in headers:
                hr.cell(_clean(h))
            for r in rows:
                tr = t.row()
                for c in r:
                    tr.cell(_clean(str(c)))
        self.ln(2)

    def image_bytes(self, data: bytes, w: Optional[float] = None, min_avail: float = 70):
        """Rasmni sahifaga SIG'ADIGAN qilib qo'yadi: kenglik va qolgan balandlikdan kichigini oladi.
        Qolgan joy juda kam bo'lsa — yangi sahifa."""
        from PIL import Image as _Image
        max_w = w or (self.w - self.l_margin - self.r_margin)
        avail = self.h - self.b_margin - self.get_y() - 4
        if avail < min_avail:
            self.add_page()
            avail = self.h - self.b_margin - self.get_y() - 4
        try:
            with _Image.open(io.BytesIO(data)) as im:
                iw, ih = im.size
        except Exception:
            iw, ih = 16, 9
        scale = min(max_w / iw, avail / ih)
        self.image(io.BytesIO(data), x=self.l_margin, w=iw * scale, h=ih * scale)
        self.set_y(self.get_y() + 2)
        self.set_x(self.l_margin)

    def ai_text(self, text: str):
        """'## ' sarlavha, '- ' ro'yxat, qolgani paragraf."""
        for line in (text or "").split("\n"):
            s = line.strip()
            if not s:
                continue
            if s.startswith("## "):
                self.h2(s[3:])
            elif s.startswith("# "):
                self.h2(s[2:])
            elif s.startswith(("- ", "• ", "* ")):
                self.bullet(s[2:])
            elif re.match(r"^\d+[.)]\s", s):
                self.bullet(s)
            else:
                self.para(s)


# ============================================================
# Raqobatchi kanallar hisoboti
# ============================================================

def build_competitor_report(
    student_name: str,
    channels: List[dict],
    niche_eval: dict,
    patterns: dict,
    ai_text: str,
    thumbs_text: str,
    sheet_png: Optional[bytes],
    suggestions: List[dict],
    unresolved: List[str],
    niche_label: str = "",
) -> bytes:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    pdf = ReportPDF("Raqobatchi kanallar analizi — YouTube AI kursi")
    pdf.alias_nb_pages()
    pdf.add_page()

    # --- Muqova ---
    pdf.ln(30)
    pdf.set_font("dv", "B", 24)
    pdf.set_text_color(*BLUE)
    pdf._mc(0, 11, "Raqobatchi kanallar analizi")
    pdf.set_font("dv", "", 13)
    pdf.set_text_color(*DARK)
    pdf.ln(2)
    pdf._mc(0, 7, _clean(f"Yo'nalish: {niche_label or 'kanallar ro‘yxati bo‘yicha'}"))
    pdf.ln(4)
    pdf.set_font("dv", "", 10.5)
    pdf.set_text_color(*GRAY)
    pdf._mc(0, 6, _clean(f"O'quvchi: {student_name}\nSana: {now}\n"
                                f"Tahlil qilingan kanallar: {len(channels)} ta, "
                                f"videolar: {sum(c['videos_analyzed'] for c in channels)} ta"))
    pdf.set_text_color(*DARK)
    pdf.ln(6)

    color = GREEN if niche_eval["score"] >= 70 else (YELLOW if niche_eval["score"] >= 45 else RED)
    lines = [f"Yo'nalish salomatligi: {niche_eval['score']}/100"]
    lines += [f"+ {r}" for r in niche_eval["reasons"]]
    lines += [f"! {w}" for w in niche_eval["warnings"]]
    pdf.box(f"Xulosa: {niche_eval['label']}", lines, color=color)

    pdf.ln(4)
    pdf.set_font("dv", "B", 10)
    pdf._mc(0, 5.5, "Metodika (Oybek Bozorov):")
    for r in (
        "Kamida 10 ta kanal: 5 tasi eski (2+ yil, obunachisi ko'p), 5 tasi yangi (3 oygacha, prosmotri ko'p). 10 ta topilmasa — yo'nalishda ishlamagan ma'qul.",
        "Faqat eski kanallar bo'lsa — yo'nalish hozir trendda emas. Faqat yangilar bo'lsa — yo'nalish monetizatsiyaga ulanishi aniq bo'lmaydi.",
        "Faollik: har kanal haftasiga kamida 3 ta video. Bo'lmasa — yo'nalishda muammo bor.",
        "Strategiya: video soni, davomiyligi, chiqarish vaqti, nom/teg/opisaniye, oblojkalar, 48 soatlik va oxirgi 7 video ko'rishlari.",
    ):
        pdf.bullet(r, size=9.5)
    if unresolved:
        pdf.ln(2)
        pdf.para("Topilmagan havolalar: " + ", ".join(unresolved), size=9, color=GRAY)

    # --- Kanallar jadvali ---
    pdf.add_page()
    pdf.h1("1. Kanallar ro'yxati")
    cat = {"old": "Eski", "new": "Yangi", "mid": "O'rta"}
    rows = []
    for c in sorted(channels, key=lambda c: (c["category"] != "old", -c["subscribers"])):
        rows.append([
            c["title"][:28], cat[c["category"]], c["age_label"], _fmt(c["subscribers"]),
            f"{c['uploads_per_week']}" + ("" if c["active"] else " !"),
            f"{c['dur_avg_min']}", _fmt(c["last7_avg"]), _fmt(c["views_48h"]),
        ])
    pdf.table(
        ["Kanal", "Tur", "Yoshi", "Obunachi", "Video/hafta", "Davom. (daq)", "Oxirgi 7 o'rt.", "48 soat"],
        rows, widths=[40, 13, 17, 19, 20, 20, 24, 20],
    )
    pdf.para("«!» — haftasiga 3 tadan kam video (faol emas). «48 soat» — oxirgi 48 soatda chiqqan videolar yig'ilgan ko'rishlar.",
             size=8.5, color=GRAY)

    # --- Yuklash strategiyasi jadvali ---
    pdf.h1("2. Yuklash strategiyasi va vaqti")
    rows = []
    for c in channels:
        hours = ", ".join(f"{h:02d}:00" for h in c["top_hours"][:2]) or "—"
        rows.append([
            c["title"][:26], f"{c['uploads_per_day']}", f"{c['uploads_7d']}",
            hours, ", ".join(c["top_weekdays"][:2])[:22] or "—", f"{c['time_consistency']}%",
            f"{c['shorts_share']}%", c["last_upload"][:16] or "—",
        ])
    pdf.table(
        ["Kanal", "Video/kun", "7 kunda", "Soat (Toshkent)", "Kunlar", "Vaqt barqarorligi", "Shorts", "Oxirgi video"],
        rows, widths=[36, 16, 14, 26, 30, 22, 14, 26],
    )
    p = patterns or {}
    pdf.bullet(f"Yo'nalish bo'yicha eng ko'p chiqarish soatlari (Toshkent): "
               + ", ".join(f"{h:02d}:00" for h in p.get("top_hours", [])[:4]))
    pdf.bullet("Eng faol kunlar: " + ", ".join(p.get("top_weekdays", [])[:3]))
    pdf.bullet(("Kanallar chiqarish vaqti bo'yicha BIR-BIRIGA YAQIN." if p.get("time_similar")
                else "Kanallar chiqarish vaqti bo'yicha HAR XIL — umumiy qoida yo'q."))
    pdf.bullet(f"O'rtacha video/hafta: {p.get('uploads_per_week_avg', 0)}")

    # --- Davomiylik, nom, teg ---
    pdf.h1("3. Davomiylik, nomlar, teglar, opisaniye")
    rows = []
    for c in channels:
        rows.append([
            c["title"][:26], f"{c['dur_avg_min']}", f"{c['dur_min_min']}–{c['dur_max_min']}",
            f"{c['title_len']}", f"{c['title_numbers']}%", f"{c['title_caps']}%",
            f"{c['tags_avg']} ({c['tags_share']}%)", f"{c['desc_len']}", f"{c['engagement']}%",
        ])
    pdf.table(
        ["Kanal", "Davom. o'rt.", "Min–max", "Nom uzunl.", "Raqamli nom", "KATTA harf", "Teg (ulush)", "Opisaniye", "Faollik"],
        rows, widths=[34, 18, 20, 18, 19, 18, 22, 18, 15],
    )
    pdf.bullet(("Video davomiyligi kanallarda BIR-BIRIGA YAQIN" if p.get("dur_similar") else "Video davomiyligi kanallarda HAR XIL")
               + f" (o'rtacha {p.get('dur_avg_all', 0)} daqiqa, Shorts ulushi ~{p.get('shorts_avg', 0)}%).")
    pdf.bullet(("Nomlar uslubi o'xshash" if p.get("title_similar") else "Nomlar uslubi har xil")
               + f" (o'rtacha {p.get('title_len_avg', 0)} belgi, {p.get('title_numbers_avg', 0)}% nomda raqam bor).")
    pdf.bullet(f"Teglar: kanallarning {p.get('tags_share_avg', 0)}% videosida teg bor, o'rtacha {p.get('tags_avg_all', 0)} ta; kanallar o'rtasida teg mosligi {p.get('tag_overlap', 0)}%.")
    if p.get("shared_tags"):
        pdf.bullet("Umumiy teglar: " + ", ".join(p["shared_tags"][:15]))
    pdf.bullet(f"Opisaniye o'rtacha {p.get('desc_len_avg', 0)} belgi. Faollik (like+comment/ko'rish) o'rtacha {p.get('engagement_avg', 0)}%.")

    # --- Oxirgi 7 video ---
    pdf.h1("4. Har kanalning oxirgi 7 videosi")
    for c in channels:
        pdf.h2(f"{c['title']} — {c['url']}")
        rows = [[v["published"], v["title"][:60], f"{v['duration_min']}", _fmt(v["views"]), f"{v['tags']}"]
                for v in c["recent"]]
        if rows:
            pdf.table(["Sana", "Nom", "Daq.", "Ko'rish", "Teg"], rows, widths=[20, 100, 14, 22, 12], size=8)
        if c.get("outliers"):
            pdf.bullet("Portlagan video(lar): " + "; ".join(f"{o['title'][:50]} ({_fmt(o['views'])})" for o in c["outliers"]), size=9)

    # --- Oblojkalar ---
    pdf.add_page()
    pdf.h1("5. Oblojkalar (thumbnail)")
    if sheet_png:
        try:
            pdf.image_bytes(sheet_png)
        except Exception as e:
            logger.warning("Contact sheet PDFga qo'shilmadi: %s", e)
    if thumbs_text:
        pdf.ai_text(thumbs_text)

    # --- AI tahlil ---
    pdf.add_page()
    pdf.h1("6. Strategiya tahlili (AI)")
    pdf.para("Quyidagi matnni AI yuqoridagi raqamlar asosida yozdi. Xulosalarni o'zingiz ham tekshiring.",
             size=9, color=GRAY)
    pdf.ai_text(ai_text)

    # --- Tavsiya kanallar ---
    if suggestions:
        pdf.add_page()
        pdf.h1("7. Yo'nalishdagi boshqa kanallar (YouTube qidiruvi)")
        pdf.para("Oxirgi 6 oyda ko'p ko'rilgan videolar bo'yicha YouTube qidiruvidan topildi. "
                 "Bu AI o'ylab topgan emas, haqiqiy kanallar; lekin yo'nalishga mosligini o'zingiz tekshiring.",
                 size=9, color=GRAY)
        rows = [[s["title"][:34], _fmt(s["subscribers"]), f"{s['video_count']}", s.get("age_label", ""), s["url"]]
                for s in suggestions[:12]]
        pdf.table(["Kanal", "Obunachi", "Video", "Yoshi", "Havola"], rows, widths=[44, 20, 14, 18, 82], size=8)

    return bytes(pdf.output())


# ============================================================
# 7 kunlik kuzatuv yakuniy hisoboti
# ============================================================

def build_watch_report(student_name: str, niche_label: str, channel_rows: List[List[str]],
                       day_rows: List[List[str]], ai_text: str) -> bytes:
    now = datetime.now().strftime("%d.%m.%Y")
    pdf = ReportPDF("7 kunlik kuzatuv hisoboti — YouTube AI kursi")
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.ln(20)
    pdf.set_font("dv", "B", 22)
    pdf.set_text_color(*BLUE)
    pdf._mc(0, 10, "Raqobatchi kanallar: 7 kunlik kuzatuv")
    pdf.set_text_color(*DARK)
    pdf.para(f"Yo'nalish: {niche_label}\nO'quvchi: {student_name}\nSana: {now}", size=10.5)
    pdf.h1("1. Kanallar bo'yicha 7 kun natijasi")
    pdf.table(["Kanal", "Obunachi (boshi → oxiri)", "+Obunachi", "Yangi video", "7 kun ko'rish"],
              channel_rows, widths=[50, 44, 24, 24, 36])
    pdf.h1("2. Kunlar bo'yicha")
    pdf.table(["Kun", "Yangi videolar", "Eng ko'p ko'rilgan", "Obunachi o'sishi"], day_rows,
              widths=[22, 28, 96, 32], size=8)
    pdf.h1("3. Xulosa va tavsiyalar (AI)")
    pdf.ai_text(ai_text)
    return bytes(pdf.output())
