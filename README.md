# AI Kurator — Telegram bot

Onlayn kurs uchun AI yordamchi: talabalar savollariga kurs materiali asosida javob beradi va uy vazifalarni tekshiradi. Gemini API + RAG arxitekturasida qurilgan.

## Lokal ishga tushirish

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# .env yarating va to'ldiring
cp .env.example .env

# Kurs materialini index'ga yuklang
python -m scripts.ingest

# Botni ishga tushiring
python bot.py
```

## Komandalar

```bash
# Faqat matn fayllarni indekslash (PDF/MD/TXT — materials/ ichida)
python -m scripts.ingest --text-only

# Faqat video/audio fayllarni indekslash (videos/ ichida)
python -m scripts.ingest --videos-only

# Index'ni noldan qayta qurish
python -m scripts.ingest --rebuild

# Index holati
python -m scripts.ingest --stats
```

## Railway'ga deploy

Pastdagi env vars'ni Railway dashboard'da to'ldiring:

- `TELEGRAM_BOT_TOKEN`
- `GEMINI_API_KEY`
- `GEMINI_MODEL` (default: `gemini-2.5-flash`)
- `ADMIN_USER_IDS` (vergul bilan)
- `COURSE_GROUP_ID` (ixtiyoriy)
