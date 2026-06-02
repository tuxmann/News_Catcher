"""Persist domains where HTTP 403 required bypass fallbacks (for logging and manual retry)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import config

_lock = threading.Lock()


def _path() -> Path:
    p = config.FALLBACK_DOMAINS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_recorded_fallback_domains() -> set[str]:
    path = _path()
    if not path.exists():
        return set()
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return set()
    domains = data.get("domains", [])
    if not isinstance(domains, list):
        return set()
    return {str(d).strip().lower() for d in domains if str(d).strip()}


def record_fallback_domain(domain: str) -> set[str]:
    """Add domain to the recorded list; return the full set."""
    d = domain.strip().lower()
    if not d:
        return load_recorded_fallback_domains()
    with _lock:
        current = load_recorded_fallback_domains()
        current.add(d)
        path = _path()
        path.write_text(
            json.dumps({"domains": sorted(current)}, indent=2) + "\n",
            encoding="utf-8",
        )
        return current
