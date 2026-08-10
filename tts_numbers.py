"""Expand long numbers (3+ digits) to words for clearer KittenTTS speech."""

from __future__ import annotations

import re

# Standalone 3+ digit ints, including comma groups (1,000). Skip letter-hyphen
# model codes (A-380) and decimals (3.14 / .5).
_LONG_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9.])(?<![A-Za-z]-)"
    r"(?P<num>\d{1,3}(?:,\d{3})+|\d{3,})"
    r"(?![A-Za-z0-9.]|\.\d)"
)

_ONES = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
)
_TEENS = (
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS = (
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)

# Typical news-year window → pair reading ("nineteen seventy seven").
_YEAR_MIN = 1000
_YEAR_MAX = 2099


def _two_digits(n: int) -> str:
    """0–99 as spoken words (spaces, no hyphens)."""
    if n < 0 or n > 99:
        raise ValueError(f"expected 0–99, got {n}")
    if n < 10:
        return _ONES[n]
    if n < 20:
        return _TEENS[n - 10]
    tens, ones = divmod(n, 10)
    if ones == 0:
        return _TENS[tens]
    return f"{_TENS[tens]} {_ONES[ones]}"


def _under_thousand(n: int) -> str:
    """0–999 as spoken words."""
    if n < 0 or n > 999:
        raise ValueError(f"expected 0–999, got {n}")
    if n < 100:
        return _two_digits(n)
    hundreds, rest = divmod(n, 100)
    if rest == 0:
        return f"{_ONES[hundreds]} hundred"
    return f"{_ONES[hundreds]} hundred {_two_digits(rest)}"


def _cardinal(n: int) -> str:
    """Non-negative integer as American English cardinal words."""
    if n < 0:
        return f"minus {_cardinal(-n)}"
    if n < 1000:
        return _under_thousand(n)

    scales = (
        (1_000_000_000_000, "trillion"),
        (1_000_000_000, "billion"),
        (1_000_000, "million"),
        (1_000, "thousand"),
    )
    parts: list[str] = []
    rest = n
    for value, name in scales:
        if rest >= value:
            qty, rest = divmod(rest, value)
            parts.append(f"{_under_thousand(qty)} {name}")
    if rest:
        parts.append(_under_thousand(rest))
    return " ".join(parts) if parts else "zero"


def _year(n: int) -> str:
    """
    Four-digit year reading for KittenTTS.

    1977 → nineteen seventy seven
    1900 → nineteen hundred
    1905 → nineteen oh five
    2000 → two thousand
    2005 → two thousand five
    2015 → twenty fifteen
    """
    if n < _YEAR_MIN or n > _YEAR_MAX:
        return _cardinal(n)

    if n >= 2000:
        if n == 2000:
            return "two thousand"
        if n < 2010:
            return f"two thousand {_ONES[n - 2000]}"
        return f"twenty {_two_digits(n - 2000)}"

    # 1000–1999: read as two pairs.
    high, low = divmod(n, 100)
    high_words = _two_digits(high)
    if low == 0:
        return f"{high_words} hundred"
    if low < 10:
        return f"{high_words} oh {_ONES[low]}"
    return f"{high_words} {_two_digits(low)}"


def number_token_to_words(token: str) -> str:
    """Convert a digit token (optional commas) to spoken words."""
    raw = token.replace(",", "")
    if not raw.isdigit():
        return token
    n = int(raw)
    # Bare 4-digit years without commas → year cadence; comma form is a quantity.
    if "," not in token and _YEAR_MIN <= n <= _YEAR_MAX and len(raw) == 4:
        return _year(n)
    return _cardinal(n)


def apply_long_numbers(text: str) -> str:
    """Replace standalone 3+ digit numbers with spoken words."""
    if not text:
        return text

    def _sub(match: re.Match[str]) -> str:
        return number_token_to_words(match.group("num"))

    return _LONG_NUMBER_RE.sub(_sub, text)
