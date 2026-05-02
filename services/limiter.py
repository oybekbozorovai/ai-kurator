"""Foydalanuvchi rate-limit + javob keshi."""
import hashlib
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

from cachetools import TTLCache

from config import ANSWER_CACHE_SIZE, ANSWER_CACHE_TTL, USER_RATE_LIMIT_PER_HOUR

# user_id → vaqt belgilari (so'nggi 1 soat ichida)
_user_history: Dict[int, Deque[float]] = {}

# savol matni hash'i → javob (24 soat)
_answer_cache: TTLCache = TTLCache(maxsize=ANSWER_CACHE_SIZE, ttl=ANSWER_CACHE_TTL)


def check_rate_limit(user_id: int) -> Tuple[bool, int]:
    """Foydalanuvchi limit ichidami? (allowed, remaining)."""
    now = time.time()
    window_start = now - 3600
    history = _user_history.setdefault(user_id, deque(maxlen=USER_RATE_LIMIT_PER_HOUR + 5))
    while history and history[0] < window_start:
        history.popleft()
    if len(history) >= USER_RATE_LIMIT_PER_HOUR:
        return False, 0
    history.append(now)
    return True, USER_RATE_LIMIT_PER_HOUR - len(history)


def _normalize_question(q: str) -> str:
    return " ".join(q.lower().split())


def _key(question: str) -> str:
    return hashlib.sha256(_normalize_question(question).encode("utf-8")).hexdigest()


def get_cached_answer(question: str) -> Optional[str]:
    return _answer_cache.get(_key(question))


def cache_answer(question: str, answer: str) -> None:
    _answer_cache[_key(question)] = answer
