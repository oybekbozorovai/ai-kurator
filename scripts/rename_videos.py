"""Video fayllarning nomlarini standart formatga keltirish skripti.

Qoida: <NN>-dars-<kebab-case-tavsif>.mp4

Foydalanish:
    .venv/bin/python -m scripts.rename_videos --dry-run   # faqat ko'rsatadi
    .venv/bin/python -m scripts.rename_videos             # haqiqiy qayta nomlash
"""
import argparse
import re
import sys
from pathlib import Path

VIDEOS_DIR = Path(__file__).parent.parent / "videos"

# Tavsif yo'q yoki noaniq fayllar uchun qo'lda kiritiladigan nomlar
# Format: filename → (lesson_number, description_kebab_case)
MANUAL_NAMES = {
    "00-modul-fikrlash-kutuvlar": {
        "1.mp4": (1, "kutuvlarni-togirlash-maqsadga-erishish"),
        "2.mp4": (2, "youtubeda-oziga-ishonch-uygotish"),
        "3.mp4": (3, "natijaga-chiqish-formulasi"),
        "4.mp4": (4, "xulosa"),
    },
    "04-modul-youtube-qoydalari": {
        # File adı `4\\3_Dars_...` (backslash with 3) — aslida 3-dars
        "4\\3_Dars_Maulliflik_huquqi_buzilgandan_audio_va_videoni_youtube_orqali.mp4":
            (3, "mualliflik-huquqi-buzilganda-youtubeda-togrilash"),
    },
    "06-modul-beamng-drive": {
        "1.mp4": (1, "steam-royxatdan-otish"),
        "2.mp4": (2, "steam-orqali-beamng-sotib-olish"),
        "3.mp4": (3, "beamng-drive-tanishish"),
    },
}


def to_kebab(text: str) -> str:
    """Matnni xavfsiz kebab-case fayl nomiga aylantirish."""
    text = text.lower().strip()
    # O'zbek apostroflarini olib tashlash
    text = text.replace("o'", "o").replace("o'", "o").replace("o`", "o")
    text = text.replace("g'", "g").replace("g'", "g").replace("g`", "g")
    # Ortiqcha apostroflarni olib tashlash
    text = text.replace("'", "").replace("'", "").replace("`", "").replace('"', "")
    # ASCII bo'lmagan belgilarni olib tashlash
    text = re.sub(r"[^a-z0-9\s\-]", " ", text)
    # Ortiqcha probel/tirelarni bittaga
    text = re.sub(r"[\s\-]+", "-", text).strip("-")
    # Uzunlik chegarasi
    if len(text) > 70:
        text = text[:70].rstrip("-")
    return text


def parse_lesson(filename: str) -> tuple:
    """Fayl nomidan dars raqami va tavsifni ajratish."""
    name = Path(filename).stem
    # "1- Dars Foo", "1-dars-Foo", "1 dars Foo", "1Foo", "1-Foo", "1 Foo"...
    m = re.match(
        r"^\s*(\d+)\s*[-_\s.|:\\]*\s*(?:darslik|dars)?\s*[-_:|.\\\s]*(.*)$",
        name, re.IGNORECASE
    )
    if not m:
        return 0, name
    try:
        number = int(m.group(1))
    except ValueError:
        return 0, name
    description = m.group(2).strip(" -_|:\\")
    return number, description


def rename_module(module_dir: Path, dry_run: bool) -> int:
    print(f"\n=== {module_dir.name} ===")
    manual = MANUAL_NAMES.get(module_dir.name, {})
    renamed = 0
    seen_targets = set()

    for f in sorted(module_dir.iterdir()):
        if not f.is_file() or f.suffix.lower() not in {".mp4", ".mkv", ".mov", ".webm", ".m4v"}:
            continue

        if f.name in manual:
            number, description = manual[f.name]
        else:
            number, raw_desc = parse_lesson(f.name)
            description = to_kebab(raw_desc)

        if number == 0:
            print(f"  ⚠️  Raqam topilmadi, o'tkazib yuborildi: {f.name}")
            continue

        if description:
            new_name = f"{number:02d}-dars-{description}{f.suffix.lower()}"
        else:
            new_name = f"{number:02d}-dars{f.suffix.lower()}"
        if new_name in seen_targets:
            # konflikt — taqdim eta olmaymiz
            print(f"  ⚠️  Konflikt: {new_name} avval ishlatilgan, o'tkazib yuborildi: {f.name}")
            continue
        seen_targets.add(new_name)

        if f.name == new_name:
            print(f"  ✓ ok: {f.name}")
            continue

        new_path = f.parent / new_name
        if new_path.exists() and not dry_run:
            print(f"  ⚠️  Target mavjud: {new_name}")
            continue

        if dry_run:
            print(f"  → {f.name}")
            print(f"     ↳ {new_name}")
        else:
            f.rename(new_path)
            print(f"  ✓ {f.name}")
            print(f"     → {new_name}")
        renamed += 1

    return renamed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Haqiqiy o'zgartirmasdan ko'rsat")
    args = parser.parse_args()

    if not VIDEOS_DIR.exists():
        print(f"videos/ papkasi topilmadi: {VIDEOS_DIR}")
        sys.exit(1)

    total = 0
    for mod_dir in sorted(VIDEOS_DIR.iterdir()):
        if mod_dir.is_dir() and mod_dir.name[0].isdigit():
            total += rename_module(mod_dir, args.dry_run)

    print(f"\n{'(DRY RUN) ' if args.dry_run else ''}Jami: {total} ta fayl qayta nomlanadi")


if __name__ == "__main__":
    main()
