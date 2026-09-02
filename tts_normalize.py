"""Pre-TTS text normalization: literal and regex replacements from JSON."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import config
from tts_acronym import apply_short_caps_acronyms
from tts_month import apply_month_abbreviations
from tts_numbers import apply_long_numbers

logger = logging.getLogger(__name__)

# 3,000km → 3,000 (TTS reads the number; drop redundant unit).
_DISTANCE_UNIT_RE = re.compile(
    r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?:km|kilometers|kilometres|kms|mi|miles|mile)\b",
    re.IGNORECASE,
)

# Memorandum Of Understanding (MOU) → Memorandum Of Understanding
_REDUNDANT_ACRONYM_RE = re.compile(
    r"\b((?:[A-Z][\w]*\s+){1,}[A-Z][\w]*)\s+\(([A-Z]{2,10})\)(?=[\s.,;:!?\)]|$)"
)


@dataclass(frozen=True)
class LiteralReplacement:
    from_text: str
    to_text: str
    whole_word: bool = True
    ignore_case: bool = False


@dataclass(frozen=True)
class RegexReplacement:
    pattern: re.Pattern[str]
    replace: str


@dataclass
class TtsReplacementRules:
    """Ordered literal replacements (longest `from` first) plus regex rules."""

    literals: list[LiteralReplacement]
    regex: list[RegexReplacement]


_rules_cache: TtsReplacementRules | None = None
_rules_cache_path: Path | None = None
_rules_cache_mtime: float | None = None


def _replacements_file_mtime(path: Path) -> float | None:
    """Modification time for cache invalidation, or None if the file is missing."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _bool_field(raw: object, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return default


def _default_whole_word(from_text: str) -> bool:
    """Whole-word match avoids replacing US inside push."""
    if any(ch in from_text for ch in ".-/"):
        return True
    if len(from_text) <= 4 and from_text.isalpha():
        return True
    return True


def _literal_regex(src: str, whole_word: bool, ignore_case: bool) -> re.Pattern[str]:
    escaped = re.escape(src)
    if whole_word:
        body = rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
    else:
        body = escaped
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(body, flags)


def _parse_replacements_file(path: Path) -> TtsReplacementRules:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: root must be a JSON object")

    literals: list[LiteralReplacement] = []
    for item in raw.get("replacements", []):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: each replacement must be an object")
        src = item.get("from")
        dst = item.get("to")
        if not isinstance(src, str) or not isinstance(dst, str):
            raise ValueError(f"{path}: replacement needs string 'from' and 'to'")
        if not src:
            continue
        literals.append(
            LiteralReplacement(
                from_text=src,
                to_text=dst,
                whole_word=_bool_field(item.get("whole_word"), _default_whole_word(src)),
                ignore_case=_bool_field(item.get("ignore_case"), False),
            )
        )

    literals.sort(key=lambda r: len(r.from_text), reverse=True)

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
    """Load rules from JSON; reload when the file changes on disk."""
    global _rules_cache, _rules_cache_path, _rules_cache_mtime

    path = (path or config.TTS_REPLACEMENTS_FILE).resolve()
    file_mtime = _replacements_file_mtime(path)
    if (
        not reload
        and _rules_cache is not None
        and _rules_cache_path == path
        and _rules_cache_mtime == file_mtime
    ):
        return _rules_cache

    if not path.is_file():
        logger.warning("TTS replacements file not found: %s (normalization skipped)", path)
        rules = TtsReplacementRules(literals=[], regex=[])
    else:
        try:
            rules = _parse_replacements_file(path)
            if _rules_cache is None or _rules_cache_mtime != file_mtime:
                logger.info(
                    "Loaded TTS replacements from %s (%s literal, %s regex)",
                    path,
                    len(rules.literals),
                    len(rules.regex),
                )
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error("Failed to load TTS replacements from %s: %s", path, e)
            if _rules_cache is not None and _rules_cache_path == path:
                return _rules_cache
            rules = TtsReplacementRules(literals=[], regex=[])

    _rules_cache = rules
    _rules_cache_path = path
    _rules_cache_mtime = file_mtime
    return rules


def _apply_builtin_preprocess(text: str) -> str:
    text = _DISTANCE_UNIT_RE.sub(r"\1", text)
    text = _REDUNDANT_ACRONYM_RE.sub(r"\1", text)
    return text


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

    out = _apply_builtin_preprocess(text)
    rules = rules if rules is not None else load_tts_replacement_rules()
    literals = sorted(rules.literals, key=lambda r: len(r.from_text), reverse=True)
    for rule in literals:
        pattern = _literal_regex(rule.from_text, rule.whole_word, rule.ignore_case)
        out = pattern.sub(rule.to_text, out)
    for rule in rules.regex:
        out = rule.pattern.sub(rule.replace, out)
    out = apply_long_numbers(out)
    out = apply_month_abbreviations(out, rules)
    out = apply_short_caps_acronyms(out, rules)
    return out


def clear_rules_cache() -> None:
    """Reset cached rules (for tests)."""
    global _rules_cache, _rules_cache_path, _rules_cache_mtime
    _rules_cache = None
    _rules_cache_path = None
    _rules_cache_mtime = None


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


def add_literal_replacement(
    from_str: str,
    to_str: str,
    *,
    path: Path | None = None,
    whole_word: bool = True,
    ignore_case: bool = False,
) -> bool:
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
            item["whole_word"] = whole_word
            item["ignore_case"] = ignore_case
            doc["replacements"] = replacements
            write_replacements_document(doc, path)
            return False
    replacements.append(
        {
            "from": src,
            "to": dst,
            "whole_word": whole_word,
            "ignore_case": ignore_case,
        }
    )
    doc["replacements"] = replacements
    write_replacements_document(doc, path)
    return True


def remove_literal_replacement(from_str: str, *, path: Path | None = None) -> bool:
    """Remove a literal rule by exact `from` value. Returns True if removed."""
    src = from_str.strip()
    if not src:
        raise ValueError("from must be non-empty")
    doc = read_replacements_document(path)
    replacements = doc.get("replacements", [])
    if not isinstance(replacements, list):
        return False
    kept: list[object] = []
    removed = False
    for item in replacements:
        if isinstance(item, dict) and item.get("from") == src:
            removed = True
            continue
        kept.append(item)
    if not removed:
        return False
    doc["replacements"] = kept
    write_replacements_document(doc, path)
    return True


def _literal_from_doc_item(item: object) -> LiteralReplacement | None:
    if not isinstance(item, dict):
        return None
    src = item.get("from")
    dst = item.get("to")
    if not isinstance(src, str) or not isinstance(dst, str) or not src.strip():
        return None
    return LiteralReplacement(
        from_text=src,
        to_text=dst,
        whole_word=_bool_field(item.get("whole_word"), _default_whole_word(src)),
        ignore_case=_bool_field(item.get("ignore_case"), False),
    )


def list_literal_replacements(*, path: Path | None = None) -> list[LiteralReplacement]:
    """All literal rules from the replacements file (document order)."""
    doc = read_replacements_document(path)
    out: list[LiteralReplacement] = []
    for item in doc.get("replacements", []):
        rule = _literal_from_doc_item(item)
        if rule is not None:
            out.append(rule)
    return out


def _match_score(query: str, candidate: str) -> float:
    """Higher is better; used to rank existing pronunciation rules."""
    from difflib import SequenceMatcher

    q = query.casefold().strip()
    c = candidate.casefold().strip()
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if q in c or c in q:
        return 0.85 + 0.1 * (min(len(q), len(c)) / max(len(q), len(c)))
    return SequenceMatcher(None, q, c).ratio()


def find_literal_replacements(
    query: str,
    *,
    path: Path | None = None,
    limit: int = 8,
) -> list[LiteralReplacement]:
    """
    Rank literal rules by similarity to query (matches `from` or `to`).
    Returns up to `limit` rules, best first.
    """
    q = query.strip()
    if not q or limit <= 0:
        return []
    scored: list[tuple[float, int, LiteralReplacement]] = []
    for idx, rule in enumerate(list_literal_replacements(path=path)):
        score = max(_match_score(q, rule.from_text), _match_score(q, rule.to_text))
        # 0.55 filters structurally similar short tokens (a.m. vs U.S. ≈ 0.5).
        if score < 0.55:
            continue
        scored.append((score, idx, rule))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [rule for _, _, rule in scored[:limit]]
