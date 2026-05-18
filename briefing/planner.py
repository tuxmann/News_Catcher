"""Briefing script length and segment planning."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScriptBudget:
    target_minutes: int
    words_per_minute: int

    @property
    def target_words(self) -> int:
        return self.target_minutes * self.words_per_minute


def estimate_read_minutes(word_count: int, words_per_minute: int) -> float:
    if words_per_minute <= 0:
        return 0.0
    return word_count / words_per_minute
