"""Transcript post-processing (deterministic term replacement)."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence

class TranscriptProcessor(ABC):
    @abstractmethod
    def process(self, text: str) -> str: ...


def _replacement_pattern(keys: Sequence[str]) -> re.Pattern[str]:
    # Longer keys win at the same position; ASCII word edges require a word
    # boundary so "diff" does not match inside "different". Japanese has no
    # word boundaries, so those keys match as plain substrings.
    parts = []
    for key in sorted(keys, key=len, reverse=True):
        pattern = re.escape(key)
        if key[0].isascii() and (key[0].isalnum() or key[0] == "_"):
            pattern = r"\b" + pattern
        if key[-1].isascii() and (key[-1].isalnum() or key[-1] == "_"):
            pattern = pattern + r"\b"
        parts.append(pattern)
    return re.compile("|".join(parts))


class Replacer(TranscriptProcessor):
    """Single-pass, case-sensitive replacement of table keys only, so a
    replacement result is never re-matched and no unspoken words appear."""

    def __init__(self, replacements: Mapping[str, str]):
        self._table = {k: v for k, v in replacements.items() if k}
        self._pattern = _replacement_pattern(list(self._table)) if self._table else None

    def process(self, text: str) -> str:
        if not text or self._pattern is None:
            return text
        return self._pattern.sub(lambda m: self._table[m.group(0)], text)
