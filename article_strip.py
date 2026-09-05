"""Per-site article strip rules: HTML selectors + trailing italic ads.

Rules only apply when the article domain is on the blog watchlist.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import config
from article_filters import normalize_site_key
from domains_store import host_allowed, registrable_domain_from_url
from watchlist_store import load_watchlist

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# Wholly italic/emphasized paragraph as emitted by trafilatura (*...* or _..._).
_WHOLE_EMPHASIS_RE = re.compile(
    r"^\*(?!\*)(.+)\*(?!\*)$|^_(?!_)(.+)_(?!_)$",
    re.DOTALL,
)


@dataclass
class SiteStripRules:
    domain: str
    html_selectors: list[str] = field(default_factory=list)
    drop_trailing_emphasis_paragraphs: bool = False


def _default_path() -> Path:
    return config.ARTICLE_STRIP_RULES_FILE


def _rules_from_raw(domain: str, raw: object) -> SiteStripRules | None:
    if not isinstance(raw, dict):
        return None
    selectors_raw = raw.get("html_selectors") or []
    selectors: list[str] = []
    if isinstance(selectors_raw, list):
        for item in selectors_raw:
            s = str(item).strip()
            if s and s not in selectors:
                selectors.append(s)
    drop = bool(raw.get("drop_trailing_emphasis_paragraphs"))
    if not selectors and not drop:
        return None
    return SiteStripRules(
        domain=domain,
        html_selectors=selectors,
        drop_trailing_emphasis_paragraphs=drop,
    )


def load_strip_rules(path: Path | None = None) -> dict[str, SiteStripRules]:
    path = (path or _default_path()).resolve()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    sites = raw.get("sites")
    if not isinstance(sites, dict):
        return {}
    out: dict[str, SiteStripRules] = {}
    for domain, body in sites.items():
        key = normalize_site_key(str(domain))
        if not key:
            continue
        rules = _rules_from_raw(key, body)
        if rules is not None:
            out[key] = rules
    return out


def save_strip_rules(
    rules: dict[str, SiteStripRules], path: Path | None = None
) -> Path:
    path = (path or _default_path()).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sites": {
            domain: {
                "html_selectors": list(r.html_selectors),
                "drop_trailing_emphasis_paragraphs": bool(
                    r.drop_trailing_emphasis_paragraphs
                ),
            }
            for domain, r in sorted(rules.items())
        }
    }
    with _lock:
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return path


def upsert_site_rules(rules: SiteStripRules, *, path: Path | None = None) -> SiteStripRules:
    key = normalize_site_key(rules.domain)
    if not key:
        raise ValueError("invalid domain")
    all_rules = load_strip_rules(path)
    cleaned = SiteStripRules(
        domain=key,
        html_selectors=[s.strip() for s in rules.html_selectors if s.strip()],
        drop_trailing_emphasis_paragraphs=bool(rules.drop_trailing_emphasis_paragraphs),
    )
    if not cleaned.html_selectors and not cleaned.drop_trailing_emphasis_paragraphs:
        all_rules.pop(key, None)
    else:
        all_rules[key] = cleaned
    save_strip_rules(all_rules, path)
    return cleaned


def remove_site_rules(domain: str, *, path: Path | None = None) -> bool:
    key = normalize_site_key(domain)
    if not key:
        return False
    all_rules = load_strip_rules(path)
    if key not in all_rules:
        return False
    del all_rules[key]
    save_strip_rules(all_rules, path)
    return True


def add_html_selector(domain: str, selector: str, *, path: Path | None = None) -> bool:
    """Add a CSS selector. Returns True if newly added."""
    key = normalize_site_key(domain)
    sel = selector.strip()
    if not key or not sel:
        raise ValueError("website and selector are required")
    all_rules = load_strip_rules(path)
    current = all_rules.get(key) or SiteStripRules(domain=key)
    if sel in current.html_selectors:
        return False
    current.html_selectors.append(sel)
    upsert_site_rules(current, path=path)
    return True


def set_trailing_emphasis(
    domain: str, enabled: bool, *, path: Path | None = None
) -> SiteStripRules:
    key = normalize_site_key(domain)
    if not key:
        raise ValueError("invalid domain")
    all_rules = load_strip_rules(path)
    current = all_rules.get(key) or SiteStripRules(domain=key)
    current.drop_trailing_emphasis_paragraphs = bool(enabled)
    return upsert_site_rules(current, path=path)


def watchlist_domains(*, watchlist_path: Path | None = None) -> set[str]:
    path = watchlist_path or config.WATCHLIST_FILE
    return {s.domain for s in load_watchlist(path)}


def domain_is_watchlisted(domain: str, *, watchlist_path: Path | None = None) -> bool:
    key = normalize_site_key(domain) or domain.strip().lower().rstrip(".")
    if not key:
        return False
    watched = watchlist_domains(watchlist_path=watchlist_path)
    # Allow subdomain matches against watchlist entries.
    return host_allowed(key, watched) or key in watched


def rules_for_url(
    source_url: str,
    *,
    path: Path | None = None,
    watchlist_path: Path | None = None,
    require_watchlist: bool = True,
) -> SiteStripRules | None:
    domain = registrable_domain_from_url(source_url)
    if not domain:
        return None
    if require_watchlist and not domain_is_watchlisted(
        domain, watchlist_path=watchlist_path
    ):
        return None
    return load_strip_rules(path).get(domain)


def apply_html_selectors(html: str, selectors: list[str]) -> str:
    """Remove matching nodes from HTML. Invalid selectors are skipped."""
    if not html or not selectors:
        return html
    try:
        from lxml import html as lhtml
    except ImportError:
        return html
    try:
        tree = lhtml.fromstring(html)
    except Exception:
        logger.debug("Could not parse HTML for strip selectors", exc_info=True)
        return html
    removed = 0
    for sel in selectors:
        try:
            nodes = tree.cssselect(sel)
        except Exception:
            logger.warning("Invalid CSS selector %r — skipped", sel)
            continue
        for node in nodes:
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
                removed += 1
    if removed:
        logger.info("Stripped %s HTML node(s) via selectors", removed)
    return lhtml.tostring(tree, encoding="unicode", method="html")


def paragraph_is_whole_emphasis(paragraph: str) -> bool:
    """True if the paragraph is entirely italic/emphasis-wrapped."""
    text = paragraph.strip()
    if not text:
        return False
    # Trafilatura often emits *whole paragraph* for <em>-only blocks.
    m = _WHOLE_EMPHASIS_RE.match(text)
    if m:
        return True
    # Also treat paragraphs that are only <em> content with a short non-em suffix
    # like "*FTC: …* More." when the suffix is a short CTA.
    m2 = re.match(
        r"^\*(?!\*)(.+)\*(?!\*)\s+(More\.?|Read more\.?|Learn more\.?)?\s*$",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    return bool(m2)


def strip_trailing_emphasis_paragraphs(text: str) -> str:
    """Drop wholly-emphasized paragraphs from the end of the article."""
    if not text or not text.strip():
        return text
    parts = [p.strip() for p in re.split(r"\n\s*\n+", text.strip()) if p.strip()]
    while parts and paragraph_is_whole_emphasis(parts[-1]):
        parts.pop()
    return "\n\n".join(parts)


def prepare_html_for_extract(html: str, source_url: str) -> str:
    """Apply watchlist-gated HTML selector strip rules before extraction."""
    rules = rules_for_url(source_url)
    if rules is None or not rules.html_selectors:
        return html
    return apply_html_selectors(html, rules.html_selectors)


def apply_text_strip_rules(text: str, source_url: str) -> str:
    """Apply watchlist-gated trailing-emphasis strip after extraction."""
    rules = rules_for_url(source_url)
    if rules is None or not rules.drop_trailing_emphasis_paragraphs:
        return text
    return strip_trailing_emphasis_paragraphs(text)
