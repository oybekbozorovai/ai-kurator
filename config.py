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

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp")

ADMIN_USER_IDS = {
    int(uid.strip())
    for uid in os.getenv("ADMIN_USER_IDS", "").split(",")
    if uid.strip().isdigit()
}

_group_id = os.getenv("COURSE_GROUP_ID", "").strip()
COURSE_GROUP_ID = int(_group_id) if _group_id.lstrip("-").isdigit() else None

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set in .env")
