"""Load briefing.yaml configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class FeedConfig:
    url: str
    label: str = ""


@dataclass
class SubjectConfig:
    name: str
    keywords: list[str] = field(default_factory=list)


@dataclass
class BriefingConfig:
    feeds: list[FeedConfig] = field(default_factory=list)
    deep_dives: list[str] = field(default_factory=list)
    subjects: list[SubjectConfig] = field(default_factory=list)
    max_articles_per_feed: int = 5
    max_articles_total: int = 25


def load_briefing_config(path: Path) -> BriefingConfig:
    if not path.is_file():
        raise FileNotFoundError(f"Briefing config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    feeds: list[FeedConfig] = []
    for item in raw.get("feeds") or []:
        if isinstance(item, str):
            feeds.append(FeedConfig(url=item))
        elif isinstance(item, dict) and item.get("url"):
            feeds.append(
                FeedConfig(
                    url=str(item["url"]),
                    label=str(item.get("label") or ""),
                )
            )
    subjects: list[SubjectConfig] = []
    for item in raw.get("subjects") or []:
        if isinstance(item, dict) and item.get("name"):
            subjects.append(
                SubjectConfig(
                    name=str(item["name"]),
                    keywords=[str(k) for k in item.get("keywords") or []],
                )
            )
    deep_dives = [str(x) for x in raw.get("deep_dives") or []]
    return BriefingConfig(
        feeds=feeds,
        deep_dives=deep_dives,
        subjects=subjects,
        max_articles_per_feed=int(raw.get("max_articles_per_feed", 5)),
        max_articles_total=int(raw.get("max_articles_total", 25)),
    )
