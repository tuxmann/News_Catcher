"""Persist the last successfully extracted article per Telegram user."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class CachedArticle:
    user_id: int
    url: str
    title: str | None
    text: str
    saved_at: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> CachedArticle:
        return cls(
            user_id=int(data["user_id"]),
            url=str(data["url"]),
            title=data.get("title"),
            text=str(data["text"]),
            saved_at=float(data["saved_at"]),
        )


def _user_path(cache_dir: Path, user_id: int) -> Path:
    return cache_dir / f"{user_id}.json"


def save_last_article(
    cache_dir: Path,
    user_id: int,
    url: str,
    title: str | None,
    text: str,
) -> CachedArticle:
    cache_dir.mkdir(parents=True, exist_ok=True)
    article = CachedArticle(
        user_id=user_id,
        url=url,
        title=title,
        text=text,
        saved_at=time.time(),
    )
    path = _user_path(cache_dir, user_id)
    path.write_text(json.dumps(article.to_dict(), ensure_ascii=False), encoding="utf-8")
    return article


def load_last_article(
    cache_dir: Path,
    user_id: int,
    *,
    ttl_seconds: int,
) -> CachedArticle | None:
    path = _user_path(cache_dir, user_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        article = CachedArticle.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        path.unlink(missing_ok=True)
        return None
    if ttl_seconds > 0 and time.time() - article.saved_at > ttl_seconds:
        path.unlink(missing_ok=True)
        return None
    return article


def purge_expired(cache_dir: Path, ttl_seconds: int) -> int:
    if not cache_dir.is_dir() or ttl_seconds <= 0:
        return 0
    removed = 0
    now = time.time()
    for path in cache_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            saved_at = float(data.get("saved_at", 0))
        except (json.JSONDecodeError, TypeError, ValueError):
            path.unlink(missing_ok=True)
            removed += 1
            continue
        if now - saved_at > ttl_seconds:
            path.unlink(missing_ok=True)
            removed += 1
    return removed
