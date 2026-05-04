"""Har bir dars uchun PDF konspekt yaratish.

Jarayon:
  1. materials/transcripts/ ichidagi har transkriptni Gemini'ga yuboramiz
  2. Gemini strukturali konspekt yaratadi (asosiy nuqtalar, qadamlar, maslahatlar)
  3. Konspekt PDF qilib saqlanadi → materials/pdf/<modul>/<dars>.pdf

Foydalanish:
    .venv/bin/python -m scripts.generate_pdfs            # barcha darslarni
    .venv/bin/python -m scripts.generate_pdfs --rebuild  # mavjudlarini ham qaytadan
"""
import argparse
import logging
import re
import time
from pathlib import Path

import google.generativeai as genai
from fpdf import FPDF
from google.api_core import exceptions as gax_exceptions

from config import BASE_DIR, GEMINI_API_KEY, GEMINI_MODEL, MATERIALS_DIR
from services.rag import _format_header, parse_module_lesson

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
# fpdf2 ichidagi font subsetting log'lari juda ko'p — o'chirib qo'yamiz
logging.getLogger("fontTools").setLevel(logging.ERROR)
logging.getLogger("fpdf").setLevel(logging.WARNING)
logger = logging.getLogger("pdf")

genai.configure(api_key=GEMINI_API_KEY)

PDF_DIR = MATERIALS_DIR / "pdf"
TRANSCRIPTS_DIR = MATERIALS_DIR / "transcripts"

# macOS'dagi Arial Unicode — to'liq Unicode qo'llab-quvvatlash
FONT_PATH = "/Library/Fonts/Arial Unicode.ttf"
FONT_FALLBACKS = [
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Geneva.ttf",
]


def _resolve_font() -> str:
    for path in FONT_FALLBACKS:
        if Path(path).exists():
            return path
    raise RuntimeError(f"Unicode font topilmadi. Fallback'lar: {FONT_FALLBACKS}")


SUMMARIZE_PROMPT = """Siz dars konspektini yaratuvchi yordamchisiz. Quyidagi dars transkripti asosida talabaga foydali, qisqa va aniq konspekt yarating.

QAT'IY FORMAT (aynan shu strukturada, Markdown):

# {title}

**Modul/Dars:** {module_lesson}

## Mavzu

Bir-ikki jumlada darsning asosiy mavzusi.

## Asosiy nuqtalar

- Birinchi muhim fikr (qisqa, 1 jumla)
- Ikkinchi muhim fikr
- Uchinchi muhim fikr
- (3-7 ta nuqta)

## Qadamma-qadam yo'riqnoma

1. Birinchi qadam (agar darsda amaliy yo'riqnoma bo'lsa)
2. Ikkinchi qadam
3. ...
(Agar dars amaliy emas bo'lsa, "Bu darsda amaliy yo'riqnoma yo'q" deb yozing)

## Foydali maslahatlar

- Ustozning aytgan eng muhim maslahatlari (3-5 ta)

## Diqqat

- Eng muhim ogohlantirishlar yoki xato qilmaslik kerak bo'lgan joylar (agar darsda aytilgan bo'lsa)

## Asosiy tushunchalar va atamalar

- **Termin 1**: qisqa tushuntirish
- **Termin 2**: qisqa tushuntirish
- (4-8 ta termin)

QOIDALAR:
1. Faqat transkriptdagi ma'lumotlardan foydalaning, qo'shimcha ma'lumot kiritmang.
2. O'zbek tilida yozing.
3. Har bo'limni qisqa tuting — talaba bir qarashda asosiy nuqtalarni ushlay olsin.
4. Salomlashish, kirish so'zlari, emoji belgilari yo'q.

--- TRANSKRIPT ---
{transcript}
--- TRANSKRIPT OXIRI ---"""


def summarize_lesson(transcript_text: str, title: str, module_lesson: str) -> str:
    model = genai.GenerativeModel(
        GEMINI_MODEL,
        generation_config={"temperature": 0.2, "max_output_tokens": 3000},
    )
    prompt = SUMMARIZE_PROMPT.format(
        title=title,
        module_lesson=module_lesson,
        transcript=transcript_text,
    )
    wait = 60
    for attempt in range(4):
        try:
            response = model.generate_content(prompt)
            return response.text or ""
        except gax_exceptions.ResourceExhausted:
            if attempt == 3:
                raise
            logger.warning("Kvota chegarasi, %ds kutaman...", wait)
            time.sleep(wait)
            wait = min(wait * 2, 600)
    return ""


class LessonPDF(FPDF):
    def __init__(self):
        super().__init__()
        font_path = _resolve_font()
        # fpdf2 — bitta TTF ham R, B, I, BI uchun ishlatishi mumkin (Bold yo'q bo'lsa hajm bilan farqlash)
        self.add_font("uni", "", font_path)
        self.add_font("uni", "B", font_path)
        self.add_font("uni", "I", font_path)
        self.add_font("uni", "BI", font_path)
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(20, 20, 20)

    def header(self):
        if self.page_no() > 1:
            self.set_font("uni", "", 9)
            self.set_text_color(150, 150, 150)
            self.cell(0, 8, "AI Kurator — Dars konspekti", align="L")
            self.cell(0, 8, f"Sahifa {self.page_no()}", align="R")
            self.ln(10)
            self.set_text_color(0, 0, 0)

    def heading(self, text: str, level: int = 1) -> None:
        sizes = {1: 18, 2: 13, 3: 11}
        spacing_before = {1: 0, 2: 6, 3: 4}
        self.ln(spacing_before.get(level, 4))
        self.set_font("uni", "B", sizes.get(level, 11))
        if level == 1:
            self.set_text_color(26, 115, 232)
        else:
            self.set_text_color(44, 62, 80)
        self.multi_cell(0, sizes.get(level, 11) * 0.6, text)
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def paragraph(self, text: str) -> None:
        self.set_font("uni", "", 11)
        self.multi_cell(0, 6, text, markdown=True)
        self.ln(2)

    def bullet_item(self, text: str, indent: int = 0) -> None:
        self.set_font("uni", "", 11)
        x_start = 20 + indent * 4
        self.set_x(x_start)
        self.cell(5, 6, "•")
        self.set_x(x_start + 5)
        # Qolgan kenglik
        right_margin = 20
        page_w = self.w - x_start - right_margin - 5
        self.multi_cell(page_w, 6, text, markdown=True)

    def numbered_item(self, num: int, text: str) -> None:
        self.set_font("uni", "", 11)
        self.set_x(20)
        self.cell(8, 6, f"{num}.")
        self.set_x(28)
        page_w = self.w - 28 - 20
        self.multi_cell(page_w, 6, text, markdown=True)

    def hr(self) -> None:
        self.ln(3)
        self.set_draw_color(220, 220, 220)
        self.line(20, self.get_y(), self.w - 20, self.get_y())
        self.ln(4)


def md_to_pdf(md_text: str, output_path: Path) -> None:
    """Markdown matnni oddiy PDF formatga aylantiradi."""
    pdf = LessonPDF()
    pdf.add_page()

    lines = md_text.split("\n")
    i = 0
    num_counter = 0

    while i < len(lines):
        line = lines[i].rstrip()

        # Bo'sh qator
        if not line:
            pdf.ln(2)
            num_counter = 0
            i += 1
            continue

        # Sarlavhalar
        if line.startswith("# "):
            pdf.heading(line[2:].strip(), 1)
        elif line.startswith("## "):
            pdf.heading(line[3:].strip(), 2)
        elif line.startswith("### "):
            pdf.heading(line[4:].strip(), 3)
        # Horizontal rule
        elif re.match(r"^-{3,}\s*$", line):
            pdf.hr()
        # Bullet list
        elif re.match(r"^\s*[-*]\s+", line):
            indent = (len(line) - len(line.lstrip())) // 2
            text = re.sub(r"^\s*[-*]\s+", "", line)
            pdf.bullet_item(text, indent=indent)
        # Numbered list
        elif re.match(r"^\d+\.\s+", line):
            num_counter += 1
            text = re.sub(r"^\d+\.\s+", "", line)
            pdf.numbered_item(num_counter, text)
        else:
            num_counter = 0
            pdf.paragraph(line)
        i += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_path))


def process_transcript(transcript_path: Path, output_path: Path) -> None:
    rel = transcript_path.relative_to(TRANSCRIPTS_DIR)
    source = f"transcripts/{rel.as_posix()}"
    info = parse_module_lesson(source)
    header = _format_header(info)

    text = transcript_path.read_text(encoding="utf-8")
    if not text.strip():
        logger.warning("Bo'sh transkript: %s", rel)
        return

    title = info.get("lesson_name", "").title() or transcript_path.stem
    module_lesson = header or transcript_path.stem

    logger.info("Konspekt yaratilmoqda: %s", rel)
    summary = summarize_lesson(text, title=title, module_lesson=module_lesson)
    if not summary.strip():
        logger.warning("Bo'sh konspekt: %s", rel)
        return

    md_to_pdf(summary, output_path)
    logger.info("✅ PDF saqlandi: %s", output_path.relative_to(BASE_DIR))


def main():
    parser = argparse.ArgumentParser(description="Har bir dars uchun PDF konspekt")
    parser.add_argument("--rebuild", action="store_true", help="Mavjud PDFlarni qaytadan yarat")
    args = parser.parse_args()

    if not TRANSCRIPTS_DIR.exists():
        logger.error("Transkriptlar topilmadi: %s", TRANSCRIPTS_DIR)
        return

    transcripts = sorted(TRANSCRIPTS_DIR.rglob("*.md"))
    if not transcripts:
        logger.error("Transkriptlar yo'q.")
        return

    logger.info("Topildi: %d ta transkript", len(transcripts))

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    done = 0
    skipped = 0
    failed = 0

    for transcript_path in transcripts:
        rel = transcript_path.relative_to(TRANSCRIPTS_DIR)
        output_path = PDF_DIR / rel.with_suffix(".pdf")

        if output_path.exists() and not args.rebuild:
            skipped += 1
            continue

        try:
            process_transcript(transcript_path, output_path)
            done += 1
        except Exception as e:
            logger.exception("Xato: %s — %s", rel, e)
            failed += 1

    logger.info(
        "✅ Tugadi. Yaratildi: %d ta, o'tkazib yuborildi: %d ta, xato: %d ta. Joy: %s",
        done, skipped, failed, PDF_DIR.relative_to(BASE_DIR),
    )


if __name__ == "__main__":
    main()
