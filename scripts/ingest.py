"""Kurs materiallarini index'ga yuklash skripti.

Foydalanish:
    .venv/bin/python -m scripts.ingest                # hammasini yuklaydi (matn + video)
    .venv/bin/python -m scripts.ingest --text-only    # faqat materials/ dagi PDF/MD/TXT
    .venv/bin/python -m scripts.ingest --videos-only  # faqat videos/ dagi video/audio
    .venv/bin/python -m scripts.ingest --rebuild      # index'ni noldan qayta quradi
    .venv/bin/python -m scripts.ingest --stats        # joriy index haqida ma'lumot
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

from pypdf import PdfReader

from config import MATERIALS_DIR, VIDEOS_DIR, INDEX_DIR
from services.rag import VectorStore, chunk_text, embed_documents, get_store
from services.transcribe import is_audio, is_video, transcribe_media

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ingest")

TEXT_EXTS = {".md", ".txt"}
PDF_EXTS = {".pdf"}


def read_text_file(path: Path) -> str:
    if path.suffix.lower() in PDF_EXTS:
        try:
            reader = PdfReader(str(path))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as e:
            logger.warning("PDF o'qib bo'lmadi %s: %s", path, e)
            return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


async def ingest_text_files(store: VectorStore, force: bool = False) -> int:
    """materials/ ichidagi PDF/MD/TXT fayllarni indexga yuklaydi."""
    files = [
        p for p in sorted(MATERIALS_DIR.rglob("*"))
        if p.is_file() and p.suffix.lower() in (TEXT_EXTS | PDF_EXTS)
    ]
    if not files:
        logger.info("materials/ papkasida matn fayllari topilmadi.")
        return 0

    existing = set(store.sources())
    added_count = 0

    for path in files:
        rel = str(path.relative_to(MATERIALS_DIR))
        if rel in existing and not force:
            logger.info("O'tkazib yuborildi (allaqachon indexda): %s", rel)
            continue
        if force:
            store.remove_source(rel)

        text = read_text_file(path)
        if not text.strip():
            logger.warning("Bo'sh fayl: %s", rel)
            continue

        chunks = chunk_text(text, source=rel)
        if not chunks:
            continue

        embeddings = await embed_documents([c["text"] for c in chunks])
        store.add(chunks, embeddings)
        added_count += len(chunks)
        logger.info("Qo'shildi: %s — %d bo'lak", rel, len(chunks))
        store.save()  # har faylda saqlash — uzilib qolsa, ish yo'qolmaydi

    return added_count


async def ingest_videos(store: VectorStore, force: bool = False) -> int:
    """videos/ ichidagi video/audio fayllarni transkripsiya qilib, indexga yuklaydi.
    Transkriptlar materials/transcripts/ ga ham saqlanadi."""
    # Yashirin papkalarni (.audio_cache va boshqalarni) o'tkazib yuborish
    files = [
        p for p in sorted(VIDEOS_DIR.rglob("*"))
        if p.is_file()
        and (is_video(p) or is_audio(p))
        and not any(part.startswith(".") for part in p.relative_to(VIDEOS_DIR).parts)
    ]
    if not files:
        logger.info("videos/ papkasida video/audio fayllari topilmadi.")
        return 0

    transcripts_dir = MATERIALS_DIR / "transcripts"
    existing = set(store.sources())
    added_count = 0

    for path in files:
        # Modul papkasini saqlab qolish: videos/03-modul/04-dars.mp4 → transcripts/03-modul/04-dars.md
        rel = path.relative_to(VIDEOS_DIR)
        source_name = f"transcripts/{rel.with_suffix('.md').as_posix()}"
        if source_name in existing and not force:
            logger.info("O'tkazib yuborildi (allaqachon indexda): %s", rel)
            continue
        if force:
            store.remove_source(source_name)

        output_subdir = transcripts_dir / rel.parent
        try:
            transcript_path = await transcribe_media(path, output_subdir)
        except Exception as e:
            logger.exception("Transkripsiya muvaffaqiyatsiz: %s — %s", rel, e)
            continue

        text = transcript_path.read_text(encoding="utf-8")
        chunks = chunk_text(text, source=source_name)
        if not chunks:
            continue

        embeddings = await embed_documents([c["text"] for c in chunks])
        store.add(chunks, embeddings)
        added_count += len(chunks)
        logger.info("Qo'shildi: %s — %d bo'lak", path.name, len(chunks))
        store.save()

    return added_count


async def main() -> None:
    parser = argparse.ArgumentParser(description="Kurs materiallarini indexga yuklash")
    parser.add_argument("--text-only", action="store_true", help="Faqat matn fayllar")
    parser.add_argument("--videos-only", action="store_true", help="Faqat video/audio")
    parser.add_argument("--rebuild", action="store_true", help="Index'ni noldan qayta qurish")
    parser.add_argument("--stats", action="store_true", help="Index haqida ma'lumot ko'rsatish")
    args = parser.parse_args()

    store = get_store()

    if args.stats:
        stats = store.stats()
        print(f"Manbalar: {stats['sources']}")
        print(f"Bo'laklar: {stats['chunks']}")
        print(f"Belgilar: {stats['characters']:,}")
        print("\nManbalar ro'yxati:")
        for s in store.sources():
            print(f"  - {s}")
        return

    if args.rebuild:
        store.chunks = []
        import numpy as np
        from config import EMBEDDING_DIM
        store.matrix = np.zeros((0, EMBEDDING_DIM), dtype="float32")
        store.save()
        logger.info("Index tozalandi.")

    total = 0
    if not args.videos_only:
        total += await ingest_text_files(store, force=args.rebuild)
    if not args.text_only:
        total += await ingest_videos(store, force=args.rebuild)

    store.save()
    stats = store.stats()
    logger.info("✅ Tugadi: %d ta bo'lak qo'shildi. Jami indexda: %d ta bo'lak, %d ta manba",
                total, stats["chunks"], stats["sources"])


if __name__ == "__main__":
    asyncio.run(main())
