"""fact_extractor: extract L3 (semantic) facts from step result text.

Phase 6. Simple regex/keyword based extractor (no LLM).
Detects "project uses X", "user prefers X", "codebase uses X" patterns.
"""

from __future__ import annotations

import re
from typing import Any


_PATTERNS = [
    # "project uses Python 3.12" / "uses uv"
    re.compile(
        r"(?:project|codebase|repo)\s+uses?\s+([\w\s\.\-/\+]{2,40})",
        re.IGNORECASE,
    ),
    # "we use X"
    re.compile(
        r"\bwe\s+use\s+([\w\s\.\-/\+]{2,40})",
        re.IGNORECASE,
    ),
    # "user prefers X"
    re.compile(
        r"user\s+prefers?\s+([\w\s\.\-/\+]{2,40})",
        re.IGNORECASE,
    ),
    # "uses X for Y"  - simpler pattern
    re.compile(
        r"\b(?:project|codebase|user)\b[^.]{0,20}\b(?:use|uses|using)\s+([A-Za-z][\w\.\-]{1,30})",
        re.IGNORECASE,
    ),
]

_STOP_WORDS = {
    "the", "a", "an", "this", "that", "these", "those", "is", "are", "was",
    "were", "be", "been", "have", "has", "had", "do", "does", "did",
    "to", "of", "in", "on", "at", "by", "for", "with", "from", "as", "it",
    "its", "i", "you", "we", "they", "he", "she", "them", "us",
}


def extract_facts(text: str, *, max_facts: int = 5) -> list[str]:
    """Extract candidate semantic facts from a step result text."""
    if not text:
        return []
    facts: list[str] = []
    seen: set[str] = set()
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(1).strip()
            # take first sentence / clause
            for sep in [",", ".", ";"]:
                idx = raw.find(sep)
                if idx > 0:
                    raw = raw[:idx]
            raw = raw.strip()
            if not raw or len(raw) < 2:
                continue
            # tokenize lower to check stopword
            toks = raw.lower().split()
            if toks and toks[0] in _STOP_WORDS:
                continue
            key = raw.lower()
            if key in seen:
                continue
            seen.add(key)
            facts.append(raw)
            if len(facts) >= max_facts:
                return facts
    return facts


def extract_episodic(text: str, *, max_facts: int = 3) -> list[str]:
    """Extract episodic (per-session) facts from a step result.

    Episodic facts are simpler: just sentences that look like a fact statement.
    """
    if not text:
        return []
    facts: list[str] = []
    # split into sentences
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for s in sentences:
        s = s.strip()
        if 5 < len(s) < 200 and any(
            kw in s.lower() for kw in ("verified", "added", "created", "fixed", "found", "ran", "compiled", "test")
        ):
            facts.append(s)
            if len(facts) >= max_facts:
                break
    return facts


__all__ = ["extract_facts", "extract_episodic"]
