"""Tokenization and stopword filtering for BM25 search.

Splits text into lowercase tokens, removing non-alphanumeric
characters and common English stopwords.
"""

from __future__ import annotations

import re

STOPWORDS = frozenset({
    "the", "is", "at", "which", "on", "a", "an", "and", "or", "but",
    "in", "of", "to", "for", "with", "as", "by", "from", "it", "its",
    "this", "that", "be", "are", "was", "were", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "can", "not", "no", "so",
    "if", "then", "than", "more", "also", "into", "about", "up",
    "out", "just", "over", "only", "very", "how", "all", "each",
    "any", "some", "such", "other", "what", "when", "where", "who",
    "why",
})


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, remove stopwords."""
    tokens = re.split(r"[^a-z0-9]+", text.lower())
    return [t for t in tokens if t and t not in STOPWORDS]
