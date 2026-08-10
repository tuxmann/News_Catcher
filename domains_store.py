"""Persisted approved-domain list (JSON)."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Iterable

_lock = threading.Lock()


def load_domains(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    domains = data.get("domains", [])
    if not isinstance(domains, list):
        return set()
    return {str(d).strip().lower() for d in domains if str(d).strip()}


def save_domains(path: Path, domains: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    unique = sorted({d.strip().lower() for d in domains if d.strip()})
    payload = {"domains": unique}
    with _lock:
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")


def load_bad_domains(path: Path) -> set[str]:
    """Domains that could not deliver usable articles."""
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    domains = data.get("domains", [])
    if not isinstance(domains, list):
        return set()
    return {str(d).strip().lower() for d in domains if str(d).strip()}


def save_bad_domains(path: Path, domains: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    unique = sorted({d.strip().lower() for d in domains if d.strip()})
    payload = {"domains": unique}
    with _lock:
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")


def add_bad_domain(
    bad_path: Path,
    good_path: Path,
    domain: str,
) -> None:
    """Move a domain off the allowlist and onto the bad list."""
    d = normalize_registrable_hint(domain)
    bad = load_bad_domains(bad_path)
    bad.add(d)
    save_bad_domains(bad_path, bad)
    good = load_domains(good_path)
    good.discard(d)
    save_domains(good_path, good)


def host_is_bad(hostname: str, bad_domains: set[str]) -> bool:
    """True if hostname matches a bad registrable domain or its subdomains."""
    return host_allowed(hostname, bad_domains)


def bad_domain_refusal_message(domain: str, *, for_bot: bool = True) -> str:
    """User-facing text when a URL is refused because the domain is on the bad list."""
    label = (domain or "").strip() or "This domain"
    if for_bot:
        return (
            f"{label} is on your bad-domains list (article extraction failed before). "
            "For troubleshooting you can temporarily override with /override_bad_domain "
            "(does not remove it from the bad list). "
            "Or permanently remove it with /remove_bad_domain."
        )
    return (
        f"{label} is on your bad-domains list (article extraction failed before). "
        "You can temporarily override to help with troubleshooting, "
        "or remove it from domains_bad.json to try again permanently."
    )


def normalize_registrable_hint(domain: str) -> str:
    """Lowercase host label; caller validates format."""
    return domain.strip().lower().rstrip(".")


def is_valid_registrable_domain(domain: str) -> bool:
    """True if domain looks like a single DNS name suitable for the allowlist."""
    d = normalize_registrable_hint(domain)
    if not d or "/" in d or ".." in d:
        return False
    return bool(re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", d))


def registrable_domain_from_url(url: str) -> str | None:
    """
    Best-effort registrable domain (eTLD+1) for a URL, for allowlist prompts.
    Returns None if the URL cannot be parsed.
    """
    try:
        from tld import get_tld
    except ImportError:
        return None
    try:
        res = get_tld(url.strip(), as_object=True, fail_silently=True)
    except Exception:
        return None
    if res is None:
        return None
    fld = getattr(res, "fld", None)
    if not fld or not isinstance(fld, str):
        return None
    d = fld.strip().lower().rstrip(".")
    return d or None


def host_allowed(hostname: str, allowed: set[str]) -> bool:
    """
    True if hostname matches an allowed registrable domain or its subdomains.
    allowed entries are like 'economist.com' (no leading dot).
    """
    host = hostname.strip().lower().rstrip(".")
    if not host:
        return False
    for base in allowed:
        if host == base or host.endswith("." + base):
            return True
    return False
