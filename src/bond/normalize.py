"""Quote normalization, shared by search and the QUOTE check.

Folds the differences OCR and copy-paste introduce without changing meaning:

- runs of whitespace (including NBSP) become one space; leading and trailing
  whitespace is dropped
- curly quotes become straight quotes
- a hyphen at a line break between letters is removed ("medi-\\ncation")
- soft hyphens are removed
- common Latin ligatures are expanded ("ﬁ" -> "fi")
- optionally, case is folded

Every normalized character remembers the raw span it came from, so a match in
normalized text maps back to an exact range of the raw text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_QUOTES = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
}
_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "ﬅ": "st", "ﬆ": "st",
}
_SOFT_HYPHEN = "­"
# A hyphen ending a line, between letters: "medi-\n  cation".
_HYPHEN_BREAK = re.compile(r"(?<=[^\W\d_])-[ \t]*\r?\n\s*(?=[^\W\d_])")


@dataclass(frozen=True)
class Normalized:
    text: str
    spans: list[tuple[int, int]]  # raw [start, end) for each normalized character

    def raw_range(self, start: int, end: int) -> tuple[int, int]:
        """Map a normalized range [start, end) back to a raw range."""
        return self.spans[start][0], self.spans[end - 1][1]


def normalize(raw: str, fold_case: bool = True) -> Normalized:
    removed = [False] * len(raw)
    for m in _HYPHEN_BREAK.finditer(raw):
        for i in range(m.start(), m.end()):
            removed[i] = True

    out: list[str] = []
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(raw)
    while i < n:
        if removed[i] or raw[i] == _SOFT_HYPHEN:
            i += 1
            continue
        c = raw[i]
        if c.isspace():
            j = i
            while j < n and (raw[j].isspace() or removed[j]):
                j += 1
            out.append(" ")
            spans.append((i, j))
            i = j
            continue
        rep = _QUOTES.get(c) or _LIGATURES.get(c) or c
        if fold_case:
            rep = rep.lower()
        for ch in rep:
            out.append(ch)
            spans.append((i, i + 1))
        i += 1

    # Trim the leading/trailing space produced by edge whitespace.
    start, end = 0, len(out)
    if end and out[0] == " ":
        start = 1
    if end > start and out[end - 1] == " ":
        end -= 1
    return Normalized("".join(out[start:end]), spans[start:end])


def find_all(haystack: Normalized, needle: str) -> list[tuple[int, int]]:
    """All (possibly overlapping) occurrences of needle, as raw ranges."""
    if not needle:
        return []
    hits = []
    i = haystack.text.find(needle)
    while i != -1:
        hits.append(haystack.raw_range(i, i + len(needle)))
        i = haystack.text.find(needle, i + 1)
    return hits


def clip(s: str, n: int = 60) -> str:
    """s on one line, cut to at most n characters, for messages."""
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"
