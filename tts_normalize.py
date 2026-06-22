"""Pre-TTS text normalization: literal and regex replacements from JSON."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegexReplacement:
    pattern: re.Pattern[str]
    replace: str


@dataclass
class TtsReplacementRules:
    """Ordered literal replacements (longest `from` first) plus regex rules."""

    literals: list[tuple[str, str]]
    regex: list[RegexReplacement]


_rules_cache: TtsReplacementRules | None = None
_rules_cache_path: Path | None = None


def _parse_replacements_file(path: Path) -> TtsReplacementRules:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: root must be a JSON object")

    literals: list[tuple[str, str]] = []
    for item in raw.get("replacements", []):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: each replacement must be an object")
        src = item.get("from")
        dst = item.get("to")
        if not isinstance(src, str) or not isinstance(dst, str):
            raise ValueError(f"{path}: replacement needs string 'from' and 'to'")
        if src:
            literals.append((src, dst))

    literals.sort(key=lambda pair: len(pair[0]), reverse=True)

    regex_rules: list[RegexReplacement] = []
    for item in raw.get("regex", []):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: each regex entry must be an object")
        pattern = item.get("pattern")
        replace = item.get("replace")
        if not isinstance(pattern, str) or not isinstance(replace, str):
            raise ValueError(f"{path}: regex entry needs string 'pattern' and 'replace'")
        flags = 0
        flag_str = item.get("flags", "")
        if isinstance(flag_str, str):
            if "i" in flag_str.lower():
                flags |= re.IGNORECASE
            if "m" in flag_str.lower():
                flags |= re.MULTILINE
        regex_rules.append(RegexReplacement(re.compile(pattern, flags), replace))

    return TtsReplacementRules(literals=literals, regex=regex_rules)


def load_tts_replacement_rules(path: Path | None = None, *, reload: bool = False) -> TtsReplacementRules:
    """Load rules from JSON; cached until path changes or reload=True."""
    global _rules_cache, _rules_cache_path

    path = (path or config.TTS_REPLACEMENTS_FILE).resolve()
    if not reload and _rules_cache is not None and _rules_cache_path == path:
        return _rules_cache

    if not path.is_file():
        logger.warning("TTS replacements file not found: %s (normalization skipped)", path)
        rules = TtsReplacementRules(literals=[], regex=[])
    else:
        try:
            rules = _parse_replacements_file(path)
            logger.debug(
                "Loaded TTS replacements from %s (%s literal, %s regex)",
                path,
                len(rules.literals),
                len(rules.regex),
            )
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error("Failed to load TTS replacements from %s: %s", path, e)
            rules = TtsReplacementRules(literals=[], regex=[])

    _rules_cache = rules
    _rules_cache_path = path
    return rules


def normalize_for_tts(
    text: str,
    *,
    rules: TtsReplacementRules | None = None,
    enabled: bool | None = None,
) -> str:
    """
    Apply literal then regex replacements for clearer KittenTTS pronunciation.

    No-op when disabled or when text is empty.
    """
    if not text or not text.strip():
        return text
    if enabled is None:
        enabled = config.TTS_NORMALIZE_ENABLED
    if not enabled:
        return text

    rules = rules if rules is not None else load_tts_replacement_rules()
    literals = sorted(rules.literals, key=lambda pair: len(pair[0]), reverse=True)
    out = text
    for src, dst in literals:
        out = re.sub(re.escape(src), dst, out, flags=re.IGNORECASE)
    for rule in rules.regex:
        out = rule.pattern.sub(rule.replace, out)
    return out


def clear_rules_cache() -> None:
    """Reset cached rules (for tests)."""
    global _rules_cache, _rules_cache_path
    _rules_cache = None
    _rules_cache_path = None


def _default_replacements_document() -> dict:
    return {"replacements": [], "regex": []}


def read_replacements_document(path: Path | None = None) -> dict:
    """Load replacements JSON; return empty structure if missing."""
    path = (path or config.TTS_REPLACEMENTS_FILE).resolve()
    if not path.is_file():
        return _default_replacements_document()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: root must be a JSON object")
    if "replacements" not in raw:
        raw["replacements"] = []
    if "regex" not in raw:
        raw["regex"] = []
    return raw


def write_replacements_document(doc: dict, path: Path | None = None) -> Path:
    path = (path or config.TTS_REPLACEMENTS_FILE).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    clear_rules_cache()
    return path


def add_literal_replacement(from_str: str, to_str: str, *, path: Path | None = None) -> bool:
    """
    Append a literal replacement if not already present. Returns True if added.
    """
    src = from_str.strip()
    dst = to_str.strip()
    if not src or not dst:
        raise ValueError("from and to must be non-empty")
    doc = read_replacements_document(path)
    replacements = doc.get("replacements", [])
    if not isinstance(replacements, list):
        replacements = []
    for item in replacements:
        if isinstance(item, dict) and item.get("from") == src:
            item["to"] = dst
            doc["replacements"] = replacements
            write_replacements_document(doc, path)
            return False
    replacements.append({"from": src, "to": dst})
    doc["replacements"] = replacements
    write_replacements_document(doc, path)
    return True
