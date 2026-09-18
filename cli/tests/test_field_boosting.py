"""Tests for BM25F field boosting: weighted scoring, ranking improvements."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_memory.bm25 import BM25, FIELD_WEIGHTS
from agent_memory.parser import Frontmatter
from agent_memory.search import SectionDocument, _build_field_corpora, search
from agent_memory.tokenizer import tokenize


class TestFieldWeightsConfig:
    """Verify default field weights are correct and configurable."""

    def test_default_weights_exist(self) -> None:
        assert "title" in FIELD_WEIGHTS
        assert "description" in FIELD_WEIGHTS
        assert "first_line" in FIELD_WEIGHTS
        assert "body" in FIELD_WEIGHTS

    def test_default_weight_values(self) -> None:
        assert FIELD_WEIGHTS["title"] == 4.0
        assert FIELD_WEIGHTS["description"] == 3.0
        assert FIELD_WEIGHTS["first_line"] == 2.0
        assert FIELD_WEIGHTS["body"] == 1.0

    def test_title_highest_weight(self) -> None:
        assert FIELD_WEIGHTS["title"] > FIELD_WEIGHTS["description"]
        assert FIELD_WEIGHTS["description"] > FIELD_WEIGHTS["first_line"]
        assert FIELD_WEIGHTS["first_line"] > FIELD_WEIGHTS["body"]


class TestBM25ScoreFields:
    """Test the BM25.score_fields() method directly."""

    def test_empty_corpus(self) -> None:
        bm25 = BM25()
        bm25.index([])
        result = bm25.score_fields(["test"], {})
        assert result == []

    def test_single_field_matches_score(self) -> None:
        """With one field at weight 1.0, score_fields == score."""
        bm25 = BM25()
        corpus = [["nan", "policy", "data"], ["shift", "window"]]
        bm25.index(corpus)
        flat_scores = bm25.score(["nan"])
        field_scores = bm25.score_fields(
            ["nan"],
            {"body": corpus},
            weights={"body": 1.0},
        )
        assert len(field_scores) == 2
        for flat, field in zip(flat_scores, field_scores):
            assert abs(flat - field) < 1e-9

    def test_weight_multiplier_effect(self) -> None:
        """Higher weight produces proportionally higher score."""
        bm25 = BM25()
        corpus = [["nan", "policy"]]
        bm25.index(corpus)
        score_w1 = bm25.score_fields(["nan"], {"f": corpus}, weights={"f": 1.0})
        score_w4 = bm25.score_fields(["nan"], {"f": corpus}, weights={"f": 4.0})
        assert score_w4[0] == pytest.approx(score_w1[0] * 4.0, rel=1e-9)

    def test_title_match_beats_body_match(self) -> None:
        """Term in title should score higher than same term in body."""
        bm25 = BM25()
        # Doc 0: term in title only; Doc 1: term in body only
        union_corpus = [["deployment"], ["deployment"]]
        bm25.index(union_corpus)

        title_match = bm25.score_fields(
            ["deployment"],
            {
                "title": [["deployment"], []],
                "body": [[], ["deployment"]],
            },
            weights={"title": 4.0, "body": 1.0},
        )
        assert title_match[0] > title_match[1]
        assert title_match[0] > 0
        assert title_match[1] > 0

    def test_description_match_beats_body_match(self) -> None:
        """Term in description scores higher than in body only."""
        bm25 = BM25()
        union_corpus = [["auth"], ["auth"]]
        bm25.index(union_corpus)

        scores = bm25.score_fields(
            ["auth"],
            {
                "description": [["auth"], []],
                "body": [[], ["auth"]],
            },
            weights={"description": 3.0, "body": 1.0},
        )
        assert scores[0] > scores[1]

    def test_multiple_fields_combine(self) -> None:
        """Score combines contributions from all fields."""
        bm25 = BM25()
        union_corpus = [["deploy", "guide"]]
        bm25.index(union_corpus)

        title_only = bm25.score_fields(
            ["deploy"],
            {"title": [["deploy"]], "body": [[]]},
            weights={"title": 4.0, "body": 1.0},
        )
        both_fields = bm25.score_fields(
            ["deploy"],
            {"title": [["deploy"]], "body": [["deploy"]]},
            weights={"title": 4.0, "body": 1.0},
        )
        assert both_fields[0] > title_only[0]

    def test_zero_weight_field_ignored(self) -> None:
        """Fields with weight 0.0 do not contribute to score."""
        bm25 = BM25()
        corpus = [["test"]]
        bm25.index(corpus)

        with_field = bm25.score_fields(
            ["test"],
            {"a": corpus, "b": corpus},
            weights={"a": 1.0, "b": 1.0},
        )
        without_field = bm25.score_fields(
            ["test"],
            {"a": corpus, "b": corpus},
            weights={"a": 1.0, "b": 0.0},
        )
        assert with_field[0] > without_field[0]

    def test_mismatched_field_length_raises(self) -> None:
        """Field corpus with wrong doc count raises ValueError."""
        bm25 = BM25()
        bm25.index([["a"], ["b"]])
        with pytest.raises(ValueError, match="has 1 docs, expected 2"):
            bm25.score_fields(
                ["a"],
                {"bad": [["a"]]},
            )

    def test_custom_weights_override_defaults(self) -> None:
        """Custom weights dict replaces FIELD_WEIGHTS entirely."""
        bm25 = BM25()
        corpus = [["term"]]
        bm25.index(corpus)
        custom = {"myfield": 10.0}
        scores = bm25.score_fields(["term"], {"myfield": corpus}, weights=custom)
        assert scores[0] > 0

    def test_unknown_field_gets_weight_one(self) -> None:
        """Fields not in weights dict get weight 1.0."""
        bm25 = BM25()
        corpus = [["term"]]
        bm25.index(corpus)
        scores = bm25.score_fields(
            ["term"],
            {"unknown_field": corpus},
            weights={},
        )
        flat = bm25.score(["term"])
        assert abs(scores[0] - flat[0]) < 1e-9


class TestBuildFieldCorpora:
    """Test _build_field_corpora helper."""

    def _make_doc(
        self,
        title: str = "Title",
        fm_desc: str = "Frontmatter desc",
        sec_desc: str = "Section first line",
        content: str = "Body content here",
    ) -> SectionDocument:
        fm = Frontmatter(description=fm_desc, raw={"description": fm_desc})
        return SectionDocument(
            file_path="test.md",
            section_title=title,
            section_description=sec_desc,
            section_content=content,
            frontmatter=fm,
            tokens=tokenize(f"{title} {sec_desc} {content}"),
        )

    def test_returns_four_fields(self) -> None:
        docs = [self._make_doc()]
        corpora = _build_field_corpora(docs)
        assert set(corpora.keys()) == {"title", "description", "first_line", "body"}

    def test_title_tokens_from_section_title(self) -> None:
        docs = [self._make_doc(title="Deployment Guide")]
        corpora = _build_field_corpora(docs)
        assert "deployment" in corpora["title"][0]
        assert "guide" in corpora["title"][0]

    def test_description_tokens_from_frontmatter(self) -> None:
        docs = [self._make_doc(fm_desc="NaN policy for pipelines")]
        corpora = _build_field_corpora(docs)
        assert "nan" in corpora["description"][0]
        assert "policy" in corpora["description"][0]
        assert "pipelines" in corpora["description"][0]

    def test_first_line_tokens_from_section_description(self) -> None:
        docs = [self._make_doc(sec_desc="How shift prevents bias")]
        corpora = _build_field_corpora(docs)
        assert "shift" in corpora["first_line"][0]
        assert "prevents" in corpora["first_line"][0]
        assert "bias" in corpora["first_line"][0]

    def test_body_tokens_from_content(self) -> None:
        docs = [self._make_doc(content="The expanding window uses NaN")]
        corpora = _build_field_corpora(docs)
        assert "expanding" in corpora["body"][0]
        assert "nan" in corpora["body"][0]

    def test_empty_fields_produce_empty_tokens(self) -> None:
        docs = [self._make_doc(fm_desc="", sec_desc="", content="")]
        corpora = _build_field_corpora(docs)
        assert corpora["description"][0] == []
        assert corpora["first_line"][0] == []
        assert corpora["body"][0] == []


def _create_boosting_memory_tree(tmp_path: Path) -> Path:
    """Create memory entries designed to test field boosting.

    entry-a: "deployment" in title, body about something else
    entry-b: "deployment" only in body, different title
    entry-c: "deployment" in frontmatter description, not in body
    """
    base = tmp_path / "memory"
    agent_dir = base / "test" / "atlas"
    agent_dir.mkdir(parents=True)

    (agent_dir / "entry-a.md").write_text(
        "---\n"
        "description: Server infrastructure documentation\n"
        "author: test\n"
        "created: 2026-03-01T00:00:00Z\n"
        "updated: 2026-03-01T00:00:00Z\n"
        "tags: [infra]\n"
        "category: atlas\n"
        "status: active\n"
        "---\n"
        "# Entry A\n\n"
        "## Deployment Guide\n"
        "Step by step process for server setup.\n\n"
        "Install the package and configure the service.\n"
    )

    (agent_dir / "entry-b.md").write_text(
        "---\n"
        "description: Internal tools and workflows\n"
        "author: test\n"
        "created: 2026-03-01T00:00:00Z\n"
        "updated: 2026-03-01T00:00:00Z\n"
        "tags: [tools]\n"
        "category: atlas\n"
        "status: active\n"
        "---\n"
        "# Entry B\n\n"
        "## Server Configuration\n"
        "How to configure servers for production.\n\n"
        "The deployment process requires SSH access and proper keys.\n"
    )

    (agent_dir / "entry-c.md").write_text(
        "---\n"
        "description: Deployment patterns and best practices\n"
        "author: test\n"
        "created: 2026-03-01T00:00:00Z\n"
        "updated: 2026-03-01T00:00:00Z\n"
        "tags: [patterns]\n"
        "category: atlas\n"
        "status: active\n"
        "---\n"
        "# Entry C\n\n"
        "## Architecture Patterns\n"
        "Common server architecture approaches.\n\n"
        "Blue-green and canary releases minimize risk.\n"
    )

    return base


class TestFieldBoostingRanking:
    """Integration tests: title/description matches rank higher."""

    def test_title_match_ranks_first(self, tmp_path: Path) -> None:
        """Entry with query term in section title should rank above
        entry with same term only in body."""
        base = _create_boosting_memory_tree(tmp_path)
        results = search("deployment", base_path=base, no_cache=True)
        assert len(results) >= 2
        # entry-a has "deployment" in title, should rank highest
        assert "entry-a" in results[0].path

    def test_frontmatter_description_boosts(self, tmp_path: Path) -> None:
        """Entry with term in frontmatter description should rank
        above entry with term only in body."""
        base = _create_boosting_memory_tree(tmp_path)
        results = search("deployment", base_path=base, no_cache=True)
        # entry-c has "deployment" in frontmatter description
        # entry-b has "deployment" only in body text
        paths = [r.path for r in results]
        c_idx = next(i for i, p in enumerate(paths) if "entry-c" in p)
        b_idx = next(i for i, p in enumerate(paths) if "entry-b" in p)
        assert c_idx < b_idx, (
            f"entry-c (frontmatter desc match) at rank {c_idx + 1} "
            f"should outrank entry-b (body only) at rank {b_idx + 1}"
        )

    def test_results_still_returned(self, tmp_path: Path) -> None:
        """All matching entries should still appear in results."""
        base = _create_boosting_memory_tree(tmp_path)
        results = search("deployment", base_path=base, no_cache=True)
        paths = {r.path for r in results}
        assert any("entry-a" in p for p in paths)
        assert any("entry-b" in p for p in paths)
        assert any("entry-c" in p for p in paths)

    def test_no_results_for_unrelated_query(self, tmp_path: Path) -> None:
        """Unrelated queries return empty -- field boosting does not
        conjure results."""
        base = _create_boosting_memory_tree(tmp_path)
        results = search("xyzzyzzyxyz", base_path=base, no_cache=True)
        assert results == []

    def test_field_description_mode_unchanged(self, tmp_path: Path) -> None:
        """--field description still searches section descriptions only,
        no field boosting applied."""
        base = _create_boosting_memory_tree(tmp_path)
        results = search("step", base_path=base, field="description", no_cache=True)
        assert len(results) > 0
        # "step" appears in entry-a's section description
        assert "entry-a" in results[0].path

    def test_field_tags_mode_unchanged(self, tmp_path: Path) -> None:
        """--field tags still searches tag fields only."""
        base = _create_boosting_memory_tree(tmp_path)
        results = search("patterns", base_path=base, field="tags", no_cache=True)
        assert len(results) > 0
        assert "entry-c" in results[0].path

    def test_scores_are_positive(self, tmp_path: Path) -> None:
        """All returned scores should be positive."""
        base = _create_boosting_memory_tree(tmp_path)
        results = search("deployment", base_path=base, no_cache=True)
        for r in results:
            assert r.score > 0

    def test_scores_are_rounded(self, tmp_path: Path) -> None:
        """Scores should be rounded to 2 decimal places."""
        base = _create_boosting_memory_tree(tmp_path)
        results = search("deployment", base_path=base, no_cache=True)
        for r in results:
            assert r.score == round(r.score, 2)


class TestFieldBoostingCLI:
    """CLI integration tests for field-boosted search output."""

    def test_json_output_with_boosting(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_boosting_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--json-output",
                "search",
                "deployment",
                "--base",
                str(base),
                "--no-cache",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) >= 2
        # Title match should be first
        assert "entry-a" in data[0]["path"]

    def test_human_output_with_boosting(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from agent_memory.cli import cli

        base = _create_boosting_memory_tree(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["search", "deployment", "--base", str(base), "--no-cache"],
        )
        assert result.exit_code == 0
        assert "entry-a.md" in result.output
        assert "Deployment Guide" in result.output


class TestBackwardCompatibility:
    """Verify existing search behavior is preserved."""

    def _create_memory_tree(self, tmp_path: Path) -> Path:
        base = tmp_path / "memory"
        kelvin_dir = base / "kelvin" / "atlas"
        kelvin_dir.mkdir(parents=True)

        (kelvin_dir / "nan-policy.md").write_text(
            "---\n"
            "description: NaN policy for data pipeline\n"
            "author: kelvin\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-11T14:30:00Z\n"
            "tags: [data, nan, pipeline]\n"
            "confidence: established\n"
            "category: atlas\n"
            "status: active\n"
            "---\n"
            "# NaN Policy\n\n"
            "## Rationale\n"
            "Why missing values must not be filled with defaults.\n\n"
            "NaN represents genuine absence of data. Filling with zeros "
            "or means introduces silent bias.\n\n"
            "## Implementation\n"
            "How the pipeline enforces NaN propagation.\n\n"
            "Every transform checks for NaN and propagates it forward.\n"
        )

        (kelvin_dir / "shift-policy.md").write_text(
            "---\n"
            "description: Shift-one policy for z-score computation\n"
            "author: kelvin\n"
            "created: 2026-02-10T08:00:00Z\n"
            "updated: 2026-02-11T14:30:00Z\n"
            "tags: [data, zscore, lookahead]\n"
            "confidence: working\n"
            "category: atlas\n"
            "status: active\n"
            "---\n"
            "# Shift-One Policy\n\n"
            "## Overview\n"
            "How shift(1) prevents lookahead bias in z-score.\n\n"
            "The expanding window z-score uses shift(1) to prevent "
            "data leakage from future observations.\n\n"
            "## Edge Cases\n"
            "When shift produces NaN at the boundary.\n\n"
            "The first observation after shift(1) is always NaN.\n"
        )

        return base

    def test_basic_search_still_works(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("NaN missing data", base_path=base, no_cache=True)
        assert len(results) > 0
        assert results[0].score > 0

    def test_results_still_ranked(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("NaN", base_path=base, no_cache=True)
        for i in range(len(results) - 1):
            assert results[i].score >= results[i + 1].score

    def test_empty_query_still_empty(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        assert search("", base_path=base, no_cache=True) == []

    def test_stopwords_only_still_empty(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        assert search("the is at", base_path=base, no_cache=True) == []

    def test_scope_filtering_preserved(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("NaN", base_path=base, scope="agent:kelvin", no_cache=True)
        for r in results:
            assert r.path.startswith("kelvin/")

    def test_confidence_filter_preserved(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("NaN", base_path=base, confidence="established", no_cache=True)
        for r in results:
            assert r.file_frontmatter.get("confidence") == "established"

    def test_limit_preserved(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("NaN", base_path=base, limit=1, no_cache=True)
        assert len(results) <= 1

    def test_snippet_populated(self, tmp_path: Path) -> None:
        base = self._create_memory_tree(tmp_path)
        results = search("NaN genuine absence", base_path=base, no_cache=True)
        assert len(results) > 0
        assert results[0].snippet != ""
