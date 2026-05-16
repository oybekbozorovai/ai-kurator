import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
MATERIALS_DIR = BASE_DIR / "materials"
HOMEWORK_DIR = BASE_DIR / "homework_submissions"
PROMPTS_DIR = BASE_DIR / "prompts"
VIDEOS_DIR = BASE_DIR / "videos"
INDEX_DIR = BASE_DIR / "index"

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200
TOP_K = 5
EMBEDDING_MODEL = "models/gemini-embedding-001"
EMBEDDING_DIM = 768

USER_RATE_LIMIT_PER_HOUR = 15
ANSWER_CACHE_SIZE = 500
ANSWER_CACHE_TTL = 60 * 60 * 24

INDEX_DIR.mkdir(exist_ok=True)
VIDEOS_DIR.mkdir(exist_ok=True)
HOMEWORK_DIR.mkdir(exist_ok=True)
MATERIALS_DIR.mkdir(exist_ok=True)

def _clean(value: str) -> str:
    """Probel, qo'shtirnoq va ortiqcha belgilardan tozalash."""
    return value.strip().strip('"').strip("'").strip()


TELEGRAM_BOT_TOKEN = _clean(os.getenv("TELEGRAM_BOT_TOKEN", ""))
GEMINI_API_KEY = _clean(os.getenv("GEMINI_API_KEY", ""))
GEMINI_MODEL = _clean(os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))

ADMIN_USER_IDS = {
    int(uid.strip())
    for uid in os.getenv("ADMIN_USER_IDS", "").split(",")
    if uid.strip().isdigit()
}

_group_id = os.getenv("COURSE_GROUP_ID", "").strip()
COURSE_GROUP_ID = int(_group_id) if _group_id.lstrip("-").isdigit() else None

# Standart kurs ruxsat muddati (oylarda)
COURSE_ACCESS_MONTHS = int(os.getenv("COURSE_ACCESS_MONTHS", "4"))

# Muddat tugaganda talabani chiqarib yuborish kerak bo'lgan chatlar (vergul bilan)
KICK_CHAT_IDS = [
    int(x.strip()) for x in os.getenv("KICK_CHAT_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
]

# --- YouTube xizmatlari: Replicate (rasm) + kunlik limitlar ---
REPLICATE_API_TOKEN = _clean(os.getenv("REPLICATE_API_TOKEN", ""))
FLUX_MODEL = "black-forest-labs/flux-schnell"
# Namunadan o'xshash rasm yaratish uchun (image-to-image)
FLUX_REDUX_MODEL = "black-forest-labs/flux-redux-schnell"
FLUX_DEV_MODEL = "black-forest-labs/flux-dev"

# Har bir o'quvchiga KUNLIK limit (adminlarga ta'sir qilmaydi)
DAILY_IMAGE_LIMIT = int(os.getenv("DAILY_IMAGE_LIMIT", "5"))
DAILY_TEXT_LIMIT = int(os.getenv("DAILY_TEXT_LIMIT", "20"))

# YouTube ishlari tarixi uchun baza
YT_DB_PATH = BASE_DIR / "data" / "yt.db"

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
if ":" not in TELEGRAM_BOT_TOKEN or not TELEGRAM_BOT_TOKEN.split(":")[0].isdigit():
    raise RuntimeError(
        f"TELEGRAM_BOT_TOKEN format noto'g'ri (uzunligi: {len(TELEGRAM_BOT_TOKEN)}). "
        "Format: '12345:ABC...'. Railway Variables'da qo'shtirnoq qo'ymang!"
    )
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set")
