"""RAG (Retrieval Augmented Generation) yadrosi.

Vector store sodda dizaynda: barcha bo'laklar va embedding'lar bitta JSON faylda.
50 soat video uchun ~5000 chunk = ~15MB → mahalliy faylda yaxshi ishlaydi."""
import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import google.generativeai as genai
import numpy as np

from config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    GEMINI_API_KEY,
    INDEX_DIR,
    TOP_K,
)

logger = logging.getLogger(__name__)
genai.configure(api_key=GEMINI_API_KEY)

INDEX_FILE = INDEX_DIR / "vector_store.json"
EMBEDDING_BATCH = 50


def _clean_name(name: str) -> str:
    """Nomdan keraksiz belgilarni tozalash: |, :, -, _ va ortiqcha probel."""
    name = name.strip().lstrip("|:-_").strip()
    name = name.replace("-", " ").replace("_", " ")
    name = re.sub(r"\s+", " ", name).strip()
    return name


def parse_module_lesson(source: str) -> Dict[str, str]:
    """Source path'idan modul va dars nomini ajratib oladi.

    Qo'llab-quvvatlanadigan nomlar:
      '00-modul-fikrlash/01-dars-kirish.md'
      '1-modul-youtube/04-Dars-monetizatsiya.md'
      '2-modul-yonalish-tanlash/3-dars-asmr-baby.md'
    """
    info: Dict[str, str] = {"module": "", "module_name": "", "lesson": "", "lesson_name": ""}
    parts = [p for p in source.replace("\\", "/").split("/") if p and p != "transcripts"]

    if len(parts) >= 2:
        m = re.match(r"^(\d+)[-_\s]*(?:modul[-_\s\|:.]*)?(.*)$", parts[-2], re.IGNORECASE)
        if m:
            info["module"] = str(int(m.group(1)))
            info["module_name"] = _clean_name(m.group(2))

    if parts:
        last = parts[-1].rsplit(".", 1)[0]
        m = re.match(r"^(\d+)[-_\s]*(?:dars[-_\s\|:.]*)?(.*)$", last, re.IGNORECASE)
        if m:
            info["lesson"] = str(int(m.group(1)))
            info["lesson_name"] = _clean_name(m.group(2))

    return info


def _format_header(info: Dict[str, str]) -> str:
    """Bo'lak boshiga qo'shiladigan sarlavha — embedding va Gemini ham ko'rishi uchun."""
    parts = []
    if info["module"]:
        m = f"{info['module']}-modul"
        if info["module_name"]:
            m += f" ({info['module_name']})"
        parts.append(m)
    if info["lesson"]:
        l = f"{info['lesson']}-dars"
        if info["lesson_name"]:
            l += f" ({info['lesson_name']})"
        parts.append(l)
    return " | ".join(parts) if parts else ""


def chunk_text(text: str, source: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[Dict]:
    """Matnni paragraf chegaralarini hurmat qilib bo'laklarga bo'ladi.
    Har bo'lak boshiga modul/dars sarlavhasi qo'shiladi."""
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if not text:
        return []

    info = parse_module_lesson(source)
    header = _format_header(info)
    # Sarlavha har bo'lakda bo'lsin — bo'sh joyni hisoblash uchun chunk size'dan ayiramiz
    effective_size = size - len(header) - 4 if header else size

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= effective_size:
            current = f"{current}\n\n{para}" if current else para
        else:
            if current:
                chunks.append(current)
            if len(para) <= effective_size:
                current = para
            else:
                for i in range(0, len(para), effective_size - overlap):
                    chunks.append(para[i:i + effective_size])
                current = ""
    if current:
        chunks.append(current)

    out: List[Dict] = []
    for i, c in enumerate(chunks):
        text_with_header = f"[{header}]\n{c}" if header else c
        out.append({
            "text": text_with_header,
            "source": source,
            "chunk_id": i,
            "module": info["module"],
            "lesson": info["lesson"],
        })
    return out


def _embed_batch(texts: List[str], task_type: str) -> np.ndarray:
    """Gemini embedding'larini olish — sinxron, batch."""
    result = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=texts,
        task_type=task_type,
        output_dimensionality=EMBEDDING_DIM,
    )
    return np.array(result["embedding"], dtype=np.float32)


async def embed_documents(texts: List[str]) -> np.ndarray:
    """Hujjat bo'laklarini batch'larda embed qiladi."""
    all_vecs: List[np.ndarray] = []
    for i in range(0, len(texts), EMBEDDING_BATCH):
        batch = texts[i:i + EMBEDDING_BATCH]
        vecs = await asyncio.to_thread(_embed_batch, batch, "RETRIEVAL_DOCUMENT")
        all_vecs.append(vecs)
        logger.info("Embedded %d/%d", min(i + EMBEDDING_BATCH, len(texts)), len(texts))
    return np.vstack(all_vecs) if all_vecs else np.zeros((0, EMBEDDING_DIM), dtype=np.float32)


async def embed_query(query: str) -> np.ndarray:
    """Bitta savolni embed qiladi."""
    vec = await asyncio.to_thread(_embed_batch, [query], "RETRIEVAL_QUERY")
    return vec[0]


class VectorStore:
    """Mahalliy JSON-da saqlanuvchi sodda vector store."""

    def __init__(self) -> None:
        self.chunks: List[Dict] = []
        self.matrix: np.ndarray = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)

    def load(self) -> None:
        if not INDEX_FILE.exists():
            logger.info("Vector store fayli topilmadi: %s", INDEX_FILE)
            return
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        self.chunks = data.get("chunks", [])
        embeddings = data.get("embeddings", [])
        if embeddings:
            self.matrix = np.array(embeddings, dtype=np.float32)
        logger.info("Vector store yuklandi: %d bo'lak", len(self.chunks))

    def save(self) -> None:
        INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "chunks": self.chunks,
            "embeddings": self.matrix.tolist(),
        }
        INDEX_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        logger.info("Vector store saqlandi: %d bo'lak", len(self.chunks))

    def remove_source(self, source: str) -> int:
        """Berilgan source'ga tegishli barcha bo'laklarni o'chiradi."""
        keep = [i for i, c in enumerate(self.chunks) if c["source"] != source]
        removed = len(self.chunks) - len(keep)
        if removed:
            self.chunks = [self.chunks[i] for i in keep]
            self.matrix = self.matrix[keep] if len(self.matrix) else self.matrix
        return removed

    def add(self, chunks: List[Dict], embeddings: np.ndarray) -> None:
        if not chunks:
            return
        self.chunks.extend(chunks)
        if len(self.matrix):
            self.matrix = np.vstack([self.matrix, embeddings])
        else:
            self.matrix = embeddings.copy()

    def search(self, query_vec: np.ndarray, k: int = TOP_K) -> List[Tuple[Dict, float]]:
        if not self.chunks or not len(self.matrix):
            return []
        # cosine similarity
        norms = np.linalg.norm(self.matrix, axis=1) * np.linalg.norm(query_vec)
        norms = np.where(norms == 0, 1e-9, norms)
        scores = (self.matrix @ query_vec) / norms
        top_idx = np.argsort(-scores)[:k]
        return [(self.chunks[i], float(scores[i])) for i in top_idx]

    def sources(self) -> List[str]:
        return sorted({c["source"] for c in self.chunks})

    def stats(self) -> Dict[str, int]:
        return {
            "chunks": len(self.chunks),
            "sources": len(self.sources()),
            "characters": sum(len(c["text"]) for c in self.chunks),
        }


_store: Optional[VectorStore] = None


def get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
        _store.load()
    return _store


async def retrieve(query: str, k: int = TOP_K) -> List[Tuple[Dict, float]]:
    """Savol uchun eng yaqin bo'laklarni topadi."""
    store = get_store()
    if not store.chunks:
        return []
    qvec = await embed_query(query)
    return store.search(qvec, k=k)


def format_context(hits: List[Tuple[Dict, float]]) -> str:
    """Topilgan bo'laklarni promptga qo'shish uchun format."""
    if not hits:
        return "(Hech qanday tegishli kurs materiali topilmadi.)"
    parts = []
    for chunk, score in hits:
        src = chunk["source"]
        module = chunk.get("module", "")
        lesson = chunk.get("lesson", "")
        label = src
        if module and lesson:
            label = f"{module}-modul {lesson}-dars ({src})"
        parts.append(f"### Manba: {label} (mosligi: {score:.2f})\n{chunk['text']}")
    return "\n\n---\n\n".join(parts)
