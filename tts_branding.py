"""Spoken source branding for article audio (intro / outro)."""

from __future__ import annotations

from tts_normalize import normalize_for_tts


def domain_to_spoken_name(domain: str, *, apply_pronunciation_rules: bool = True) -> str:
    """
    Turn a registrable domain into TTS-friendly speech.

    hackaday.com → "hackaday dot com" (then pronunciation rules, if any)
    fox10phoenix.com + rule → "fox ten fehnix dot com"
    """
    domain = domain.strip().lower().rstrip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    if not domain:
        return "this site"
    parts = domain.split(".")
    if len(parts) < 2:
        spoken = parts[0].replace("-", " ")
    else:
        label = parts[0].replace("-", " ")
        suffix = " dot ".join(parts[1:])
        spoken = f"{label} dot {suffix}"
    if apply_pronunciation_rules:
        spoken = normalize_for_tts(spoken)
    return spoken


def build_intro_text(source_domain: str | None) -> str | None:
    if not source_domain or not source_domain.strip():
        return None
    name = domain_to_spoken_name(source_domain)
    return normalize_for_tts(f"From {name}.")


def build_outro_text(source_domain: str | None) -> str | None:
    if not source_domain or not source_domain.strip():
        return None
    name = domain_to_spoken_name(source_domain)
    return normalize_for_tts(f"That's the end from {name}.")
