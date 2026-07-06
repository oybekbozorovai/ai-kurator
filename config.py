import os
import re
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

# --- Supabase (yagona auth/roster manbai — A.Y.P.I Platforma bilan umumiy) ---
SUPABASE_URL = _clean(os.getenv("SUPABASE_URL", "")).rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = _clean(os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))

# Qo'shtirnoq, probel, vergul yoki nuqta-vergul — qanday yozilsa ham ID'larni ajratadi
ADMIN_USER_IDS = {int(x) for x in re.findall(r"\d+", os.getenv("ADMIN_USER_IDS", ""))}

# Guruh/kanal ID'lari — qo'shtirnoq/probel bo'lsa ham to'g'ri o'qiladi.
# ID manfiy bo'lishi mumkin (masalan -1001234567890), shuning uchun -? bilan.
_group_ids = re.findall(r"-?\d+", os.getenv("COURSE_GROUP_ID", ""))
COURSE_GROUP_ID = int(_group_ids[0]) if _group_ids else None

# Standart kurs ruxsat muddati (oylarda)
COURSE_ACCESS_MONTHS = int(os.getenv("COURSE_ACCESS_MONTHS", "4"))

# Muddat tugaganda talabani chiqarib yuborish kerak bo'lgan chatlar (vergul bilan)
KICK_CHAT_IDS = [int(x) for x in re.findall(r"-?\d+", os.getenv("KICK_CHAT_IDS", ""))]

# --- YouTube xizmatlari: Replicate (rasm) + kunlik limitlar ---
REPLICATE_API_TOKEN = _clean(os.getenv("REPLICATE_API_TOKEN", ""))
FLUX_MODEL = "black-forest-labs/flux-schnell"
# Namunadan o'xshash rasm yaratish uchun (image-to-image)
FLUX_REDUX_MODEL = "black-forest-labs/flux-redux-schnell"
FLUX_DEV_MODEL = "black-forest-labs/flux-dev"
# Banner uchun (kompozitsiya sifati yuqori)
IDEOGRAM_MODEL = "ideogram-ai/ideogram-v2-turbo"

# Har bir o'quvchiga KUNLIK limit (adminlarga ta'sir qilmaydi)
DAILY_IMAGE_LIMIT = int(os.getenv("DAILY_IMAGE_LIMIT", "5"))
DAILY_TEXT_LIMIT = int(os.getenv("DAILY_TEXT_LIMIT", "20"))

# YouTube Data API v3 — kanal analizi uchun (ixtiyoriy; o'rnatilmasa xizmat "tez orada")
YOUTUBE_API_KEY = _clean(os.getenv("YOUTUBE_API_KEY", ""))

# YouTube ishlari tarixi uchun baza
YT_DB_PATH = BASE_DIR / "data" / "yt.db"

# --- Sertifikat ---
CERT_VERIFY_BASE_URL = _clean(os.getenv("CERT_VERIFY_BASE_URL", "https://youtubeai.uz/sert/"))
CERT_PROMPT_DAYS = int(os.getenv("CERT_PROMPT_DAYS", "10"))
COURSE_NAME = _clean(os.getenv("COURSE_NAME", "YouTube AI"))
CERT_TEMPLATE = BASE_DIR / "assets" / "certificate_template.png"
CERT_FONT = BASE_DIR / "assets" / "fonts" / "AlexBrush-Regular.ttf"

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
if ":" not in TELEGRAM_BOT_TOKEN or not TELEGRAM_BOT_TOKEN.split(":")[0].isdigit():
    raise RuntimeError(
        f"TELEGRAM_BOT_TOKEN format noto'g'ri (uzunligi: {len(TELEGRAM_BOT_TOKEN)}). "
        "Format: '12345:ABC...'. Railway Variables'da qo'shtirnoq qo'ymang!"
    )
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set")
if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError(
        "SUPABASE_URL va SUPABASE_SERVICE_ROLE_KEY o'rnatilishi shart "
        "(auth Supabase orqali ishlaydi). Railway Variables'da to'ldiring."
    )
