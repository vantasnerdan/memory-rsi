"""Snippet extraction for search results.

Finds the sentence or passage with the highest density of query
terms within a section's content. Trims to a maximum length.
"""

from __future__ import annotations

import re


def extract_snippet(content: str, query_tokens: list[str],
                    max_len: int = 150) -> str:
    """Extract the best matching snippet from section content.

    Finds the sentence with the highest density of query terms,
    then trims to max_len characters.
    """
    sentences = re.split(r"(?<=[.!?])\s+|\n\n+", content)
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        return ""

    if not query_tokens:
        text = sentences[0]
        if len(text) > max_len:
            return text[:max_len - 3] + "..."
        return text

    best_score = -1.0
    best_sentence = sentences[0]

    for sentence in sentences:
        if sentence.startswith("## "):
            continue
        words = set(re.split(r"[^a-z0-9]+", sentence.lower()))
        hits = sum(1 for t in query_tokens if t in words)
        word_count = len(words) or 1
        density = hits / word_count
        if density > best_score:
            best_score = density
            best_sentence = sentence

    if len(best_sentence) > max_len:
        return best_sentence[:max_len - 3] + "..."
    return best_sentence
