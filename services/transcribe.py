"""Video/audio fayllarni Gemini orqali transkripsiya qilish.

Jarayon:
  1. ffmpeg orqali video'dan audio (mp3, mono, 64kbps) ajratiladi — bir necha barobar kichikroq
  2. Audio Gemini File API'ga yuklanadi
  3. Gemini transkripsiya qiladi (vaqt belgilari bilan)
"""
import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import google.generativeai as genai
from static_ffmpeg import add_paths

from config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)
genai.configure(api_key=GEMINI_API_KEY)

add_paths(weak=True)
_FFMPEG = shutil.which("ffmpeg")

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac"}

TRANSCRIBE_PROMPT = (
    "Quyidagi audio darsni to'liq transkripsiya qiling. "
    "Faqat aytilgan so'zlarni o'zbek tilida (yoki dars qaysi tilda bo'lsa, shu tilda) yozing. "
    "Har 30-60 soniyada [HH:MM:SS] ko'rinishida vaqt belgisi qo'ying. "
    "Hech narsa qo'shmang, izoh bermang, faqat sof transkript."
)


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTS


def extract_audio(video_path: Path, audio_path: Path) -> None:
    """Video'dan audio'ni ajratib oladi (mp3, mono, 64kbps)."""
    if not _FFMPEG:
        raise RuntimeError("ffmpeg topilmadi")
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _FFMPEG, "-y", "-loglevel", "error",
        "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k",
        str(audio_path),
    ]
    logger.info("Audio ajratilmoqda: %s → %s", video_path.name, audio_path.name)
    subprocess.run(cmd, check=True)


async def transcribe_audio(audio_path: Path) -> str:
    """Audio faylni Gemini orqali transkripsiya qiladi."""
    def _run() -> str:
        logger.info("Gemini File API'ga yuklanmoqda: %s", audio_path.name)
        uploaded = genai.upload_file(str(audio_path))
        # Fayl ACTIVE bo'lguncha kutamiz
        import time
        while uploaded.state.name == "PROCESSING":
            time.sleep(2)
            uploaded = genai.get_file(uploaded.name)
        if uploaded.state.name != "ACTIVE":
            raise RuntimeError(f"Fayl ACTIVE bo'lmadi: {uploaded.state.name}")

        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(
            [TRANSCRIBE_PROMPT, uploaded],
            generation_config={"temperature": 0.0, "max_output_tokens": 32000},
        )
        try:
            genai.delete_file(uploaded.name)
        except Exception:
            pass
        return response.text or ""

    return await asyncio.to_thread(_run)


async def transcribe_media(media_path: Path, output_dir: Path, work_dir: Optional[Path] = None) -> Path:
    """Video yoki audio faylni transkripsiya qilib, .md sifatida saqlaydi.
    Qaytaradi: yaratilgan transkript fayl yo'li."""
    output_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = output_dir / f"{media_path.stem}.md"

    if transcript_path.exists() and transcript_path.stat().st_size > 0:
        logger.info("Transkript allaqachon mavjud, o'tkazib yuborildi: %s", transcript_path.name)
        return transcript_path

    if is_video(media_path):
        work_dir = work_dir or media_path.parent / ".audio_cache"
        audio_path = work_dir / f"{media_path.stem}.mp3"
        if not audio_path.exists():
            extract_audio(media_path, audio_path)
        transcribe_path = audio_path
    elif is_audio(media_path):
        transcribe_path = media_path
    else:
        raise ValueError(f"Qo'llab-quvvatlanmaydigan format: {media_path.suffix}")

    text = await transcribe_audio(transcribe_path)
    if not text.strip():
        raise RuntimeError(f"Bo'sh transkript: {media_path.name}")

    transcript_path.write_text(
        f"# Transkript: {media_path.stem}\n\n_Manba fayl: {media_path.name}_\n\n{text.strip()}\n",
        encoding="utf-8",
    )
    logger.info("Transkript saqlandi: %s (%d belgi)", transcript_path.name, len(text))
    return transcript_path
