"""BM25Okapi scoring with optional BM25F field boosting.

score(q, d) = sum(IDF(qi) * tf(qi,d) * (k1+1) /
                  (tf(qi,d) + k1*(1 - b + b*|d|/avgdl)))

Field boosting: score = sum_f(weight_f * BM25(q, d_f))
Each field scored with per-field length normalization;
IDF shared across fields from union of all field tokens.
"""

from __future__ import annotations

import math

# Default field weights for BM25F scoring.
# Higher weight = matches in that field are worth more.
FIELD_WEIGHTS: dict[str, float] = {
    "title": 4.0,
    "description": 3.0,
    "first_line": 2.0,
    "body": 1.0,
}


class BM25:
    """BM25Okapi ranking over a list of token sequences.

    Parameters:
        k1: Term frequency saturation. Default 1.5.
        b: Length normalization. Default 0.75.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._corpus: list[list[str]] = []
        self._doc_len: list[int] = []
        self._avgdl: float = 0.0
        self._n: int = 0
        self._df: dict[str, int] = {}

    def index(self, corpus: list[list[str]]) -> None:
        """Build the index from a list of token lists."""
        self._corpus = corpus
        self._n = len(corpus)
        if self._n == 0:
            return

        self._doc_len = [len(doc) for doc in corpus]
        self._avgdl = sum(self._doc_len) / self._n

        self._df = {}
        for doc in corpus:
            seen: set[str] = set()
            for token in doc:
                if token not in seen:
                    self._df[token] = self._df.get(token, 0) + 1
                    seen.add(token)

    def _idf(self, term: str) -> float:
        """Inverse document frequency for a term."""
        n_q = self._df.get(term, 0)
        return math.log((self._n - n_q + 0.5) / (n_q + 0.5) + 1)

    def _score_field(
        self,
        query_tokens: list[str],
        field_corpus: list[list[str]],
    ) -> list[float]:
        """Score one field with per-field length normalization, shared IDF."""
        n = len(field_corpus)
        if n == 0:
            return []

        field_lens = [len(doc) for doc in field_corpus]
        total = sum(field_lens)
        avgdl = total / n if n > 0 else 1.0

        scores = [0.0] * n
        for q in query_tokens:
            idf = self._idf(q)
            for i, doc in enumerate(field_corpus):
                tf = doc.count(q)
                if tf == 0:
                    continue
                dl = field_lens[i]
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * dl / (avgdl or 1.0))
                scores[i] += idf * numerator / denominator
        return scores

    def score(self, query_tokens: list[str]) -> list[float]:
        """Score every document against the query. Returns list of scores."""
        scores = [0.0] * self._n
        for q in query_tokens:
            idf = self._idf(q)
            for i, doc in enumerate(self._corpus):
                tf = doc.count(q)
                if tf == 0:
                    continue
                dl = self._doc_len[i]
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * dl / self._avgdl)
                scores[i] += idf * numerator / denominator
        return scores

    def score_fields(
        self,
        query_tokens: list[str],
        field_corpora: dict[str, list[list[str]]],
        weights: dict[str, float] | None = None,
    ) -> list[float]:
        """Score documents with BM25F field-weighted approach.

        Each field in field_corpora must have self._n document
        token lists. Weights default to FIELD_WEIGHTS.
        """
        if self._n == 0:
            return []

        if weights is None:
            weights = FIELD_WEIGHTS

        combined = [0.0] * self._n
        for field_name, corpus in field_corpora.items():
            if len(corpus) != self._n:
                raise ValueError(
                    f"Field '{field_name}' has {len(corpus)} docs, expected {self._n}"
                )
            w = weights.get(field_name, 1.0)
            if w == 0.0:
                continue
            field_scores = self._score_field(query_tokens, corpus)
            for i, s in enumerate(field_scores):
                combined[i] += w * s

        return combined
